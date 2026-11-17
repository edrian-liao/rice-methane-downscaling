#!/usr/bin/env python3
"""
Lagged-window features for the 2026 E2 models, in the same format as the
2021-2024 model: one column per variable per day for the WINDOW_DAYS days
before the target day (t-WINDOW_DAYS .. t-1 predict day t; the 2021-2024 model used 14).

Normally used FROM THE NOTEBOOK (notebooks/train_model.ipynb): the notebook's
config cell sets WINDOW_DAYS, FILL, LABEL_UNIT, ... on this module and calls
build(), then picks the variables to model. Every available variable is built.

Run on its own, it writes the two tables below with the CONFIG defaults:

    python scripts/build_causal_features_2026.py

How the daily values are filled (FILL)
--------------------------------------
"locf"   CAUSAL. Each day carries forward the most recent real observation on or
         before that day (last observation carried forward). The value on day
         t-k never uses anything observed after t-k, so nothing after t-1 enters
         the row for day t. Days before a field's first observation are NaN.
"interp" AS IN THE 2021-2024 NOTEBOOK: linear interpolation in time between
         observations, then ffill/bfill at the ends of the season. NOT causal:
         an interpolated day leans toward the next observation, which can fall
         after t, and bfill copies later values backwards.

Outputs
-------
outputs/causal_features_2026.csv   one row per FIELD x day (all days START..END).
                                   Satellite features only. The notebook needs the
                                   field-level table for the NISAR permutation test.
outputs/model_table_2026.csv       one row per FIELD-day (or GAUGE-day, see LABEL_UNIT):
                                   the field's features + the water level and labels.

Feature columns: <sensor>_<var>_lag<k>, k = 1..WINDOW_DAYS, sensor in {s2, s1, nisar}
  lag1 = day t-1 (most recent), lag<WINDOW_DAYS> = oldest day.
  (The 2021-2024 notebook named the same values day1..day14 with day1 = oldest.)

Conventions (same as build_daily_dataset_2026.py)
-----------
* Day = LOCAL date (US/Central). S1 (~00:05 UTC) and NISAR descending (~01:00
  UTC) belong to the previous local evening.
* S1 orbits and NISAR tracks are POOLED into one series per sensor, with no
  track information (same as the 2021-2024 S1 handling). The incidence angle of
  each observation is available as its own variable (inc_deg); whether a model
  uses it is chosen in the notebook.
* Several observations on one local day are averaged: dB variables in linear
  power, then back to dB; other variables directly.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# ============================ CONFIG ==========================================
START = "2026-03-15"           # first local day with satellite data, inclusive
END   = "2026-10-20"           # last local day, inclusive
TZ    = "America/Chicago"

WINDOW_DAYS = 14               # days t-14 .. t-1 predict day t (as in the 2021-2024 model)
FILL        = "locf"           # "locf" (causal) or "interp" (2021-2024 method, not causal)

S1_CSV     = "data/S1_fields_2026.csv"
S2_CSV     = "data/S2_fields_2026.csv"
NISAR_CSV  = "outputs/nisar_field_means_2026.csv"
INCIDENCE_CSV = "outputs/incidence_2026.csv"   # NISAR incidence angle per granule x gauge
WATER_CSV  = "outputs/water_continuous_2026.csv"
GAUGES_CSV = "scripts/gauges_2026.csv"
OUT_FIELDS = "outputs/causal_features_2026.csv"
OUT_MODEL  = "outputs/model_table_2026.csv"

S1_MIN_VALID = 0.9             # use an S1 scene for a field only if it covers >= this fraction
S2_MIN_CLEAR = 0.5             # use an S2 date for a field only if >= this fraction is cloud-free

LABEL_UNIT = "field"           # "field": one row per field-day (gauges of a field averaged)
                               # "gauge": one row per gauge-day (Way 3 / Way 4 have 2 gauges each)
REQUIRE_ALL_GAUGES = False     # field mode: label a day only if ALL of the field's gauges have data

WATER_COL       = "water_cm_fixedbias"
WATER_VALID_CM  = (-80, 60)    # drop logger glitches (W3-CS reaches +1248 cm)
MIN_RECORDS_DAY = 24           # a gauge-day is labelled only with >= this many 30-min records (of 48)
DRY_2C     = 0                 # binary: water < 0 -> 0 (dry), else 1 (wet)
THRESH_3C  = (-1, 5)           # 3-class: < -1 -> 0, -1..5 -> 1, > 5 -> 2

# CV groups: Way 3 and Way 4 are adjacent fields, so they are held out together.
FIELD_GROUP = {"Way 3": "Way 3+4", "Way 4": "Way 3+4", "4 East": "4 East",
               "South 0": "South 0", "S16": "S16"}
# ==============================================================================

DAYS = pd.date_range(START, END, freq="D")

S2_ANGLES = ["solar_zenith", "solar_azimuth", "view_zenith_B4", "view_azimuth_B4",
             "view_zenith_B8", "view_azimuth_B8"]
SENSORS = {   # sensor -> (variables, variables stored in dB)
    # EVERY available variable is built; choose which ones a model uses in the notebook
    # (VARIABLES in notebooks/train_model.ipynb).
    "s2":    (["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12", "ndvi", "ndwi"] + S2_ANGLES, []),
    "s1":    (["vv_db", "vh_db", "ratio_db", "ratio_lin", "waterlike", "inc_deg"], ["vv_db", "vh_db"]),
    "nisar": (["hh_db", "hv_db", "ratio_db", "inc_deg"], ["hh_db", "hv_db"]),
}


def local_day(utc_times):
    """UTC timestamps -> local calendar date (naive, midnight)."""
    t = pd.to_datetime(utc_times)
    t = t.dt.tz_localize("UTC") if t.dt.tz is None else t
    return t.dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()


# ---------------------------------------------------------------- loaders -----
def load_s1():
    s = pd.read_csv(ROOT / S1_CSV)
    s = s[s.valid_frac >= S1_MIN_VALID].copy()
    s["day"] = local_day(s.time_utc)                  # NOT the GEE 'date' (UTC)
    return s.rename(columns={"VV_dB": "vv_db", "VH_dB": "vh_db", "VV_VH_ratio_dB": "ratio_db",
                             "VV_VH_ratio_lin": "ratio_lin", "WaterLike_Index": "waterlike",
                             "incidence_angle": "inc_deg"})


def load_s2():
    s = pd.read_csv(ROOT / S2_CSV).rename(columns={"NDVI": "ndvi", "NDWI": "ndwi"})
    s = s[s.clear_frac >= S2_MIN_CLEAR].dropna(subset=["ndvi"]).copy()
    s["day"] = pd.to_datetime(s.date).dt.normalize()  # ~17:00 UTC = local midday
    return s


def load_nisar():
    n = pd.read_csv(ROOT / NISAR_CSV, parse_dates=["acq_utc"]).dropna(subset=["hh_db"])
    inc = pd.read_csv(ROOT / INCIDENCE_CSV)
    inc = inc.groupby(["granule", "field"], as_index=False).incidence_deg.mean()   # mean over a field's gauges
    n = n.merge(inc.rename(columns={"incidence_deg": "inc_deg"}), on=["granule", "field"], how="left")
    n["day"] = local_day(n.acq_utc)
    return n


def daily_obs(obs, variables, db_vars):
    """Observations of one field -> one row per observation day (same-day rows averaged)."""
    o = obs[["day"] + variables].copy()
    for v in db_vars:                                  # average backscatter in linear power
        o[v] = 10 ** (o[v] / 10)
    g = o.groupby("day")[variables].mean()
    for v in db_vars:
        g[v] = 10 * np.log10(g[v])
    if "ratio_db" in variables and len(db_vars) == 2:  # keep ratio = ratio of the day's means
        g["ratio_db"] = g[db_vars[0]] - g[db_vars[1]]
    return g.sort_index()


# ---------------------------------------------------------------- features ----
def daily_filled(g, variables):
    """One field's observation days -> a value for every day in DAYS (see FILL)."""
    g = g[(g.index >= DAYS[0]) & (g.index <= DAYS[-1])][variables]
    daily = g.reindex(DAYS)
    if FILL == "locf":
        return daily.ffill()
    if FILL == "interp":
        return daily.interpolate(method="time").ffill().bfill()
    raise ValueError(f"FILL must be 'locf' or 'interp', not {FILL!r}")


