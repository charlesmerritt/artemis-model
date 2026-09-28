# Regeneration investigation — 2026-09-14

The requested hierarchy is **not implemented consistently**. Nearby-donor initialization
exists, but clearcut regeneration commonly uses fixed species/density defaults, and one
library path supplies no explicit regeneration. The current code does not establish that
50-year trajectories correctly represent repeated harvest and reestablishment.

Branch: `investigate-regeneration-hierarchy`, inspected base commit `18af1ee`. This is an investigation with executable
reproductions, not a regeneration implementation. Production code and config are unchanged.

## Findings and evidence

| Priority | Finding | Code evidence |
| --- | --- | --- |
| High | Plantation clearcuts use LP at 605 TPA even when supplied stand composition is entirely slash pine. Natural clearcuts use SDI shares at a fixed 400 TPA, dropping species below a 5% share. Neither restores original species-specific stocking. Missing composition becomes LP without any neighbor search. | `pipeline/s4_fvs/regime_templates.py:_regen_after`, `apportion_by_sdi`; `config/fvs_keywords.yaml:defaults.regeneration` |
| High | The YAML regime library passes `thins` without `regen`. The template renderer only derives regeneration automatically when `thins` is omitted. Thus library clearcuts lack an explicit `Estab` packet; their post-cut behavior depends on FVS defaults. | `pipeline/s4_fvs/regime_library.py:render_keyfile`; `pipeline/s4_fvs/regime_templates.py:render_keyfile`; `config/regimes.yaml` |
| High | The proposed original-stand restoration path is absent. `resolve_regeneration` unconditionally returns `REGEN_FIXED`, with no production callers found. The documented fixed-list restart loop is not established by executable production code. | `pipeline/s4_fvs/fallback_treelists.py:resolve_regeneration`; `config/management_regimes.yaml` |
| High | Regeneration delay and repeat intent do not survive assignment. Hardwood's configured 3-year delay becomes the renderer default of 1 year. `repeats` is not consumed by the Python prescription path. The experimental policy scheduler is a separate implementation, not evidence that this config works. | `pipeline/s3_management/regime_assignment.py:assign_prescription`; `config/management_regimes.yaml:prescriptions` |
| High | The experimental policy renderer independently plants default LP for pine types, and for hardwood with missing SDI. Repairing only the shared template builder would leave this path inconsistent. | `experiments/2026-08-24_leto-ca-forest-viz/policies.py:_regen_records`; `04_fvs_run.py` |
| Medium | Initialization already searches same broad type within 5 km, then any type within 2 km, then fixed slots. It copies whole donor tree rows, including sizes; it is a missing-inventory initializer, not an age-zero establishment procedure. No age, site, or bottomland/upland compatibility is required for neighbor eligibility. | `pipeline/s4_fvs/build_fvs_inputs.py:ladder_decisions`, `impute_nearest_runnable`; `config/fallback_treelists.yaml:initialization_ladder` |
| Medium | A stale runnable set can select a donor with no tree rows, then jump directly to fixed fallback despite a valid second neighbor. The normal builder derives its runnable set from its trees, so this is a public-helper robustness gap rather than proof of ordinary-path failures. | `pipeline/s4_fvs/build_fvs_inputs.py:impute_nearest_runnable` |
| Medium | A unit with some unmatched weighted plots remains runnable using only the matched fraction. For two equally weighted plots, one missing plot leaves half the stocking; the initialization ladder never visits that unit. This needs an explicit partial-coverage policy. | `pipeline/s4_fvs/build_fvs_inputs.py:build_tree_init` |
| Medium | The fixed fallback system is pinned real FIA plots, not a configurable planting blueprint. Slots are declared unresolved, with no checked-in lock file. It raises by default; measurement mode can omit units with unavailable fallback lists. Young regeneration slots can contain already-grown trees, rather than age-zero seedlings. | `config/fallback_treelists.yaml`; `pipeline/s4_fvs/fallback_treelists.py:plt_cn_for_slot`; `build_fvs_inputs.py:impute_nearest_runnable` |

The August 31 batch (`weekly-artifact/2026-08-31/make_fvs_batch.py`) supplies donor-plot
SDI composition to templates, not immutable management-unit species/TPA blueprints.
Its donor-based deduplication must be revisited if recipient units have different original
configurations. The August 17 trajectory builder carries `regen_slot` as metadata; this
does not execute a restart.

The add-back pipeline ends at a mask (`pipeline/s1_initial_state/finalize_add_back.py`).
Searches found mask consumers for validation and figures, but no explicit connection of
that output to management-unit/FVS tree initialization. Accepting an acre into the mask
does not by itself assign it a treelist. Some accepted strata already represent regrown
forest: those should not automatically be reset to age zero.

