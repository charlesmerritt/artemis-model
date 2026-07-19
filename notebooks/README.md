# Notebooks

These notebooks are exploratory interfaces around ARTEMIS data acquisition, classification, and
validation. They are not a linear pipeline. Run them from the repository root so relative imports
and paths resolve consistently:

```bash
uv sync
uv run jupyter lab
```

## Notebook groups

| Entry point | Purpose | Main prerequisites |
|---|---|---|
| `TreeMap_COG_County_Summary.ipynb` | Windowed zonal summaries from a remote COG or STAC item | Network access |
| `Embedding-Similarity-AOI-Finder.ipynb` | Find regions similar to selected clearcut references using AlphaEarth embeddings | Earth Engine authentication |
| `Clearcut-vs-Agriculture-Embeddings.ipynb` | Test embedding separation between recent clearcuts and agriculture | Earth Engine + local LANDFIRE data |
| `Clearcut-vs-Agriculture-EVT-Change.ipynb` | Compare forest-to-herb/agriculture/shrub EVT change with LCMS evidence | Earth Engine + local LANDFIRE data |
| `Clearcut-Grassland-Feature-Engineering.ipynb` | Assemble model features and spatial cross-validation baselines | Earth Engine + local LANDFIRE data |
| `Similarity-Embeddings.ipynb` | Original similarity prototype | Superseded; retained for reference |
| `FVS_5county_growth_smoke.ipynb.old` | Retired five-county FVS smoke workflow retained as a recovery reference | External TreeMap/FIA data and missing FVS helper modules |

`clearcut_ag_common.py` contains shared helpers for the embedding and clearcut notebooks. Its pure
functions are covered by `tests/test_clearcut_ag_common.py`; optional output checks live in
`tests/test_clearcut_ag_outputs.py`.

## Before running

1. Check `git status --short -- notes/` and read the
   [full notebook status](../notes/notebooks.md). Notebook findings and environmental blockers
   change more frequently than this overview.
2. Confirm the `/mnt/d` external data mount for workflows using local TreeMap, FIA, or LANDFIRE
   files. Paths are configured in [`../config/data_paths.yaml`](../config/data_paths.yaml).
3. Authenticate Earth Engine interactively when required:

   ```bash
   uv run earthengine authenticate
   ```

4. Avoid committing generated outputs or embedded map-widget state. Large notebook state can
   increase a notebook by many megabytes.

## Detailed notes

- [Full notebook inventory and latest run status](../notes/notebooks.md)
- [Clearcut versus agriculture and embedding workflows](../notes/clearcut-vs-agriculture-embeddings.md)
- [TreeMap COG county summaries](../notes/treemap-cog-county-summary.md)
- [Five-county FVS smoke workflow](../notes/fvs-5county-growth-smoke.md)
- [TreeMap-to-FVS workflow](../notes/treemap-fvs-workflow.md)

The FVS smoke notebook is not currently a maintained runnable entry point: its documented helper
modules are absent from the committed repository. Treat the associated note as the recovery plan,
not as evidence that the notebook can run end-to-end.
