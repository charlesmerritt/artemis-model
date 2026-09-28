# ARTEMIS: ownership, management prescriptions, and scenarios

**14 September 2026 · Repository snapshot `18af1ee`**

This document describes the parameters currently in this repository, including differences between declared policy and executable behavior. It is a methods and assumptions brief, not a finding that these prescriptions reproduce observed Florida landowner behavior. The numerical treatment choices are working assumptions unless explicitly identified otherwise. No new landscape simulation was run for this brief.

## 1. At a glance

ARTEMIS gives each management unit a menu of treatments based on ownership and forest type. A default prescription provides a reference choice; a landscape optimizer can select among eligible trajectories to approach observed timber removals. **Grow-only is an option for every owner and is compulsory for riparian units.**

The current owner policy contains **eight prescriptions, seven substantive owner classes, and an unknown-ownership class**. The projection is regular Southern FVS (SN), starting in **2022**, over **50 years to 2072**, in **ten 5-year cycles**. Headline raster years are 2022, 2047, and 2072. [S1–S4]

The most consequential distinctions for review are:

- **Owner-policy pine rotations:** first thinning targets stand age **15 or 18**, and final harvest targets age **25 or 35**, respectively. Hardwood final harvest targets age **50**.
- **Scenario timings differ:** the separate comparison library schedules industrial pine harvest **25 or 40 years after inventory**, and hardwood harvest **20 or 40 years after inventory**. These are elapsed-time schedules, not stand-age thresholds.
- **Regeneration:** Templates vs. imputation vs. Identical reestablishment. The template renderer supplies fixed planting/natural-regeneration keywords directly to FVS. While it makes sense to use the same configuration of a stand after a clearcut, essentially replanting it identically. Lastly, we can consider a nearest neighbor imputation to fill in holes in the rasters where there is no tree data to build off of. 

## 2. Vocabulary and how to read the numbers

| Term | Meaning in this brief |
|---|---|
| Owner class | Behavioral policy group assigned from ownership data; distinct from the three TPO reporting groups. |
| Prescription / regime | A sequence of thinning, selection, or final-harvest operations. The repository uses both words. |
| Default | The one prescription assigned deterministically for an owner and forest-type branch. It is not a mandatory treatment. |
| Eligible menu | The options that may be represented in that unit’s trajectory library. Forest type further restricts the owner’s menu. |
| Scenario | A landscape-wide comparison of management assumptions, such as short versus extended rotation and retention/buffer policy. |
| Stand age | Biological/inventory age used to schedule the age-based prescriptions. |
| +10 years | Ten years after the 2022 inventory, i.e. 2032. It does **not** mean stand age 10. |
| Removal percentage | Fraction of trees per acre (TPA) removed **within the stated diameter window**, across all species. It is not a prescribed percentage of stand volume, basal area, or land area. |
| Selection | Here, proportional removal across the diameter distribution. It is a simplified representation, not individual-tree selection to a target diameter distribution. |

## 3. Who receives which treatments?

The short names below refer to the complete parameter table in Section 4. Pine rotations are eligible only on the pine branch; the hardwood clearcut is eligible on hardwood and other/mixed branches. All other prescriptions accept any forest branch. [S1–S3]

| Owner class | TPO group | Default on pine | Default on hardwood / other | Eligible active prescriptions |
|---|---|---|---|---|
| Private industrial | Private | Short pine rotation | Hardwood clearcut | Short pine; long pine; hardwood clearcut |
| Other corporate private | Private | Long pine rotation | Family light thin | Long pine; family light thin; family selection |
| Family / small private | Private | Family light thin | Family light thin | Family light thin; family selection; long pine |
| Tribal | Private | Public selection | Public selection | Public selection; family light thin |
| Federal | Federal | Public selection | Public selection | Public selection; restoration thin |
| State | Other public | Restoration thin | Public selection | Restoration thin; public selection; long pine |
| County / municipal | Other public | Grow-only | Grow-only | Public selection; restoration thin |
| Unknown ownership | Private, flagged | Family light thin | Family light thin | Family light thin only |

