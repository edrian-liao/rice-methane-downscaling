#!/usr/bin/env python3
"""
Sentinel-2 L2A NDVI at the gauge coordinates, 2026 -- read straight from the
public cloud-optimized GeoTIFFs, no full-scene downloads.

Source: Element84 Earth Search STAC (https://earth-search.aws.element84.com/v1),
collection sentinel-2-l2a. Public, no credentials. Only a few KB per field per
scene are fetched, via HTTP range requests.

Choices worth knowing
---------------------
* Window: 3x3 pixels at 10 m, centred on the gauge -- same footprint as the
  NISAR extraction, so the two are spatially comparable.
* Cloud mask: Scene Classification Layer (SCL, 20 m). A 10 m pixel is kept only
  if its SCL class is 4 (vegetation), 5 (not vegetated) or 6 (WATER). Keeping 6
  matters here: a flooded paddy before canopy closure classifies as water, and
  dropping it would remove the flooded fields systematically and bias NDVI
  upward.
* Offset: processing baseline >= 04.00 adds +1000 to reflectance. Earth Search
  has already removed it (earthsearch:boa_offset_applied = True on every item),
  so NDVI is computed directly. The script refuses items where it is not.
* Duplicates: the two MGRS tiles overlap, so a field can appear twice on one
  date. Both are kept in the raw output; the daily file keeps the observation
  with more valid pixels.

    python scripts/fetch_s2_ndvi.py
"""
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from shapely.geometry import Point, shape

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "4")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")

STAC = "https://earth-search.aws.element84.com/v1/search"
VALID_SCL = {4, 5, 6}
HALF = 1                                   # 3x3 window


def search(bbox, start, end):
    items, body = [], {"collections": ["sentinel-2-l2a"], "bbox": bbox,
                       "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "limit": 200}
    while True:
        r = requests.post(STAC, json=body, timeout=60)
        r.raise_for_status()
        j = r.json()
        items += j["features"]
        nxt = [l for l in j.get("links", []) if l.get("rel") == "next"]
        if not nxt:
            return items
        body = nxt[0].get("body", body)


def sample_item(item, gauges):
    """Every gauge inside this scene's footprint -> list of dicts."""
    p = item["properties"]
    if not p.get("earthsearch:boa_offset_applied", False):
        return [], f"{item['id']}: BOA offset NOT applied, skipped"
    foot = shape(item["geometry"])
    inside = gauges[[foot.contains(Point(x, y)) for x, y in zip(gauges.lon, gauges.lat)]]
    if inside.empty:
        return [], None

    href = {k: "/vsicurl/" + item["assets"][k]["href"] for k in ("red", "nir", "scl")}
    out = []
    with rasterio.open(href["red"]) as red, rasterio.open(href["nir"]) as nir, \
         rasterio.open(href["scl"]) as scl:
        tf = Transformer.from_crs("EPSG:4326", red.crs, always_xy=True)
        for _, g in inside.iterrows():
            x, y = tf.transform(g.lon, g.lat)
            r0, c0 = red.index(x, y)
            win = rasterio.windows.Window(c0 - HALF, r0 - HALF, 2*HALF + 1, 2*HALF + 1)
            R = red.read(1, window=win).astype("f8")
            N = nir.read(1, window=win).astype("f8")
            # SCL is 20 m. Read ONE small window that covers the 3x3 at 10 m,
            # then look up the class under each 10 m pixel centre locally --
            # one HTTP read instead of nine.
            sr0, sc0 = scl.index(x, y)
            sw = rasterio.windows.Window(sc0 - 2, sr0 - 2, 5, 5)
            Sblk = scl.read(1, window=sw)
            S = np.zeros_like(R, dtype="i2")
            for i in range(R.shape[0]):
                for j in range(R.shape[1]):
                    px, py = red.xy(r0 - HALF + i, c0 - HALF + j)
                    sr, sc = scl.index(px, py)
                    S[i, j] = Sblk[sr - (sr0 - 2), sc - (sc0 - 2)]
            ok = np.isin(S, list(VALID_SCL)) & (R > 0) & (N > 0)
            nd = np.where(ok, (N - R) / (N + R), np.nan)
            out.append({
                "gauge_id": g.gauge_id, "date": p["datetime"][:10],
                "datetime_utc": p["datetime"], "tile": p.get("grid:code"),
                "item_id": item["id"], "scene_cloud_pct": p.get("eo:cloud_cover"),
                "n_valid": int(ok.sum()), "n_total": int(ok.size),
                "scl_centre": int(S[HALF, HALF]),
                "ndvi_mean": float(np.nanmean(nd)) if ok.any() else np.nan,
                "ndvi_median": float(np.nanmedian(nd)) if ok.any() else np.nan,
                "red_mean": float(R[ok].mean()) if ok.any() else np.nan,
                "nir_mean": float(N[ok].mean()) if ok.any() else np.nan,
            })
    return out, None


def sample_with_retry(item, gauges, tries=4):
    for k in range(tries):
        try:
            return sample_item(item, gauges)
        except Exception as e:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gauges", default="data/2026_WaterLevel_Runkle/gauges_2026.csv")
    ap.add_argument("--start", default="2026-03-15")
    ap.add_argument("--end", default="2026-10-20")
    ap.add_argument("--out", default="outputs/ndvi_2026_at_gauges.csv")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    g = pd.read_csv(a.gauges)
    bbox = [g.lon.min() - .01, g.lat.min() - .01, g.lon.max() + .01, g.lat.max() + .01]
    items = search(bbox, a.start, a.end)
    print(f"{len(items)} scenes, {a.start}..{a.end}", flush=True)

    rows, t0, done = [], time.time(), 0
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(sample_with_retry, it, g): it["id"] for it in items}
        for f in as_completed(futs):
            done += 1
            try:
                r, msg = f.result()
                rows += r
                if msg:
                    print("  " + msg, flush=True)
            except Exception as e:
                print(f"  FAILED {futs[f]}: {type(e).__name__}: {e}", flush=True)
            if done % 20 == 0:
                print(f"  {done}/{len(items)} scenes  {time.time()-t0:.0f}s", flush=True)

    raw = pd.DataFrame(rows).sort_values(["gauge_id", "date", "n_valid"])
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    raw_path = a.out.replace(".csv", "_raw.csv")
    raw.to_csv(raw_path, index=False)
    # One observation per gauge-day: the tile with more valid pixels.
    daily = (raw.sort_values("n_valid").groupby(["gauge_id", "date"]).tail(1)
                .sort_values(["gauge_id", "date"]))
    daily.to_csv(a.out, index=False)
    clear = daily[daily.n_valid >= 5]
    print(f"\nraw  -> {raw_path}: {len(raw)} gauge-scene samples")
    print(f"daily-> {a.out}: {len(daily)} gauge-days, {len(clear)} with >=5/9 clear "
          f"pixels  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
