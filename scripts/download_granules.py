#!/usr/bin/env python3
"""
Download NISAR granules, resumably, with a log you can tail from anywhere.

Built for a cluster (MIT ORCD): long walltimes, jobs that get pre-empted, and
nobody watching the terminal. So:

  * every download RESUMES from a partial file via an HTTP Range request, which
    matters because GCOV granules are ~7 GB and a requeued job should not start
    over;
  * progress is written as whole LINES on a throttle, not with carriage
    returns. A \\r-style bar looks right on a terminal but turns a log file into
    one unreadable 10 MB line, and `tail -f` shows nothing until it finishes;
  * the log is flushed on every record, so `tail -f logs.txt` is live;
  * a granule already on disk at the right size is skipped, so re-running the
    job is cheap and safe.

Auth: Earthdata login via ~/.netrc (chmod 600), or EARTHDATA_TOKEN in the
environment, or EARTHDATA_USER / EARTHDATA_PASS.

    machine urs.earthdata.nasa.gov login YOUR_USER password YOUR_PASS

Typical use
-----------
    # see what it would do, no bytes transferred
    python download_granules.py --from-coverage nisar_coverage_long.csv \
        --direction DESC --dry-run

    # the real thing, capped so it cannot fill the filesystem
    python download_granules.py --from-coverage nisar_coverage_long.csv \
        --direction DESC --track 84 --out-dir data/nisar --max-gb 90

    # watch from another shell / login node
    tail -f logs.txt
"""

import argparse
import logging
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import pandas as pd

CHUNK = 8 * 1024 * 1024          # 8 MiB; big enough that per-chunk overhead is noise
RETRIES = 5
BACKOFF = 15                     # seconds, doubled each retry

log = logging.getLogger("nisar-dl")
_stop = False


def _handle_signal(signum, frame):
    """Finish the current chunk, then stop cleanly so the partial file resumes."""
    global _stop
    _stop = True
    log.warning("signal %s received - will stop after the current chunk "
                "(partial files resume on the next run)", signum)


def setup_logging(path, verbose=False):
    """Log to file and stdout. Flush every record so tail -f is live."""
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    class FlushingFile(logging.FileHandler):
        def emit(self, record):
            super().emit(record)
            self.flush()

    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.handlers.clear()
    fh = FlushingFile(path, mode="a")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} PB"


def hms(seconds):
    if seconds is None or seconds != seconds or seconds > 86400 * 7:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def bar(frac, width=28):
    frac = 0.0 if frac != frac else max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def make_session():
    """Earthdata-authenticated session: token, explicit creds, or ~/.netrc."""
    import asf_search as asf
    tok = os.environ.get("EARTHDATA_TOKEN")
    if tok:
        log.info("authenticating with EARTHDATA_TOKEN")
        return asf.ASFSession().auth_with_token(tok)
    user, pw = os.environ.get("EARTHDATA_USER"), os.environ.get("EARTHDATA_PASS")
    if user and pw:
        log.info("authenticating as %s (EARTHDATA_USER)", user)
        return asf.ASFSession().auth_with_creds(user, pw)
    netrc = Path.home() / ".netrc"
    if netrc.exists() and "urs.earthdata.nasa.gov" in netrc.read_text():
        mode = oct(netrc.stat().st_mode & 0o777)
        if netrc.stat().st_mode & 0o077:
            log.warning("~/.netrc is mode %s - Earthdata tooling may refuse it; "
                        "chmod 600 ~/.netrc", mode)
        log.info("authenticating via ~/.netrc")
        return asf.ASFSession()
    raise SystemExit("No Earthdata credentials. Set EARTHDATA_TOKEN, or "
                     "EARTHDATA_USER/EARTHDATA_PASS, or create ~/.netrc.")


def parse_granule_name(name):
    """Pull track / frame / direction / acquisition time out of the filename.

    NISAR_L2_PR_GCOV_017_012_D_071_4005_DHDH_A_20260404T010047_...
                      |   |   |   |                |
                   cycle track dir frame        start time

    This is why --index is optional: everything the log lines need is already
    in the name, and the size comes from the server's Content-Length.
    """
    p = name.split("_")
    try:
        return {"track": int(p[5]), "frame": int(p[7]),
                "direction": "DESC" if p[6] == "D" else "ASC",
                "acq_datetime_utc": (f"{p[11][:4]}-{p[11][4:6]}-{p[11][6:8]}T"
                                     f"{p[11][9:11]}:{p[11][11:13]}:{p[11][13:15]}Z")}
    except (IndexError, ValueError):
        return {"track": None, "frame": None, "direction": None,
                "acq_datetime_utc": ""}