**Interpretation by owner.** Industrial ownership is represented by rotation forestry, with pulpwood versus sawtimber as the pine alternatives. Other corporate owners have a longer/lighter menu. Family ownership emphasizes infrequent partial entries, while allowing a managed pine subset to take a long rotation. Federal and tribal defaults retain cover through periodic selection. State pine has a restoration-thinning default and a sawtimber option. Local ownership defaults to no entry. Unknown ownership receives a deliberately narrow menu so an optimizer cannot invent aggressive management on unattributed land. These are declared policy rationales, not fitted owner-level probabilities. [S2]

**Important consequences:** no clearcut is eligible for federal, tribal, local, or unknown ownership under this policy. Family and other-corporate hardwood also lack a clearcut option. State clearcuts are available only through the long pine rotation. Industrial non-pine has just hardwood clearcut or grow-only after forest-type filtering.

### Ownership classification parameters

The Harris ownership raster provides the base ownership vocabulary: **0 unknown, 1 non-forest, 2 water, 3 family, 4 corporate, 5 tribal, 6 federal, 7 state, 8 local**. Non-forest and water are excluded from FVS rather than assigned grow-only. The policy specifies forest-area-majority ownership for a unit; the row-level classifier consumes the resulting code and does not itself perform this spatial aggregation. Unknown ownership is charged to Private and reported separately. [S1, S3]

Forest branching accepts FIA FORTYPCD **140–179 as pine** and **500–998 as hardwood**; other values, including numeric oak/pine group 400–499 and unknown type, take the other branch. Text-name heuristics are used when numeric type is unavailable or unusable. Planting eligibility is based on this pine branch, not a separate planted-origin screen. [S3]

## 4. Complete owner-policy prescription parameters

**All timings in this table are either target stand ages or explicit inventory offsets, as labeled.** The actual entry year is rounded to a 5-year cycle. Full prescription IDs are provided in Appendix A. [S2–S4]

| Prescription | Timing | Operation and intensity | Final harvest / rotation |
|---|---|---|---|
| Grow-only | No entries | Grow existing forest | None |
| Short pine rotation | Target first thin age **15** | One thin; remove **40%** of TPA at DBH **0–8 in** | Target age **25**; remove **100%** at DBH **0–999 in** |
| Long pine rotation | Target first thin age **18** | One thin; remove **35%** of TPA at DBH **0–9 in** | Target age **35**; remove **100%** at DBH **0–999 in** |
| Hardwood clearcut | Target harvest age **50** | No prior thin | One final cut; remove **100%** at DBH **0–999 in** |
| Family light thin | **+10** (2032) | One thin; remove **35%** at DBH **0–8 in** | None; no rotation length |
| Family selection | **+15, +30, +45** (2037, 2052, 2067) | **15%** at DBH **0–999 in** each entry; **15-year** interval | None; continuous-cover intent |
| Public selection | **+10, +20, +30, +40** (2032, 2042, 2052, 2062) | **20%** at DBH **0–999 in** each entry; **10-year** interval | None; continuous-cover intent |
| Restoration thin | **+5, +20, +35** (2027, 2042, 2057) | **30%** at DBH **0–10 in** each entry; **15-year** interval | None; mechanical treatment only, no prescribed fire |

The restoration window ends at +45, but the 15-year sequence starting at +5 contains only +5, +20, and +35. **There is no +45 entry.** The declared entry counts are 0, 2, 2, 1, 1, 3, 4, and 3 in the table’s order. They describe the nominal schedules; horizon truncation or removal of an overlapping thin can reduce actual counts. The `repeats` metadata does not generate additional rotation cuts in the current template renderer.

### Missing ages, cycle rounding, and earliest entries

For an age-based operation:

