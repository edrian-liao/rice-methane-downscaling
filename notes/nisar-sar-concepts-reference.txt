# NISAR for paddy water level — conceptual reference

_Companion to the inherited baseline notes. Covers how the data is organized, what
the radar actually measures, and what to extract for the model._

---

## 1. How NISAR data is organized

### Track vs frame

A **track** (path, relative orbit) is the ground path the satellite flies — the whole
diagonal chain of boxes across the AOI in the Vertex search view. A **frame** is one
segment of that chain, produced by chopping the continuous acquisition into
manageable file sizes.

| | Same track, different frames | Different tracks |
|---|---|---|
| Acquisition time | Same pass, seconds apart | Different days |
| Look geometry | Identical | Different incidence angle, possibly opposite orbit direction |
| Merging | Safe — bookkeeping | Not safe — injects geometry artifacts |

The same numbered track repeats every 12 days. That repeat is what makes a time
series possible: same geometry, different date, so a change in backscatter is a
change on the ground rather than a change in viewing angle.

### There is no "everything on one date"

The satellite flies one orbit at a time. On any given date only one track crosses the
AOI, covering a strip — not the whole area. Tiling the full AOI requires a complete
12-day cycle assembled from several tracks on several dates.

Exception: ascending (~18:00 local) and descending (~06:00 local) passes can share a
calendar date. These are two separate observations 12 hours apart with opposite look
directions. Never merge them.

### Consequence for the pipeline

Invert the usual framing. Instead of "what covers my AOI on date D", ask "for gauge G,
which track covers it, and what is its 12-day time series".

Extra observations beyond the 12-day cadence come from two sources:

- **Side-lap** between adjacent tracks — a gauge in the overlap zone is seen by two
  paths, on different dates at different incidence angles
- **Ascending + descending** coverage of the same gauge

So a gauge may get one to four observations per 12-day window, but they are not
interchangeable. Build the coverage table (gauge x path x orbit direction x date)
before touching any pixels, and start modeling with single-track gauges only.

### Mechanics

GCOV is already geocoded and radiometrically terrain corrected, so this is a
grid-alignment problem, not a geometric correction problem.

1. Query with `earthaccess.search_data(short_name=..., polygon=..., temporal=...)`
2. Group results by (orbit direction, path, date) and inspect before downloading
3. Define one target grid up front — single CRS, resolution, origin, extent. Adjacent
   frames can land in different UTM zones
4. Apply layover/shadow and quality masks before merging, so nodata is honest
5. Reproject to the target grid (`rioxarray.reproject_match` or `gdal.Warp`)
6. Stitch frames along-track; average overlaps in linear power, never in dB
7. Stack by time into `(time, y, x)` per channel, one cube per track

**Shortcut:** for gauge-based modeling, a full mosaic may be unnecessary. Extract a
small window around each gauge from each granule natively and align by time. Build
mosaics only for figures and visual inspection. Either way, record the path number
alongside every extracted sample.

---

## 2. What the radar measures

A radar measures how much of the pulse it transmitted comes back, and in what state.
Per pixel, GCOV gives the polarimetric covariance matrix — diagonal terms are channel
intensities, off-diagonal terms are complex cross-correlations carrying relative phase
and coherence between channels.

### Three scattering regimes over a paddy

| Regime | Mechanism | Return |
|---|---|---|
| Open water | Specular — energy reflects away from sensor | Very low |
| Flooded stems | Double bounce off water then stem, retroreflected | **Very high** |
| Closed canopy | Random volume scattering in leaves | Moderate, depolarized |

The counterintuitive part: **a flooded paddy is bright, not dark.** Standard flood
mapping relies on open water being a mirror, but once stems emerge through the water
the water–stem dihedral acts as a corner reflector. The signal to key on is the
double-bounce enhancement — strongest in HH, with the HH–VV phase difference near 180°
instead of near 0°.

### Why wavelength is the whole argument

Scattering regime depends on object size relative to wavelength.

- **C-band, 5.6 cm** — comparable to a rice leaf. After canopy closure the canopy is
  effectively opaque; you get volume scattering and the water underneath is invisible.
- **L-band, 24 cm** — large compared to a leaf, so leaves are nearly transparent. The
  wave interacts with stems and the water surface. Double bounce survives closure.

This is the entire physical basis for the NISAR track, and exactly what the residual
diagnostic in the baseline notes would make visible.

---

## 3. What NISAR adds over Sentinel-1

In descending order of importance:

1. **Wavelength.** No amount of processing recovers a signal the wave never reached.
2. **HH polarization.** Sentinel-1 IW over land is almost always VV+VH; HH+HV is
   reserved mostly for polar regions. Double bounce is strongest in HH and weak in VV,
   so the inherited C-band pipeline may be observing flooded vegetation in close to the
   worst available polarization — independent of the wavelength problem.
3. **Inter-channel phase.** Dual-pol VV+VH gives two intensities and one correlation.
   The HH–VV co-pol phase difference — the cleanest surface-vs-double-bounce
   discriminator — is not computable from Sentinel-1 IW at all.