def select_granules(args):
    """Resolve the download list to a DataFrame of granule, url, size_gb."""
    if args.list:
        names = [l.strip() for l in open(args.list) if l.strip()
                 and not l.startswith("#")]
        if args.index and os.path.exists(args.index):
            idx = pd.read_csv(args.index)
            sel = idx[idx.granule.isin(names)].copy()
            missing = set(names) - set(sel.granule)
            if missing:
                log.warning("%d name(s) in --list not found in %s: %s",
                            len(missing), args.index,
                            ", ".join(sorted(missing)[:3]))
        else:
            # No index: derive metadata from the names, size from the server.
            log.info("no granule index - reading metadata from the file names, "
                     "sizes from the server")
            sel = pd.DataFrame([{"granule": n, "size_gb": float("nan"),
                                 **parse_granule_name(n)} for n in names])
    else:
        cov = pd.read_csv(args.from_coverage)
        if args.only_matched:
            if "gauge_reading" not in cov.columns:
                raise SystemExit("--only-matched needs a gauge_reading column; "
                                 "re-run nisar_coverage.py with --readings")
            cov = cov[cov.gauge_reading.notna()]
        for col, want in (("track", args.track), ("frame", args.frame),
                          ("direction", args.direction)):
            if want:
                cov = cov[cov[col].astype(str).isin([str(w) for w in want])]
        if args.index and os.path.exists(args.index):
            idx = pd.read_csv(args.index)
            sel = idx[idx.granule.isin(cov.granule.unique())].copy()
        else:
            sel = (cov.drop_duplicates("granule")
                      [["granule", "track", "frame", "direction",
                        "acq_datetime_utc", "size_gb"]].copy())

    if sel.empty:
        raise SystemExit("No granules selected - loosen the filters.")

    # The index caches no URL, so rebuild the standard ASF path from the name.
    base = ("https://nisar.asf.earthdatacloud.nasa.gov/NISAR/"
            "NISAR_L2_GCOV_PROVISIONAL_V1")
    sel["url"] = sel.granule.map(lambda g: f"{base}/{g}/{g}.h5")
    sel = sel.sort_values("acq_datetime_utc").reset_index(drop=True)
    return sel[["granule", "url", "size_gb", "track", "frame", "direction",
                "acq_datetime_utc"]]


