#!/usr/bin/env python3
"""
Sample NISAR GCOV backscatter at the field-plot coordinates.

Turns ~261 GB of granules into one small CSV you can pull back and plot.
For every (granule, gauge) it reads a window around the pixel containing the
point and records HHHH, HVHV and their ratio in dB.

Three things this gets deliberately right
-----------------------------------------
1. MASKING. GCOV 'mask' holds the SUBSWATH NUMBER of valid samples, so valid
   pixels are NON-zero: 0 means a radar sample in the averaging set was
   partially focused or invalid, 255 is outside the acquisition. Testing
   `mask == 0` keeps only the bad pixels -- a trap we already hit once.

2. AVERAGING IN LINEAR POWER, NOT dB. GCOV stores linear gamma0. The mean of
   dB values is the geometric mean of the powers, which is not what you want.
   Average the linear values, then take 10*log10 once at the end.

3. PER-GRANULE CRS. Frames along a track can land in different UTM zones
   (we have seen both 32615 and 32616 over Arkansas), so the gauge coordinates
   are reprojected per granule rather than once up front.

A 1x1 window is speckle-dominated at 10 m posting; the default 3x3 median is
far steadier. Both mean and median are written, with the valid-pixel count, so
you can judge the trade-off rather than inherit mine.

Usage
-----
    python extract_at_gauges.py \
        --granule-dir ../data/nisar \
        --gauges ../data/2026_WaterLevel_Runkle/gauges_2026.csv \
        --out ../outputs/nisar_at_gauges.csv --window 3
"""

import argparse
import glob
import os
import sys
import time

import h5py
import numpy as np
import pandas as pd
from pyproj import Transformer

GRID = "/science/LSAR/GCOV/grids/frequency{}"
POLS = ["HHHH", "HVHV"]


def parse_granule_name(name):
    """track / frame / direction / acquisition time, straight from the name."""
    p = os.path.basename(name).replace(".h5", "").split("_")
    try:
        return {
            "track": int(p[5]), "frame": int(p[7]),
            "direction": "DESC" if p[6] == "D" else "ASC",
            "acq_datetime_utc": (f"{p[11][:4]}-{p[11][4:6]}-{p[11][6:8]}T"
                                 f"{p[11][9:11]}:{p[11][11:13]}:{p[11][13:15]}Z"),
        }
    except (IndexError, ValueError):
        return {"track": None, "frame": None, "direction": None,
                "acq_datetime_utc": ""}


def window_stats(arr, mask, half):
    """Linear-power stats over a window, ignoring masked and non-positive cells.

    Returns (mean, median, n_valid, n_total) in LINEAR power, or NaNs if the
    window holds nothing usable.
    """
    valid = (mask != 0) & (mask != 255) & np.isfinite(arr) & (arr > 0)
    n_total = arr.size
    n_valid = int(valid.sum())
    if n_valid == 0:
        return np.nan, np.nan, 0, n_total
    v = arr[valid].astype("f8")
    return float(v.mean()), float(np.median(v)), n_valid, n_total


def to_db(x):
    return 10 * np.log10(x) if (x is not None and np.isfinite(x) and x > 0) else np.nan


