# Proposed regeneration revisions

Status: proposed, following the user's clarified hierarchy. No production configuration
or pipeline behavior is changed by this document. Paths and YAML below describe the next
implementation on `investigate-regeneration-hierarchy`.

The policy is:

- Repaired forest raster areas receive treelists from nearby suitable stands, retaining
  donor inventory structure rather than being treated as newly planted forest.
- Clearcut stands are replanted with their original species and species-specific planting
  density. Every rotation uses the same immutable replanting record.
- Fixed donor lists or planting blueprints are used only after usable local sources are
  exhausted. Their use is recorded explicitly.

## 1. Define what is preserved after clearcutting

Create a per-unit replanting table after protected-area partition and before any management runs. One row per species carries
`MU_ID`, `SPECIES`, `PLANT_TPA`, `DENSITY_BASIS`, `BASELINE_ID`, and source provenance.
`PLANT_TPA` means trees planted per acre, before survival; it is never multiplied by stand
acreage when passed to FVS. A separate baseline header records inventory vintage,
configuration version and a deterministic content hash.

Choose the complete species/density record in this order:

1. A supplied original planting record, if available for the unit.
2. The unit's initialized live species-specific TPA, labeled `INITIAL_INVENTORY_PROXY`.
   This includes the area-weighted imputed contribution for repaired land. Use the same
   record across all candidate regimes and subsequent rotations.
3. A suitable nearby donor's baseline, if the unit still lacks a usable configuration.
4. An explicitly applicable fixed planting blueprint; otherwise report an unresolved unit
   and fail production preflight.

Step 2 is the proposed operational assumption when historical planting records are absent.
It preserves the starting model configuration; it does not reconstruct stocking lost to
mortality or historical thinning. Do not derive TPA from SDI shares, convert every pine
stand to loblolly, drop minority species, or recapture the baseline after a thin.

For example, a baseline of 300 SA and 200 LL trees/acre produces planting requests for
300 SA and 200 LL after each clearcut. Seedling size and cohort age are reset. Mature
DBH, height and biomass are not copied. Survival is a separate parameter: planting 500
trees/acre at 90% survival is still a 500-tree planting request, with 450 expected survivors.

Use enums for `DensityBasis`, `InitializationSource`, and `EstablishmentMethod`. Unknown
species, nonfinite density, nonpositive density and lossy CN inputs fail validation.

## 2. Configuration revisions

### Add `config/regeneration.yaml`

This becomes the policy source for baseline capture and clearcut replanting. The renderer's
keyword syntax remains in `fvs_keywords.yaml`; spatial donor search remains in
`fallback_treelists.yaml` to avoid defining two competing donor ladders.

```yaml
version: 1

baseline:
  source_order:
    - original_planting_record
    - initial_inventory_proxy
    - nearest_eligible_baseline
    - fixed_planting_blueprint
  species_density: species_tpa
  capture_at: initialization_complete
  reuse: immutable_across_rotations

clearcut:
  method: replant_original
  establishment: plant
  cohort_age_at_establishment_years: 0
  missing_baseline: error

partial_harvest:
  regeneration: none

protected_units:
  management: grow_only

fallback:
  blueprint_catalog: config/fallback_treelists.yaml
  unknown_forest_type: error
```

`missing_baseline: error` is intentional: donor/blueprint recovery happens during preflight
baseline construction. Clearcut rendering must not silently choose a new source halfway
through a trajectory. `partial_harvest` covers thinning/selection; retention harvests need
a separately declared policy and must not accidentally trigger full-stand replacement.

The age-zero value expresses the model contract at establishment, not an assumption that
FVS will accept every numeric field unchanged. Validate the SN age/height encoding with a
real binary; keep any required representation conversion in the renderer and record it.

### Revise `config/fallback_treelists.yaml`

Keep existing FIA pin selection and lock-file mechanics for missing-inventory failsafes.
Remove regeneration's unconditional fixed-slot route and stale restart claims. Replace
the unqualified any-type neighbor rung with explicit compatibility rules. Proposed ladder:

```yaml
initialization_ladder:
  - rung: nearest_same_type
    method: nearest_runnable_unit
    constraints:
      max_distance_m: 5000
      forest_type_match: exact
  - rung: nearest_compatible_group
    method: nearest_runnable_unit
    constraints:
      max_distance_m: 5000
      forest_type_match: ecological_group
  - rung: fixed_inventory_by_type
    method: fallback_slot
    mapping: {}  # carry forward the reviewed existing type-to-slot mapping

donor_selection:
  eligibility: validated_direct_inventory
  tie_break: ascending_mu_id
  unknown_recipient_type: require_external_type_or_error
  unresolved: error
```

