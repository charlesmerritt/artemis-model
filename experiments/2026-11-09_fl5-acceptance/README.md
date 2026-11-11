# FL5 acceptance against the golden run (#173)

The default `leto` stage on the v4 FL5 raster run, then `library` and `schedule`, compared
with the 2026-10-19 golden run within #173's tolerances. Run at leto-core `b86e60a`
(speckle `merge_delete`, small stands `force_merge`, plot layout).

Status: `leto` and `library` complete. **`schedule` has not completed**: it was stopped by the
machine's low-memory guard in annealing (11 workers on a 31 GB workstation), after the greedy
allocator. Scheduled acres and the objective are still to be measured.

## Results

| Measure | Golden | Default run | Deviation | Tolerance | Verdict |
|---|--:|--:|--:|--:|---|
| Forest acres | 1,174,862 | 1,165,075 | −0.83% | ±0.1% | miss, by design (below) |
| Young-plot acres | 86,953 | 85,823 | −1.30% | ±1% | miss, by design (below) |
| Riparian acres | 34,566 | 34,191 | −1.08% | ±5% | pass |
| Scheduled acres | 1,157,293 + ~17,340 | — | — | ±1% | not measured |
| Options per stand | 31.4 | 33.8 | +7.5% | ±10% | pass |

Reported, not gated: 49,363 units (golden 114,727), 116,006 before the speckle merge. The
objective (golden 0.1033) is not yet measured.

`library`: 1,666,966 options over 49,363 stands, 51,112 distinct FVS runs in 42 min on 10
workers. 1,329 distinct FVS stands (golden 1,502).

Options per stand counts every stand, riparian included, as the golden figure does (3.2M
options / 102,109 scheduled stands, 23,465 of them riparian). A riparian stand has one option.
Riparian units are 17% of stands here against 23% in the golden run, which raises the average.
Managed stands alone average 40.3.

## Hard invariants

On the default run:

- **Unit acres equal parent-stand acres: holds only with deleted speckle counted.** Per
  county, units plus deleted speckle equal parent stands (`drift_acres` ≤ 0.002 ac). Units
  alone fall 761–3,281 ac short per county. This is the same deletion as the forest-acre
  miss below, and the same ruling settles both.
- **Riparian area is never in a managed unit.** In the units layer every `managed` unit has
  `RIPARIAN` 0 and every `riparian` unit 1. Riparian acres reconcile to the acre: 35,546
  before the speckle merge, + 964 managed speckle merged in, − 2,318 riparian speckle
  deleted = 34,191.18, the run's figure. So no riparian acre moved to managed land.
- No parent stand crosses a county line: by construction (each county is clipped and
  segmented on its own), not measured;
- unit IDs are unique;
- every unit's `PLT_CN` is in `FVS_StandInit_Plot`, and no plot is missing from the FIA extract
  (`qa_leto/donor_plots_missing.csv` is empty);
- young-plot units carry IDs from 1,000,000, and no TreeMap unit does, so no add-back
  pixel's neighbour TreeMap ID reaches trees;
- no unit has a blank stand age.

## The misses: the speckle that cannot merge is deleted

Forest cells sum to 1,174,862.1 ac, the golden figure to the acre, and parent stands carry all
of it. The shortfall is ADR 0005's 2026-11-09 amendment, which postdates #173's tolerances:
`MERGE_DELETE` deletes speckle that no step can place. FL5 has 67,813 speckle units:

| Speckle step | Units | Acres |
|---|--:|--:|
| Merged, same owner | 8,967 | 2,155 |
| Merged, other owner | 20,047 | 5,915 |
| Merged into riparian | 5,150 | 964 |
| Cannot merge: deleted | 32,479 | 9,787 (2,318 riparian) |

To confirm, `leto_merge_only.py` reruns the stage with `SpeckleAction.MERGE`, which keeps
that speckle (`outputs/merge_only.json`):

| Measure | Default (`merge_delete`) | `merge` | Golden |
|---|--:|--:|--:|
| Forest acres | 1,165,075 (−0.83%) | 1,174,862 (+0.000%) | 1,174,862 |
| Young-plot acres | 85,823 (−1.30%) | 86,164 (−0.91%) | 86,953 |
| Riparian acres | 34,191 (−1.08%) | 36,510 (+5.62%) | 34,566 |
| Units | 49,363 | 81,842 | 114,727 |

- **Forest acres:** the whole 9,787 ac deficit is deleted speckle, 0.83% of forest.
- **Young-plot acres:** 341 ac of young speckle are deleted. The other −0.9% is young speckle
  merged into mature neighbours, which ADR 0005 accepts.
- **Riparian acres:** without the deletion, riparian is 5.6% over the golden run, so the
  deletion is what brings it inside ±5%.

So under the default, neither the forest-acre tolerance nor the unit-acre invariant can hold,
because both predate the amendment. Either the tolerances become "forest acres less deleted speckle", or the
amendment's deletion is reconsidered. That decision is the architect's.

## FVS failures

3 of 51,112 runs fail (`outputs/fvs_failures.csv`). Joining their `run_hash` to the run's
`library.csv` gives 5 options on 5 young-plot stands in Columbia (plots `Y1001971`,
`Y1001973`, `Y1002589`), all `pine_plantation_short_rotation@+5`. Every stand keeps other options. Rerun by hand, FVSsn
raises SIGFPE in the National Volume Library's Clark profile (`r9ht_` ← `r9clark_` ←
`volinit_`), computing volumes after the 2037 thinning of a young slash-pine (`SA1`) stand. It
is an FVS volume-equation fault, not the leto stage's.

## QA report

`qa/leto/index.html` was reviewed. Its tables are copied to `outputs/qa_leto/`. Region maps
show county lines as hard edges, and young-plot units on add-back land. Some add-back cells
lie in TreeMap units: the young speckle merged into mature land, as above. Peak memory per
county is 0.7–1.3 GB. Segmentation and the riparian split dominate run time (125 s for
Suwannee). `management_units.gpkg` has only its `units` layer at this commit. The `segments`
layer comes with #169.

## Run

    M=<artemis-model checkout>
    uv run python main.py leto $M/data/runs/fl5_v4_leto_173 \
        --repair $M/data/processed/improved-rasters-evt2022-gate-v4/aoi_5county \
        --region-dir $M/data/regions/fl --data-root $M/data/r2_cache --workers 2
    FVSSN_BIN=$M/fvs/bin/FVSsn uv run python main.py library $M/data/runs/fl5_v4_leto_173 --workers 10
    uv run python main.py schedule $M/data/runs/fl5_v4_leto_173     # fewer --workers on 31 GB
    PYTHONPATH=. uv run python experiments/2026-11-09_fl5-acceptance/measure.py \
        $M/data/runs/fl5_v4_leto_173 --json experiments/2026-11-09_fl5-acceptance/outputs/default.json

The diagnostic takes the same `leto` arguments through `leto_merge_only.py`, into
`data/runs/fl5_v4_leto_173_merge`.
