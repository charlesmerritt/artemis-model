# ARTEMIS: Ownership, Management Prescriptions, and Scenarios

## 14 September 2026 · Repository snapshot 986ab3a (harvest eligibility update)

This document describes the parameters currently in this repository, including differences between declared policy and executable behavior. It is a methods and assumptions brief, not a finding that these prescriptions reproduce observed Florida landowner behavior. The numerical treatment choices are working assumptions unless explicitly identified otherwise. No new landscape simulation was run for this brief.

# 1. TL;DR

ARTEMIS gives each management unit a menu of treatments based on ownership and forest type. A default prescription provides a reference choice; a landscape optimizer can select among eligible trajectories to approach observed timber removals. **Grow-only is an option for every owner and is compulsory for riparian units.**

The current owner policy contains **eight prescriptions, seven substantive owner classes, and an unknown-ownership class**. The projection is regular Southern FVS (SN), starting in **2022**, over **50 years to 2072**, in **ten 5-year cycles**. Headline raster years are 2022, 2047, and 2072.

The most consequential distinctions for review are:

- **Owner-policy rotations:** first thinning targets stand age **15 or 18**, and final harvest targets age **25 or 35**, respectively. Hardwood final harvest targets age **50**.
- **Minimum harvest age:** every managed entry, thinnings included, now requires a stand age of at least **15** at the time of treatment. Young-stand schedules are deferred; a unit with missing age gets grow-only. This is a conservative project assumption, not a universal silvicultural minimum.
- **Clearcut behavior:** Who gets to do full clearcut harvests, and when? Clearcutting is the default only for private industrial owners (short pine rotation; hardwood clearcut). Family, other corporate, and state owners can reach a clearcut only by choosing the long pine rotation. Federal, tribal, local, and unknown owners have no clearcut option. This could be extended or narrowed.
- **Constraints:** We could constrain by volume harvested across all owners and counties, by volume from each county, by volume from each owner group, or by volume from each owner group in each county. **Currently the scheduler scores county targets and owner-group targets at the same time, as separate even-flow targets.** The TPO inputs have no owner-group-by-county table, so the fourth option would need new target data. Adjacency/green-up and maximum-opening penalties exist as functions, but they can't be evaluated yet because a "stand" is still a pixel class (TreeMap plot × county × ownership), not a compact polygon. They also seem less relevant in the Southeast at the scale we're working at. Other constraints besides the even-flow TPO target should be specified if they are desired.

**Regeneration:** Templates vs. imputation vs. identical reestablishment. The template renderer supplies fixed planting/natural-regeneration keywords directly to FVS. It makes sense to reuse a stand's own configuration after a clearcut, essentially replanting it identically. Lastly, we can consider nearest-neighbor imputation to fill holes in the rasters where there is no tree data to build from, as in the raster improvement procedures.

# 2. Vocabulary

| **Term** | **Meaning in this brief** |
| :- | :- |
| Owner class | Behavioral policy group assigned from ownership data; distinct from the three TPO reporting groups. |
| Prescription / regime | A sequence of thinning, selection, or final-harvest operations. The repository uses both words. |
| Default | The one prescription assigned deterministically for an owner and forest-type branch. It is not a mandatory treatment. |
| Eligible menu | The options that may be represented in that unit's trajectory library. Forest type further restricts the owner's menu. |
| Scenario | A landscape-wide comparison of management assumptions, such as short versus extended rotation and retention/buffer policy. |
| Stand age | Biological/inventory age used to schedule the age-based prescriptions and to apply the minimum harvest age. |
| +10 years | Ten years after the 2022 inventory, i.e. 2032. It does **not** mean stand age 10. |
| Removal percentage | Fraction of trees per acre (TPA) removed **within the stated diameter window**, across all species. It is not a prescribed percentage of stand volume, basal area, or land area. |
| Selection | Here, proportional removal across the diameter distribution. It is a simplified representation, not individual-tree selection to a target diameter distribution. |

# 3. Who receives which treatments?

Pine rotations are eligible only on the pine branch; the hardwood clearcut is eligible on hardwood and other/mixed branches. All other prescriptions accept any forest branch.

| **Owner class** | **TPO group** | **Default on pine** | **Default on hardwood / other** | **Eligible active prescriptions** |
| :- | :- | :- | :- | :- |
| Private industrial | Private | Short pine rotation | Hardwood clearcut | Short pine; long pine; hardwood clearcut |
| Other corporate private | Private | Long pine rotation | Family light thin | Long pine; family light thin; family selection |
| Family / small private | Private | Family light thin | Family light thin | Family light thin; family selection; long pine |
| Tribal | Private | Public selection | Public selection | Public selection; family light thin |
| Federal | Federal | Public selection | Public selection | Public selection; restoration thin |
| State | Other public | Restoration thin | Public selection | Restoration thin; public selection; long pine |
| Local (county / municipal) | Other public | Grow-only | Grow-only | Public selection; restoration thin |
| Unknown ownership | Private, flagged | Family light thin | Family light thin | Family light thin only |

