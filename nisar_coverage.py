"""
Build a NISAR coverage table from ASF metadata alone -- no granule downloads.

What this answers, per gauge: which granules contain it, on which dates, from
which track/frame, ascending or descending, and how many of those dates have a
matching in-situ reading.

Columns that CANNOT come from metadata and are therefore left NaN:
  inc_angle   -- ASF returns no incidence angle for NISAR GCOV (centerLat,
                 centerLon and beamModeType all come back None). It lives in
                 /science/LSAR/GCOV/metadata/radarGrid inside the HDF5.
  valid_frac  -- needs the per-pixel 'mask' raster, i.e. the granule itself.
Fill those in later, for the handful of granules you actually download.

A caveat on the footprints: CMR gives a 4-corner quadrilateral per granule, so
point-in-polygon is approximate within roughly a pixel-ish of the swath edge.
It is reliable for gauges well inside a frame, which is the normal case.

Usage
-----
    python nisar_coverage.py --gauges my_gauges.csv --readings final/all_daily_averages_2026.csv

--gauges needs columns: gauge_id, lon, lat   (EPSG:4326, lon = x, negative west)
--readings is optional; needs columns: gauge_id, date, <value column>
"""

import argparse
import os

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import shape

AOI = ("POLYGON ((-89.74727 29.96523, -89.74727 37.03901, -93.0009 37.03901, "
       "-93.0009 29.96523, -89.74727 29.96523))")
START, END = "2026-04-01", "2026-12-31"
CACHE = "nisar_granule_index.csv"


def granule_size_gb(props):
    """NISAR reports 'bytes' as {fileName: {'bytes': n}}, one entry per file."""
    b = props.get("bytes") or 0
    if isinstance(b, dict):
        b = sum((v.get("bytes") or 0) for v in b.values())
    return b / 1e9


def build_index(aoi=AOI, start=START, end=END, cache=CACHE, refresh=False):
    """Query ASF once for every GCOV granule over the AOI; cache to CSV.

    The cached file is the granule index: one row per granule, with its
    footprint as WKT. Everything downstream is offline.
    """
    if os.path.exists(cache) and not refresh:
        df = pd.read_csv(cache)
        return gpd.GeoDataFrame(
            df, geometry=gpd.GeoSeries.from_wkt(df.footprint_wkt), crs=4326)

    import asf_search as asf
    res = asf.search(dataset=asf.DATASET.NISAR, intersectsWith=aoi,
                     start=start, end=end, processingLevel="GCOV",
                     maxResults=10000)
    rows = []
    for g in res:
        p = g.properties
        rows.append({
            "granule": p["sceneName"],
            "track": p.get("pathNumber"),
            "frame": p.get("frameNumber"),
            "direction": ("ASC" if (p.get("flightDirection") or "").upper()
                          .startswith("A") else "DESC"),
            "acq_datetime_utc": p["startTime"],
            "pols": "+".join(p.get("mainBandPolarization") or []),
            "bandwidth_mhz": "+".join(p.get("rangeBandwidth") or []),
            "frame_coverage": p.get("frameCoverage"),
            "size_gb": round(granule_size_gb(p), 2),
            "footprint_wkt": shape(g.geometry).wkt,
        })
    df = pd.DataFrame(rows).sort_values(["track", "frame", "acq_datetime_utc"])
    df.to_csv(cache, index=False)
    print(f"cached {len(df)} granules -> {cache}")
    return gpd.GeoDataFrame(
        df, geometry=gpd.GeoSeries.from_wkt(df.footprint_wkt), crs=4326)


def coverage_table(gauges, index, readings=None, value_col=None):
    """One row per (gauge, granule) pair whose footprint contains the gauge."""
    pairs = gpd.sjoin(gauges, index, predicate="within", how="inner")
    pairs = pairs.drop(columns=[c for c in ("index_right", "footprint_wkt")
                                if c in pairs.columns])

    pairs["acq_datetime_utc"] = pd.to_datetime(pairs.acq_datetime_utc)
    pairs["acq_date"] = pairs.acq_datetime_utc.dt.date
    pairs["inc_angle"] = np.nan      # needs the granule (radarGrid)
    pairs["valid_frac"] = np.nan     # needs the granule (mask raster)
    pairs["gauge_reading"] = np.nan

    if readings is not None:
        r = readings.copy()
        r["date"] = pd.to_datetime(r["date"]).dt.date
        r = r.rename(columns={value_col: "_val"})[["gauge_id", "date", "_val"]]
        pairs = pairs.merge(r, left_on=["gauge_id", "acq_date"],
                            right_on=["gauge_id", "date"], how="left")
        pairs["gauge_reading"] = pairs.pop("_val")
        pairs = pairs.drop(columns=["date"])

    cols = ["gauge_id", "track", "frame", "direction", "acq_datetime_utc",
            "inc_angle", "valid_frac", "gauge_reading", "granule", "size_gb",
            "pols", "bandwidth_mhz", "frame_coverage"]
    return (pairs[cols].sort_values(["gauge_id", "acq_datetime_utc"])
                       .reset_index(drop=True))


def summarise(tbl):
    """Per-gauge rollup: the counts from the slide."""
    out = []
    for gid, d in tbl.groupby("gauge_id"):
        out.append({
            "gauge_id": gid,
            "n_passes":      len(d),
            "n_ascending":   int((d.direction == "ASC").sum()),
            "n_descending":  int((d.direction == "DESC").sum()),
            "tracks":        ",".join(str(t) for t in sorted(d.track.unique())),
            "frames":        ",".join(str(f) for f in sorted(d.frame.unique())),
            "first_acq":     d.acq_datetime_utc.min().date(),
            "last_acq":      d.acq_datetime_utc.max().date(),
            "n_matched_readings": int(d.gauge_reading.notna().sum()),
            "total_gb":      round(d.size_gb.sum(), 1),
        })
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gauges", required=True,
                    help="CSV with gauge_id, lon, lat (EPSG:4326)")
    ap.add_argument("--readings", help="CSV with gauge_id, date, <value>")
    ap.add_argument("--value-col", default="daily_mean")
    ap.add_argument("--refresh", action="store_true", help="re-query ASF")
    ap.add_argument("--out-prefix", default="nisar_coverage")
    a = ap.parse_args()

    g = pd.read_csv(a.gauges)
    gauges = gpd.GeoDataFrame(g, geometry=gpd.points_from_xy(g.lon, g.lat),
                              crs=4326)
    index = build_index(refresh=a.refresh)
    readings = pd.read_csv(a.readings) if a.readings else None

    tbl = coverage_table(gauges, index, readings, a.value_col)
    summary = summarise(tbl)

    tbl.to_csv(f"{a.out_prefix}_long.csv", index=False)
    summary.to_csv(f"{a.out_prefix}_summary.csv", index=False)
    print(f"\n{len(index)} granules indexed, {len(gauges)} gauges, "
          f"{len(tbl)} (gauge x granule) pairs\n")
    print(summary.to_string(index=False))
    print(f"\nwrote {a.out_prefix}_long.csv and {a.out_prefix}_summary.csv")


if __name__ == "__main__":
    main()