**Years until entry = maximum of 5 and [target age − current stand age, rounded upward to a multiple of 5].**

Thus a short-rotation stand aged 22 at inventory is harvested in **2027**, at approximately age **27**, not literally three years later. Its thin is dropped because the thin and clearcut resolve to the same cycle. Overmature stands also wait at least one cycle. Entries after 2072 are removed. A prescription with no surviving entries renders as grow-only. [S3]

| Prescription | If age is missing or unusable: resolved inventory offsets | Calendar years |
|---|---|---|
| Short pine | Thin **+15**, clearcut **+30** | 2037, 2052 |
| Long pine | Thin **+20** (configured +18 rounded up), clearcut **+35** | 2042, 2057 |
| Hardwood clearcut | Clearcut **+30** | 2052 |

## 5. Regeneration and repeated rotations

### What the current template renderer supplies

The renderer’s defaults come from `config/fvs_keywords.yaml`. They are shared across owners; there is no separate density or survival table by owner. A plantation template uses planting after its final cut. [S4, S5]

| Regeneration parameter | Current renderer default |
|---|---|
| Planting species | **LP (loblolly pine)**, one shared default |
| Planted density | **605 trees/acre** |
| Natural density | **400 trees/acre total**, apportioned among species if composition is supplied |
| Survival | **100%** for both modes |
| Cohort age / height | **1 year / 0.5 ft** at establishment |
| Delay | **1 year after harvest** for both modes |
| Natural species allocation | Relative stand density index (SDI) shares of species already present, when `stand_sdi` is passed |
| Small-species cutoff | Drop species below **5% of SDI**, renormalize survivors; if all are below the cutoff, retain the largest contributor |
| Missing composition | Warn and use **LP** as the single natural-regeneration species |
| Automatic establishment | Plant packets include `NoInGrow` and `NoAutAly` when suppression is enabled; the register records related side effects for Natural |
| Caller overrides | Mode (`plant`, `natural`, `none`), species, density, survival, age, height, and delay are supported by renderer parameters; these are not owner-specific defaults |

The register identifies density, species choice, survival, and the SDI cutoff as assumptions needing review. [S5]

### What the owner-policy YAML declares instead

| Prescription | Declared donor slot | Declared delay | Repeat flag |
|---|---|---|---|
| Short pine rotation | `planted_pine_regen` | 1 year | Yes |
| Long pine rotation | `planted_pine_regen` | 1 year | Yes |
| Hardwood clearcut | `hardwood_regen` | 3 years | No |
| All partial entries / grow-only | None | None | No |

Those slots refer to a fixed-FIA-tree-list restart design. Planted-pine donors must have planted origin and age ≤10; hardwood donors natural origin and age ≤15. A natural-pine regeneration slot also exists (natural origin, age ≤15), but none of the eight current prescriptions selects it. Donors are drawn from FL/GA/AL, with at least ten candidates, selecting the lower median live-basal-area plot and breaking ties by string-valued PLT_CN. These are donor selection ages, not minimum harvest ages. [S2, S7]

**Execution gaps that matter for interpretation:**

- The assignment object returns a donor-slot label, but does not pass the YAML’s regeneration delay or repeat flag into template parameters. Current hardwood template regeneration therefore defaults to **1 year**, not the YAML’s **3 years**.
- When an older plantation’s thin is dropped, the resolver changes its template to `clearcut`. That template defaults to **natural regeneration**, although its prescription label and donor slot still indicate a plantation. With no composition supplied, it falls back to LP at **400 TPA**, rather than planting LP at **605 TPA**.

## 6. Riparian protection in every scenario

| Feature | Baseline configured width | Wider configured width |
|---|---|---|
| Ephemeral / intermittent stream | 35 ft | 70 ft |
| Small perennial stream | 50 ft | 100 ft |
| Large perennial stream | 75 ft | 150 ft |
| Waterbody | 75 ft | 150 ft |

