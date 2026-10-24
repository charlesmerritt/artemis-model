# FL5 harvest-scheduling deck: LETO stands on the improved rasters

Slides 1–2 of the harvest-scheduling presentation: every LETO management unit across the
five-county pilot at 2022, then one unit's FVS tree list.

| Step | Script | Reads | Writes |
|---|---|---|---|
| 1 | `01_build_inputs.py` | `data/processed/improved-rasters-evt2022-gate-v2/aoi_5county/treemap2022_{improved,provenance}.tif`, TreeMap VAT, `FIA_5county_consolidated.db` | `work/attributes.npz`, `work/vat_extended.csv`, `outputs/young_plot_matches.csv` |
| 2 | `02_segment.py` | step 1, `nwos2022_improved.tif`, NHD streams | `work/segmentation.npz`, `work/mu_summary.csv`, `work/mu_donor_weights.csv`, `outputs/segmentation_qa.json` |
| 3 | `03_figures.py` | steps 1–2 | `outputs/fig_*.png`, `outputs/selected_stand.json` |

Shared inputs are staged where `experiments/2026-08-24_leto-ca-forest-viz/01_stage_aoi.py`
puts them (`work/staged/`: `treemap_vat.dbf`, `FIA_5county_consolidated.db`,
`FL_5_Co_Streams/`, `counties/`), pulled from R2. The CA constants, riparian buffer rules and
owner legend come from that experiment so the two stay comparable. Run each step with
`uv run python experiments/2026-10-05_fl5-harvest-deck/<script>`. Step 2 takes about 4 minutes
(100 CA iterations over 5.65M cells).

## Add-back pixels take a young plot

On 160,582 ac of provenance-4 ("added back young") pixels, the improved TreeMap keeps the
neighbour plot's `TM_ID` for forest type and species only. LETO would read that mature plot's
STDAGE/BALIVE/QMD/TPA there and merge the patch into the stand around it.
`young_plots.py` is a provisional cut of ADR 0003 (branch `add-back-stand-age`) that fixes this.
Each patch gets a stand age drawn from 1–10 (seed `AGE_SEED`), and each pixel takes a real FIA
plot with the neighbour's FORTYPCD at that age, widening ±1, ±2, then to the forest type group.
The plot gets a new raster VALUE above TreeMap's range. On this run, 68% of acres matched on
exact type and age and none fell back to nearest age. Replace the random age once the raster
correction dates each cut.

## Results (2026-10-05)

73,790 parent stands, 105,227 management units (19,481 riparian, 27.5k ac), 1,256,759 forest ac.
53k upland units are under 5 ac. They are isolated fragments with no same-owner neighbour to
merge into, and hold 3.5% of the area. LETO's 5-ac sliver drop (`s3_management/sliver_merge.py`)
has not been applied here.

`TREE_COUNT` in the FVS-ready FIA tables is a per-subplot record count. FVS expands it by the
plot design: ×24/4 at or above `BRK_DBH`, ×300/4 below. `03_figures.py` expands it only for
display. With it expanded, unit 53400's tree list gives 78.1 ft²/ac of basal area, against
78.9 from the TreeMap VAT.
