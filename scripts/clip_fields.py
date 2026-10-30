#!/usr/bin/env python3
"""
Clip every NISAR granule to small GeoTIFFs around the 2026 fields.

Run ONCE where the granules live (ORCD). Each clip is a few hundred KB, so the
whole set (~38 passes x 5 fields) is tens of MB: copy that folder to your laptop
and notebooks/nisar_field_means.ipynb runs from it, without the .h5 files.

Every GeoTIFF holds:
  band 1 = HHHH, band 2 = HVHV, as LINEAR power (gamma0) -- not dB, so that
           field averages computed later stay correct.
  invalid samples already set to NaN (GCOV mask: 0 = bad sample, 255 = off-swath),
  so nothing downstream needs to know the mask convention.
  the granule's own CRS (UTM) and pixel grid -- values are copied, not resampled.

Window = field polygon + MARGIN_M on every side. A field without a polygon (S16)
gets a box of NO_POLY_HALF_M around its gauge, big enough that a polygon drawn
later still falls inside the clip -- so you never have to re-clip for it.

Usage (from the repo root):
    python scripts/clip_fields.py
    python scripts/clip_fields.py --granules "data/nisar/*.h5" --out data/nisar_field_clips
"""
import argparse
import glob
import re
from pathlib import Path

import geopandas as gpd
import h5py
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from shapely.geometry import Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]               # repo root
GRID = "/science/LSAR/GCOV/grids/frequencyA"             # 10 m grid

# Which areas to clip. Keep in step with FIELDS_2026 in the notebook; this only
# decides WHERE clips are cut, so extra margin here is harmless.
FIELDS_2026 = {
    "Way 3":   ["W3-EC", "W3-CS"],
    "Way 4":   ["W4-EC", "W4-CS"],
    "4 East":  ["R8"],
    "South 0": ["S0"],
    "S16":     [],                                       # no polygon -> box around gauge
}
MARGIN_M = 400            # context around each polygon, metres
NO_POLY_HALF_M = 800      # half-width of the box around a polygon-less field's gauge


def parse_ee_polygon(text):
    """Shapely Polygon from an 'ee.Geometry.Polygon([[[lon, lat], ...]])' string."""
    num = r"-?\d+\.\d+"
    pts = re.findall(rf"\[\s*({num})\s*,\s*({num})\s*\]", str(text))
    return Polygon([(float(a), float(b)) for a, b in pts])


def footprints(fields_csv, gauges_csv):
    """Field name -> clip footprint (lon/lat polygon)."""
    raw = pd.read_csv(fields_csv)
    polys = {c.strip(): parse_ee_polygon(t)
             for c, t in zip(raw["Field code"], raw["Geometry details"])}
    g = pd.read_csv(gauges_csv)
    g = gpd.GeoDataFrame(g, geometry=gpd.points_from_xy(g.lon, g.lat), crs=4326).to_crs(32615)
    out = {}
    for name, codes in FIELDS_2026.items():
        if codes and all(c in polys for c in codes):
            shape = gpd.GeoSeries([unary_union([polys[c] for c in codes])], crs=4326)
            out[name] = shape.to_crs(32615).buffer(MARGIN_M, join_style=2).to_crs(4326).iloc[0]
        else:
            pts = g[g.field_name == name]
            if pts.empty:
                print(f"  skipping {name}: no polygon and no gauge"); continue
            box = unary_union(list(pts.buffer(NO_POLY_HALF_M, cap_style=3)))
            out[name] = gpd.GeoSeries([box], crs=32615).to_crs(4326).iloc[0]
    return out


