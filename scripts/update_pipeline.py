#!/usr/bin/env python3
"""
Refresh the whole 2026 pipeline when new NISAR L2 GCOV granules appear.

The pipeline spans two machines, so this runs in three steps:

  1. check   (laptop or ORCD, needs internet)
       Ask ASF which GCOV granules cover the fields, compare with what has
       already been processed (data/nisar_field_clips/index.csv). If there are
       new ones:
         * add them to scripts/nisar_granule_index.csv and
           scripts/granules_to_download.txt (inputs of download_granules.py);
         * move END forward to the newest acquisition (local date) in
           scripts/gee_s1_fields_2026.js, scripts/gee_s2_fields_2026.js
           (END_DATE, exclusive = END + 1 day), build_daily_dataset_2026.py and
           build_causal_features_2026.py (END, inclusive). END never moves back.
       Then: paste the two GEE scripts into the Code Editor, run the exports, and
       save the CSVs as data/S1_fields_2026.csv and data/S2_fields_2026.csv.

  2. orcd    (on ORCD, where the .h5 granules live; long -> use the .sbatch)
       download_granules.py -> clip_fields.py -> incidence_at_fields.py.
       Then copy data/nisar_field_clips/ and outputs/incidence_2026.csv to the
       laptop (the command is printed at the end).

  3. local   (laptop)
       Checks the inputs are up to date, then runs
       nisar_field_means.ipynb -> build_daily_dataset_2026.py ->
       build_causal_features_2026.py, and with --notebooks also executes
       data_exploratory.ipynb and train_model.ipynb.

    python scripts/update_pipeline.py check            # add --dry-run to change nothing
    python scripts/update_pipeline.py orcd             # or: sbatch scripts/update_pipeline_orcd.sbatch
    python scripts/update_pipeline.py local --notebooks
    python scripts/update_pipeline.py status           # what is processed / stale, no network

Water level is NOT refreshed by default (the loggers stopped by September). Use
`local --water` after adding new logger files to data/waterlevel_uarkansas/.
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

# ============================ CONFIG ==========================================
START = "2026-03-15"                       # first local day of the season (unchanged)
TZ = "America/Chicago"
AOI_WKT = ("POLYGON ((-91.876 34.481, -91.738 34.481, -91.738 34.864, "
           "-91.876 34.864, -91.876 34.481))")   # same rectangle as the GEE scripts

GRANULE_INDEX = "scripts/nisar_granule_index.csv"
GRANULE_LIST  = "scripts/granules_to_download.txt"
CLIP_INDEX    = "data/nisar_field_clips/index.csv"
NISAR_MEANS   = "outputs/nisar_field_means_2026.csv"
INCIDENCE_CSV = "outputs/incidence_2026.csv"
S1_CSV, S2_CSV = "data/S1_fields_2026.csv", "data/S2_fields_2026.csv"
GRANULE_DIR   = "data/nisar"
STATE_JSON    = "outputs/pipeline_state.json"

GEE_SCRIPTS = ["scripts/gee_s1_fields_2026.js", "scripts/gee_s2_fields_2026.js"]
PY_END_FILES = ["scripts/build_daily_dataset_2026.py", "scripts/build_causal_features_2026.py"]

NOTEBOOKS_DATA  = ["notebooks/nisar_field_means.ipynb"]
NOTEBOOKS_FINAL = ["notebooks/data_exploratory.ipynb", "notebooks/train_model.ipynb"]
LOGGERS_GLOB = "data/waterlevel_uarkansas/2026_*Master_file_*.xlsx"
READINGS_CSV = "data/waterlevel_uarkansas/readings_2026.csv"

# Where the repo sits on ORCD, for the copy-back command printed after `orcd`.
ORCD_HOST, ORCD_REPO = "orcd-login.mit.edu", "~/rice-methane-downscaling"
# ==============================================================================


def say(msg=""):
    print(msg, flush=True)


def local_date(utc):
    t = pd.to_datetime(utc, utc=True)
    if isinstance(t, pd.Series):
        return t.dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()
    return t.tz_convert(TZ).tz_localize(None).normalize()


def processed_granules():
    """Granules already clipped (the clip index is the record of processed passes)."""
    p = ROOT / CLIP_INDEX
    return set(pd.read_csv(p).granule) if p.exists() else set()


def current_end():
    """END currently set in build_causal_features_2026.py."""
    text = (ROOT / PY_END_FILES[1]).read_text()
    return pd.Timestamp(re.search(r'^END\s*=\s*"(\d{4}-\d{2}-\d{2})"', text, re.M).group(1))


# ---------------------------------------------------------------- check -------
def query_asf():
    """Every GCOV granule over the field rectangle, START..today, from ASF metadata."""
    import asf_search as asf
    from shapely.geometry import shape
    res = asf.search(dataset=asf.DATASET.NISAR, intersectsWith=AOI_WKT, processingLevel="GCOV",
                     start=START, end=dt.date.today().isoformat(), maxResults=10000)
    rows = []
    for g in res:
        p = g.properties
        b = p.get("bytes") or 0
        if isinstance(b, dict):                       # one entry per distributed file
            b = sum((v.get("bytes") or 0) for k, v in b.items() if str(k).endswith(".h5")) or \
                sum((v.get("bytes") or 0) for v in b.values())
        rows.append({
            "granule": p["sceneName"], "track": p.get("pathNumber"), "frame": p.get("frameNumber"),
            "direction": "ASC" if (p.get("flightDirection") or "").upper().startswith("A") else "DESC",
            "acq_datetime_utc": p["startTime"],
            "pols": "+".join(p.get("mainBandPolarization") or []),
            "bandwidth_mhz": "+".join(p.get("rangeBandwidth") or []),
            "frame_coverage": p.get("frameCoverage"), "size_gb": round(b / 1e9, 2),
            "footprint_wkt": shape(g.geometry).wkt, "url": p.get("url"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # The same granule can be listed more than once (reprocessed versions); keep one per name.
    return df.drop_duplicates("granule").sort_values("acq_datetime_utc").reset_index(drop=True)


def set_date(path, pattern, new, dry):
    """Replace the date captured by `pattern` (group 2) in a file. Returns (old, new)."""
    p = ROOT / path
    text = p.read_text()
    m = re.search(pattern, text, re.M)
    if not m:
        raise SystemExit(f"could not find the date to update in {path}")
    old = m.group(2)
    if old != new and not dry:
        p.write_text(text[:m.start(2)] + new + text[m.end(2):])
    return old, new


def cmd_check(a):
    say(f"querying ASF for NISAR GCOV over the field rectangle, {START}..today ...")
    avail = query_asf()
    if avail.empty:
        say("ASF returned no granules - nothing to do.")
        return
    avail["local_day"] = local_date(avail.acq_datetime_utc)
    done = processed_granules()
    new = avail[~avail.granule.isin(done)]
    say(f"available: {len(avail)} granules, {avail.local_day.min():%Y-%m-%d}..{avail.local_day.max():%Y-%m-%d}")
    say(f"processed (clipped): {len(done)}   new: {len(new)}")
    if len(new):
        say(new[["local_day", "track", "direction", "frame", "frame_coverage", "size_gb", "granule"]]
            .assign(local_day=new.local_day.dt.strftime("%Y-%m-%d")).to_string(index=False))
    urls = avail.url.dropna()
    odd = urls[~urls.str.contains("NISAR_L2_GCOV_PROVISIONAL_V1", regex=False)]
    if len(odd):
        say(f"\nnote: {len(odd)} granule(s) are in another ASF collection than the earlier ones, e.g.\n  "
            + odd.iloc[0] + "\n  their URLs are written to the `url` column of the granule index, "
            "which download_granules.py uses.")

    if not len(new) and not a.force:
        say("\nup to date - no files changed.")
        return

    # --- download inputs for ORCD ---
    idx_path = ROOT / GRANULE_INDEX
    idx = pd.read_csv(idx_path) if idx_path.exists() else pd.DataFrame()
    add = avail[~avail.granule.isin(set(idx.granule) if len(idx) else set())]
    if len(idx) and "url" not in idx.columns:
        idx["url"] = pd.NA                            # old rows: downloader falls back to its default path
    cols = list(idx.columns) if len(idx) else [c for c in avail.columns if c != "local_day"]
    lines = ([f"# All NISAR GCOV granules over the 2026 field rectangle (written by update_pipeline.py check).",
              f"# {len(avail)} granules, {avail.size_gb.sum():.1f} GB, "
              f"{avail.local_day.min():%Y-%m-%d}..{avail.local_day.max():%Y-%m-%d}; "
              f"download_granules.py skips the ones already on disk."] + list(avail.granule))

    # --- END date ---
    end_new = max(current_end(), avail.local_day.max())
    end_s, end_excl = f"{end_new:%Y-%m-%d}", f"{end_new + pd.Timedelta(days=1):%Y-%m-%d}"
    changes = []
    for js in GEE_SCRIPTS:
        changes.append((js, *set_date(js, r"^(var END_DATE\s*=\s*')(\d{4}-\d{2}-\d{2})", end_excl, a.dry_run)))
    for py in PY_END_FILES:
        changes.append((py, *set_date(py, r'^(END\s*=\s*")(\d{4}-\d{2}-\d{2})', end_s, a.dry_run)))

    if not a.dry_run:
        if len(add):
            pd.concat([idx, add[cols]], ignore_index=True).to_csv(idx_path, index=False)
        (ROOT / GRANULE_LIST).write_text("\n".join(lines) + "\n")
        state = {"checked": dt.datetime.now().isoformat(timespec="seconds"), "end": end_s,
                 "available": len(avail), "processed": len(done), "new": list(new.granule)}
        (ROOT / STATE_JSON).write_text(json.dumps(state, indent=1))

    tag = "(dry run, nothing written)" if a.dry_run else ""
    say(f"\nfiles {tag}")
    say(f"  {GRANULE_INDEX}: +{len(add)} granules")
    say(f"  {GRANULE_LIST}: {len(avail)} granules")
    for f, old, newv in changes:
        say(f"  {f}: {old} -> {newv}" + ("" if old != newv else " (unchanged)"))
    say("\nnext:")
    say("  1. Earth Engine: paste scripts/gee_s1_fields_2026.js and scripts/gee_s2_fields_2026.js into the")
    say("     Code Editor, Run, start the export in the Tasks tab, and save the CSVs from Drive as")
    say(f"     {S1_CSV} and {S2_CSV} (same names, overwrite).")
    say("  2. ORCD: git pull, then  sbatch scripts/update_pipeline_orcd.sbatch")
    say("  3. laptop, after copying the clips back:  python scripts/update_pipeline.py local --notebooks")


# ---------------------------------------------------------------- orcd --------
def run(cmd, cwd=ROOT):
    say(f"\n$ {' '.join(map(str, cmd))}")
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode:
        raise SystemExit(f"step failed (exit {r.returncode}): {' '.join(map(str, cmd))}")


def cmd_orcd(a):
    if not (ROOT / GRANULE_LIST).exists():
        raise SystemExit(f"{GRANULE_LIST} missing - run `check` first (and commit/pull it).")
    (ROOT / "logs").mkdir(exist_ok=True)
    run([PY, "scripts/download_granules.py", "--list", GRANULE_LIST, "--index", GRANULE_INDEX,
         "--out-dir", GRANULE_DIR, "--log", "logs/download.log", "--progress-every", "60",
         "--reserve-gb", str(a.reserve_gb)])
    run([PY, "scripts/clip_fields.py", "--granules", f"{GRANULE_DIR}/*.h5"])
    run([PY, "scripts/incidence_at_fields.py", "--granules", f"{GRANULE_DIR}/*.h5"])
    say("\nORCD steps done. On the laptop, from the repo root:")
    say(f"  rsync -av {ORCD_HOST}:{ORCD_REPO}/data/nisar_field_clips/ data/nisar_field_clips/")
    say(f"  rsync -av {ORCD_HOST}:{ORCD_REPO}/{INCIDENCE_CSV} {INCIDENCE_CSV}")
    say("  python scripts/update_pipeline.py local --notebooks")


# ---------------------------------------------------------------- local -------
def preflight():
    """Problems that would make a local run use stale inputs. Returns a list of messages."""
    msgs = []
    lst = ROOT / GRANULE_LIST
    wanted = [l.strip() for l in lst.read_text().splitlines() if l.strip() and not l.startswith("#")] \
        if lst.exists() else []
    clipped = processed_granules()
    miss = [g for g in wanted if g not in clipped]
    if miss:
        msgs.append(f"{len(miss)} granule(s) in {GRANULE_LIST} are not clipped yet "
                    f"(run `orcd` and copy data/nisar_field_clips/ back), e.g. {miss[0]}")
    if (ROOT / INCIDENCE_CSV).exists():
        inc = set(pd.read_csv(ROOT / INCIDENCE_CSV).granule)
        miss_inc = [g for g in clipped if g not in inc]
        if miss_inc:
            msgs.append(f"{len(miss_inc)} clipped granule(s) have no incidence angle in {INCIDENCE_CSV} "
                        "(copy it back from ORCD)")
    end = current_end()
    for csv, col in ((S1_CSV, "date"), (S2_CSV, "date")):
        p = ROOT / csv
        if not p.exists():
            msgs.append(f"{csv} missing (Earth Engine export)")
            continue
        last = pd.to_datetime(pd.read_csv(p, usecols=[col])[col]).max()
        if last < end - pd.Timedelta(days=12):     # S1/S2 revisit is <= ~12 days
            msgs.append(f"{csv} ends {last:%Y-%m-%d}, END is {end:%Y-%m-%d}: re-run the GEE export")
    return msgs


def run_notebook(nb):
    run([PY, "-m", "jupyter", "nbconvert", "--to", "notebook", "--execute", "--inplace",
         "--ExecutePreprocessor.timeout=-1", nb])


def cmd_local(a):
    msgs = preflight()
    if msgs:
        say("inputs not up to date:")
        for m in msgs:
            say(f"  - {m}")
        if not a.ignore_stale:
            raise SystemExit("\nfix the above, or re-run with --ignore-stale to continue anyway.")
        say("--ignore-stale: continuing.")
    for nb in NOTEBOOKS_DATA:                         # clips -> outputs/nisar_field_means_2026.csv
        run_notebook(nb)
    if a.water:
        run([PY, "scripts/build_analysis_table.py", "--loggers", LOGGERS_GLOB, "--readings", READINGS_CSV])
    run([PY, "scripts/build_daily_dataset_2026.py"])
    run([PY, "scripts/build_causal_features_2026.py"])
    if a.notebooks:
        for nb in NOTEBOOKS_FINAL:
            run_notebook(nb)
    say("\nlocal pipeline done." + ("" if a.notebooks else
        " Open notebooks/data_exploratory.ipynb and notebooks/train_model.ipynb and run them."))


# ---------------------------------------------------------------- status ------
def cmd_status(a):
    say(f"END (build scripts): {current_end():%Y-%m-%d}")
    for js in GEE_SCRIPTS:
        m = re.search(r"^var END_DATE\s*=\s*'(\d{4}-\d{2}-\d{2})'", (ROOT / js).read_text(), re.M)
        say(f"END_DATE in {js}: {m.group(1)} (exclusive)")
    say(f"clipped granules: {len(processed_granules())}")
    if (ROOT / NISAR_MEANS).exists():
        m = pd.read_csv(ROOT / NISAR_MEANS)
        say(f"{NISAR_MEANS}: {m.granule.nunique()} granules, last {pd.to_datetime(m.acq_utc).max():%Y-%m-%d}")
    if (ROOT / STATE_JSON).exists():
        say(f"last check: {json.loads((ROOT / STATE_JSON).read_text())}")
    msgs = preflight()
    say("inputs: " + ("up to date" if not msgs else ""))
    for m in msgs:
        say(f"  - {m}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="step", required=True)
    c = sub.add_parser("check", help="look for new granules; update END dates and download lists")
    c.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    c.add_argument("--force", action="store_true", help="rewrite lists/dates even with no new granules")
    o = sub.add_parser("orcd", help="download + clip + incidence (run on ORCD)")
    o.add_argument("--reserve-gb", type=float, default=20.0, help="free space to leave on the filesystem")
    l = sub.add_parser("local", help="rebuild tables (and notebooks) on the laptop")
    l.add_argument("--notebooks", action="store_true", help="also execute data_exploratory + train_model")
    l.add_argument("--water", action="store_true", help="also rebuild the logger water table")
    l.add_argument("--ignore-stale", action="store_true", help="run even if inputs look out of date")
    sub.add_parser("status", help="show what is processed / stale (no network)")
    a = ap.parse_args()
    {"check": cmd_check, "orcd": cmd_orcd, "local": cmd_local, "status": cmd_status}[a.step](a)


if __name__ == "__main__":
    main()