**Corporate split:** the Harris raster has a single corporate value (4). The classifier treats all of it as **private industrial by default** and demotes a unit to other corporate only when parcel evidence (Florida DOR land-use code, parcel acreage, owner name) shows no industrial signal. The DOR code table is not yet verified against the parcel data, so in practice most corporate land currently resolves to industrial.

**Interpretation by owner:** Industrial ownership is represented by rotation forestry, with pulpwood versus sawtimber as the pine alternatives. Other corporate owners have a longer/lighter menu. Family ownership emphasizes infrequent partial entries, while allowing a managed pine subset to take a long rotation. Federal and tribal defaults retain cover through periodic selection. State pine has a restoration-thinning default and a sawtimber option. Local ownership defaults to no entry. Unknown ownership receives a deliberately narrow menu so an optimizer cannot invent aggressive management on unattributed land. These are declared policy rationales, not fitted owner-level probabilities.

**Important consequences:** no clearcut is eligible for federal, tribal, local, or unknown ownership under this policy. Family and other-corporate hardwood also lack a clearcut option. Family, other-corporate, and state clearcuts are available only through the long pine rotation. Industrial non-pine has just hardwood clearcut or grow-only after forest-type filtering.

## Ownership classification parameters

The Harris ownership raster provides the base ownership vocabulary: **0 unknown, 1 non-forest, 2 water, 3 family, 4 corporate, 5 tribal, 6 federal, 7 state, 8 local**. Non-forest and water are excluded from FVS rather than assigned grow-only. The policy specifies forest-area-majority ownership for a unit; the row-level classifier consumes the resulting code and does not itself perform this spatial aggregation. **Unknown ownership is charged to Private and reported separately.**

Forest branching accepts FIA FORTYPCD **140–179 as pine** and **500–998 as hardwood**; other values, including numeric oak/pine group 400–499 and unknown type, take the other branch.

# 4. Complete owner-policy prescription parameters

**All timings in this table are either target stand ages or explicit inventory offsets, as labeled.** Actual entries are rounded to 5-year cycles and deferred when needed to meet the minimum age of 15. Full prescription IDs are provided in Appendix A.

| **Prescription** | **Timing** | **Operation and intensity** | **Final harvest / rotation** |
| :- | :- | :- | :- |
| Grow-only | No entries | Grow existing forest | None |
| Short pine rotation | Target first thin age **15** | One thin; remove **40%** of TPA at DBH **0–8 in** | Target age **25**; remove **100%** at DBH **0–999 in** |
| Long pine rotation | Target first thin age **18** | One thin; remove **35%** of TPA at DBH **0–9 in** | Target age **35**; remove **100%** at DBH **0–999 in** |
| Hardwood clearcut | Target harvest age **50** | No prior thin | One final cut; remove **100%** at DBH **0–999 in** |
| Family light thin | **+10** (2032) | One thin; remove **35%** at DBH **0–8 in** | None; no rotation length |
| Family selection | **+15, +30, +45** (2037, 2052, 2067) | **15%** at DBH **0–999 in** each entry; **15-year** interval | None; continuous-cover intent |
| Public selection | **+10, +20, +30, +40** (2032, 2042, 2052, 2062) | **20%** at DBH **0–999 in** each entry; **10-year** interval | None; continuous-cover intent |
| Restoration thin | **+5, +20, +35** (2027, 2042, 2057) | **30%** at DBH **0–10 in** each entry; **15-year** interval | None; mechanical treatment only, no prescribed fire |

## Minimum age, cycle rounding, and missing ages

For an age-based operation:

**Years until entry = maximum of 5 and [target age − current stand age, rounded upward to a multiple of 5].**

Thus a short-rotation stand aged 22 at inventory is clearcut in **2027**, at approximately age **27**, not literally three years later. Its thin is dropped because the thin and clearcut resolve to the same cycle. Overmature stands also wait at least one cycle.

**Minimum age of 15 at treatment, thinnings included.** If a prescription's first entry would come before the stand reaches 15, the **whole sequence moves forward in 5-year steps**, keeping its intervals; entries after 2072 are then removed. Higher prescription age targets are unchanged. Examples: for an age-zero stand, family light thin moves from +10 to +15, and restoration thin moves from +5/+20/+35 to +15/+30/+45. A stand already aged five can still thin at +10. A prescription with no surviving entries renders as grow-only.

**Missing or unusable age now produces grow-only**, with the reason recorded. Inventory offsets are no longer used as a fallback when age is unknown.

