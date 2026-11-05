# Weekly artifact — 2026-11-02

The canonical **s6 FVS output report**: the age-class structure of the five-county north
Florida pilot, projected 2026–2076, cut by forest type, state, county, owner class and
management type — ten CSV tables, eight figures, and a `run_summary.json` that ties every
number back to the FVS project that produced it.

Produced by the repository's own canonical entry point, unmodified:

```bash
uv run python -m pipeline.s6_outputs.run_pipeline
```

## Why this artifact

`pipeline/s6_outputs/` is the reporting end of the pipeline — AGENTS.md step 4, "a final
output table with the trajectory for each management unit and its vegetation data from FVS",
plus the visualizations drawn from it. It landed on 2026-09-15 (`4858b0d`, reviewed in
`b3196b6`) and **has never been delivered as a weekly artifact**. Every previous artifact in
this series is something else: basal-area rasters (07-19, 08-03), even-flow targets (07-26),
the greedy harvest schedule (08-10), the trajectory library (08-17), riparian/SMZ corridors
(08-24), and the annealed plans (08-31, 09-07, 09-14). The 09-28 convergence sweep committed
its drivers but no artifact.

So this is the first time the landscape's **age-class structure** — the thing the even-flow
argument ultimately rests on — has been reported from a real FVS run through committed,
canonical code rather than a dated one-off driver.

It is also the one flagship deliverable reproducible in this sandbox. The annealed plan needs
a compiled `FVSsn`, and the build is blocked here (see *What did not run*). This stage needs
no FVS binary: it reads a finished `FVSOut.db`.

## What the run was

`NoManagement_5countyFL_1`, FVS Southern (SN) variant, 693 cases / 693 stands, 1997–2076,
3,797,722 sampling-weight acres. It is the **no-management control**: the baseline the managed
trajectories are scored against.

Reporting is restricted to the 11 balanced years **2026–2076**. The 28 earlier years are
excluded because stands enter an FVS Online run at their own FIA inventory year, so only
106–181 of the 693 cases report before 2026 — an age-class distribution over those years
describes which stands happened to be measured, not the landscape. The pipeline makes that
exclusion itself and records it in `run_summary.json`.

## What it shows

Under no management the pilot landscape ages almost exactly one year per year, and the young
end of the distribution drains completely:

| Acres, whole run | 2026 | 2076 |
|---|---|---|
| age 0–19 | 550,500 (14.5%) | **0 (0.0%)** |
| age 100+ | 213,121 (5.6%) | **1,540,843 (40.6%)** |

Acre-weighted mean stand age, by owner class (crosswalked plots only):

| Owner class | 2026 | 2076 | Δ |
|---|---|---|---|
| NGO | 32.2 | 82.2 | +50.0 |
| Corporate | 38.6 | 88.6 | +50.0 |
| Private | 42.6 | 92.6 | +50.0 |
| State | 49.8 | 99.8 | +50.0 |
| Other | 50.4 | 100.4 | +50.0 |
| County | 55.3 | 105.3 | +50.0 |
| Federal | 57.4 | 107.4 | +50.0 |
| Unknown | 64.3 | 114.3 | +50.0 |

Every class gains exactly 50 years over 50 years. That uniform +50 is the point: it confirms
the control run really is unmanaged — no harvest resets any stand's age anywhere — so the
owner-class *ordering* (NGO and Corporate youngest, Federal oldest) is inherited initial
structure, not a management signal. By forest type the same holds: pine 38.2 → 85.3, oak/pine
48.4 → 99.5, hardwood 58.4 → 109.8.

This is the surface a managed plan has to move. Without harvest there is no 0–19 class left by
2076, which is the age-class evidence behind the even-flow problem the annealer exists to solve.

### Owner coverage is partial, and the tables say so

The owner crosswalk reaches **375 of 693 cases (54.1%)**, 1,052,306 acres; 318 cases are left
`Unattributed`. The four owner/management tables are therefore weighted on `owner_acres` and
foot to 1.05 M acres, while the six whole-run tables are weighted on `sampling_weight` and foot
to 3.80 M acres. `run_summary.json` carries `weight_basis` and `acres_first_year` per table —
do not add a number from one group to a number from the other. 293 of the 375 crosswalked plots
span more than one owner class and are apportioned by acre share, not assigned whole.

## Files

