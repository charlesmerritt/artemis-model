# Reference papers — the two core guides

These two documents are the **primary methodological guides** for ARTEMIS v1. The
architecture in [`notes/trajectory-library-and-annealing.md`](../../notes/trajectory-library-and-annealing.md)
is built to follow them, and the rest of the documentation points here rather than
restating them.

| Ref key | Document | Guides |
|---|---|---|
| `CLIMATE-FVS` | Diaz et al. (2015), Ecotrust — Climate-FVS simulation of western Oregon BLM forestlands | **The end-to-end precedent**: batch FVS over all eligible prescriptions per stand, then simulated-annealing scheduling |
| `LAMPS` | Bettinger & Lennette et al. — Landscape Management Policy Simulator | Eligibility screening, adjacency/green-up, and heuristic harvest scheduling |

## Status of the files

| Filename | Status |
|---|---|
| `Climate_FVS_Simulation_Report_20150306_SUBMITTED.pdf` | ✅ **Committed** (7.4 MB, 61 pp.), supplied 2026-08-06. |
| `LAMPS_Bettinger_et_al.pdf` | **Not present.** Referred to throughout the repo but never committed; it lives on the author's `/mnt/d` workstation drive, which is not mounted in web sessions. Drop it here under this filename. |

The Climate-FVS report could not be downloaded from
`john-bell-associates.com` — that host is denied by the Claude Code egress policy and the
proxy answers `403` to the CONNECT, for both `curl` and the fetch tool. The same class of
block is documented for `r2.cloudflarestorage.com` in
[`notes/claude-code-web-environment.md`](../../notes/claude-code-web-environment.md). The
file was supplied directly instead. If it needs re-fetching in a future session, either
commit it locally as here, or have an admin allowlist the host.

## What each reference contributes

### `CLIMATE-FVS` — Diaz, Perry, Tutak, Hodges & Mertens (2015)

> Diaz, D., Perry, M., Tutak, J., Hodges, R. and Mertens, M. (2015). *Potential climate
> change impacts on management outcomes for western Oregon BLM forestlands simulated using
> Climate-FVS.* Report to the Bureau of Land Management, in partial fulfillment of
> Cooperative Assistance Agreement #L10AC20425. Ecotrust, Portland, OR. March 6, 2015.
> 61 pp.

**This is the end-to-end precedent for the ARTEMIS architecture.** Ecotrust built the same
two-stage system — batch-simulate every eligible management alternative for every stand,
then select among the precomputed results with simulated annealing — and published the code
for both stages. Read §"Methods for modeling forest management under climate change"
(pp. 14–28) before implementing anything in `PLAN.md` §4.

Their pipeline, and how ARTEMIS maps onto it:

| Diaz et al. (2015) | ARTEMIS |
|---|---|
| 40-acre grid cells clipped to BLM ownership, small remnants merged; "management units (used interchangeably with 'stands')" | Parcel-derived management units + `sliver_merge.py`. Same term, same role |
| Inventory imputed to stands by Gradient Nearest Neighbor (GNN/LEMMA) | TreeMap 2022 imputed FIA plots — the same imputation-initialized design |
| **"Identifying prescription zones"**: land classification (Critical Habitat, wilderness/ACEC/RNA, stream buffers) determines which prescriptions may be applied | **Ownership class** determines the eligible set (`config/prescriptions.yaml`) |
| Grow-only simulated for *all* classifications; exclusion areas and stream buffers get no active management; Critical Habitat limited to complex thinning or patch cuts | `no_management` in every library; riparian library is `{no_management}` only; public classes exclude clearcut |
| Six prescriptions: grow only; 80-yr rotation; 100(+)-yr rotation; thin every 20–25 yr; complex-structure thinning; patch cut | Five families: `no_management`, `clearcut`, `thin_from_below`, `selection_harvest`, `plantation_rotation` |
| **"Offsets"** delaying the first activity by 5, 10, or 15 years, "to offer choices to the optimization model" | `*_offset` parameter grids in `config/prescriptions.yaml` — the same idea, same purpose |
| Batch system rendering keyfiles for every prescription × offset × emissions scenario × GCM; distributed, fault-tolerant, parallel FVS; results parsed to SQLite | Library generation, `PLAN.md` §4c; results to DuckDB/Parquet |
| Simulated-annealing scheduler over "all possible combinations of prescriptions and timing for every stand" | `PLAN.md` §4d |
| 100-year horizon, 5-year timestep, FFE for carbon and fire metrics | 50-year horizon, 5-year cycles; FFE currently off |

**Published code** (both referenced in the report, worth reading before building ours):

- Batch growth-and-yield system: `https://github.com/Ecotrust/growth-yield-batch`
  (prescription keyfile code for this study is under `projects/BLMClimate/rx`)
- Scheduler: `https://github.com/Ecotrust/harvest-scheduler`

**Four objective forms** the scheduler supports (§"Scheduling model", p. 27–28) — a cleaner
taxonomy than a bare objective-plus-penalties split, and adopted in
`config/projection.yaml`:

| Form | Meaning |
|---|---|
| `maximize` | Maximize a metric across the study area (e.g. carbon storage) |
| `minimize` | Minimize a metric (e.g. acres of high fire hazard, harvest cost) |
| `evenflow` | Minimize the standard deviation of a metric across periods |
| `evenflow_target` | Minimize variation around a set target, which may be a single value or a range, **and may vary over time** |

