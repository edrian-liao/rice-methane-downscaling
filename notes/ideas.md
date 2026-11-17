# Ideas

## I-01: Reduce number of features coming from S02 (2026-11-15)
What if the model is over-relying on S-2 because most features come from there? That is why it already performed well and there's only a little bump in F3 and F5 performance? 

This could be answered by doing an experiment where we only use NDVI and NDWI instead.

**Answer**
- F1's performance, in general, declined, and in some fields, the F5 performance is now superio, but there are still fields where using model trained on F1 is still superior over the model trained on F5.

## I-02: Time lag in covariates that contribute to the dry prediction (2026-11-17)
The model is better at catching the wet days. Is it because of the lag in the observations' signal indicating that the field is dry?