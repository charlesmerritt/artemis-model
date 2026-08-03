# Pipeline

The committed pipeline currently contains two implemented slices of the larger workflow in
[`../PLAN.md`](../PLAN.md). Numbering follows the target architecture, so missing stage numbers
are planned work rather than missing directories.

## Implemented modules

| Module | Purpose | Maturity |
|---|---|---|
| `s3_management/sketch_management_units.py` | Build draft Florida management units by intersecting forested parcels with road, water, and BMP exclusions, processing one county at a time | Pilot; requires visual QA and policy decisions |
| `s4_fvs/paint_fvs_to_raster.py` | Join FVS stand trajectories through a TreeMap crosswalk and paint stand metrics onto TreeMap pixels | Five-county prototype; external inputs required |

## Identifier precision (`ids.py`)

Every join in this pipeline runs on a FIA control number — `PLT_CN`, `STAND_CN`, `COND.CN`,
`PLOT.CN` — or on a `TM_ID`/`MU_ID` derived from one. These are integers up to 19 digits wide;
a float64 holds 15–17. Any trip through a float damages them in one of two ways, and neither
one raises at the point it happens:

- **Truncation** above `2**53`: `int(float("1234567890123456789")) == 1234567890123456768`.
  Two distinct plots can collapse onto one key.
- **Reformatting**: a value that survives the double intact stops being a usable key once
  it is printed from one. pandas writes `236048879010661.0`; R's `write.csv` writes
  `1.7498047010478e+13`. The join then silently matches nothing.

Rules:

1. Read identifier columns as text — `read_id_csv(path)` in Python, `colClasses = c(PLT_CN =
   "character")` in R — and keep them as text.
2. Pull them out of SQLite with `CAST(... AS TEXT)`, on the stored integer, rather than
   converting whatever the driver hands back.
3. Never call `.astype(str)` on an ID column; use `pipeline.ids.as_id_series`. On a float
   column `.astype(str)` produces `"1.0"` where the other side of the join holds `"1"`.
4. Never cast an ID to numeric to make a join typecheck. That is what
   `r/02_subset_FIA_SQLite_multistateR.R` used to do, and it put re-rendered control
   numbers into the TM_ID→PLT_CN crosswalk every later stage reads.

`as_id_series` repairs values that provably survived a double (below `2**53`) and logs a
warning naming the column; it raises `IdPrecisionError` on anything that lost digits.
`report_key_overlap` logs a warning when two ID columns share no keys at all, which is the
signature of a formatting mismatch rather than genuinely disjoint data.

## Management-unit sketch

```bash
# Validate paths and enumerate work without writing
uv run python -m pipeline.s3_management.sketch_management_units \
  --pilot-five-county --dry-run

# Run one county with QA layers
uv run python -m pipeline.s3_management.sketch_management_units \
  --county-fips 125 --save-qa --overwrite

# Show every option
uv run python -m pipeline.s3_management.sketch_management_units --help
```

Inputs are configured in [`../config/data_paths.yaml`](../config/data_paths.yaml). Outputs default
to `data/interim/management_units/`. Before statewide use, review
[`../notes/management_units.md`](../notes/management_units.md) for the latest pilot results and
open decisions.

## FVS raster painting

```bash
uv run python -m pipeline.s4_fvs.paint_fvs_to_raster
```

The painter currently uses constants in the module rather than command-line options. It expects:

- `data/interim/no_management_fl5co_fvs_output/fvs_trajectory.csv`
- matching TreeMap crosswalk and raster files under `/mnt/d/TreeMap_Chaz`

It reports candidate-pair coverage, selects the best matching TreeMap vintage, and writes initial
and final basal-area rasters to `data/processed/no_management_fl5co_rasters/`. Read
[`../notes/fvs-to-raster-painting.md`](../notes/fvs-to-raster-painting.md) before changing
snapshots or metrics.

## Verification

```bash
uv run pytest tests/test_s3_sketch_management_units.py \
  tests/test_s4_paint_fvs_to_raster.py
```

The broader stages—initial-state assembly, site attributes, automated FVS execution, validation,
and product packaging—remain described in [`../PLAN.md`](../PLAN.md) until implemented.