S-band (9 cm) is close enough to C-band to add little, and its acquisition is
geographically limited.

**What Sentinel-1 still has:** a decade-long archive back to 2014, where the training
labels almost certainly live. NISAR gives the right physics on a very short record;
Sentinel-1 gives the wrong physics on a long one. This is why the ALOS-2 / ALOS-1
fallback matters — it is the only way to test the L-band hypothesis against historical
in-situ records.

**Action item:** check which Sentinel-1 polarizations are actually in the inherited
bandstack. If VV+VH, part of the R² ≈ 0.4 may be attributable to polarization choice
rather than wavelength — much cheaper to establish than a sensor integration.

---

## 4. Optical vs SAR

Optical is passive, SAR is active. Everything else follows.

An optical sensor collects sunlight someone else emitted. Sunlight is broadband, so
the return carries a spectrum, and materials reflect different fractions at different
wavelengths. Slicing that spectrum is free information — that is what a band is.

A radar makes its own illumination at a single frequency. There is no spectrum to
slice. It has bandwidth (tens of MHz), but that is spent on range resolution, not
material discrimination. Instead it gets two dimensions optical lacks:

- **Polarization** — you control transmit and receive orientation. A geometric
  dimension, where optical bands are a chemical one.
- **Phase** — the radar knows what it transmitted, so the return is coherent. Each
  channel is a complex number, not a scalar.

|  | Optical | SAR |
|---|---|---|
| Sensitive to | Pigments, chemistry, surface composition | Geometry, structure, dielectric constant |
| Sees | Top of canopy only | Through canopy, wavelength dependent |
| Requires | Daylight, clear sky | Nothing |
| Water signal | Canopy water content | Standing water, via double bounce |

The two are complementary rather than redundant: Sentinel-2 tells you the growth
stage, NISAR tells you what is happening at the ground beneath it.

### Why covariance rather than four numbers

Coherent imaging is heavily speckled, so neighboring looks are averaged. Averaging
destroys the absolute phase of any single channel — it is essentially random, depending
on sub-wavelength scatterer positions. But *relative* phase between channels survives,
because both channels illuminated the same scatterers at the same instant.

The covariance matrix is the smallest object that survives multi-looking while
retaining that information. For quad-pol, reciprocity (HV = VH for natural targets)
collapses four channels to three, giving a Hermitian 3×3 matrix = 9 real numbers per
pixel:

- 3 real diagonal: `HHHH`, `HVHV`, `VVVV`
- 3 complex off-diagonal: `HHHV`, `HHVV`, `HVVV` (stored as real and imaginary parts)

Everything is in **linear power**. Do all matrix algebra in linear units and convert to
dB only at the very end.

---

## 5. Can SAR measure water depth?

Partially, weakly, and conditionally.

The mechanism is not depth directly. As water rises, less stem is exposed, changing
the effective area of the water–stem dihedral. Backscatter tracks submerged stem
fraction; depth is inferred from that. Consequences:

- **Saturation and reversal.** Once water fully submerges the emergent vegetation the
  dihedral disappears and the signal collapses back toward specular open water — it does
  not keep trending.
- **Plant height confound.** An ALOS-2 study over Japanese paddies found surface
  scattering contributions dropping beyond 70 cm plant height, with random forest
  *inundation classification* reaching 88% only below that threshold, and only when
  plant height was known. That is the easier binary task degrading.
- **Wind** roughens the surface and suppresses the specular component.

### Realistic benchmark

A 2026 study over Subang, West Java — ALOS-2/PALSAR-2 quad-pol, random forest, in-situ
water level — reported MAE 1.37 cm, RMSE 1.79 cm, R² 0.52, r 0.73, integrating
backscatter, Freeman–Durden, and H/α parameter groups. No single group was sufficient.
Mean alpha contributed most. The vegetative–generative transition was the best temporal
window, due to enhanced double bounce and reduced saturation.

So R² ≈ 0.52 is the realistic target. The inherited baseline is at 0.4. L-band with
full polarimetry buys something real, but nobody is getting 0.9 on this problem.

**Worth checking on the existing model:** does it separate flooded from non-flooded much
better than it ranks depths within flooded cases? If so, the ceiling may be partly
physical rather than a data-alignment problem — the measurement contains a strong
inundation signal and a weak depth signal, and a continuous regression is being asked
for more than is there.

### Phenology

The timing of crop life-cycle stages: transplanting, tillering, stem elongation,
panicle initiation, heading, flowering, grain filling, ripening.

Backscatter changes enormously across these stages at constant water level. Newly
transplanted ≈ open water, dark. Tillering ≈ stems in water, bright double bounce.
Heading ≈ dense canopy, volume scattering dominant. Same depth, three very different
pixel values.

This is why the literature splits the season into phases. A single model trained across
the whole season is inverting a function whose shape changes underneath it. Sentinel-2
NDVI is a reasonable proxy for growth stage — useful primarily as a **conditioning or
stratification variable**, not just another predictor.

---

## 6. Features to extract