def granule_info(path):
    """Track / frame / direction / UTC time from a NISAR filename."""
    p = Path(path).stem.split("_")
    t = p[11]
    return {"granule": Path(path).stem, "track": int(p[5]), "frame": int(p[7]),
            "direction": "DESC" if p[6] == "D" else "ASC",
            "acq_utc": pd.Timestamp(f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[9:11]}:{t[11:13]}:{t[13:15]}")}


def clip_granule(path, fps, out_dir):
    """Write one GeoTIFF per field for this granule. Returns index rows."""
    info, rows = granule_info(path), []
    tag = f"{info['acq_utc']:%Y%m%d}_T{info['track']}_{info['direction']}"
    with h5py.File(path, "r") as f:
        g = f[GRID]
        proj = g["projection"]
        epsg = int(proj.attrs.get("epsg_code", proj[()]))
        x, y = g["xCoordinates"][:], g["yCoordinates"][:]
        dx, dy = x[1] - x[0], y[1] - y[0]                 # dy < 0
        transform = Affine(dx, 0, x[0] - dx / 2, 0, dy, y[0] - dy / 2)   # centres -> corner

        for name, fp in fps.items():
            minx, miny, maxx, maxy = gpd.GeoSeries([fp], crs=4326).to_crs(epsg).total_bounds
            c0, r0 = ~transform * (minx, maxy)
            c1, r1 = ~transform * (maxx, miny)
            r0, c0 = max(int(np.floor(r0)), 0), max(int(np.floor(c0)), 0)
            r1, c1 = min(int(np.ceil(r1)), len(y)), min(int(np.ceil(c1)), len(x))
            if r1 <= r0 or c1 <= c0:
                rows.append({**info, "field": name, "note": "outside grid"}); continue

            sl = (slice(r0, r1), slice(c0, c1))
            hh = g["HHHH"][sl].astype("float32")
            hv = g["HVHV"][sl].astype("float32")
            m = g["mask"][sl]
            bad = (m == 0) | (m == 255) | ~(hh > 0) | ~(hv > 0)
            hh[bad], hv[bad] = np.nan, np.nan

            fname = f"{tag}__{name.replace(' ', '_')}.tif"
            profile = dict(driver="GTiff", height=hh.shape[0], width=hh.shape[1], count=2,
                           dtype="float32", crs=f"EPSG:{epsg}", nodata=np.nan,
                           transform=transform * Affine.translation(c0, r0),
                           compress="deflate", predictor=3)
            with rasterio.open(out_dir / fname, "w", **profile) as dst:
                dst.write(hh, 1); dst.write(hv, 2)
                dst.set_band_description(1, "HHHH gamma0 linear power")
                dst.set_band_description(2, "HVHV gamma0 linear power")
                dst.update_tags(field=name, granule=info["granule"], track=info["track"],
                                direction=info["direction"], acq_utc=str(info["acq_utc"]))
            rows.append({**info, "field": name, "file": fname, "epsg": epsg,
                         "width": hh.shape[1], "height": hh.shape[0],
                         "valid_frac": round(float(np.isfinite(hh).mean()), 3)})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--granules", default="data/nisar/*.h5", help="glob, relative to repo root")
    ap.add_argument("--fields-csv", default="final/Fields_2024 - Sheet1.csv")
    ap.add_argument("--gauges-csv", default="scripts/gauges_2026.csv")
    ap.add_argument("--out", default="data/nisar_field_clips")
    ap.add_argument("--year", type=int, default=2026)
    a = ap.parse_args()

    out_dir = ROOT / a.out
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = footprints(ROOT / a.fields_csv, ROOT / a.gauges_csv)
    files = sorted(p for p in glob.glob(str(ROOT / a.granules))
                   if granule_info(p)["acq_utc"].year == a.year)
    print(f"{len(files)} granules from {a.year} x {len(fps)} fields -> {out_dir}")
    if not files:
        raise SystemExit("no granules found -- check --granules")

    rows = []
    for i, p in enumerate(files, 1):
        rows += clip_granule(p, fps, out_dir)
        print(f"[{i:2d}/{len(files)}] {Path(p).name[:50]}", flush=True)

    idx = pd.DataFrame(rows).sort_values(["acq_utc", "field"])
    idx.to_csv(out_dir / "index.csv", index=False)
    size = sum(f.stat().st_size for f in out_dir.glob("*.tif")) / 1e6
    print(f"\nwrote {idx.file.notna().sum()} GeoTIFFs ({size:.1f} MB) + index.csv to {out_dir}")


if __name__ == "__main__":
    main()
