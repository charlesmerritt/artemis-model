# The `leto` QA report on the T3 county (#166)

`report/` is `qa/leto/` from `main.py leto` on Union County (12125, the T3 county), v4 FL5
raster run, run on 2026-11-09 with the default settings: `force_merge`, then the speckle
merge with the speckle that cannot merge deleted (`merge_delete`, ADR 0005 amendment). Open `report/index.html` offline. `report/viewer/12125.html` is the interactive unit
viewer (`pipeline/leto_viewer.py`). The run folder itself is `work/run/`, which
is gitignored.

Run:

    D=<artemis-model>/data
    uv run python main.py leto experiments/2026-11-09_leto-qa-t3/work/run \
        --repair $D/processed/improved-rasters-evt2022-gate-v4/aoi_5county --county 12125 \
        --region-dir $D/regions/fl --data-root $D/r2_cache

| Measure | Union |
|---|--:|
| Forest acres (cells → parents → units) | 112,606.29, drift 0.0001 ac |
| Parent stands: count, median, p10–p90 | 5,625; 1.33 ac; 0.22–60.2 ac |
| Parents under 1 ac / of one cell | 2,586 / 1,674 |
| Units after the speckle merge: count, median, p10 | 5,654 (12,666 before); 6.47 ac; 1.34 ac |
| Speckle deleted: units, acres (riparian acres) | 2,437; 761.5 (289.6) |
| Unit acres (parents less deleted speckle) | 111,844.78 |
| Kept donor plots per parent: median (max) | 3 (13) |
| Representative plot's share: median, acre-weighted | 0.53, 0.40 |
| Acres whose parent's representative plot holds under half its cells | 84,860 |
| TreeMap plots missing from the FIA extract | 0 |
| Riparian acres: total; perennial streams, the 55800 stand-in, outside every buffer | 6,547; 3,475, 2,287, 78 |
| Total seconds (segment, riparian, maps); peak memory | 33.5 (9.1, 10.3, 6.9); 704 MB |

What the report shows:

- **Small parents come out of segmentation.** `force_merge` still leaves 1,674 one-cell
  parents. Owner classes are hard edges, and 715 of them have no forest cell of the same
  owner among their 8 neighbours. The other 959 do, and why they stay unmerged is not
  diagnosed here. The speckle merge takes the unit count from 12,666 to 5,654: 4,575
  merge and 2,437 (761.5 ac) cannot, and are deleted (`speckle.csv`).
- **The plot layout is a loose approximation here.** The median parent's representative
  plot covers 53% of its cells. Weighted by acres, it covers 40%: the large parents are the
  mixed ones.
- **Riparian acres outside every buffer (78 ac)** are riparian units whose representative
  point lands outside every buffer. Before the merge, every riparian piece lies inside the
  buffers. So these are units that a managed speckle joined (step 3 of the merge: 229 ac
  in all), and each is counted whole.
- **Add-back cells in TreeMap units** (pink on `figures/12125_young_plots.png`): add-back
  cells whose parent's representative plot is a mature TreeMap plot. They run as that
  plot, not as young stands.

The viewer is for tuning the CA, so it shows the CA's stands next to its inputs:

- **Units** coloured by owner class, unit class, forest type, origin, stand age,
  representative share, acres or parent stand.
- **Parent stands** (the CA's stands, before the riparian split and the speckle merge) as
  outlines.
- **The CA's inputs per 30 m cell** underneath (forest type, stand age, basal area, QMD,
  TPA) and the add-back cells.
- **Clicking a unit** shows its attributes, its parent stand's cells, acres and owner, the
  mean, standard deviation and coefficient of variation of each CA input over the parent's
  cells, and its donor plots with exact `PLT_CN`.
- **The run's CA and speckle settings** are listed in the panel.

To compare settings, run `leto` into two run folders and open each one's viewer.