Weights set relative priority. In their run, timber yield carried a **6× weight** and every
other objective 1×, so the scheduler "will first and foremost attempt to achieve harvest
targets and will try to minimize/maximize the other objectives within that constraint."
Objectives used: timber yield (evenflow-target, 502 mmbf/yr), carbon storage (max), acres of
high fire hazard (min), spotted-owl habitat acres (max), and a harvest/transport cost proxy
of board-foot volume × slope (min).

**The pitfall they document, and ARTEMIS should not repeat.** All their targets were set at
a *global* level, leaving the scheduler free to shift harvest between BLM Districts to hit
the landscape total. It did: harvest concentrated into the Salem District and away from Coos
Bay, Roseburg and Medford as climate scenarios worsened, visible only in district-level
figures. Their own footnotes flag this as a departure from practice — BLM defines allowable
sale quantities by Sustained-Yield Unit and does not shift them between units. ARTEMIS's TPO
caps are already dimensioned by **county and owner group** rather than a single landscape
total, which is the right side of this lesson; keep them that way, and report per-dimension
outcomes, not just the total.

**A validation output worth copying.** Their Figure 16 reports the *prescription mix the
scheduler chose* — 18% regeneration harvest, 25% no active management, 58% thinning or patch
cut under no climate change, shifting to 44–66% regeneration harvest under high emissions.
That distribution is a first-class result, not a diagnostic: it is how the plan's behaviour
is read. ARTEMIS should report the selected-prescription mix by ownership class the same way.

**Scope note.** Climate-modified growth is out of ARTEMIS v1 scope (`PLAN.md` scope notes),
so the Climate-FVS extension itself and the GCM/emissions axes of their design are *not*
adopted. Their own Key Findings are candid that Climate-FVS behaviour "justif[ies] further
evaluation before integrating this model directly into management planning" and needs
comparison against other models and field validation — which supports leaving it out of v1.
What ARTEMIS takes is the **architecture**, which is independent of the climate extension.

### `LAMPS` — Bettinger & Lennette, Landscape Management Policy Simulator

Landscape-scale forest policy simulation, developed at the Oregon State University Forest
Research Laboratory as part of the Coastal Landscape Analysis and Modeling Study (CLAMS),
a joint USFS PNW Research Station / OSU College of Forestry / Oregon Department of
Forestry effort. Bettinger's broader body of work on **heuristics in forest planning** —
simulated annealing, tabu search, and genetic algorithms applied to spatially constrained
harvest scheduling — is the direct lineage for the ARTEMIS scheduler. Diaz et al. above is
that lineage in working, published form; LAMPS supplies the constraint machinery their
study did not need.

What ARTEMIS takes from it:

- **Eligibility screening** — minimum harvest age (MHA) and minimum harvestable percentage
  (MHP) decide *whether and when* a unit can be cut. In ARTEMIS these are applied at
  library-build time, so ineligible prescriptions are never offered to the scheduler.
- **Adjacency and blocking** — unit restriction model (URM) and area restriction model
  (ARM) for green-up and maximum opening size. **This is the main gap in the Diaz et al.
  design for our purposes**: their scheduler optimizes global metrics with no spatial
  adjacency constraint, so nothing there stops selected regeneration harvests from abutting.
  ARTEMIS prices adjacency as a penalty and needs a block move because of it.
- **Heuristic scheduling** — the precedent for choosing simulated annealing over exact
  optimization at landscape scale, and for the discipline of reporting solution quality
  (baselines, bounds, seed spread) rather than presenting a heuristic result as optimal.
- **Ownership-differentiated behaviour** — LAMPS distinguishes industrial from public
  owners. ARTEMIS generalizes this to the seven Harris et al. (2025) forest ownership
  classes, one prescription library each.

Already staged against this reference:
[`docs/superpowers/plans/2026-07-28-lamps-scheduler-integration.md`](../superpowers/plans/2026-07-28-lamps-scheduler-integration.md)
(the `harvest_eligibility.py` / `adjacency.py` port) and
[`config/prescriptions.yaml`](../../config/prescriptions.yaml) (`eligibility_screens`).

> **Citation to verify against the PDF.** Best available identification from open sources:
> Bettinger, P. and Lennette, M. (2004), *Landscape Management Policy Simulator (LAMPS),
> Version 1.1 User Guide*, Forest Research Laboratory, Oregon State University. Confirm
> authors, year, and edition from the file itself before citing it in a manuscript — the
> repo has been carrying "Bettinger et al." informally since 2026-07-20 without a
> verifiable record.

## Citing these in the docs

Use the ref keys `LAMPS` and `CLIMATE-FVS` and link back to this README, so there is one
place to fix when the files land and the citations are verified. Notes that currently
point here:

- [`notes/trajectory-library-and-annealing.md`](../../notes/trajectory-library-and-annealing.md) — architecture of record
- [`notes/management-pipeline-plan.md`](../../notes/management-pipeline-plan.md) — build phases
- [`PLAN.md`](../../PLAN.md) — §3c, §4
- [`README.md`](../../README.md) — primary datasets and references
