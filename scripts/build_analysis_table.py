#!/usr/bin/env python3
"""
Join NISAR backscatter to water level and LAI -> one analysis table.

Inputs
    outputs/nisar_at_gauges.csv                 backscatter per (granule, gauge)
    data/2026_WaterLevel_Runkle/2026_*Master_file_*.xlsx   30-min TOA5 loggers
    data/2026_WaterLevel_Runkle/readings_2026.csv          weekly LAI / visits

Timezone
--------
The logger TIMESTAMP is LOCAL (US/Central), established empirically: mean
shallow-soil-temperature peaks at 14-16h and troughs at 06h across all seven
stations. Under UTC those would land at 19-21h and 11h. NISAR times are UTC,
so they are converted to US/Central here -- zoneinfo handles CDT/CST, which
matters because the season spans the November DST change.

Consequence worth knowing: descending passes (~01:00 UTC) sample ~20:00 the
PREVIOUS EVENING local, ascending (~11:20 UTC) ~06:20 the same morning. The
two geometries see different times of day.

LAI
---
Visits are weekly, so LAI is linearly interpolated in time per gauge and never
extrapolated beyond the first/last visit. `lai_gap_days` records the distance
to the nearest actual measurement so you can filter on it.
"""

import argparse
import glob
import os
import warnings
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SHEET_TO_GAUGE = {"Way3EC": "W3-EC", "Way3CS": "W3-CS", "Way4EC": "W4-EC",
                  "Way4CS": "W4-CS", "S16": "S16", "4East": "R8", "south_0": "S0"}
LOCAL = ZoneInfo("America/Chicago")


def load_loggers(pattern):
    """All TOA5 workbooks -> one long frame of 30-minute records (local time)."""
    out = []
    for f in sorted(glob.glob(pattern)):
        tag = os.path.basename(f).split("Master_file_")[1].replace(".xlsx", "")
        gid = SHEET_TO_GAUGE.get(tag)
        if gid is None:
            print(f"  skipping unmapped file {os.path.basename(f)}")
            continue
        sheet = pd.ExcelFile(f).sheet_names[0]
        # TOA5: row0 file info, row1 names, row2 units, row3 aggregation
        d = pd.read_excel(f, sheet_name=sheet, header=1, skiprows=[2, 3])
        d["ts_local"] = pd.to_datetime(d["TIMESTAMP"], errors="coerce")
        keep = pd.DataFrame({
            "gauge_id": gid,
            "ts_local": d.ts_local,
            "water_cm": pd.to_numeric(d.get("wt_corr_AVG_cm"), errors="coerce"),
            "water_cm_fixedbias": pd.to_numeric(d.get("wt_corr_AVG_cm_fixedBias"),
                                                errors="coerce"),
            "soil_t_1cm": pd.to_numeric(d.get("T107_C_1cm_Avg"), errors="coerce"),
            "vwc_8cm": pd.to_numeric(d.get("VWC_8cm_Avg"), errors="coerce"),
        }).dropna(subset=["ts_local"])
        out.append(keep)
        print(f"  {gid:6s} {len(keep):5d} records  "
              f"{keep.ts_local.min():%Y-%m-%d}..{keep.ts_local.max():%Y-%m-%d}")
    return pd.concat(out, ignore_index=True)