def extract_one(h5_path, gauges, half, freq="A"):
    """Every gauge inside this granule, as a list of dicts."""
    meta = parse_granule_name(h5_path)
    out = []
    with h5py.File(h5_path, "r") as f:
        gpath = GRID.format(freq)
        if gpath not in f:
            print(f"  no frequency{freq} in {os.path.basename(h5_path)}", flush=True)
            return out
        g = f[gpath]
        proj = g["projection"]
        epsg = int(proj.attrs.get("epsg_code", proj[()]))
        x = g["xCoordinates"][:]
        y = g["yCoordinates"][:]
        dx = float(abs(x[1] - x[0]))

        tf = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        has_looks = "numberOfLooks" in g

        for _, s in gauges.iterrows():
            gx, gy = tf.transform(s.lon, s.lat)
            # y descends, so searchsorted needs the reversed axis
            col = int(np.argmin(np.abs(x - gx)))
            row = int(np.argmin(np.abs(y - gy)))
            rec = {**meta, "granule": os.path.basename(h5_path).replace(".h5", ""),
                   "gauge_id": s.gauge_id, "lon": s.lon, "lat": s.lat,
                   "epsg": epsg, "pixel_m": dx, "row": row, "col": col,
                   "window": 2 * half + 1}

            # Point outside the grid, or too close to an edge for a full window
            if not (half <= row < len(y) - half and half <= col < len(x) - half):
                rec["note"] = "outside grid"
                out.append(rec)
                continue
            # Distance from the point to the pixel centre -- a sanity check
            rec["offset_m"] = float(np.hypot(x[col] - gx, y[row] - gy))

            sl = (slice(row - half, row + half + 1),
                  slice(col - half, col + half + 1))
            m = g["mask"][sl]
            for pol in POLS:
                if pol not in g:
                    continue
                a = g[pol][sl]
                mean, med, nv, nt = window_stats(a, m, half)
                key = pol.lower()
                rec[f"{key}_lin_mean"] = mean
                rec[f"{key}_lin_median"] = med
                rec[f"{key}_db_mean"] = to_db(mean)
                rec[f"{key}_db_median"] = to_db(med)
                rec["n_valid"], rec["n_total"] = nv, nt
            if has_looks:
                rec["looks_mean"] = float(np.nanmean(g["numberOfLooks"][sl]))
            # Ratio from the LINEAR means, then one log -- never a mean of dB.
            hh, hv = rec.get("hhhh_lin_mean"), rec.get("hvhv_lin_mean")
            if hh and hv and np.isfinite(hh) and np.isfinite(hv) and hv > 0:
                rec["ratio_db"] = to_db(hh / hv)
            out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--granule-dir", default="../data/nisar")
    ap.add_argument("--gauges", default="../data/2026_WaterLevel_Runkle/gauges_2026.csv")
    ap.add_argument("--out", default="../outputs/nisar_at_gauges.csv")
    ap.add_argument("--window", type=int, default=3,
                    help="odd window size in pixels (1 = single pixel, 3 = 3x3)")
    ap.add_argument("--frequency", default="A", choices=["A", "B"])
    ap.add_argument("--limit", type=int, help="process only the first N granules")
    a = ap.parse_args()

    if a.window % 2 == 0:
        sys.exit("--window must be odd")
    half = a.window // 2

    gauges = pd.read_csv(a.gauges)
    files = sorted(glob.glob(os.path.join(a.granule_dir, "*.h5")))
    if a.limit:
        files = files[:a.limit]
    if not files:
        sys.exit(f"no .h5 files in {a.granule_dir}")
    print(f"{len(files)} granules x {len(gauges)} gauges, "
          f"{a.window}x{a.window} window, frequency{a.frequency}", flush=True)

    rows, t0 = [], time.time()
    for i, fp in enumerate(files, 1):
        t = time.time()
        try:
            r = extract_one(fp, gauges, half, a.frequency)
            rows += r
            ok = sum(1 for x in r if x.get("n_valid", 0) > 0)
            print(f"[{i:2d}/{len(files)}] {os.path.basename(fp)[:46]} "
                  f"{ok}/{len(gauges)} gauges valid  {time.time()-t:.1f}s", flush=True)
        except Exception as e:
            print(f"[{i:2d}/{len(files)}] FAILED {os.path.basename(fp)[:46]}: "
                  f"{type(e).__name__}: {e}", flush=True)

    df = pd.DataFrame(rows).sort_values(["gauge_id", "acq_datetime_utc"])
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    df.to_csv(a.out, index=False)
    valid = df.n_valid.gt(0).sum() if "n_valid" in df else 0
    print(f"\nwrote {a.out}: {len(df)} rows, {valid} with valid pixels, "
          f"in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
