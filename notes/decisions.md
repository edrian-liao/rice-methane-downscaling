# Decisions

## D-01 — Include ascending passes? (2026-11-12)

**Question:** Use all passes, or descending only?

**Evidence**
- ASC (T105) vs DESC (T84), 34.5 h apart, 45 field-pairs / 9 dates:
  HH +0.21 dB (p=0.34, per-date), HV +0.02 dB (p=0.93).
- DESC T12 vs T84, 5 d apart, 14 dates: HV +1.58 dB (p=5.5e-7), ratio −1.88 dB.
- Source: outputs/nisar_field_means_2026.csv @ commit abc1234; analysis in notebooks/…

**Literature**
- Using Sentinel-1 [^1] says that because of the limited number of passes within ~2 months both passes were taken into account.
- Using Sentinel-1, [^2] only used the descending passes to mitigate the variation between the backscattering responses. However, the passes we have don't have signifant differences with their $\sigma^0$ coefficients.

**Decision:** All passes; interpolate within track; keep incidence angle as features; 

**Revisit:** sensitivity runs (DESC-only, T84-only) change results materially.

## D-02 - Model decisions (2026)

**Experimental Design**
- Want to test the hypothesis that NISAR improves the S1+S2 model in terms of forecasting with 14-day horizon.
- Unit: each gauge and each day
- Season: from the first day with the full 14-day window to each gauge's last water reading
- Window: observations from $\mathbf{x_{t-13}},...,\mathbf{x_{t-1}}$ to predict $y_t$.

**Features**
- Per field and variable: 14-day flat vector by linear interpolation *(could be a source of future data leakage as it might use observation by linear interpolation that could be a value in the future)*
- Sensors:
  - S2 (Cloud score >= 50%): 10bands, NDVI, NDWI
  - S1: VV, VH, ratio db, ratio linear, water-like index. **Pooled**, not track info, no inc angle.
  - NISAR: HH, HV, HH-HV. **Pooled**, not track info, no inc angle.
- Feature sets
  - F1: S2
  - F2: S1
  - F3: S1+S2
  - F4: NISAR
  - F5: S1+S2+NISAR

**Labels**
- binary (<0)
- 3-class (<-1, -1 to 5,  >5)
- water in cm for regressors

**Models**
- Primary: random forest
- Secondary: 
  - majority class and day-of-season climatology;
  - logistic regression, ordinal for 3-class;
  - ridge / elastic net;
  - SVM;
  - ExtraTrees;
  - XGBoost;
  - gradient boosting regression.

What each baseline is for:
- Majority: the score you get from the class balance alone.
- Climatology: the score you get from knowing only the date, i.e. where the field typically is in the flood/drain cycle at that time of year. A satellite model that doesn’t beat climatology isn’t adding information beyond the calendar.

**Evaluation**
- Cross-Validation: leave one field group out, 4 folds: {Way 3 + Way 4}, 4 East, South 0, S16. Scaling and imputation are fit inside each fold.
- Primary metrics: balanced accuracy for binary, macro-F1 for 3-class, RMSE for regression.
- Significance of F5 − F3:
  - a permutation test that swaps whole NISAR series between fields;
  - a field-block bootstrap CI on the out-of-fold predictions.

***Standardization?***

[^1]: Stroppiana, D. et al. In-season early mapping of rice area and flooding dynamics from optical and SAR satellite data. European Journal of Remote Sensing 52, 206–220 (2019).

[^2]: McGiven, L. E. & Müller, M. F. Unsupervised mapping of rice paddy fields and their inundation patterns using Sentinel-1 SAR images and GIS. European Journal of Remote Sensing 58, 2484711 (2025).


