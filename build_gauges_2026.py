#!/usr/bin/env python3
"""
Build gauges_2026.csv and readings_2026.csv from the 2026 Runkle workbooks.

Sources, both under data/2026_WaterLevel_Runkle/:
  AgronomyData2026.xlsx                      -- field codes + coordinates
  RiceFieldDataCanopyLAI_Summer2026 ....xlsx -- one sheet per field, weekly visits

Coordinates in the agronomy sheet are a single "lat, lon" string, so they are
split here rather than retyped.
"""
import numpy as np
import pandas as pd

D = "data/2026_WaterLevel_Runkle"
AGRO = f"{D}/AgronomyData2026.xlsx"
LAI = f"{D}/RiceFieldDataCanopyLAI_Summer2026 - To Share.xlsx"

# field code -> sheet name in the LAI workbook (they differ: R8 is filed as "4 East")
SHEET = {"W3-EC": "W3EC", "W3-CS": "W3CS", "W4-EC": "W4EC", "W4-CS": "W4CS",
         "S16": "S16", "R8": "4 East", "S0": "S0"}

a = pd.read_excel(AGRO, sheet_name="Farmer data 2026", header=3)
a = a.dropna(subset=["Field code", "Coordinates"])
# The sheet carries a unit-conversion block below the data (1kg, 1 acre, ...)
# whose values land in the Coordinates column -- keep only real field codes.
a = a[a["Field code"].astype(str).str.strip().isin(SHEET)]
latlon = a.Coordinates.str.split(",", expand=True).astype(float)
g = pd.DataFrame({
    "gauge_id": a["Field code"].str.strip(),
    "lat": latlon[0].values,
    "lon": latlon[1].values,          # already negative (west)
    "field_name": a["Field name"].values,
    "acres": a["Acres"].values,
    "levees": a["Levees"].values,
})
g["sheet"] = g.gauge_id.map(SHEET)
assert g.sheet.notna().all(), f"unmapped field codes: {g[g.sheet.isna()].gauge_id.tolist()}"
assert g.lat.between(25, 50).all() and g.lon.between(-100, -80).all(), "coords out of range"
g.drop(columns="sheet").to_csv(f"{D}/gauges_2026.csv", index=False)
print(f"gauges_2026.csv: {len(g)} fields")
print(g[["gauge_id", "lat", "lon", "field_name"]].to_string(index=False))

def _lai(d):
    """Leaf area index per visit, from whichever LAI column actually has data."""
    if "LAI raw" in d.columns:
        v = pd.to_numeric(d["LAI raw"], errors="coerce")
        if v.notna().any():
            return v
    v1 = pd.to_numeric(d.get("LAI value 1"), errors="coerce")
    v2 = pd.to_numeric(d.get("LAI value 2"), errors="coerce")
    return pd.concat([v1, v2], axis=1).mean(axis=1)


rows = []
for _, s in g.iterrows():
    d = pd.read_excel(LAI, sheet_name=s.sheet, header=5)
    rows.append(pd.DataFrame({
        "gauge_id": s.gauge_id,
        "date": pd.to_datetime(d["Date"], errors="coerce"),
        "floodwater_depth_cm": pd.to_numeric(d.get("Floodwater Depth Avg."), errors="coerce"),
        "pipe_depth_cm": pd.to_numeric(d.get("Floodwater Depth (Pipe)"), errors="coerce"),
        # NB: the plain "LAI" column exists but is EMPTY in every sheet. The
        # real values are in "LAI raw", which is the mean of LAI value 1 and 2;
        # fall back to computing that mean if "LAI raw" is ever absent.
        "lai": _lai(d),
        "canopy_height_cm": pd.to_numeric(d.get("Canopy Height Avg"), errors="coerce"),
        "growth_stage": d.get("Growth stage"),
        "dap": pd.to_numeric(d.get("DAP"), errors="coerce"),
    }).dropna(subset=["date"]))
r = pd.concat(rows, ignore_index=True)

# Water level = flood depth when flooded, else the (negative) pipe reading.
# This is a modelling CHOICE, not a measurement -- change it here if you
# would rather keep the two series separate.
r["water_level_cm"] = np.where(r.floodwater_depth_cm.fillna(0) > 0,
                               r.floodwater_depth_cm, r.pipe_depth_cm)
r.to_csv(f"{D}/readings_2026.csv", index=False)
print(f"\nreadings_2026.csv: {len(r)} rows, {r.date.min().date()}..{r.date.max().date()}, "
      f"{(r.water_level_cm > 0).sum()} flooded / {(r.water_level_cm <= 0).sum()} dry")