Stream widths are measured on each side of the centerline; waterbody buffers surround the waterbody boundary. These are repository model settings. The scenario’s wider widths require a changed spatial partition and regenerated management units; simply swapping harvest prescriptions leaves that part of the scenario unevaluated. [S6, S11]

The owner-policy override is **SMZ_Pct ≥50 → grow-only**, ahead of owner treatment assignment. The intended spatial preparation separates buffer polygons (SMZ_Pct=100) from managed polygons (SMZ_Pct=0). The owner-only menu function has no SMZ input; callers constructing a unit’s candidate library must restrict riparian units to grow-only. Buffers remain in growth outputs and should retain separate unit IDs. [S2, S3]

## 7. Timber targets and optimization parameters

Owners have different treatment menus, but the TPO inputs have only **three owner reporting groups**, not an independent volume target for every owner class. Forward scheduling selects the **2013–2024** target series; the configured hindcast series is **pre-2015**. Annual targets are multiplied by five for each 5-year timestep. [S1, S9, S12]

| Owner group | Annual target, ft³/year | Five-year target, ft³/cycle |
|---|---:|---:|
| Private | 70,994,800 | 354,974,000 |
| Federal (NF) | 2,023,500 | 10,117,500 |
| Other public | 4,486,900 | 22,434,500 |
| All owners | 77,505,200 | 387,526,000 |

| County | Annual target, ft³/year | Five-year target, ft³/cycle |
|---|---:|---:|
| Baker | 11,451,200 | 57,256,000 |
| Columbia | 19,725,500 | 98,627,500 |
| Hamilton | 16,211,500 | 81,057,500 |
| Suwannee (`Suwanee` in source) | 22,474,700 | 112,373,500 |
| Union | 7,642,900 | 38,214,500 |
| All five counties | 77,505,800 | 387,529,000 |

The adopted optimization configuration treats TPO as an **even-flow target**, penalizing departures rather than imposing a hard ceiling. [S9]

## 8. Decisions for review

| Decision | Why it affects the interpretation |
|---|---|
| Choose one authoritative prescription/scenario library | Establish minimum harvest age, and size per owner class. Repeat for thinnings. Identify feasible rotation lengths and ground decisions in intentions survey. |
| Reconcile regeneration policy with emitted records | Decide densities, species, survival, delays, natural composition, and behavior of overmature plantation clearcuts. Verify establishment in FVS. |
| Settle constraints for scheduler | Are spatial green up constraints needed? Other constraints or secondary objectives? |

**Output limits:** timber removals are the available scenario KPI. `carbon_extension` remains false, and no harvested-wood-products carbon pool is implemented in the scenario specification. NPV lacks agreed prices, costs, and a discount rate. [S6, S9]

## Appendix A. Exact prescription names

| Short name used here | ID in `management_regimes.yaml` |
|---|---|
| Grow-only | `no_management` |
| Short pine rotation | `pine_plantation_short_rotation` |
| Long pine rotation | `pine_plantation_long_rotation` |
| Hardwood clearcut | `hardwood_clearcut_regen` |
| Family light thin | `family_light_thin` |
| Family selection | `family_uneven_aged_selection` |
| Public selection | `public_selection_light` |
| Restoration thin | `public_thin_restore` |

## Appendix B. Full separate scenario-library inventory

The following table inventories **all 15** entries in `config/regimes.yaml`, including treatments whose labels refer to owners but which do not have an executable crosswalk from the current eight-class owner policy. In particular, an NGO/conservation label here does not add an NGO class to the Harris ownership vocabulary. **Every time is an inventory offset.** Percentages are TPA removed; DBH windows are inches; all species are included. [S8]