**Scope:** the rule is enforced before FVS, in owner assignment and in scenario-library rendering. Dated experiments, saved schedules, and raw templates can bypass it, and changed dates require new FVS trajectories. Subsequent rotations need a new cohort age. A merchantable-size screen and the intended 25% harvestable-share screen are still not enforced.

# 5. Regeneration and repeated rotations

## Fallback templates for re-establishment are underdeveloped

The template defaults are shared across owners; there is no separate density or survival table by owner. A plantation template uses planting after its final cut.

| **Regeneration parameter** | **Current renderer default** |
| :- | :- |
| Planting species | **Loblolly pine (LP)**, one shared fallback default for Florida |
| Planted density | **605 trees/acre** |
| Natural density | **400 trees/acre total**, apportioned among species if composition is supplied |
| Survival | **100%** for both modes |
| Cohort age / height | **1 year / 0.5 ft** at establishment |
| Delay | **1 year after harvest** for both modes |
| Natural species allocation | Relative stand density index (SDI) shares of species already present, when stand_sdi is passed |
| Small-species cutoff | Drop species below **5% of SDI**, renormalize survivors; if all are below the cutoff, retain the largest contributor |
| Missing composition | Warn and use **LP** as the single natural-regeneration species |
| Automatic establishment | Plant packets include NoInGrow and NoAutAly when suppression is enabled; the register records related side effects for Natural |
| Caller overrides | Mode (plant, natural, none), species, density, survival, age, height, and delay are supported by renderer parameters; these are not owner-specific defaults |

Density, species choice, survival, and the SDI cutoff are assumptions needing review for stands regenerated from a template. Imputation and identical reestablishment are likely to be settled soon, with templates kept only as a fallback mechanism.

## Declared post-harvest regeneration settings

| **Prescription** | **Declared donor slot** | **Declared regeneration delay** | **Repeat flag** |
| :- | :- | :- | :- |
| Short pine rotation | planted_pine_regen | 1 year | Yes |
| Long pine rotation | planted_pine_regen | 1 year | Yes |
| Hardwood clearcut | hardwood_regen | 3 years | No |
| All partial entries / grow-only | None | None | No |

Those slots refer to a fixed-FIA-tree-list restart design. Planted-pine donors must have planted origin and age ≤10; hardwood donors natural origin and age ≤15. A natural-pine regeneration slot also exists (natural origin, age ≤15), but none of the eight current prescriptions selects it. Donors are drawn from FL/GA/AL, with at least ten candidates, selecting the lower median live-basal-area plot and breaking ties by string-valued PLT_CN. These are donor selection ages, not minimum harvest ages.

**Execution gaps that matter for interpretation:**

- The assignment object returns a donor-slot label, but does not pass the YAML's regeneration delay or repeat flag into template parameters. Current hardwood template regeneration therefore defaults to **1 year**, not the YAML's **3 years**, and the repeat flag does not generate additional rotations.
- When an older plantation's thin is dropped, the resolver changes its template to clearcut. That template defaults to **natural regeneration**, although its prescription label and donor slot still indicate a plantation. With no composition supplied, it falls back to LP at **400 TPA**, rather than planting LP at **605 TPA**.
- Plantation clearcuts plant LP at 605 TPA even when the harvested stand was, for example, entirely slash pine. Neither template mode restores a stand's original species-specific stocking.
- The scenario library (Appendix B) passes its harvests without explicit regeneration, so post-cut establishment there depends on FVS defaults. The identical-reestablishment path does not exist in production code yet.

# 6. Riparian protection in every scenario

| **Feature** | **Baseline configured width** | **Wider configured width** |
| :- | :- | :- |
| Ephemeral / intermittent stream | 35 ft | 70 ft |
| Small perennial stream | 50 ft | 100 ft |
| Large perennial stream | 75 ft | 150 ft |
| Waterbody | 75 ft | 150 ft |

Stream widths are measured on each side of the centerline; waterbody buffers surround the waterbody boundary. These are repository model settings. The scenario's wider widths require a changed spatial partition and regenerated management units; simply swapping harvest prescriptions leaves that part of the scenario unevaluated.

The owner-policy override is **SMZ_Pct ≥50 → grow-only**, ahead of owner treatment assignment and the minimum-age rule. The intended spatial preparation separates buffer polygons (SMZ_Pct=100) from managed polygons (SMZ_Pct=0). The owner-only menu function has no SMZ input; callers constructing a unit's candidate library must restrict riparian units to grow-only. Buffers remain in growth outputs and should retain separate unit IDs.

# 7. Timber targets and optimization parameters

Owners have different treatment menus, but the TPO inputs have only **three owner reporting groups**, not an independent volume target for every owner class, and no owner-group-by-county breakdown. Forward scheduling selects the **2013–2024** target series; the configured hindcast series is **pre-2015**. Annual targets are multiplied by five for each 5-year timestep.

