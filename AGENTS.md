# Notes for agents working in this repo

## FIA control numbers (`PLT_CN`, `STAND_CN`, etc.) must not be cast via `str(int(...))`

`PLT_CN` and the other FIA control numbers (`STAND_CN`, `COND_CN`, `PLOT_CN`, `TREE_CN`,
`SUBP_CN`, …) are up to 19-digit integers. Any time one of these passes through a float64
(e.g. a numeric field read out of a GeoPackage/DBF via geopandas), it can silently lose
digits — an IEEE-754 double only carries 15–17 significant digits.

`str(int(x))` on such a value does **not** raise on this: it just returns the
truncated/rounded number as a string, which then silently fails to match the exact-string
key on the other side of a join. The join looks like it ran fine; it just quietly drops or
mis-assigns rows.

Use `pipeline.ids.as_id_series` (or `pipeline.ids.normalize_id` for a scalar) instead,
anywhere an ID column's dtype is not already guaranteed to be an exact string — reading a
GeoPackage/DBF field, joining two frames, or accepting a caller's DataFrame. It repairs
values that are still inside the source dtype's exact-integer range and raises
`IdPrecisionError` on ones that already lost digits, instead of handing on a
corrupted-but-plausible-looking key. See `pipeline/ids.py` for the full writeup of the two
failure modes and why `.astype(str)` / `str(int(...))` are never correct on an ID column.

This was flagged as a real bug in PR #40 (`research/leto_ca_demo/make_leto_figure.py`),
where `str(int(rec.PLT_CN))` was used to join TreeMap VAT records to `fvs_trajectory.csv`
instead of `as_id_series`.