def interp_lai(readings, gid, when):
    """LAI at `when` (local naive), linearly interpolated between visits.

    Returns (lai, gap_days_to_nearest_visit). No extrapolation: outside the
    visit range you get NaN, because a canopy curve says nothing before
    emergence or after harvest.
    """
    r = readings[(readings.gauge_id == gid) & readings.lai.notna()].sort_values("date")
    if r.empty:
        return np.nan, np.nan
    t = r.date.values.astype("datetime64[ns]").astype("float64")
    w = np.asarray(when, dtype="datetime64[ns]").astype("float64")
    gap = np.min(np.abs(t[None, :] - w[:, None]), axis=1) / 8.64e13
    lai = np.interp(w, t, r.lai.values, left=np.nan, right=np.nan)
    return lai, gap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nisar", default="outputs/nisar_at_gauges.csv")
    ap.add_argument("--loggers",
                    default="data/2026_WaterLevel_Runkle/2026_*Master_file_*.xlsx")
    ap.add_argument("--readings",
                    default="data/2026_WaterLevel_Runkle/readings_2026.csv")
    ap.add_argument("--out", default="outputs/nisar_analysis_table.csv")
    ap.add_argument("--out-water", default="outputs/water_continuous_2026.csv",
                    help="the full 30-min logger series, for plotting traces")
    ap.add_argument("--ndvi", default="outputs/ndvi_2026_at_gauges.csv",
                    help="Sentinel-2 NDVI per gauge-day from fetch_s2_ndvi.py")
    ap.add_argument("--ndvi-min-valid", type=int, default=5,
                    help="clear pixels (of 9) needed for an NDVI observation to count")
    ap.add_argument("--water-tolerance-min", type=float, default=30.0,
                    help="max gap between acquisition and a logger record")
    a = ap.parse_args()

    n = pd.read_csv(a.nisar)
    n["acq_utc"] = pd.to_datetime(n.acq_datetime_utc, utc=True)
    n["acq_local"] = n.acq_utc.dt.tz_convert(LOCAL).dt.tz_localize(None)
    n["acq_hour_local"] = n.acq_local.dt.hour + n.acq_local.dt.minute / 60
    print(f"{len(n)} backscatter rows")
    print("acquisition hour, local, by direction:")
    print(n.groupby("direction").acq_hour_local.agg(["min", "mean", "max"]).round(2)
            .to_string())

    print("\nloggers:")
    log = load_loggers(a.loggers)

    # The full 30-minute series, so plots can show the trace rather than only
    # the points that happen to coincide with an overpass.
    os.makedirs(os.path.dirname(a.out_water) or ".", exist_ok=True)
    log.to_csv(a.out_water, index=False)
    print(f"  -> {a.out_water}: {len(log):,} records")

    # Nearest logger record within tolerance, per gauge.
    parts = []
    for gid, g in n.groupby("gauge_id"):
        L = (log[log.gauge_id == gid].dropna(subset=["water_cm"])
                .sort_values("ts_local"))
        g = g.sort_values("acq_local")
        if L.empty:
            parts.append(g)
            continue
        m = pd.merge_asof(g, L.drop(columns="gauge_id"),
                          left_on="acq_local", right_on="ts_local",
                          direction="nearest",
                          tolerance=pd.Timedelta(minutes=a.water_tolerance_min))
        m["water_gap_min"] = (m.acq_local - m.ts_local).dt.total_seconds() / 60
        parts.append(m)
    j = pd.concat(parts, ignore_index=True)

    # LAI, interpolated between weekly visits.
    r = pd.read_csv(a.readings)
    r["date"] = pd.to_datetime(r.date)
    j["lai"] = np.nan
    j["lai_gap_days"] = np.nan
    for gid, g in j.groupby("gauge_id"):
        lai, gap = interp_lai(r, gid, g.acq_local.values)
        j.loc[g.index, "lai"] = lai
        j.loc[g.index, "lai_gap_days"] = gap

    # NDVI from Sentinel-2, interpolated between CLEAR observations only.
    # Same rule as LAI: linear in time, no extrapolation, gap recorded.
    j["ndvi"] = np.nan
    j["ndvi_gap_days"] = np.nan
    if a.ndvi and os.path.exists(a.ndvi):
        nv = pd.read_csv(a.ndvi)
        nv = nv[nv.n_valid >= a.ndvi_min_valid].copy()
        nv["date"] = pd.to_datetime(nv.date)
        nv = nv.rename(columns={"ndvi_mean": "lai"})   # reuse interp_lai's column name
        for gid, g in j.groupby("gauge_id"):
            val, gap = interp_lai(nv, gid, g.acq_local.values)
            j.loc[g.index, "ndvi"] = val
            j.loc[g.index, "ndvi_gap_days"] = gap
        print(f"  NDVI: {len(nv)} clear observations (>= {a.ndvi_min_valid}/9 pixels)")
    else:
        print(f"  NDVI file not found ({a.ndvi}) -- skipped")

    j = j.sort_values(["gauge_id", "acq_local"])
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    j.to_csv(a.out, index=False)

    nw, nl = j.water_cm.notna().sum(), j.lai.notna().sum()
    both = (j.water_cm.notna() & j.lai.notna()).sum()
    print(f"\nwrote {a.out}: {len(j)} rows")
    print(f"  with water level (+/-{a.water_tolerance_min:.0f} min): {nw}")
    print(f"  with interpolated LAI                 : {nl}")
    print(f"  with BOTH                             : {both}")
    print(f"  with interpolated NDVI                : {j.ndvi.notna().sum()}")
    if nw:
        print(f"  water gap: median {j.water_gap_min.abs().median():.1f} min, "
              f"max {j.water_gap_min.abs().max():.1f} min")


if __name__ == "__main__":
    main()