The 5 km limit is retained from current policy, not newly calibrated. Define ecological
groups explicitly, splitting bottomland from upland hardwood; do not use the current broad
`pine: 100..399` label as an exact forest-type match. Repaired/imputed units do not become
new donor seeds within the same run, avoiding order-dependent chains of imputation. Search
beyond unusable candidates before entering a fixed rung.

Add a separate `planting_blueprints` catalog for last-resort species/TPA configurations,
keyed by forest type (and owner class only where a planting policy is explicitly supplied).
Each entry must have a version, source/rationale, explicit species TPA and applicability.
Do not activate a universal LP/605 or LP/400 blueprint. Existing young FIA regeneration
slots can supply an explicitly labeled species/density proxy, but never their mature tree
sizes. No new numerical planting blueprint is proposed without a suitable source.

### Revise `config/management_regimes.yaml`

Replace `regen.treelist_slot` in all full-clearcut prescriptions with the following policy
reference; preserve each prescription's existing delay:

```yaml
regen:
  method: replant_original
  policy: config/regeneration.yaml
  delay_years: 1    # keep 3 for the existing hardwood prescription
rotation:
  mode: repeat     # single for prescriptions currently marked repeats: false
```

Use a rotation enum instead of `repeats`. Carry rotation length and regeneration delay into
resolved assignments. For repeated rotations, compute subsequent target ages from the
actual new cohort establishment year, then apply the configured five-year cycle rule.
Keep grow-only `regen: null`; never let a later scheduler override protected status.

### Revise `config/regimes.yaml`

Give `regeneration_harvest` operations an explicit `regeneration: replant_original` policy
reference and propagate timing. Both library interfaces must resolve to the same event
model. Replace blanket terminal-clearcut validation with rules requiring establishment
before subsequent cohort management. Preserve current owner menus and harvest timings
during this migration. Do not automatically enable extra rotations for unrelated regimes.

Keep retention-harvest validation separate. Until an explicit retained-overstory plus
recruitment policy exists, reject requests that attempt to use full-clearcut replacement
on retention operations; do not silently erase residual trees or plant a full extra stand.

### Revise `config/fvs_keywords.yaml` and `config/data_paths.yaml`

Remove `plant_species`, `fallback_species`, fixed `plant_tpa`/`natural_tpa`, SDI apportionment
and the 5% species floor from the ordinary regeneration defaults. Retain keyword formatting,
seedling height and survival settings as separately documented physical assumptions, with
their current numerical values unchanged until validated. Resolve establishment age from
the new policy and delay from the prescription.

Register paths for the accepted repair mask and strata, initialization coverage manifest,
donor assignments, and baseline table through existing data-access conventions. These are
new output/input products, not hardcoded machine paths.

## 3. Pipeline changes, file by file

| File | Proposed revision |
| --- | --- |
| `pipeline/s1_initial_state/finalize_add_back.py` | Continue deciding which pixels are forest. Export an aligned repair/source mask with strata and a manifest; do not fabricate FIA IDs or treelists here. |
| `pipeline/leto_ca.py` and its segmentation caller | Include accepted repaired cells in the forest domain. Resolve missing segmentation features/type before CA, or explicitly handle their missingness. A new valid-mask cell must not be assigned zero biomass/type as if measured. Keep ownership and buffer boundaries. |
| `pipeline/s3_management/assign_plt_cn.py` | Accept repair/source masks. Count all eligible forest cells, mapped cells, repaired cells and crosswalk failures per unit. Preserve missing contributions rather than dropping them from the denominator. Emit contribution rows plus coverage accounting. |
| `pipeline/s4_fvs/build_fvs_inputs.py` | Validate donor inventory before search; fill missing contribution groups using nearest eligible direct donors. Mix direct and imputed rows by their fraction of total eligible unit area. Carry recipient site metadata. Produce initialization tree rows, complete area accounting, and the immutable baseline before FVS runs. |
| `pipeline/s4_fvs/fallback_treelists.py` | Retain pin lookup and inventory fallback loading, validate usable trees, and expose explicit failsafe outcomes. Remove unconditional clearcut `REGEN_FIXED` decisions. Validate compatibility and provenance consistently. |
| **New** `pipeline/s4_fvs/regeneration.py` | Own typed baseline records, baseline construction/validation, stable hashes and replanting requests from a frozen baseline. No FVS keyword strings or spatial geometry logic here. |
| `pipeline/s3_management/regime_assignment.py` | Resolve regeneration method, delay and rotation mode as typed assignment fields instead of only `regen_slot`. Preserve those fields through serialization. |
| `pipeline/s4_fvs/regime_templates.py` | Accept explicit resolved planting requests; make species and TPA required. Translate the baseline into Plant records without SDI redistribution. Remove implicit LP defaults. Validate full-clearcut/establishment pairing and preserve fractional TPA within verified keyword precision. |
| `pipeline/s4_fvs/regime_library.py` | Render establishment alongside explicit `thins`; compile both config interfaces to the same harvest/establishment schedule. Full-clearcut keyfiles without a resolved baseline fail before execution. |
| `pipeline/s3_management/harvest_scheduler.py` | Continue allocating/selecting trajectories. It must not choose planting species or mutate baseline snapshots. Validate selected trajectory metadata against unit baseline and protection status. |
| `experiments/2026-08-24_leto-ca-forest-viz/policies.py`, `04_fvs_run.py` | Remove independent `_regen_records` species/density decisions; use the shared baseline and event resolver. Keep experiment-specific harvest choices. |
| `weekly-artifact/2026-08-17/make_trajectory_library.py`, `2026-08-31/make_fvs_batch.py` | Update runnable generators to carry baselines and call the shared resolver. Preserve archived generated outputs. Include baseline hash and full relevant FVS input/config identity in deduplication/cache keys. SDI-only donor keys cannot represent recipient-specific replants. |
| `AGENTS.md`, `pipeline/README.md`, affected module/config comments | Replace conflicting fixed-regeneration descriptions with the new implemented contract after its tests pass. |