def lag_features(g, sensor, variables):
    """Columns <sensor>_<var>_lag<k>: the filled value on day t-k, k = 1..WINDOW_DAYS."""
    daily = daily_filled(g, variables)
    cols = {}
    for v in variables:
        for k in range(1, WINDOW_DAYS + 1):
            cols[f"{sensor}_{v}_lag{k}"] = daily[v].shift(k).values
    return pd.DataFrame(cols, index=DAYS)


def field_features(sources, fields):
    frames = []
    for field in fields:
        parts = []
        for sensor, (variables, db_vars) in SENSORS.items():
            obs = sources[sensor]
            g = daily_obs(obs[obs.field == field], variables, db_vars)
            parts.append(lag_features(g, sensor, variables))
        d = pd.concat(parts, axis=1)
        d.insert(0, "doy", DAYS.dayofyear)
        d.insert(0, "date", DAYS)
        d.insert(0, "group", FIELD_GROUP[field])
        d.insert(0, "field", field)
        frames.append(d.reset_index(drop=True))
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------- labels ------
def water_labels():
    """Daily water level and labels per FIELD or GAUGE (local days), see LABEL_UNIT."""
    w = pd.read_csv(ROOT / WATER_CSV, parse_dates=["ts_local"])
    lo, hi = WATER_VALID_CM
    w = w[w[WATER_COL].between(lo, hi)].copy()
    w["date"] = w.ts_local.dt.normalize()             # logger clock is already local
    d = (w.groupby(["gauge_id", "date"])[WATER_COL]
          .agg(water_cm="mean", water_min="min", water_max="max", n_records="count")
          .reset_index())
    d = d[d.n_records >= MIN_RECORDS_DAY]
    g = pd.read_csv(ROOT / GAUGES_CSV)[["gauge_id", "field_name"]].rename(columns={"field_name": "field"})
    d = d.merge(g, on="gauge_id")
    if LABEL_UNIT == "field":
        # Field-day = mean of its gauges' daily values (water_min/max = mean of the gauges' min/max).
        d = (d.groupby(["field", "date"])
              .agg(water_cm=("water_cm", "mean"), water_min=("water_min", "mean"),
                   water_max=("water_max", "mean"), n_records=("n_records", "sum"),
                   n_gauges=("gauge_id", "nunique"))
              .reset_index())
        if REQUIRE_ALL_GAUGES:
            need = g.groupby("field").gauge_id.nunique()
            d = d[d.n_gauges == d.field.map(need)]
        d["unit"] = d.field
    else:
        d["n_gauges"] = 1
        d["unit"] = d.gauge_id
    m = d.water_cm
    d["label_2c"] = np.where(m < DRY_2C, 0, 1)
    d["label_3c"] = np.select([m < THRESH_3C[0], m <= THRESH_3C[1]], [0, 1], 2)
    return d