| | |
|---|---|
| `tables/age_class_overall.csv` | acres and share per age class per reporting year, whole run |
| `tables/age_class_by_forest_type.csv` | the pine / oak-pine / hardwood split |
| `tables/age_class_by_forest_type_detail.csv` | by FIA forest type code |
| `tables/age_class_by_state.csv`, `tables/age_class_by_county.csv` | geographic cuts |
| `tables/age_class_by_owner.csv`, `tables/age_class_by_owner_forest_type.csv`, `tables/age_class_by_management_type.csv` | the owner cuts (`owner_acres` basis) |
| `tables/mean_age_by_forest_type.csv`, `tables/mean_age_by_owner.csv` | acre-weighted mean age |
| `figures/*.png` | eight figures, each drawn only from the table beside it |
| `run_summary.json` | run identity, reporting grid, excluded years, crosswalk coverage, per-table acreage |

Stage 5 is deliberately inert: every number in every figure is already in a CSV before any
chart is drawn, so a figure can be restyled or discarded without recomputing anything. If a bar
looks wrong, it is wrong in `tables/`.

## R2 inputs pulled

Two files, 35.4 MB total. `pipeline/data_access.py` fetched them automatically because the
workstation drive `/mnt/d` is not mounted in this container.

| R2 key | Size | Role |
|---|---|---|
| `data/Artemis_project_fvs_copy_no_management/FVSOut.db` | 14.4 MB | the run: `FVS_Cases` ⨝ `FVS_Summary2` |
| `data/20260804_095846_Hard_Ownership_Boundaries/Inputs/FVS_StandInit.csv` | 21.0 MB | the `PLT_CN` → owner-class crosswalk |

They land under gitignored `data/r2_cache/` and are **not** committed. The 1.5 GB
`FVS_TreeInit.csv` sitting beside the crosswalk was deliberately not pulled — the stage caps
that fetch at 64 MB precisely to stop a mis-pointed key dragging it in. The 1.08 GB
`FVS_Data.db` in the same project folder was not needed either: this stage reads outputs, not
inventory.

Absolute paths inside `run_summary.json` point at this container's `data/r2_cache/`; they are
the pipeline's own provenance record and were left exactly as written.

## Dependencies

None installed. The committed `uv.lock` environment was used as-is (`uv sync`): Python 3.14,
pandas, PyYAML, matplotlib, rclone already on the image. No code in the repository was changed.

## How to regenerate

```bash
uv sync
uv run python -m pipeline.s6_outputs.run_pipeline          # all five stages
uv run python -m pipeline.s6_outputs.run_pipeline --dry-run    # what it would read/write
uv run python -m pipeline.s6_outputs.run_pipeline --stages visualize   # redraw figures only
```

Outputs land in gitignored `data/processed/fvs_outputs/`; the files here are a verbatim copy.
The run is deterministic — the same `FVSOut.db` and the same `config/fvs_outputs.yaml`
reporting policy give the same tables.

## What did not run, and why

**The annealed plan (`uv run python main.py library|schedule`) was not attempted.** Its
`library` stage needs a compiled Southern-variant FVS at `fvs/bin/FVSsn`, which is not in the
container. The documented build from `weekly-artifact/2026-08-31/README.md` got as far as:

```bash
git clone --depth 1 https://github.com/USDAForestService/ForestVegetationSimulator.git fvs/src   # ok
git -C fvs/src submodule update --init --depth 1 volume/NVEL                                     # ok
sed -i 's/FVSbc_sourceList.txt/FVSsn_sourceList.txt/' fvs/src/bin/CMakeLists.txt                 # ok
sed -i 's/-ffpe-trap=...,underflow,...,denormal/-ffpe-trap=invalid,zero,overflow/' fvs/src/bin/makefile  # ok
make -C fvs/src/bin FVSsn -j4                                                                    # DENIED
```

The `make` was refused by this sandbox's auto-mode permission classifier, which blocks
compiling freshly cloned third-party source (`[Code from External]`). That is an environment
policy, not a repository defect, and it is not something the agent can work around. Nothing was
faked in its place — the artifact above is a different, fully reproducible deliverable.

Two further notes for whoever picks this up:

1. `pipeline/run.py`'s docstring points at `experiments/2026-10-19_fl5-leto-stands` steps 1–2
   and 8 as the FL5 driver that writes a run directory's `stands.csv` and `FVS_Data.db`. **That
   experiment is not committed** — `experiments/` holds only `2026-07-28_16-33-pr9-validation-audit`
   and `2026-08-24_leto-ca-forest-viz`. So even with a working `FVSsn` there is currently no
   committed path to build an FL5 LETO run directory.
2. `weekly-artifact/2026-09-28/` contains `make_converged_plan.py` and `make_figure.py` but no
   README and no artifact, so that week's deliverable appears never to have been published.
