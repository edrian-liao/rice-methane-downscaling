#!/usr/bin/env python3
"""
Build the daily 2026 dataset: one row per field per day, with NISAR, Sentinel-1
and Sentinel-2 linearly interpolated to daily, and the logger water level.

    python scripts/build_daily_dataset_2026.py

Output: outputs/daily_dataset_2026.csv. Edit the CONFIG block below, nothing else.

Conventions
-----------
* DAY = LOCAL calendar date (US/Central), because the loggers record local time.
  Satellite UTC times are converted first. This matters: Sentinel-1 (~00:05 UTC)
  and NISAR descending (~01:00 UTC) are the PREVIOUS EVENING locally, so their
  GEE/UTC 'date' is one day later than the local day they belong to.
* GEOMETRIES ARE KEPT APART. Each NISAR track and each Sentinel-1 orbit is its
  own viewing geometry (incidence: T12 ~45, T84 ~35, T105 ~41 deg; S1 orbit 63
  ~44, orbit 165 ~33 deg), so each is interpolated on its own, giving one set of
  columns per track/orbit. Pooled columns (all passes in date order, as the
  2021-2024 training data did for S1) are added too if POOLED = True.
* NO EXTRAPOLATION. A series is interpolated only between its first and last
  real observation for that field; outside that it is NaN.
* <prefix>_gap_days = days from that row to the NEAREST real observation of
  that series (0 on an acquisition day). Use it to filter or as a feature.
* WATER IS NEVER INTERPOLATED. Daily stats from the 30-min loggers, averaged
  over a field's gauges (Way 3 and Way 4 have two each). Days without logger
  data have empty labels.

Columns
-------
field, date, doy
nisar_{hh_db,hv_db,ratio_db,inc_deg,gap_days}_T{12,84,105}   (+ _all if POOLED)
s1_{vv_db,vh_db,ratio_db,ratio_lin,waterlike,inc_deg,gap_days}_O{63,165}  (+ _all)
s2_{B2..B12,ndvi,ndwi,solar_zenith,...,view_azimuth_B8}, s2_gap_days
water_{mean,min,max,std,n_records}, n_gauges, label_2c, label_3c
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# ============================ CONFIG ==========================================
START = "2026-03-15"          # first day (local), inclusive -- same as the GEE exports
END   = "2026-10-20"          # last day (local), inclusive
TZ    = "America/Chicago"

S1_CSV        = "data/S1_fields_2026.csv"            # gee_s1_fields_2026.js
S2_CSV        = "data/S2_fields_2026.csv"            # gee_s2_fields_2026.js
NISAR_CSV     = "outputs/nisar_field_means_2026.csv" # notebooks/nisar_field_means.ipynb
INCIDENCE_CSV = "outputs/incidence_2026.csv"         # scripts/incidence_at_fields.py
WATER_CSV     = "outputs/water_continuous_2026.csv"  # scripts/build_analysis_table.py
GAUGES_CSV    = "scripts/gauges_2026.csv"
OUT_CSV       = "outputs/daily_dataset_2026.csv"

POOLED         = True         # also add series pooled across tracks / orbits (suffix _all)
S2_MIN_CLEAR   = 0.5          # use an S2 date for a field only if >= this fraction is cloud-free
S1_MIN_VALID   = 0.9          # use an S1 scene for a field only if it covers >= this fraction
WATER_COL      = "water_cm_fixedbias"
WATER_VALID_CM = (-80, 60)    # drop logger glitches (W3-CS reaches +1248 cm)
DRY_2C         = 0            # 2-class: water < 0 -> dry (0), else wet (1)
THRESH_3C      = (-1, 5)      # 3-class: < -1 dry (0), -1..5 medium (1), > 5 high (2)
# ==============================================================================

DAYS = pd.date_range(START, END, freq="D")


def local_day(utc_times):
    """UTC timestamps -> local calendar date (naive, midnight)."""
    t = pd.to_datetime(utc_times)
    t = t.dt.tz_localize("UTC") if t.dt.tz is None else t
    return t.dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()


def daily_interp(obs, value_cols, prefix, suffix=""):
    """One field's observations -> daily frame over DAYS.

    obs: DataFrame with a 'day' column and value_cols (several rows on one day
    are averaged). Returns interpolated columns named f"{prefix}_{col}{suffix}"
    plus f"{prefix}_gap_days{suffix}".
    """
    out = pd.DataFrame(index=DAYS)
    if obs.empty:
        for c in value_cols + ["gap_days"]:
            out[f"{prefix}_{c}{suffix}"] = np.nan
        return out
    g = obs.groupby("day")[value_cols].mean()
    g = g[(g.index >= DAYS[0]) & (g.index <= DAYS[-1])]
    daily = g.reindex(DAYS)
    # Linear in time, only BETWEEN real observations (no extrapolation at the ends).
    daily = daily.interpolate(method="time", limit_area="inside")
    for c in value_cols:
        out[f"{prefix}_{c}{suffix}"] = daily[c].values
    # Days to the nearest real observation; NaN where the series is NaN.
    obs_days = g.dropna(how="all").index.values.astype("datetime64[D]")
    if len(obs_days):
        d = np.abs(DAYS.values.astype("datetime64[D]")[:, None] - obs_days[None, :]).min(axis=1)
        gap = d.astype(float)
        gap[daily[value_cols].isna().all(axis=1).values] = np.nan
    else:
        gap = np.nan
    out[f"{prefix}_gap_days{suffix}"] = gap
    return out


# ---------------------------------------------------------------- NISAR -------
def load_nisar():
    n = pd.read_csv(ROOT / NISAR_CSV, parse_dates=["acq_utc"]).dropna(subset=["hh_db"])
    inc = pd.read_csv(ROOT / INCIDENCE_CSV, parse_dates=["acq_utc"])
    inc = inc.groupby(["granule", "field"], as_index=False).incidence_deg.mean()   # mean over gauges
    n = n.merge(inc.rename(columns={"incidence_deg": "inc_deg"}), on=["granule", "field"], how="left")
    n["day"] = local_day(n.acq_utc)
    return n


def nisar_daily(n, field):
    f = n[n.field == field]
    parts = []
    groups = [(f"_T{t}", f[f.track == t]) for t in sorted(n.track.unique())]
    if POOLED:
        groups.append(("_all", f))
    for suffix, g in groups:
        d = daily_interp(g, ["hh_db", "hv_db", "inc_deg"], "nisar", suffix)
        # Ratio from the interpolated dB values (dB difference is linear, so this is consistent).
        d[f"nisar_ratio_db{suffix}"] = d[f"nisar_hh_db{suffix}"] - d[f"nisar_hv_db{suffix}"]
        parts.append(d)
    return pd.concat(parts, axis=1)


# ---------------------------------------------------------------- Sentinel-1 --
S1_VARS = ["vv_db", "vh_db", "ratio_db", "ratio_lin", "waterlike", "inc_deg"]


def load_s1():
    s = pd.read_csv(ROOT / S1_CSV)
    s = s[s.valid_frac >= S1_MIN_VALID].copy()
    s["day"] = local_day(s.time_utc)                    # NOT the GEE 'date' column (UTC)
    s["orbit"] = s.relative_orbit.astype(int)
    return s.rename(columns={"VV_dB": "vv_db", "VH_dB": "vh_db", "VV_VH_ratio_dB": "ratio_db",
                             "VV_VH_ratio_lin": "ratio_lin", "WaterLike_Index": "waterlike",
                             "incidence_angle": "inc_deg"})


def s1_daily(s, field):
    f = s[s.field == field]
    groups = [(f"_O{o}", f[f.orbit == o]) for o in sorted(s.orbit.unique())]
    if POOLED:
        groups.append(("_all", f))
    # Same-day duplicates of one scene are averaged inside daily_interp.
    return pd.concat([daily_interp(g, S1_VARS, "s1", sfx) for sfx, g in groups], axis=1)


# ---------------------------------------------------------------- Sentinel-2 --
S2_VARS = ["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12", "ndvi", "ndwi",
           "solar_zenith", "solar_azimuth", "view_zenith_B4", "view_azimuth_B4",
           "view_zenith_B8", "view_azimuth_B8"]


def load_s2():
    s = pd.read_csv(ROOT / S2_CSV).rename(columns={"NDVI": "ndvi", "NDWI": "ndwi"})
    s = s[s.clear_frac >= S2_MIN_CLEAR].dropna(subset=["ndvi"]).copy()
    # S2 passes at ~17:00 UTC = midday locally, so the GEE date is already the local date.
    s["day"] = pd.to_datetime(s.date).dt.normalize()
    return s


def s2_daily(s, field):
    return daily_interp(s[s.field == field], S2_VARS, "s2")


# ---------------------------------------------------------------- water -------
def load_water_daily():
    """Daily water stats per FIELD (mean of its gauges' daily stats), local days."""
    w = pd.read_csv(ROOT / WATER_CSV, parse_dates=["ts_local"])
    lo, hi = WATER_VALID_CM
    w = w[w[WATER_COL].between(lo, hi)].copy()
    w["day"] = w.ts_local.dt.normalize()               # logger clock is already local
    g = pd.read_csv(ROOT / GAUGES_CSV)[["gauge_id", "field_name"]]
    w = w.merge(g, on="gauge_id")
    per_gauge = (w.groupby(["field_name", "gauge_id", "day"])[WATER_COL]
                  .agg(mean="mean", min="min", max="max", std="std", n_records="count")
                  .reset_index())
    per_field = (per_gauge.groupby(["field_name", "day"])
                 .agg(water_mean=("mean", "mean"), water_min=("min", "mean"),
                      water_max=("max", "mean"), water_std=("std", "mean"),
                      water_n_records=("n_records", "sum"), n_gauges=("gauge_id", "nunique"))
                 .reset_index().rename(columns={"field_name": "field"}))
    m = per_field.water_mean
    per_field["label_2c"] = np.where(m < DRY_2C, 0, 1)
    per_field["label_3c"] = np.select([m < THRESH_3C[0], m <= THRESH_3C[1]], [0, 1], 2)
    return per_field


# ---------------------------------------------------------------- build -------
def main():
    nisar, s1, s2 = load_nisar(), load_s1(), load_s2()
    water = load_water_daily()
    fields = list(pd.read_csv(ROOT / GAUGES_CSV).field_name.drop_duplicates())

    frames = []
    for field in fields:
        d = pd.concat([nisar_daily(nisar, field), s1_daily(s1, field), s2_daily(s2, field)], axis=1)
        d.insert(0, "doy", d.index.dayofyear)
        d.insert(0, "date", d.index)
        d.insert(0, "field", field)
        frames.append(d.reset_index(drop=True))
    out = pd.concat(frames, ignore_index=True)
    out = out.merge(water.rename(columns={"day": "date"}), on=["field", "date"], how="left")
    for c in ("label_2c", "label_3c", "n_gauges", "water_n_records"):
        out[c] = out[c].astype("Int64")                 # integers, with empty where no logger

    (ROOT / OUT_CSV).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(ROOT / OUT_CSV, index=False)

    # ---- summary ----
    print(f"wrote {OUT_CSV}: {len(out):,} rows ({out.field.nunique()} fields x {len(DAYS)} days, "
          f"{START}..{END}), {out.shape[1]} columns\n")
    key = ["nisar_hh_db_T12", "nisar_hh_db_T84", "nisar_hh_db_T105", "nisar_hh_db_all",
           "s1_vv_db_O63", "s1_vv_db_O165", "s1_vv_db_all", "s2_ndvi", "water_mean"]
    key = [k for k in key if k in out]
    cov = out.groupby("field")[key].apply(lambda g: g.notna().sum()).T
    print("non-empty days per column and field:")
    print(cov.to_string())
    lab = out.dropna(subset=["water_mean"])
    print(f"\nlabelled days: {len(lab)}  | 2-class dry/wet: "
          f"{(lab.label_2c == 0).sum()}/{(lab.label_2c == 1).sum()}  | 3-class dry/med/high: "
          f"{(lab.label_3c == 0).sum()}/{(lab.label_3c == 1).sum()}/{(lab.label_3c == 2).sum()}")
    full = lab.dropna(subset=["nisar_hh_db_all", "s1_vv_db_all", "s2_ndvi"])
    print(f"labelled days with NISAR (pooled), S1 (pooled) and S2 all present: {len(full)}")


if __name__ == "__main__":
    main()
