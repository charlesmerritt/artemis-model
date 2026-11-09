# LETO is a core stage, run per county, and units run on their representative plot by default

**Status:** accepted, 2026-11-09. Settles the inventory question #125 left open.

## Context

The runner from #125 (`pipeline/run.py`) consumes a run folder's `stands.csv` and
`FVS_Data.db`, but nothing in `pipeline/` writes them. The FL5 golden run (2026-10-19) made
them in a dated experiment. It segmented all five counties in one window and split units at
county lines afterwards. Maine's statewide run could not do that, because LETO's full-grid
float rasters do not fit in 31 GB, so it ran one county at a time. Five other code paths also
make management units, and only the `leto_*` modules follow LETO v1.

## Decision

- **One stage.** It composes the LETO v1 modules (`leto_attributes`, `leto_ca`,
  `leto_riparian`, `leto_donors`, and for the LETO layout `leto_inventory` and
  `leto_database`). The non-v1 paths leave `pipeline/`: `segmentation/`,
  `leto_initial_state`, and `sketch_management_units` → `sliver_merge` → `assign_plt_cn`.
- **Per county.** The county is the unit of work, and county lines are hard edges for
  segmentation: no parent stand crosses one. Counties run independently.
- **Plot layout by default.** Each unit runs on its parent stand's representative plot
  (`InventoryLayout.PLOT`). The per-unit LETO inventory (`InventoryLayout.LETO`) is built only
  on request. `stands.csv` always names the representative plot.
- **Inputs are fixed upstream.** Young plots (`add_back_ids`) are the last step of raster
  repair. The stage refuses a run whose provenance raster holds add-back pixels without
  `young_plots/`. FIA rows come from a per-region extract of the full FIADB, never the
  five-county consolidated DB.
- **Unknown owner is scheduled.** Harris 0 goes to the Private owner group with the
  `unknown_ownership` flag (`config/ownership_policy.yaml`). Only 1 and 2 are masked.

## Considered options

- **One window per region.** This reproduces FL5 stand for stand, but it cannot run statewide
  on this machine, and TPO targets are per county anyway.
- **LETO layout by default.** FL5 needs 3.2M FVS runs under it (about 56 h on 10 workers)
  against 57,802 under the plot layout (18 min). Statewide Florida is about 14 times FL5. The
  median standing-volume gap between a unit's LETO composite and its representative plot is
  16.3%. That is the accuracy cost of this decision.

## Consequences

- The FL5 golden run cannot be reproduced stand for stand. Parents that straddle a county line
  split before segmentation, FL5's 17,340 unknown-owner acres are now scheduled, and plots are
  read from the full FIADB. It is compared within tolerances instead.
