# How each ownership class is managed

The regime menus, per owner class, as configured in
[`config/management_regimes.yaml`](../config/management_regimes.yaml) and
[`config/ownership_policy.yaml`](../config/ownership_policy.yaml).

Reading the tables:

- **Owner classes** are the Harris raster's forest classes
  (`config/ownership_policy.yaml`): family (3), corporate (4), tribal (5), federal (6),
  state (7), local (8), unknown (0).
- **Forest-type branches.** Pine is FORTYPCD 140–179. Hardwood is 500–998 (plus the
  hardwood words: oak, hickory, gum, cypress, …). Everything else — 400–499 and unknown
  type — is the `other` branch.
- **Roles.** *Always available* is `no_management`, eligible for every owner class
  everywhere (any owner may decline to harvest). *Default* is what
  `pipeline/s3_management/regime_assignment.py` assigns deterministically today.
  *Optimizer option* is the rest of the eligible menu, which the landscape scheduler
  chooses from once the trajectory library exists.
- **Entry years.** `+N` is an offset from the 2022 inventory year, snapped up to a
  5-year cycle boundary. `age N` is a target stand age; entry lands
  `N − stand_age` years out, also snapped, and falls back to the offsets when the
  stand has no age. Selection entries repeat every `interval` years from `start_year`
  to `end_year`. Entries past the 50-year horizon are dropped.
- **DBH windows** are thin-from-below caps in inches; `0–999` means no DBH restriction.
- **Regeneration** after a stand-replacing entry restarts the stand from a fixed
  donor tree list (`config/fallback_treelists.yaml`), not FVS PLANT/NATREGEN keywords.
- **Riparian override.** Units with SMZ ≥ 50% get `no_management` regardless of owner
  class, forest type, or scheduler choice. No buffer class is exempted.

---

## Family forest (Harris 3)

Small private holdings. Large in aggregate, small per owner; harvest is irregular and
income-driven, so the default is a single light entry and the rotation option is there
for the managed minority of family pine.

**Pine (FORTYPCD 140–179)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 1 | Grow-only | Always available | - | - |
| 2 | Family light thin | Default | +10; 35%; DBH 0–8 in | - |
| 3 | Family selection | Optimizer option | +15 / +30 / +45; 15%; DBH 0–999 in | - |
| 4 | Long pine rotation | Optimizer option | age 18; 35%; DBH 0–9 in | age 35; 100%; DBH 0–999 in; replant loblolly ~605 TPA, 1-yr delay, repeat |

**Hardwood / other (500–998, plus 400–499 and unknown)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 5 | Grow-only | Always available | - | - |
| 6 | Family light thin | Default | +10; 35%; DBH 0–8 in | - |
| 7 | Family selection | Optimizer option | +15 / +30 / +45; 15%; DBH 0–999 in | - |

## Corporate / other private (Harris 4)

Rotation forestry. The pulpwood and sawtimber rotations are the real economic choice the
scheduler makes per stand. Includes not-for-profits and institutions by the Harris
definition, which is why grow-only stays available.

**Pine (FORTYPCD 140–179)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 1 | Grow-only | Always available | - | - |
| 2 | Industrial pine plantation (pulpwood rotation) | Default | age 15; 40%; DBH 0–8 in | age 25; 100%; DBH 0–999 in; replant loblolly ~605 TPA, 1-yr delay, repeat |
| 3 | Pine plantation — sawtimber rotation | Optimizer option | age 18; 35%; DBH 0–9 in | age 35; 100%; DBH 0–999 in; replant loblolly ~605 TPA, 1-yr delay, repeat |

**Hardwood / other (500–998, plus 400–499 and unknown)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 4 | Grow-only | Always available | - | - |
| 5 | Hardwood / mixed clearcut regen | Default | - | age 50; 100%; DBH 0–999 in; natural regeneration (hardwood donor list), 3-yr delay |

A stand-replacing harvest with natural regeneration is the type-agnostic option;
planting pine on a stand that may not be pine is the guess the forest-type filter exists
to prevent. Falls back to +30 when the stand has no age.

## Tribal forest (Harris 5)

Placeholder policy, not a finding: pilot-scale tribal area is below the 500-event floor,
so the class uses the conservative public menu rather than a class-specific estimate.

**Pine (FORTYPCD 140–179) and hardwood / other (500–998, plus 400–499 and unknown)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 1 | Grow-only | Always available | - | - |
| 2 | Public selection light | Default | +10 / +20 / +30 / +40; 20%; DBH 0–999 in | - |
| 3 | Family light thin | Optimizer option | +10; 35%; DBH 0–8 in | - |

## Federal forest (Harris 6)

