# Pipeline

Stage numbers follow the architecture. A missing number is planned work, not a missing
directory. Where each stage stands, and what is not yet built:
[`docs/architecture/presentation.html#status`](../docs/architecture/presentation.html#status).

Every module documents itself. Read the docstring, run `--help`, and treat the doctests as
the usage examples. `scripts/check_docs.py` runs those doctests.

```bash
uv run python -c "import pipeline.s4_fvs.build_fvs_inputs as m; help(m)"   # any module
uv run python -m pipeline.s3_management.sketch_management_units --help
```

| Stage | Modules | Entry point |
|---|---|---|
| shared | `ids` (exact-string join keys), `spatial_ref` (the one CRS), `data_access` (drive, then R2), `raster_clip`, `raster_windows`, `leto_ca` | `python -m pipeline.spatial_ref` |
| `s1_initial_state/` | TreeMap hole stratification, sampling, embedding, classification, add-back, FIA and LCMS validation | each module's `--help` |
| `s3_management/` | `sketch_management_units` → `sliver_merge` → `assign_plt_cn`; `owner_classes`, `regime_assignment`, `tpo_targets`, `harvest_scheduler` (greedy baseline) | `python -m pipeline.s3_management.regime_assignment` |
| `s4_fvs/` | `build_fvs_inputs` (area-weighted union), `regime_templates` / `regime_library` (keyfiles), `fallback_treelists`, `paint_fvs_to_raster` | `python -m pipeline.s4_fvs.fallback_treelists` |
| `s5_imagery/` | NAIP acquisition with a per-year coverage check, embedding clustering inside vs outside an AOI, raster correction, viewer catalog | `python -m pipeline.s5_imagery.naip_acquire --help` |

Rules that apply to every module, enforced by `scripts/check_conventions.py`:

- Identifier columns (`PLT_CN`, `STAND_CN`, `TM_ID`, `MU_ID`, …) stay exact strings. Pass them
  through `pipeline.ids.as_id_series`, and never use `.astype(str)` or `str(int(...))` on them.
  The doctests in `pipeline/ids.py` show why.
- The CRS comes from `pipeline.spatial_ref.project_crs()`. Never hardcode it.

Two invariants the modules enforce themselves, by failing the run:

- `sketch_management_units` writes `area_accounting.csv` and exits non-zero when
  `Σ managed + Σ riparian` drifts from `(forest ∩ parcels) − (waterbodies ∪ road buffer)` by more
  than 1e-6.
- `sliver_merge` never merges across `unit_class`, so riparian acres never end up inside a
  harvest unit.