def download_one(session, url, dest, expected, tag, every):
    """Stream one granule to dest, resuming if a partial exists.

    Returns (ok, bytes_transferred_this_run).
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    have = tmp.stat().st_size if tmp.exists() else 0

    if dest.exists():
        size = dest.stat().st_size
        if expected and abs(size - expected) < 1024:
            log.info("%s SKIP already complete (%s)", tag, human(size))
            return True, 0
        log.warning("%s exists but is %s, expected %s - re-downloading",
                    tag, human(size), human(expected))
        dest.unlink()

    for attempt in range(1, RETRIES + 1):
        try:
            headers = {}
            if have:
                headers["Range"] = f"bytes={have}-"
                log.info("%s RESUME from %s", tag, human(have))
            r = session.get(url, stream=True, headers=headers, timeout=120)

            if have and r.status_code == 200:
                # Server ignored the Range header; start over rather than
                # silently appending a second copy of the file.
                log.warning("%s server ignored Range - restarting from 0", tag)
                have = 0
                tmp.unlink(missing_ok=True)
            elif have and r.status_code != 206:
                r.raise_for_status()

            r.raise_for_status()
            total = expected or (int(r.headers.get("Content-Length", 0)) + have)

            t0 = t_last = time.time()
            done_at_start = have
            with open(tmp, "ab" if have else "wb") as fh:
                for chunk in r.iter_content(CHUNK):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    have += len(chunk)
                    now = time.time()
                    if now - t_last >= every:
                        moved = have - done_at_start
                        rate = moved / max(now - t0, 1e-6)
                        eta = (total - have) / rate if rate > 0 and total else None
                        log.info("%s %s %5.1f%%  %s / %s  %s/s  eta %s",
                                 tag, bar(have / total if total else 0),
                                 100 * have / total if total else 0,
                                 human(have), human(total), human(rate), hms(eta))
                        t_last = now
                    if _stop:
                        log.warning("%s interrupted at %s - partial kept",
                                    tag, human(have))
                        return False, have - done_at_start

            if expected and abs(have - expected) >= 1024:
                raise IOError(f"size mismatch: got {have}, expected {expected}")

            tmp.rename(dest)
            elapsed = time.time() - t0
            moved = have - done_at_start
            log.info("%s %s 100.0%%  %s  in %s  (%s/s)", tag, bar(1.0),
                     human(have), hms(elapsed),
                     human(moved / max(elapsed, 1e-6)))
            return True, moved

        except Exception as e:
            if attempt == RETRIES:
                log.error("%s FAILED after %d attempts: %s: %s",
                          tag, RETRIES, type(e).__name__, e)
                return False, 0
            wait = BACKOFF * (2 ** (attempt - 1))
            log.warning("%s attempt %d/%d failed (%s: %s) - retrying in %ds",
                        tag, attempt, RETRIES, type(e).__name__, e, wait)
            time.sleep(wait)
            have = tmp.stat().st_size if tmp.exists() else 0
    return False, 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-coverage", help="nisar_coverage_long.csv")
    src.add_argument("--list", help="text file of granule names, one per line")
    ap.add_argument("--index", default="nisar_granule_index.csv",
                    help="optional granule index from nisar_coverage.py; only "
                         "supplies sizes up front. Without it, metadata comes "
                         "from the file names and sizes from the server.")
    ap.add_argument("--out-dir", default="data/nisar")
    ap.add_argument("--log", default="logs.txt")
    ap.add_argument("--track", nargs="*", help="keep only these tracks")
    ap.add_argument("--frame", nargs="*", help="keep only these frames")
    ap.add_argument("--direction", nargs="*", choices=["ASC", "DESC"])
    ap.add_argument("--only-matched", action="store_true",
                    help="only granules with at least one matched gauge reading")
    ap.add_argument("--max-gb", type=float,
                    help="stop once this much has been downloaded this run")
    ap.add_argument("--reserve-gb", type=float, default=20.0,
                    help="refuse to start a file if free space would drop below this")
    ap.add_argument("--progress-every", type=float, default=30.0,
                    help="seconds between progress lines")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    setup_logging(a.log, a.verbose)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    log.info("=" * 72)
    log.info("NISAR download starting | host=%s | pid=%d | out=%s",
             os.uname().nodename, os.getpid(), out.resolve())
    if os.environ.get("SLURM_JOB_ID"):
        log.info("slurm job %s on %s", os.environ["SLURM_JOB_ID"],
                 os.environ.get("SLURM_NODELIST", "?"))

    sel = select_granules(a)
    free = shutil.disk_usage(out).free
    known = sel.size_gb.notna()
    log.info("%d granule(s) selected, %s | free on %s: %s", len(sel),
             (f"{sel.size_gb.sum():.1f} GB total" if known.all()
              else f"size known for {known.sum()}/{len(sel)}"), out, human(free))

    already = sum(1 for g in sel.granule if (out / f"{g}.h5").exists())
    if already:
        log.info("%d of them already present on disk", already)
    if known.all() and sel.size_gb.sum() * 1e9 > free - a.reserve_gb * 1e9:
        log.warning("selection (%.1f GB) may not fit in free space (%s) "
                    "minus the %.0f GB reserve - will stop when it runs low",
                    sel.size_gb.sum(), human(free), a.reserve_gb)

    if a.dry_run:
        log.info("--dry-run: listing only, nothing will be transferred")
        for i, row in sel.iterrows():
            log.info("  [%2d/%2d] %s  %s  track %s frame %s %s  %s",
                     i + 1, len(sel), row.granule,
                     f"{row.size_gb:.2f} GB" if pd.notna(row.size_gb) else "size ?",
                     row.track, row.frame, row.direction,
                     row.acq_datetime_utc[:10])
        log.info("dry run complete")
        return

    session = make_session()
    t_start = time.time()
    ok = failed = skipped = 0
    moved_total = 0

    for i, row in sel.iterrows():
        if _stop:
            log.warning("stopping early by signal")
            break
        if a.max_gb and moved_total / 1e9 >= a.max_gb:
            log.info("--max-gb %.1f reached (%s transferred) - stopping",
                     a.max_gb, human(moved_total))
            break

        free = shutil.disk_usage(out).free
        need = (row.size_gb * 1e9) if pd.notna(row.size_gb) else 8e9  # assume 8 GB
        if free - need < a.reserve_gb * 1e9:
            log.error("not enough space for %s: need %.1f GB, free %s, "
                      "reserve %.0f GB - stopping",
                      row.granule, row.size_gb, human(free), a.reserve_gb)
            break

        tag = f"[{i + 1:2d}/{len(sel)}]"
        dest = out / f"{row.granule}.h5"
        log.info("%s START %s  %s  track %s frame %s %s  acq %s", tag, row.granule,
                 f"{row.size_gb:.2f} GB" if pd.notna(row.size_gb) else "size ?",
                 row.track, row.frame, row.direction, row.acq_datetime_utc[:10])

        expected = int(row.size_gb * 1e9) if pd.notna(row.size_gb) else 0
        good, moved = download_one(session, row.url, dest, expected,
                                   tag, a.progress_every)
        moved_total += moved
        if good and moved == 0:
            skipped += 1
        elif good:
            ok += 1
        else:
            failed += 1

        elapsed = time.time() - t_start
        log.info("STATUS  done %d  skipped %d  failed %d  of %d  |  "
                 "%s this run  |  elapsed %s",
                 ok, skipped, failed, len(sel), human(moved_total), hms(elapsed))

    log.info("-" * 72)
    log.info("FINISHED  downloaded %d, skipped %d, failed %d, of %d selected",
             ok, skipped, failed, len(sel))
    log.info("transferred %s in %s | free space now %s", human(moved_total),
             hms(time.time() - t_start), human(shutil.disk_usage(out).free))
    if failed:
        log.error("%d granule(s) failed - re-run the same command to retry; "
                  "completed files are skipped and partials resume", failed)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
