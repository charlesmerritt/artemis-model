# LANDFIRE disturbance as a third cut-year detector (#171)

`demo.py` dates each run's add-back pixels with Obata and Hansen alone, then with LANDFIRE
disturbance as well (`pipeline/s1_initial_state/landfire_disturbance.py`). It writes nothing
into the run. The outputs are in `outputs/<label>.json`.

Inputs: Obata `lastDist-2010-2023-p17-r39-MovAvgRange3-mif4-tol3.tif` and
`regions/fl/hansen.tif` (the statewide manifest's; v4 and v5 share it), and LANDFIRE disturbance at `artemis-data/mnt_d/landfire/`
(R2: `r2:artemis-r2/data/landfire/`).

| Run | Add-back ac | Age changes | Undated, now dated | Still undated |
|---|--:|--:|--:|--:|
| v4 FL5 (`data/processed/improved-rasters-evt2022-gate-v4/aoi_5county`) | 80,204 | 31 | 1.6 | 1,870 |
| v4 statewide (`runs/fl/v4/fl_statewide`) | 446,098 | 621 | 74 | 34,957 |

Florida uses the v4 raster runs (#160, 2026-11-09). The first statewide demo ran on v5
(`outputs/v5_fl_statewide.json`: 786,087 add-back ac, 1,474 change, 322 newly dated, 185,658
still undated) and is kept only for the record.

- **LANDFIRE dates very little.** Only Clearcut and Harvest date a cut. Over FL5, LANDFIRE
  labels almost all forest change Unknown, Fire or Other Mechanical. Clearcut and Harvest
  cover 9,477 pixels across 2001–2022, nearly all from 2008 and 2021. On add-back pixels
  they cover 369, from those two years alone.
- **Where it moves a dated age, it is mostly by 1 year:** LANDFIRE's harvest year is one
  later than Hansen's (469 of 621 ac on v4 statewide). It agrees with Hansen on 3,002 ac.
- **The event year is the product's own year**, or the legend's `CALENDAR_YR` from LF2024 on.
  `YEAR` and `DIST_YEAR` are not read: they match the product's year everywhere except LF2008,
  whose legend labels its twelve 4xx and 7xx harvest codes 2009 (pinned in the legend test).
- **v4's stored `add_back_cut_year.tif` predates the 2026-10-12 amendment.** It leaves
  29,324 pixels undated that a 2001–2011 year now dates, so the demo recomputes the baseline.

Run:

    D=<artemis-data>
    PYTHONPATH=. uv run python experiments/2026-11-09_landfire-cut-year/demo.py <run> <label> \
        --obata-tif $D/mnt_d/gee_raster_correct_data/runs/disturbance-2010-2023-p17-r39-MovAvgRange3-mif4-tol3/lastDist-2010-2023-p17-r39-MovAvgRange3-mif4-tol3.tif \
        --hansen-tif $D/regions/fl/hansen.tif --landfire-dist-dir $D/mnt_d/landfire
