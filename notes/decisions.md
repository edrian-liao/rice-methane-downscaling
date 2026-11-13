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

[^1]: Stroppiana, D. et al. In-season early mapping of rice area and flooding dynamics from optical and SAR satellite data. European Journal of Remote Sensing 52, 206–220 (2019).

[^2]: McGiven, L. E. & Müller, M. F. Unsupervised mapping of rice paddy fields and their inundation patterns using Sentinel-1 SAR images and GIS. European Journal of Remote Sensing 58, 2484711 (2025).