Configuration comments disagree with executable behavior. Both fallback and management
config describe fixed-list restarts and no planting keywords, while the template module
now emits establishment keywords. Treat these comments as historical intent.

## Proposed contract for the requested hierarchy

1. **Clearcut an existing stand:** reuse an immutable original establishment configuration.
   Preserve species-specific stocking, recipient site attributes, and identity; instantiate
   a new cohort with explicit seedling size and age rules. Subsequent rotations reuse the
   same snapshot, rather than the depleted stand after thinning or harvest. Keep harvest
   year, establishment year, and elapsed stand age distinct.
2. **A stand without its own configuration:** impute from the nearest eligible stand.
   Validate donor trees before spatial selection, search eligible same-type donors first,
   and use stable ID tie-breaking for equal distances. Suitability should explicitly cover
   forest type and establishment state, with site/moisture compatibility where available.
   Distinguish recovering missing inventory on existing forest from creating new forest:
   the former can need existing tree sizes; the latter needs an establishment cohort.
3. **All eligible donors exhausted:** use a versioned forest-type/density blueprint.
   Fixed FIA donor lists may remain an explicitly tagged failsafe, but should not be the
   normal clearcut path. If neither a validated donor nor an applicable blueprint exists,
   fail visibly instead of silently converting unknown hardwood to loblolly.

“Identical configuration” cannot safely mean copying mature DBH/height and merely changing
age to zero: that restores mature biomass immediately after harvest. The proposed meaning
is original species and species-specific TPA recreated as seedlings. If a historical
planting record exists, use that as the original establishment configuration; otherwise
the current inventory is only a proxy, and its mature TPA is not evidence of historical
planting density. That distinction needs an explicit modeling decision before implementation.

Use enum values for initialization purpose and regeneration source, rather than booleans.
Record source, original snapshot hash, donor unit/plot IDs, donor distance, fallback reason,
blueprint version, cohort age, and establishment year. Preserve FIA CNs as validated strings.
Report acres and stocking by source, including uncovered acres; tree-only summaries cannot
account for skipped units. Never apply harvest or planting actions to protected buffers;
they retain the grow-only policy.

## Small implementation sequence and acceptance tests

1. Define the establishment snapshot and source resolver with failing tests for original
   snapshot priority, species/TPA preservation, age/size reset, immutable repeated use,
   and 19-digit IDs. Decide how inventory proxies differ from known planting histories.
2. Harden donor eligibility and partial coverage. Test distance boundaries, same-type
   priority, equal-distance determinism, unusable nearest donor with a usable second donor,
   all donors absent, and explicit failures for unavailable blueprints. Do not silently
   renormalize a partially covered unit without choosing the ecological policy.
3. Route template, YAML-library, experimental, and batch callers through the same resolved
   regeneration input. Test configured delays, 50-year horizon boundaries, second rotations,
   thinning without replacement, and buffer exclusions. Propagate recipient-specific
   configurations into trajectory-library keys so incompatible stands cannot share a run.
4. Connect add-back classification to initialization purpose and report area closure from
   accepted mask through units, inputs, trajectories, and outputs. Use county fixtures first.
5. Exercise the actual SN binary for clearcut → establishment → growth → second clearcut,
   comparing species, TPA, age, biomass and harvest outputs. Then run the repo's coverage,
   CRAP and mutation gates on the implementation. Keyword presence alone is insufficient.

## Verification performed

Run the synthetic reproductions from the repository root:

```sh
.venv/bin/python -m research.regeneration_audit.probe
.venv/bin/python -m pytest pipeline/s4_fvs pipeline/s3_management/regime_assignment.py -q
```

All seven reproduction assertions passed, confirming current gaps: fixed plantation,
missing-composition default, missing library establishment, lost delay, unconditional fixed
resolver, dead donor skipping a usable neighbor, and partial donor coverage. These are
characterization probes of deficiencies, not acceptance tests for desired behavior.
The targeted existing suite passed **2 tests**; it does not cover these regeneration guarantees.
The full repository suite passed at the audit's base commit `18af1ee` (26 tests then). Ruff passed for the reproduction script,
and `git diff --check` passed.

No external FIA data or FVS binary was used, so donor availability, affected landscape
acreage and resulting yield bias are not quantified. The archived restart gate at
`research/restart_fidelity/outputs/gate_cut_injection.txt` verifies a 30% proportional thin
and its restart fidelity. It does not verify replacement with an age-zero cohort or repeated
clearcut rotations. No mutation score is claimed for this investigation.
