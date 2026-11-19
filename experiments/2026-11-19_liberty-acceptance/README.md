# Liberty County acceptance (#174)

`leto` with defaults, then `library`, on Liberty (12077) from the v4 statewide raster run, at
leto-core `a417ef7`. Liberty is the first county outside FL5, with the Apalachicola National
Forest and a Panhandle landscape.

| Criterion | Result |
|---|---|
| `leto` completes with `young_plots/` | 12,740 units, 458,290.9 ac (drift 0.01 ac); 740 young-plot units, 16,505 ac, IDs `Y…` |
| Same as the statewide run's Liberty | yes: 12,740 units and 458,290.9 ac in both |
| `library` completes | 391,030 options (30.7 per unit), 17,295 distinct runs, 1 failed; every unit keeps options |
| Federal acres in Federal (NF) | 253,812 ac (55% of the county); Other public 19,754, Private 184,725 |
| QA report reviewed | yes: owner map shows the national forest in the south; young plots sit on add-back land |
| `schedule` | not run: Liberty has no TPO target (`config/tpo_targets.yaml` covers FL5 only) |

The failed run (`outputs/fvs_failures.csv`, stop code −8, SIGFPE) is plot `90825750010478`'s
`@+20` variants of `natural_short_rotation` and `hardwood_clearcut_regen`, shared by 196
options. It is the FVS volume-equation fault seen in FL5 (#173), not the leto stage's.

`library` first ran with every run failing (stop code 20): a relative run folder left the
FVS_Data.db symlink dangling in each run's temporary directory. Fixed in `a417ef7`.

## Run

    uv run python main.py leto data/runs/liberty_v4_leto_174 --county 12077 \
        --repair <artemis-data>/runs/fl/v4/fl_statewide \
        --region-dir <artemis-data>/regions/fl --data-root data/r2_cache
    FVSSN_BIN=$PWD/fvs/bin/FVSsn uv run python main.py library data/runs/liberty_v4_leto_174 --workers 4