Avoid a new external cut/restart engine as the first implementation. Extend the existing
Plant-keyword path and validate it end to end. The archived thinning restart gate does not
prove replacement-cohort fidelity and should not be cited as if it does.

## 4. Repaired-area weighting contract

Introduce a contribution ledger keyed by `(MU_ID, component_id)` with `coverage_kind`,
`eligible_area`, `area_fraction`, `resolution_source`, `donor_id`, `donor_distance_m` and
`fallback_reason`. Use separate enum values for mapped inventory, repaired existing forest
and new establishment. Sum ledger area by source; replace `summarize_tree_sources()`'s
current one-source-per-unit deduplication, which cannot describe mixed-source units.

For a unit with 60% usable TreeMap coverage and 40% repaired/unmatched coverage, retain 0.6
of its direct per-acre inventory and impute 0.4 from an eligible donor. If both sources have
100 TPA, the result is 100 TPA, not 60 TPA or 200 TPA. Keep per-contribution provenance so
the repaired 40% remains visible in acreage reports.

Perform imputation on grouped missing contributions (unit, repair stratum, forest type)
or spatially distinct repair patches, not one expensive statewide search per pixel. A unit
can have multiple donor contributions. The existing 5% plot-weight filter must not remove
small repair contributions or inflate direct coverage; define and report any approximation
within an already accounted contribution rather than renormalizing the whole unit.

If a plot passes the raster crosswalk but has no usable live trees, move its area into the
missing contribution pool and retry donors. Validate status, positive finite TPA, required
tree attributes and supported species, not merely row existence. Retain unresolved acreage
in diagnostics even when a measurement-only mode skips simulation. Production requires
all intended forest acreage to be resolved or explicitly excluded with a recorded reason.

## 5. Acceptance checks and implementation order

1. **Baseline contract first:** failing tests for original planting records versus inventory
   proxies, immutable species/TPA across thins and two clearcuts, minority species retention,
   per-acre units, fractional densities, invalid inputs and exact 19-digit IDs.
2. **Repair coverage:** a tiny aligned raster fixture with direct, repaired, missing-crosswalk
   and missing-tree pixels. Verify every acre is counted once, deterministic nearest donors,
   suitability/radius boundaries, unusable nearest donor retry, and fixed fallback last.
3. **Both regime renderers and callers:** the same baseline and schedule must produce the
   same establishment requests. Test three-year hardwood delays, repeated rotations,
   horizon boundaries, thinning, retention rejection and protected grow-only units.
4. **Real SN integration:** run a mixed-species stand through clearcut, establishment, growth
   and another rotation. Check harvested biomass removal, requested versus realized planting
   TPA, species retention, seedling sizes, cohort age and time continuity. Check automatic
   regeneration does not add an unintended extra cohort. Confirm keyword rounding cannot
   erase low-density species. Compare with a grow-only control.
5. **County pilot:** reconcile mask → units → initialization → trajectory library → outputs,
   with acreage by source and fallback reason. Invalidate old trajectories under the changed
   regeneration policy. Run coverage/CRAP and mutation checks for the changed modules, then
   expand to the five-county area only after the county results pass.

No implementation should claim regeneration correctness based solely on nonempty keyfiles
or the current passing test suite. The existing audit probes are evidence of old defects;
replace those characterizations with desired-behavior regression tests as each defect is fixed.
