# Weekly artifact — 2026-08-17

## Artifact

**The enumerated ARTEMIS trajectory library for the five-county north-Florida pilot** —
the decision space the simulated-annealing scheduler will search.

`notes/trajectory-library-and-annealing.md` was adopted as the design of record on
2026-08-06 and merged last week (PR #21). It says ownership class decides which
prescriptions a stand is *eligible* for, FVS runs once per `(stand, prescription)` pair
offline, and the annealer then selects one trajectory per stand. §3–§4 of that note are
the library-build stage, and nothing had ever run it against the real landscape. The only
number the repository carried was the declared upper bound in
`config/management_regimes.yaml`:

```yaml
si_bins: 3
estimated_max_runs_pilot: 16632      # 693 × 8 × 3
```

That is a worst case assuming every stand is eligible for every prescription. This
artifact replaces the estimate with the measured library.

| File | What it is |
|---|---|
| `trajectory_library.csv` | The decision space. One row per (unit × eligible prescription): unit, TreeMap plot, PLT_CN, county, owner class, forest branch, acres, stand age, prescription, resolved FVS template, entry years, entries in horizon, regeneration slot, resolver notes. 15,747 rows. |
| `fvs_run_manifest.csv` | The FVS batch. One row per distinct `(PLT_CN, prescription)` — what actually has to be run — with the SHA-256 prefix of its rendered keyfile. 3,788 rows. |
| `library_by_owner.csv` | Units, stands, acres, menu size, and library rows per owner class × forest branch. |
| `library_by_prescription.csv` | Units/stands/acres eligible and FVS runs required, per prescription. |
| `library_menu_realized.csv` | The policy menu each owner class × forest branch actually receives on this landscape. |
| `sample_keyfiles/*.key` | One rendered FVS keyfile per prescription (8 files), from the real batch — the reviewable sample. |
| `trajectory_library.png` | Four-panel render of the four CSVs. |
| `make_trajectory_library.py` | The driver that produced the CSVs and keyfiles. |
| `make_figure.py` | Renders the figure from the committed CSVs alone — no R2 access needed. |

**Why this artifact.** It is the next unbuilt stage of the adopted architecture, and it is
not a repeat of any prior week: `2026-07-19` and `2026-08-03` shipped the FVS-painted
basal-area rasters, `2026-07-26` the harvest-scheduling analysis figures, and `2026-08-10`
the constrained harvest schedule from the *greedy* allocator. This is the input the
annealer replaces that greedy allocator with — the explicit, auditable enumeration the
note calls for ("Decision space: explicit, enumerated up front, auditable", §2).

**Not fabricated.** Every modelling decision is made by committed repository code:
`owner_classes` (Harris class → ARTEMIS owner class), `regime_assignment`
(`eligible_prescriptions`, `forest_type_branch`, and the schedule/template/params
resolution), and `regime_templates` (keyfile rendering). The landscape attribution is
reused verbatim from `weekly-artifact/2026-08-10/make_schedule.py`, and reproduces its
totals exactly — 5,240 units, 676 of 693 FVS stands, 925,098 acres. The full test suite
passes in this environment (`uv run pytest tests/ -q` → **798 passed, 10 skipped**).

## Headline results

**The batch is 68% of the declared bound, not 100%.**

| | FVS runs | of the bound |
|---|---:|---:|
| Declared upper bound (693 × 8 × 3) | 16,632 | 100% |
| Eligible pairs after owner + forest-branch screening (3,788 × 3) | 11,364 | 68% |
| After dropping identical renders (3,662 × 3) | 10,986 | 66% |

Eligibility screening removes about a third of the assumed batch before a single FVS run
is submitted. The bound was never wrong — it is explicitly a bound — but the real figure
is the one to plan compute against.

**The decision space is 15,747 (unit × prescription) options over 5,240 units.** Menu size
per unit is 2 (912 units), 3 (3,389), or 4 (939). It collapses to 3,788 distinct FVS runs
because units sharing a TreeMap plot share a tree list, so the library is keyed by
`(PLT_CN, prescription)` — a **4.2× reduction** in simulation work over the naive
per-unit reading, and the single most useful number here for sizing the FVS batch.

**126 of those runs are degenerate, and that is a modelling finding, not a cost note.**
Rendering every keyfile and hashing it turned up 3,788 runs but only 3,662 distinct
keyfiles. Every collision is the same pair: on 126 stands, `pine_plantation_long_rotation`
and `pine_plantation_short_rotation` render to a *byte-identical* keyfile. The cause is
visible in the resolver's own notes — `thin dropped: stand is at or past rotation age`.
Those stands are already older than both rotations, so both prescriptions drop their thin
and resolve to the same bare clearcut in the same year (2027):

```
         PLT_CN                   prescription template entry_years  stand_age
173002897010854  pine_plantation_long_rotation clearcut        2027       93.0
173002897010854 pine_plantation_short_rotation clearcut        2027       93.0
```

For the annealer this is a pair of options that look distinct and are not: it can swap
between them forever at zero objective change. Worth either deduplicating at library-build
time or giving over-rotation stands a genuinely different second option (a deferred entry,
say) before the search is built. 1,227 of the 15,747 library rows carry the
`thin dropped` note; 322 of the 3,788 runs degrade from `plantation_rotation` to a bare
`clearcut` (135 long-rotation, 187 short-rotation).

**Prescription reach across the landscape** (`library_by_prescription.csv`):

| Prescription | Acres eligible | Stands | FVS runs | Entries in 50 yr |
|---|---:|---:|---:|---:|
| `no_management` | 925,098 | 676 | 676 | 0 |
| `family_light_thin` | 566,517 | 628 | 628 | 1 |
| `family_uneven_aged_selection` | 566,517 | 628 | 628 | 3 |
| `pine_plantation_long_rotation` | 425,741 | 290 | 290 | 1–2 |
| `public_selection_light` | 175,327 | 480 | 480 | 4 |
| `public_thin_restore` | 175,327 | 480 | 480 | 3 |
| `pine_plantation_short_rotation` | 106,920 | 271 | 271 | 1–2 |
| `hardwood_clearcut_regen` | 76,334 | 335 | 335 | 1 |

`no_management` is eligible everywhere, which is `no_management_universally_eligible: true`
behaving as declared — declining to harvest is always a legal choice. The paired identical
acreages are structural, not a bug: `family_light_thin` and `family_uneven_aged_selection`
are eligible for exactly the same owner class across all three forest branches, as are
`public_selection_light` and `public_thin_restore`.

**Three of the eight owner classes never appear.** The pilot landscape realises only
`private_family`, `private_industrial`, `federal`, `state`, and `local`. `tribal` has no
pixels here; `private_corporate_other` requires parcel evidence that this attribution does
not join; `unknown` is dropped with the masked classes upstream. Their menus are declared
policy that this artifact does not exercise.

## Caveats

- **No riparian units.** No BMP layer is joined yet, so `SMZ_Pct` is 0 everywhere and the
  absolute riparian override never fires. On the real landscape those units would collapse
  to a one-item `{no_management}` library, shrinking the batch further. This is the same
  limitation the 2026-08-10 artifact carried.
- **Scheduling unit is not the Phase 2.3 management unit.** It is
  `TreeMap plot × county × ownership class`, inherited from the prior driver, because the
  unit × stand crosswalk does not exist yet.
- **The library is enumerated, not simulated.** These are the runs the FVS batch would
  submit and the keyfiles it would submit them with. No FVS executable ran here; producing
  the trajectories themselves is the next stage, and `si_bins` expansion (×3) is applied
  arithmetically because site-index binning is declared in config but not yet implemented.
- **`params` resolution for non-default prescriptions.** `assign_prescription` resolves
  whatever the config names as the default for an owner/branch slot. To resolve an
  *eligible* prescription through that same code path rather than reimplementing it, the
  driver hands the resolver a config copy whose default for that slot is the prescription
  under test. The resolver is pure with respect to its config argument, so this exercises
  the real resolution path — but it is the driver's own mechanic and worth knowing about
  when reading the code.

## R2 inputs pulled

Only the inputs the run reads, from bucket `r2:artemis-r2` (bucket `data/` maps to the
repo's `/mnt/d`). **No downloaded data is committed** — everything lands under gitignored
`data/`.

| R2 key | Local path | Size |
|---|---|---|
| `data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv` | `data/interim/no_management_fl5co_fvs_output/` | 2.9 MB |
| `data/Lowe_TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv` | `data/interim/treemap_link/` | 64 KB |
| `data/Lowe_TreeMap_Chaz/output/FL_5county_TreeMap_summary.csv` | `data/interim/treemap_link/` | <1 KB |
| `data/Lowe_TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif` | `data/interim/treemap5co/` | 7.2 MB |
| `data/county_p010g.shp_nt00934/countyp010g.{shp,shx,dbf,prj}` | `data/interim/counties/` | 48 MB |
| `data/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx` | `data/raw/` | 32 KB |
| `data/RDS-2025-0045/Data/US_forest_ownership.tif` | **not downloaded** — read remotely | 3.87 GB on R2 |

The Harris et al. 2025 ownership raster is never copied: the driver opens it through GDAL
`/vsis3` against the same R2 endpoint and reads only the tiles under the pilot AOI via a
`WarpedVRT` onto the TreeMap grid.

> **Path note for future runs.** The TreeMap keys moved since the 2026-08-10 artifact:
> `data/TreeMap_Chaz/…` is now `data/Lowe_TreeMap_Chaz/…`. The commands below use the
> current keys.

## Exact commands

```bash
# inputs (rclone remote `r2` is preconfigured via RCLONE_CONFIG_R2_* env vars)
rclone copy r2:artemis-r2/data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv \
  data/interim/no_management_fl5co_fvs_output/
rclone copy r2:artemis-r2/data/Lowe_TreeMap_Chaz/output/ data/interim/treemap_link/ \
  --include "FL_5county_TreeMap_TMIDs.csv" --include "FL_5county_TreeMap_summary.csv"
rclone copy r2:artemis-r2/data/Lowe_TreeMap_Chaz/FiveFloridaCounties/ data/interim/treemap5co/ \
  --include "TreeMap2022_CONUS_5FlCntys.tif"
rclone copy r2:artemis-r2/data/county_p010g.shp_nt00934/ data/interim/counties/ \
  --include "countyp010g.shp" --include "countyp010g.shx" \
  --include "countyp010g.dbf" --include "countyp010g.prj"
rclone copy "r2:artemis-r2/data/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx" data/raw/

# the run
uv run python weekly-artifact/2026-08-17/make_trajectory_library.py
uv run python weekly-artifact/2026-08-17/make_figure.py
```

## Dependencies

No new dependencies. The committed `uv.lock` environment was used as-is: Python 3.14,
pandas, geopandas, rasterio (GDAL with `/vsis3`), matplotlib, openpyxl, PyYAML.
`uv sync` reproduces it.

## How to regenerate

```bash
uv sync
# stage the R2 inputs with the rclone commands above, then:
uv run python weekly-artifact/2026-08-17/make_trajectory_library.py   # CSVs + keyfiles
uv run python weekly-artifact/2026-08-17/make_figure.py               # figure only, no R2
```

The driver caches the landscape attribution at
`data/interim/schedule_units_attributed.csv` (the prior driver's cache); delete it to
force the ownership warp to re-run. The full keyfile corpus — all 3,788 renders — is
written to `data/interim/trajectory_library_keyfiles/`, which is gitignored; only the
8-file sample is committed.