| **Owner group** | **Annual target, ft³/year** | **Five-year target, ft³/cycle** |
| :- | -: | -: |
| Private | 70,994,800 | 354,974,000 |
| Federal (NF) | 2,023,500 | 10,117,500 |
| Other public | 4,486,900 | 22,434,500 |
| All owners | 77,505,200 | 387,526,000 |

| **County** | **Annual target, ft³/year** | **Five-year target, ft³/cycle** |
| :- | -: | -: |
| Baker | 11,451,200 | 57,256,000 |
| Columbia | 19,725,500 | 98,627,500 |
| Hamilton | 16,211,500 | 81,057,500 |
| Suwannee (Suwanee in source) | 22,474,700 | 112,373,500 |
| Union | 7,642,900 | 38,214,500 |
| All five counties | 77,505,800 | 387,529,000 |

The adopted optimization configuration treats TPO as an **even-flow target**, penalizing departures rather than imposing a hard ceiling. County and owner-group departures are scored together. Standing volume is a secondary maximize objective (weight 1, versus 6 for harvest even-flow).

# 8. Decisions for review

| **Decision** | **Why it affects the interpretation** |
| :- | :- |
| Choose one authoritative prescription/scenario library | Both libraries now share the age-15 floor, but their timings still differ. Establish rotation lengths and merchantable size requirements per owner class, repeat for thinnings, justify any treatment-specific age exceptions, and ground decisions in the intentions survey. |
| Decide clearcut eligibility by owner | Currently a default only for industrial, and an option for family, other corporate, and state through the long pine rotation. |
| Reconcile regeneration policy with emitted records | Decide densities, species, survival, delays, natural composition, identical reestablishment, and behavior of overmature plantation clearcuts. Verify establishment in FVS. |
| Settle constraints for scheduler | Keep county + owner-group even-flow, or source owner-group-by-county targets? Are spatial green-up constraints needed once units are real polygons? Other constraints or secondary objectives? |

**Output limits:** timber removals are the available scenario KPI. carbon_extension remains false, and no harvested-wood-products carbon pool is implemented in the scenario specification. NPV lacks agreed prices, costs, and a discount rate.

# Appendix A. Current prescriptions list

| **Short name used here** | **ID in management_regimes.yaml** |
| :- | :- |
| Grow-only | no_management |
| Short pine rotation | pine_plantation_short_rotation |
| Long pine rotation | pine_plantation_long_rotation |
| Hardwood clearcut | hardwood_clearcut_regen |
| Family light thin | family_light_thin |
| Family selection | family_uneven_aged_selection |
| Public selection | public_selection_light |
| Restoration thin | public_thin_restore |

# Appendix B. Separate scenario-library inventory

These scenarios have been defined but need to be mapped into current ownership classes; an NGO/conservation label here does not add an NGO class to the ownership vocabulary. **Every time is a planned inventory offset, before minimum-age deferral.** Percentages are TPA removed; DBH windows are inches; all species are included.

| **Regime ID** | **Complete operation sequence** |
| :- | :- |
| no_management | No operations |
| nipf_light | t+10: thin from below, 30% at 0–8 in; t+30: thin from below, 25% at 0–10 in |
| pine_plantation_industrial | t+10: thin from below, 40% at 0–8 in; t+25: regeneration harvest, 100% at 0–999 in |
| hardwood_industrial | t+20: regeneration harvest, 100% at 0–999 in |
| public_uneven_aged | t+10: selection, 15% at 0–999 in; t+25: selection, 15% at 0–999 in; t+40: selection, 15% at 0–999 in |
| public_active_thinning | t+5: thin from below, 30% at 0–10 in; t+20: selection, 20% at 0–999 in; t+35: selection, 20% at 0–999 in |
| conservation_restoration | t+10: thin from below, 40% at 0–6 in; t+30: thin from below, 30% at 0–6 in |
| custodial_light | t+20: thin from below, 15% at 0–8 in |
| light_default | t+15: thin from below, 25% at 0–8 in |
| pine_plantation_extended | t+10: thin from below, 40% at 0–8 in; t+25: thin from below, 25% at 0–12 in; t+40: regeneration harvest, 100% at 0–999 in |
| pine_plantation_retention | t+10: thin from below, 40% at 0–8 in; t+25: retention harvest, 85% at 0–999 in |
| pine_plantation_extended_retention | t+10: thin from below, 40% at 0–8 in; t+25: thin from below, 25% at 0–12 in; t+40: retention harvest, 85% at 0–999 in |
| hardwood_extended | t+40: regeneration harvest, 100% at 0–999 in |
| hardwood_retention | t+20: retention harvest, 85% at 0–999 in |
| hardwood_extended_retention | t+40: retention harvest, 85% at 0–999 in |
