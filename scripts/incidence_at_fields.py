#!/usr/bin/env python3
"""
Incidence angle of every NISAR granule at each 2026 gauge -> CSV.

Read from the GCOV metadata cube /science/LSAR/GCOV/metadata/radarGrid/incidenceAngle
(dims: height x y x x), at the height layer closest to the fields' ground height.
Run where the granules are (ORCD):

    python scripts/incidence_at_fields.py
    python scripts/incidence_at_fields.py --granules "data/nisar/*.h5" --out outputs/incidence_2026.csv
"""
import argparse
import glob
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
RG = "/science/LSAR/GCOV/metadata/radarGrid"


def granule_info(path):
    p = Path(path).stem.split("_")
    t = p[11]
    return {"granule": Path(path).stem, "track": int(p[5]), "frame": int(p[7]),
            "direction": "DESC" if p[6] == "D" else "ASC",
            "acq_utc": pd.Timestamp(f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[9:11]}:{t[11:13]}:{t[13:15]}")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--granules", default="data/nisar/*.h5", help="glob, relative to repo root")
    ap.add_argument("--gauges", default="scripts/gauges_2026.csv")
    ap.add_argument("--ground-m", type=float, default=60.0,
                    help="ground height above ellipsoid; the Arkansas Delta is ~50-70 m")
    ap.add_argument("--out", default="outputs/incidence_2026.csv")
    a = ap.parse_args()

    gauges = pd.read_csv(ROOT / a.gauges)
    files = sorted(glob.glob(str(ROOT / a.granules)))
    if not files:
        raise SystemExit("no granules found -- check --granules")

    rows = []
    for path in files:
        info = granule_info(path)
        with h5py.File(path, "r") as f:
            r = f[RG]
            h = r["heightAboveEllipsoid"][:]
            k = int(np.argmin(np.abs(h - a.ground_m)))       # nearest height layer
            x, y = r["xCoordinates"][:], r["yCoordinates"][:]
            tf = Transformer.from_crs(4326, int(r["projection"][()]), always_xy=True)
            inc = r["incidenceAngle"][k]                      # 2-D at that height
        for _, g in gauges.iterrows():
            gx, gy = tf.transform(g.lon, g.lat)
            i, j = int(np.argmin(np.abs(y - gy))), int(np.argmin(np.abs(x - gx)))
            rows.append({**info, "gauge_id": g.gauge_id, "field": g.field_name,
                         "height_layer_m": h[k], "incidence_deg": round(float(inc[i, j]), 2)})
        print(f"  {info['acq_utc']:%Y-%m-%d} T{info['track']} {info['direction']}")

    out = pd.DataFrame(rows)
    (ROOT / a.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(ROOT / a.out, index=False)
    print(f"\nwrote {a.out}: {len(out)} rows\n")
    print(out.pivot_table(index="field", columns=["track", "direction"], values="incidence_deg",
                          aggfunc=["min", "max"]).round(2).to_string())


if __name__ == "__main__":
    main()
