# Identifier Precision — FIA Control Numbers as Join Keys

Findings from a full audit of `PLT_CN`, `TM_ID`, `STAND_CN`, `COND.CN` and `PLOT.CN`
handling across the R scripts, the Python pipeline, the notebooks, and the DuckDB coupling
cells. Enforcement now lives in [`pipeline/ids.py`](../pipeline/ids.py); the operating rules
are in [`pipeline/README.md`](../pipeline/README.md#identifier-precision-idspy).

## Why this matters more than it looks

Every join in ARTEMIS runs on a FIA control number, or on a `TM_ID`/`MU_ID` derived from
one. These are integers up to 19 digits; a float64 carries 15–17. A control number that
passes through a float is damaged in one of two ways, and **neither raises at the point it
happens** — the join simply returns fewer rows, and a stand quietly inherits the wrong tree
list.

1. **Truncation** above the float's exact-integer range. For a float64 that is `2**53`:
   `int(float("1234567890123456789")) == 1234567890123456768`. Digits are gone; no
   downstream cast recovers them, and two distinct plots can collapse onto one key.

   **The bound belongs to the dtype, not to the pipeline.** A float32 carries 24 mantissa
   bits and so gives out at `2**24` — an ordinary 15-digit control number sitting in a
   float32 column is *already* rounded (`236048879010661` → `236048886005760`) while still
   looking far too small to worry about. Checking every float against `2**53` waves that
   through and emits the corrupted value as an exact-looking key. `exact_int_limit()`
   derives the right bound per dtype from the mantissa width. This was caught in review of
   PR #15, not in the original audit.
2. **Reformatting.** A value that survives the double intact stops being a usable *key* once
   printed from one. pandas writes `236048879010661.0`; R's `write.csv` writes
   `1.7498047010478e+13`. The digits are all there, the string key is not, and every
   equality join misses.

Mode 2 is the one that bit this project, and it is the more dangerous of the two precisely
because the data still looks correct when you open the file.

## The defect with the widest blast radius

`r/02_subset_FIA_SQLite_multistateR.R` cast the control number to a double before writing:

```r
plot_meta$PLT_CN <- as.numeric(plot_meta$PLT_CN)   # removed
```

That value went straight into `write.csv(tmid_lookup, "FL_5county_TMID_PLT_lookup.csv")` —
the TM_ID→PLT_CN crosswalk that `pipeline/s3_management/assign_plt_cn.py` joins on. The
Python side read it with `dtype=str` and so faithfully preserved an already-damaged string.
The cast was presumably added to make a `left_join` typecheck after `read.csv` had inferred
`PLT_CN` as numeric on the other side; the fix is to make both sides character, never to
push an identifier through a number to satisfy a join.

## Everything found, by layer

| Where | Defect |
|---|---|
| `r/02:806` | `as.numeric(PLT_CN)` before `write.csv` of the crosswalk (above) |
| `r/02:525` | A **second** `as.numeric(PLT_CN)`, in Section 7, to make a `left_join` typecheck. Missed by the first sweep and caught in review of PR #15 — once Section 2 reads character it does not merely lose digits, dplyr refuses the join and the script aborts *before* Section 10 writes the crosswalk. Lesson: sweep the whole file for coercions, not just the one the symptom points at |

### The fix that was itself a defect

Worth recording, because it is the same shape as the bug it was meant to prevent. The
first pass on `r/01` replaced `as.character(PLT_CN)` with
`format(PLT_CN, scientific = FALSE, trim = TRUE)` to keep a numeric out of scientific
notation. That is correct for a numeric and **wrong for a character vector**: `format()`
left-justifies character input to the width of the longest element, and `trim` does not
suppress it — per `?format`, `trim` applies to "logical, numeric and complex values" only.
Control numbers vary in width, so every shorter key silently gained a trailing blank, and
the *healthy* path (PLT_CN already character, which is what the rest of this work exists to
achieve) was the one that broke. Downstream, `"17498047010478 "` never matches
`CAST(CN AS TEXT)`, `setdiff` never clears the unmatched pool, and `r/04`'s digits-only
guard aborts.

The conversion now happens once, inside the numeric branch where the type is known, and
both branches are followed by a digits-only assertion. Two lessons: a whitespace-visible
identifier bug is exactly as silent as a precision one, and a guard that asserts the
*output* shape (`^[0-9]+$`) catches defects in the fix as well as in the original code —
which is why the same assertion now sits in `r/01`, `r/02` and `r/04`.
| `r/02:135`, `r/03:108` | `read.csv(tmid_csv)` with no `colClasses` — the only two of the seven R scripts missing it. PLT_CN became a double, then fed the SQL `IN` clauses |
| `r/02`, `r/03`, `r/06` | `CN` / `STAND_ID` pulled from SQLite raw and `paste()`d into `IN` clauses — typing left to the driver |
| `r/02:299` | `integer(0)` sentinel in an otherwise character PLT_CN pool |
| `r/07:304` | `PLT_CN` used as a `group_by` key while numeric — two CNs differing past the 15th digit merge into one group |
| `build_fvs_inputs.py` | `.astype(str)` on `MU_ID`. A GeoPackage stores the field as REAL as soon as one row is NULL, so reading it back gave `"1.0"` against `"1"` in the weights table. Zero-row join, no error |
| `build_fvs_inputs.py:74` | `.astype(str)` on `STAND_CN` — an unpinned float column becomes `"2.36048879010661e+14"` |
| `assign_plt_cn.py:53` | `int(float(value))` in the lookup loader — loses digits above `2**53` |
| `paint_fvs_to_raster.py` | An empty PLT_CN join wrote an all-nodata GeoTIFF and reported success |
| `TreeMap_COG_County_Summary.ipynb` | `categorical_counts` emitted `treemap_value` as `2623.0` from a float band, against a VAT holding `2623`; float32 additionally collapses TM_IDs above `2**24` |
| `duckdb-iterative-coupling-cells.md` | View SQL left identifier typing to the CSV sniffer; DuckDB implicitly casts across a `VARCHAR = DOUBLE` join, which matches nothing and reports zero rows rather than raising |

`r/01`, `r/04` and `r/05` were already reading `PLT_CN` as character — the convention
existed, it just was not enforced anywhere.

**Checked and clean, no change needed:** `sliver_merge.py` (`_dissolve_by_edges` keeps the
largest member's attributes wholesale, so `unit_id` survives), `sketch_management_units.py`
and `research/mgmt_units/segmentation_delineation.py` (`unit_id` is an f-string label like
`mu_12125_00000001`, never numeric), `harvest_scheduler.py` (counts by `unit_id`, never
joins on it), `research/restart_fidelity/*` (`STAND_CN` is a string literal; `compare_arms`
joins inside SQLite/DuckDB with no Python round trip), `regime_templates.py`, `gee/scripts/`,
and the `docs/superpowers/` design docs.

## What is enforced now

- **`as_id_series()`** replaces `.astype(str)` on any ID column. It repairs values that
  provably survived the float (inside that dtype's exact-integer range — `2**53` for a
  float64, `2**24` for a float32), logging a warning that names the column, and
  raises `IdPrecisionError` on anything that lost digits. Non-numeric identifiers such as
  `mu_12125_00000001` pass through untouched — the module guards against float damage, it
  does not police identifier formats. Zero-padded IDs like FVS `STAND_ID`
  `"010006100083"` keep their padding, but a zero-padded ID that has *already* been through
  a float has lost it irrecoverably (nothing knows the intended width), so those must be
  read as strings at the source.
- **`read_id_csv()`** pins identifier columns to text at the read and validates them there,
  so a damaged CSV fails at the boundary rather than three joins later.
- **`report_key_overlap()`** logs a warning when two ID columns share no keys at all — the
  signature of a formatting mismatch rather than genuinely disjoint data.
- **R:** `colClasses = c(PLT_CN = "character")` on every `read.csv`, `CAST(... AS TEXT)` on
  every identifier leaving SQLite (the cast runs on the stored integer, so no digit is lost
  on the way out), `options(scipen = 999)` in all seven scripts, and a guard before
  `r/02` writes the crosswalk. `r/01` now **stops** rather than writing a crosswalk whose
  `PLT_CN` reached it from the VAT as a double at or above `2**53` — that is the one place
  in the pipeline where the digits can still be intact, so it is the only place worth
  failing.

Regression coverage: `tests/test_ids.py`, plus cases in `tests/test_s3_assign_plt_cn.py` and
`tests/test_s4_build_fvs_inputs.py` for a 19-digit control number end to end, a float
`MU_ID` on the units side, a float `STAND_CN` on the tree-list side, and R-style scientific
notation in a lookup CSV.

## Open

- **Existing `/mnt/d` outputs were not audited** — the drive is not mounted in the container
  this work was done in, and the in-repo `data/build/artemis.duckdb` turned out to be an
  empty initialised database. Any `FL_5county_TMID_PLT_lookup.csv` generated before this
  change needs checking: decimal points or `e+` in the `PLT_CN` column mean it was written
  from a double. Values below `2**53` the loader now repairs on read, but a genuinely 16+
  digit CN truncated at the source is unrecoverable and needs `r/02` re-run.
- **How wide are these CNs really?** The stale comments in `r/04`/`r/05` asserted "16-digit
  integers … the CSV values are 14 digits (truncated during original write.csv)". That
  reads like an inference from damaged output rather than a measurement. Worth confirming
  the true digit width against FIADB, because it decides whether the historical damage was
  mode 1 (unrecoverable) or mode 2 (repairable). The comments were rewritten to stop
  asserting it as fact.
- **Zero-overlap joins warn rather than raise.** `build_tree_init` returning no matched rows
  logs a warning and continues, because an existing test encodes that an unmatched unit is
  simply not runnable and gets imputed from its nearest neighbour later. If a total-miss
  join should instead be fatal, that is a one-line change plus a test update.
