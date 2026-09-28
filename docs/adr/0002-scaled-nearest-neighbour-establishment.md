# Scaled nearest-neighbour establishment for added-back forest

**Status:** accepted, 2026-09-28

## Context

The five-county repair adds 74,068 ac of forest back to TreeMap 2022. Each accepted
patch takes the modal TreeMap plot in the ring around it as a donor. Until now the
donor's own tree rows were the patch's establishment list. Most of that land was cut
or is regrowing, and the donors are standing plots. On the 72,469 ac we now scale, the
donors carry 80 ft²/ac of live basal area, so the verbatim list put mature biomass on
cut ground. The regeneration gotcha in `AGENTS.md` names the same trap: resetting age
alone restores mature DBH and height.

## Decision

**Scaled nearest neighbour.** Donor selection does not change. The donor gives the
**forest type** (TreeMap VAT `FORTYPCD`, grouped as in `young_stand_profiles.ForestTypeGroup`)
and the **species mix** (share of live TPA by `SPCD`). **Density and tree size** come from
an age-5 profile of that forest type group, measured on FIA plots. The scaled list has
one row per donor species: `TPA_UNADJ` = profile TPA × species share, `DIA` and `HT`
from the profile, `STATUSCD` 1, `CR` left to FVS. Policy: `config/establishment.yaml`.
Code: `impute_establishment.scaled_establishment_lists`.

**Mode per LANDFIRE bookend stratum** (`EstablishmentMode`):

| Stratum | Evidence | Mode | Raster provenance |
|---|---|---|---|
| S1 | logged 2016 → tree 2024 | `scaled_young` | 4 `ADDED_BACK_YOUNG` |
| S2 | tree 2016 and 2024 | `donor_as_is` | 2 `ADDED_BACK` |
| S3 | tree 2016 → open 2024 | `scaled_young` | 4 |
| S4 | open 2016 → tree 2024 | `scaled_young` | 4 |
| S5, or none | no evidence (reaches add-back only through a disturbance detector) | `scaled_young` | 4 |

Provenance 4 matters. A consumer that joins the improved raster's `TM_ID` to the TreeMap
tree table would silently pull mature biomass. On provenance 4 the `TM_ID` names the
donor for type and species mix only; the trees are the `scaled_young` rows of
`establishment_tree_lists.csv`, keyed by `(TM_ID, establishment_mode)`.

**Density source** (`EstablishmentDensity`): `forest_type_profile` (default) or `donor`,
which keeps the donor's live TPA by species and still resets size. The profile is the
default because a mature donor's TPA is what survived decades of mortality, thinning and
sapling ingrowth, not a planting density. The profile's TPA is measured at the target age.

**Target age** 5 (`target_age`). Profiles come from FIA plots with `STDAGE` 3–7.

## Profile source

FIA SQLite (`SQLite_FIADB_ENTIRE.db`), Alabama, Florida and Georgia (`STATECD` 1, 12, 13).
Accessible-forest conditions (`COND_STATUS_CD` 1) that cover the whole plot
(`CONDPROP_UNADJ` ≥ 0.999) on national-design plots (`DESIGNCD` 1), 1997–2024: 3,188 plots.
The full `TREE` scan over the data drive takes about four minutes, so the preferred source
was used; no TreeMap fallback was needed. Per group: median plot live TPA (`STATUSCD` 1,
`DIA` ≥ 1 in), TPA-weighted quadratic mean DBH and mean height. Seedling TPA (`DIA` < 1 in)
is measured and recorded but not added: TreeMap's own tree lists carry no seedling rows.
A group under 10 plots takes its pool (softwood, hardwood, then all plots).

| Group | TPA | DBH in | HT ft | FIA plots |
|---|---|---|---|---|
| Longleaf / slash pine | 381 | 2.94 | 15.7 | 532 |
| Loblolly / shortleaf pine | 471 | 2.75 | 16.7 | 1,203 |
| Oak / pine | 332 | 2.74 | 17.8 | 452 |
| Oak / gum / cypress | 237 | 2.96 | 20.0 | 174 |
| Other hardwood | 279 | 3.00 | 20.1 | 824 |
| Other (3 plots: all-plot pool) | 375 | 2.84 | 17.7 | 3,188 |

Committed numbers, query and filters: `config/young_stand_profiles.yaml`. Regenerate with
`uv run python -m pipeline.s1_initial_state.young_stand_profiles`.

## Consequences

- AOI added-back live basal area falls from 5.94M ft² (80.2 ft²/ac) with mature donors to
  1.36M ft² (18.4 ft²/ac) as established. Live TPA falls from 469 to 381. On the scaled
  acres alone: 80.3 → 17.1 ft²/ac (`aoi_5county/summary.json`, `establishment.live_effect`).
- 72,469 ac are `scaled_young` and 1,599 ac `donor_as_is`. 191 donors give 684 scaled rows;
  50 S2 donors give 1,981 verbatim rows.
- Every scaled stand starts at age 5, whatever year it was cut. S1 patches, logged by
  2016, are at least six years old in 2022.
- `statewide_repair` still writes verbatim donor lists and its own 0/1 provenance. It has
  not adopted the modes.

## Alternatives considered

- **Verbatim donor** (the previous behaviour): mature biomass on cut ground. Kept only for S2.
- **Prescribed planting by owner class** (for example corporate slash pine at a set
  density): needs a planting density and species per owner class that we have not
  measured, and it would ignore the local forest type the neighbour gives.
- **Feature-space donor** (nearest AlphaEarth embedding, cosine ≥ 0.9): picks a plot that
  looks alike, which for a cut patch is still a standing plot. It would change the donor
  but not the mature-biomass problem. It stays open as a better type and species source.
- **Cut-year age from the Obata detector**: once Obata runs, a dated cut gives each patch
  its own age (2022 minus cut year) instead of a fixed 5. The profile would then be indexed
  by age, not measured at one age. That is the planned follow-up.
