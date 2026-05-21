# FVS Management Regime Keyword Templates

Each `.key` file is a parameterized FVS keyword template for one management regime.
Templates use `{placeholder}` syntax; `pipeline/s4_fvs/keyword_builder.py` fills them
at run time from the pixel's site attributes.

## Regime inventory

| File | Regime | Ownership targets | Build order |
|------|--------|-------------------|-------------|
| `no_management.key` | No harvest, no thinning | All (Phase 1 growth validation) | **Build first** |
| `nipf_light.key` | Occasional partial harvest | `family_forest` | Phase 2 |
| `industrial_pine.key` | Site prep → plant → thin → clearcut | `corporate_forest` | Phase 2 |
| `industrial_hardwood.key` | Hardwood/mixed industrial rotation | `corporate_forest` | Phase 2 |
| `public_conservative.key` | Light management, retention focus | `federal_forest`, `state_forest`, `local_forest` | Phase 2 |
| `riparian.key` | No entry or thin-only per BMP class | All (buffer pixels) | Phase 2 |

## Regime assignment logic

`pipeline/s4_fvs/regime_assignment.py` maps each pixel to a regime via:

```
f(ownership_class, forest_type_group, riparian_buffer_class, stand_age) → regime
```

Rule table is in `config/projection.yaml` under `fvs.regime_rules` (to be added
when Phase 2 begins).

## Build order note

`no_management.key` is the only template needed for Phase 1.
Do not parameterize harvest regimes until FIA remeasurement validation passes.