Effectively Osceola National Forest in the pilot, where density reduction in pine is the
characteristic treatment. The TPO group is "Federal (NF)"; non-NF federal land mapped
here overstates that cap (see `known_limitations.federal_non_nf`).

**Pine (FORTYPCD 140–179) and hardwood / other (500–998, plus 400–499 and unknown)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 1 | Grow-only | Always available | - | - |
| 2 | Public selection light | Default | +10 / +20 / +30 / +40; 20%; DBH 0–999 in | - |
| 3 | Public thin-and-restore (thinning only) | Optimizer option | +5 / +20 / +35; 30%; DBH 0–10 in | - |

Prescribed fire is not modelled in the thin-and-restore option: the FVS fire keywords
are unverified in this project and FFE state is known corrupted across restart barriers.
The regime is the mechanical thinning only.

## State forest (Harris 7)

State forests carry an active timber program alongside conservation objectives, so pine
defaults to the density-reduction thinning rather than light selection, and a sawtimber
rotation stays on the menu.

**Pine (FORTYPCD 140–179)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 1 | Grow-only | Always available | - | - |
| 2 | Public thin-and-restore (thinning only) | Default | +5 / +20 / +35; 30%; DBH 0–10 in | - |
| 3 | Public selection light | Optimizer option | +10 / +20 / +30 / +40; 20%; DBH 0–999 in | - |
| 4 | Long pine rotation | Optimizer option | age 18; 35%; DBH 0–9 in | age 35; 100%; DBH 0–999 in; replant loblolly ~605 TPA, 1-yr delay, repeat |

**Hardwood / other (500–998, plus 400–499 and unknown)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 5 | Grow-only | Always available | - | - |
| 6 | Public selection light | Default | +10 / +20 / +30 / +40; 20%; DBH 0–999 in | - |
| 7 | Public thin-and-restore (thinning only) | Optimizer option | +5 / +20 / +35; 30%; DBH 0–10 in | - |

## Local forest (Harris 8)

County and municipal forest — parks, watershed land, school-board holdings.
Predominantly non-commercial, so the default is no entry at all; the scheduler can opt
in to light selection or a density-reduction thin where a county does manage its forest.

**Pine (FORTYPCD 140–179) and hardwood / other (500–998, plus 400–499 and unknown)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 1 | Grow-only | Default (and always available) | - | - |
| 2 | Public selection light | Optimizer option | +10 / +20 / +30 / +40; 20%; DBH 0–999 in | - |
| 3 | Public thin-and-restore (thinning only) | Optimizer option | +5 / +20 / +35; 30%; DBH 0–10 in | - |

## Unknown forest (Harris 0)

Not an owner type — missing information. Deliberately minimal menu: with no owner known,
letting the scheduler place a rotation clearcut here would be inventing behaviour to hit
a volume target. The shortfall lands on known private units instead, where it is
attributable. Every unit here carries the `unknown_ownership` flag.

**Pine (FORTYPCD 140–179) and hardwood / other (500–998, plus 400–499 and unknown)**

| # | Regime | Role | Thinning (year; % TPA removed; DBH window) | Final harvest (year; % TPA removed; DBH window; regeneration) |
|---|--------|------|--------------------------------------------|----------------------------------------------------------------|
| 1 | Grow-only | Always available | - | - |
| 2 | Family light thin | Default | +10; 35%; DBH 0–8 in | - |

---

## Regime parameter reference

The named regimes above, with their library definitions
(`config/management_regimes.yaml`):

| Regime | Template | Schedule | Entries | Regeneration |
|--------|----------|----------|---------|--------------|
| Grow-only | `no_management` | none | 0 | none |
| Family light thin | `thin_from_below` | offset +10 | 1 | none |
| Family selection | `selection_harvest` | offsets +15 → +45, interval 15 | 3 | none |
| Long pine rotation | `plantation_rotation` | age-based, thin 18 / rotation 35 | 2 per rotation | planted pine, 1-yr delay, repeats |
| Industrial pine plantation | `plantation_rotation` | age-based, thin 15 / rotation 25 | 2 per rotation | planted pine, 1-yr delay, repeats |
| Hardwood / mixed clearcut regen | `clearcut` | age-based, rotation 50 | 1 | natural hardwood, 3-yr delay |
| Public selection light | `selection_harvest` | offsets +10 → +40, interval 10 | 4 | none |
| Public thin-and-restore | `thin_from_below_repeated` | offsets +5 → +45, interval 15 | 3 | none |

Library cost: 8 prescriptions × 693 pilot plots × 3 site-index bins = 16,632 maximum FVS
runs before eligibility pruning (`si_bins`, `estimated_max_runs_pilot` in
`config/management_regimes.yaml`).