**First, check the polarization mode.** NISAR's default land mode is dual-pol,
typically HH+HV, with quad-pol only over selected areas. Dual-pol means no VV, hence no
HH–VV phase difference and no Freeman–Durden — the two features the paddy literature
leans on hardest. Verify before planning around features you may not have.

### Tier 1 — always compute (quad-pol)

| Feature | Expression | Meaning |
|---|---|---|
| Intensities | `HHHH`, `HVHV`, `VVVV` | Base backscatter, linear power |
| Co-pol ratio | HH / VV | Double-bounce discriminator |
| Cross-pol ratio | HV / (HH + VV) | Volume scattering, canopy density |
| Co-pol phase difference | `angle(HHVV)` | Cleanest inundation indicator: ~180° double bounce, ~0° surface |
| Co-pol coherence | `abs(HHVV) / sqrt(HHHH * VVVV)` | Confidence in the phase difference |

Phase near 180° with low coherence is noise, not double bounce — always carry both.

### Tier 2 — decompositions

- **Freeman–Durden** — surface / double-bounce / volume power fractions. The
  double-bounce fraction is close to a physical "water present under canopy" feature;
  the volume fraction is close to a canopy-stage feature.
- **H / A / α** — entropy, anisotropy, mean alpha. Mean alpha was the top contributor
  in the Subang study.

These overlap substantially with Tier 1 (alpha largely repackages the co-pol
relationships). Check correlations and drop duplicates rather than stacking both.

### Tier 3 — context

- **Local incidence angle** (ships with GCOV). Double-bounce strength depends on it
  steeply; RTC corrects terrain-induced area distortion but not this dependence.
- **Path number** as a categorical, for any gauge seeing multiple tracks.
- **Temporal deltas** — change from previous same-track pass, and from a dry-season
  reference. Absolute backscatter conflates water, biomass, and soil; change relative to
  the same pixel at another date cancels many static confounds. Often where the real
  signal lives.
- **Sentinel-2 NDVI** as phenology conditioner; **plant height** if available in the
  field data.

### Avoid

- Feeding all nine raw covariance elements in directly. Real and imaginary parts of an
  off-diagonal term have no separate physical meaning; magnitude and phase do. Splitting
  them forces the model to relearn a coordinate transform from limited data.
- Computing features on dB values, then averaging spatially. Do the algebra in linear
  power inside the gauge buffer; transform at the end.
- Large feature counts. With ~7–8 NISAR passes, a random forest on twenty features will
  memorize. Start with Tier 1 + incidence angle + NDVI; add decompositions only if they
  earn it.

---

## 7. Sanity check before any modeling

Plot co-pol phase difference against measured water level, colored by NDVI.

- Flooded points cluster near 180°, drained near 0° → the physics is present in the
  data and the rest is engineering.
- No separation at all → something upstream is wrong: geolocation, buffer size, or
  product calibration status. No feature engineering will rescue it.

**Related open item:** the baseline notes give 17 June 2026 as the start of provisional
calibrated L-band products, but the Vertex search shows acquisitions from 2 February
2026. Those earlier granules are likely beta or uncalibrated — fine for coverage
planning, not for backscatter intended for regression. Check the processing level and
calibration status field. If the Feb–June products turn out usable at some level, it
materially changes the temporal overlap picture against the in-situ record.

---

## References

1. Evaluation of ALOS-2/PALSAR-2 L-Band SAR Polarimetric Parameters for Water-Level
   Estimation in Irrigated Rice Paddy Fields. *Remote Sensing*, 2026.
   https://doi.org/10.3390/rs18091313
   — Closest analogue to this project: L-band quad-pol, random forest, in-situ water
   level. Start here.

2. Characterizing L-Band Backscatter in Inundated and Non-Inundated Rice Paddies for
   Water Management Monitoring. *Remote Sensing*, 2026.
   https://doi.org/10.3390/rs18020370
   — Freeman–Durden against plant height and water level; where the signal saturates.

3. Spatiotemporal variations of wetland backscatter: the role of water depth and
   vegetation characteristics in Sentinel-1 dual-polarization SAR observations.
   *Remote Sensing of Environment*, 2022.
   https://www.sciencedirect.com/science/article/abs/pii/S0034425721005848
   — C-band depth sensitivity in the Everglades; why it appears in co-pol but not
   cross-pol.

4. Flood mapping under vegetation using single SAR acquisitions.
   *Remote Sensing of Environment*, 2020.
   https://www.sciencedirect.com/science/article/abs/pii/S0034425719306029
   — When double bounce appears, and when canopy density defeats it.

5. Waterline mapping in flooded vegetation from airborne SAR imagery.
   *Remote Sensing of Environment*, 2003.
   https://www.sciencedirect.com/science/article/abs/pii/S0034425703000063
   — Cleanest early demonstration of the 180° HH–VV phase difference at L-band.

6. Separating Water-Level Variations and Phenological Changes in Rice Paddies.
   *Remote Sensing*, 2026.
   https://doi.org/10.3390/rs18071055
   — Sentinel-1 and ALOS-2 compared directly, with phenology as the central problem.