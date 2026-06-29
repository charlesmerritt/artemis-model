# ARTEMIS: Adaptive Regional Timber and Ecosystem Modeling Integrated Simulator

Deterministic, pixel-level (30 m) forward projection of forest stand dynamics using
**FVS Southern variant**, initialized from **TreeMap 2022** + **FIA tree lists**,
with management calibrated from **LCMS**. v1 extent: Florida. Designed to scale to
full eastern US.

---

## Architecture decisions (locked)

| Decision | Value |
|----------|-------|
| Extent v1 | Florida (FIPS 12), FVS Southern (SN) variant throughout |
| CRS | EPSG:5070 — CONUS Albers Equal Area, snapped to TreeMap 2022 grid |
| Resolution | 30 m |
| Projection horizon | 50 years, 5-year FVS cycles |
| Forest state initialization | TreeMap 2022 (Houtman et al. 2025) + FIA CONUS SQLite |
| Ownership | Harris, Caputo & Butler 2025 — 7 forest classes, 30 m, circa 2022 |
| Soils | POLARIS via GEE community catalog |
| Terrain | 3DEP 1/3 arc-sec via GEE, resampled to 30 m |
| Climate | PRISM 30-year normals 1991–2020 via GEE |
| Management calibration | LCMS Change "Tree Removal" 1985–2024 |
| Harvest forward method | Pseudo-deterministic, fixed random seed (documented in `versions.lock`) |
| Carbon pools | All five IPCC pools via FVS CARBON extension |
| BMP rules | State-specific; Florida FSB Manual 2020 for v1; `config/bmp_rules.yaml` |
| Build order | Growth pipeline → growth validation → harvest model → harvest validation |
| Compute | GEE (raster ops) + local (FIA SQL, FVS prototype, Zarr assembly) + HPC (trajectory library, gated) |
| Python toolchain | uv + ruff + pytest |
| Publication bar | Full validation suite, data dictionary, methods writeup, `versions.lock` |

---

## Repository layout

```
├── config/
│   ├── extent.geojson       # Florida state boundary (EPSG:4326; pipeline reprojects)
│   ├── bmp_rules.yaml       # Riparian BMP buffer widths keyed by state FIPS
│   └── projection.yaml      # CRS, resolution, chunk size, FVS cycle config
│
├── data/
│   ├── raw/                 # Downloaded inputs — gitignored, listed in versions.lock
│   ├── interim/             # Intermediate rasters/tables — gitignored
│   └── processed/           # Final outputs — gitignored
│
├── pipeline/                # Python pipeline modules, one sub-package per step
│   ├── s0_scaffold/         # Extent, grid, storage setup
│   ├── s1_initial_state/    # TreeMap acquisition, FIA join, FVS-ready tree lists
│   ├── s2_site_attributes/  # Soils, terrain, climate, site index, Zarr stack
│   ├── s3_management/       # Stand segmentation, stream buffers, ownership, harvest model
│   ├── s4_fvs/              # FVS wrapper, keyword templates, trajectory library, painting
│   ├── s5_validation/       # Hindcast validation, BIGMAP cross-check, EVALIDator comparison
│   └── s6_outputs/          # Raster packaging, summary tables, documentation assembly
│
├── gee/                     # Google Earth Engine scripts (Python via earthengine-api / geemap)
│   └── scripts/
│
├── fvs/
│   ├── regimes/             # Parameterized FVS keyword (.key) templates
│   └── wrapper/             # Python wrapper around the FVS binary
│
├── tests/                   # pytest test suite
│
├── notebooks/               # Exploratory analysis and QC notebooks
│
├── PLAN.md                  # Full build plan with all decisions documented
├── versions.lock            # Pinned dataset versions and access paths
└── pyproject.toml           # uv project definition
```

---

## Scope (v1)

**In scope:** deterministic, pixel-level forward projection using FVS Southern variant,
initialized from TreeMap 2022 + FIA tree lists, with calibrated management from LCMS.

**Out of scope (v1):** natural disturbance overlays (hurricane, SPB, fire, ice),
climate-modified growth, stochastic Monte Carlo replicates, uncertainty quantification.

---

## Quickstart

```bash
# Install uv if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create environment and install dependencies
uv sync

# Run tests
uv run pytest

# Activate environment (optional — uv run works without activation)
source .venv/bin/activate
```

---

## Future Rust components

Performance-critical path candidates for Rust once the Python pipeline is validated:
- FVS output parser (cycle report → Parquet; currently the hot loop in trajectory library construction)
- Pixel-to-trajectory lookup painter (embarrassingly parallel read from Parquet → write to Zarr)

These will live in `src/` as a Cargo workspace and be called from Python via PyO3 bindings.
Not in scope for v1.

---

## Dataset citations

See `versions.lock` for full version pins. Primary sources:

- Houtman et al. (2025). TreeMap 2022 CONUS. doi:10.2737/RDS-2025-0032
- Harris, Caputo & Butler (2025). Forest ownership circa 2022. doi:10.2737/RDS-2025-0045
- LCMS v2024.10 — USFS GTAC
- PRISM 1991–2020 normals — Oregon State PRISM Climate Group
- POLARIS — Chaney et al. 2019, via GEE community catalog
- FIA CONUS SQLite — USFS FIA DataMart (download date recorded in `versions.lock`)