| Regime ID | Complete operation sequence |
|---|---|
| `no_management` | No operations |
| `nipf_light` | +10: thin from below, 30% at 0–8 in; +30: thin from below, 25% at 0–10 in |
| `pine_plantation_industrial` | +10: thin from below, 40% at 0–8 in; +25: regeneration harvest, 100% at 0–999 in |
| `hardwood_industrial` | +20: regeneration harvest, 100% at 0–999 in |
| `public_uneven_aged` | +10: selection, 15% at 0–999 in; +25: selection, 15% at 0–999 in; +40: selection, 15% at 0–999 in |
| `public_active_thinning` | +5: thin from below, 30% at 0–10 in; +20: selection, 20% at 0–999 in; +35: selection, 20% at 0–999 in |
| `conservation_restoration` | +10: thin from below, 40% at 0–6 in; +30: thin from below, 30% at 0–6 in |
| `custodial_light` | +20: thin from below, 15% at 0–8 in |
| `light_default` | +15: thin from below, 25% at 0–8 in |
| `pine_plantation_extended` | +10: thin from below, 40% at 0–8 in; +25: thin from below, 25% at 0–12 in; +40: regeneration harvest, 100% at 0–999 in |
| `pine_plantation_retention` | +10: thin from below, 40% at 0–8 in; +25: retention harvest, 85% at 0–999 in |
| `pine_plantation_extended_retention` | +10: thin from below, 40% at 0–8 in; +25: thin from below, 25% at 0–12 in; +40: retention harvest, 85% at 0–999 in |
| `hardwood_extended` | +40: regeneration harvest, 100% at 0–999 in |
| `hardwood_retention` | +20: retention harvest, 85% at 0–999 in |
| `hardwood_extended_retention` | +40: retention harvest, 85% at 0–999 in |

## Sources and verification

Source labels refer to files at the repository snapshot above. The brief’s tables describe repository assumptions; external silvicultural guidance and certification requirements were not independently reviewed. The project cites the local Diaz et al. 2015 report for scheduling/regeneration methods and the 2018 paper in the scenario design; this brief does not infer Florida parameter calibration from those references.

| Label | Repository source | What was checked |
|---|---|---|
| S1 | `config/ownership_policy.yaml` | Owner vocabulary, refinement thresholds, TPO groups, audit status |
| S2 | `config/management_regimes.yaml` | Eight prescriptions, owner menus/defaults, ages, offsets, intensities, regeneration metadata |
| S3 | `pipeline/s3_management/owner_classes.py`; `pipeline/s3_management/regime_assignment.py` | Actual classification, forest filtering, timing resolution, riparian override, returned parameters |
| S4 | `pipeline/s4_fvs/regime_templates.py` | ThinDBH operations and regeneration keyword generation |
| S5 | `config/fvs_keywords.yaml` | Actual template and regeneration defaults; declared validation gaps |
| S6 | `config/scenarios.yaml` | Four scenarios, retention/buffer axes, KPI limitations |
| S7 | `config/fallback_treelists.yaml`; `pipeline/s4_fvs/fallback_treelists.py` | Alternative donor-list design and unresolved status |
| S8 | `config/regimes.yaml`; `pipeline/s4_fvs/regime_library.py` | Fifteen fixed-offset regimes, renderer behavior, terminal-cut restriction |
| S9 | `config/projection.yaml`; `pipeline/s3_management/harvest_scheduler.py`; `weekly-artifact/2026-08-31/make_annealed_plan.py` | Projection, objectives, optimization settings, greedy versus annealed behavior |
| S10 | `AGENTS.md` | Intended minimum harvest age, harvestable share, regeneration, and offset choices |
| S11 | `config/bmp_rules.yaml` | Baseline model buffer widths |
| S12 | `config/tpo_targets.yaml` | Stored annual county and owner-group targets |

Verification comprised direct YAML extraction, code-path inspection, and executable checks of owner/forest menus, age rounding, missing-age fallback, SMZ override, treatment operations, and regeneration record generation. It did not include a new FVS run, parcel audit, or annealing run. The HTML companion is self-contained and formatted for browser reading and printing to PDF.