# ---------------------------------------------------------------- build -------
def build():
    """-> (feats, model): field x day features and unit x day model table, using the
    module-level settings (WINDOW_DAYS, FILL, LABEL_UNIT, ...). The notebook sets those
    attributes and calls this; running the script calls it via main()."""
    global DAYS
    DAYS = pd.date_range(START, END, freq="D")
    sources = {"s1": load_s1(), "s2": load_s2(), "nisar": load_nisar()}
    fields = list(pd.read_csv(ROOT / GAUGES_CSV).field_name.drop_duplicates())
    feats = field_features(sources, fields)
    labels = water_labels()
    # A label day needs a full window inside START..END.
    first_ok = DAYS[0] + pd.Timedelta(days=WINDOW_DAYS)
    labels = labels[(labels.date >= first_ok) & (labels.date <= DAYS[-1])]
    model = labels.merge(feats, on=["field", "date"], how="left")
    front = ["unit", "field", "group", "date", "doy", "water_cm", "water_min", "water_max",
             "n_records", "n_gauges", "label_2c", "label_3c"]
    model = model[front + [c for c in model.columns if c not in front + ["gauge_id"]]]
    model = model.sort_values(["unit", "date"]).reset_index(drop=True)
    return feats, model


def main():
    feats, model = build()
    fields = list(feats.field.unique())
    for path, df in ((OUT_FIELDS, feats), (OUT_MODEL, model)):
        (ROOT / path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(ROOT / path, index=False)

    # ---- summary ----
    n_feat = {s: sum(c.startswith(s + "_") for c in feats.columns) for s in SENSORS}
    print(f"FILL = {FILL!r}, window = days t-{WINDOW_DAYS}..t-1")
    print(f"wrote {OUT_FIELDS}: {len(feats):,} rows ({len(fields)} fields x {len(DAYS)} days)")
    print(f"wrote {OUT_MODEL}: {len(model):,} {LABEL_UNIT}-days, {model.unit.nunique()} {LABEL_UNIT}s")
    print(f"feature columns per sensor (variables x {WINDOW_DAYS} days): {n_feat}\n")
    print(f"{LABEL_UNIT}-days per {LABEL_UNIT}, date range, and labels:")
    print(model.groupby("unit").agg(
        field=("field", "first"), first=("date", "min"), last=("date", "max"), n=("date", "size"),
        two_gauge_days=("n_gauges", lambda s: (s >= 2).sum()),
        dry_2c=("label_2c", lambda s: (s == 0).sum()), wet_2c=("label_2c", lambda s: (s == 1).sum()),
        c0=("label_3c", lambda s: (s == 0).sum()), c1=("label_3c", lambda s: (s == 1).sum()),
        c2=("label_3c", lambda s: (s == 2).sum())).to_string())
    print(f"\nshare of {LABEL_UNIT}-days with an empty (NaN) feature, by sensor and lag:")
    first_var = {s: v[0] for s, (v, _) in SENSORS.items()}
    print(pd.DataFrame({s: [model[f"{s}_{first_var[s]}_lag{k}"].isna().mean() for k in (1, WINDOW_DAYS)]
                        for s in SENSORS}, index=["lag1", f"lag{WINDOW_DAYS}"]).round(3).to_string())


if __name__ == "__main__":
    main()
