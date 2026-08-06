# Reference papers — the two core guides

These two documents are the **primary methodological guides** for ARTEMIS v1. The
architecture in [`notes/trajectory-library-and-annealing.md`](../../notes/trajectory-library-and-annealing.md)
is built to follow them, and the rest of the documentation points here rather than
restating them.

| Ref key | Document | Guides |
|---|---|---|
| `LAMPS` | Bettinger & Lennette et al. — Landscape Management Policy Simulator | Eligibility screening, adjacency/green-up, and heuristic harvest scheduling |
| `CLIMATE-FVS` | Climate-FVS Simulation Report (GMUG, 2015-03-06 submitted draft) | Running FVS to produce alternative management trajectories per stand |

## Status of the files

**Neither PDF is committed yet.** Drop them in this directory using the filenames below
and they will be picked up by every reference in the docs.

| Expected filename | Status |
|---|---|
| `LAMPS_Bettinger_et_al.pdf` | **Not present.** Referred to throughout the repo but never committed; it lives on the author's `/mnt/d` workstation drive, which is not mounted in web sessions. |
| `Climate_FVS_Simulation_Report_2015-03-06_SUBMITTED.pdf` | **Not present — download blocked.** See below. |

### Why Climate-FVS could not be fetched automatically

Source URL, as supplied 2026-08-06:

```
https://john-bell-associates.com/gmug/2016/Climate_FVS_Simulation_Report_2015-03-06_SUBMITTED.pdf
```

`john-bell-associates.com` is **denied by the Claude Code egress policy** for this
session — the proxy answers `403` to the CONNECT, for both `curl` and the fetch tool.
This is the same class of block already documented for `r2.cloudflarestorage.com` in
[`notes/claude-code-web-environment.md`](../../notes/claude-code-web-environment.md). Two ways
to resolve it, in order of preference:

1. Download the PDF locally and commit it to this directory. It is a few MB, well under
   the 99 MiB pre-commit hook limit.
2. Ask an admin to allowlist the host in the Claude Code environment's network policy,
   then re-run the download from a web session.

## What each reference contributes

### `LAMPS` — Bettinger & Lennette, Landscape Management Policy Simulator

Landscape-scale forest policy simulation, developed at the Oregon State University Forest
Research Laboratory as part of the Coastal Landscape Analysis and Modeling Study (CLAMS),
a joint USFS PNW Research Station / OSU College of Forestry / Oregon Department of
Forestry effort. Bettinger's broader body of work on **heuristics in forest planning** —
simulated annealing, tabu search, and genetic algorithms applied to spatially constrained
harvest scheduling — is the direct lineage for the ARTEMIS scheduler.

What ARTEMIS takes from it:

- **Eligibility screening** — minimum harvest age (MHA) and minimum harvestable percentage
  (MHP) decide *whether and when* a unit can be cut. In ARTEMIS these are applied at
  library-build time, so ineligible prescriptions are never offered to the scheduler.
- **Adjacency and blocking** — unit restriction model (URM) and area restriction model
  (ARM) for green-up and maximum opening size. In ARTEMIS these are priced as scheduler
  penalties and drive the block-move operator.
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

### `CLIMATE-FVS` — Climate-FVS Simulation Report (GMUG, 2015)

An applied FVS simulation study for the Grand Mesa, Uncompahgre and Gunnison National
Forests, using the Climate-FVS extension.

> **Content not yet transcribed.** The PDF could not be retrieved in this session, so
> nothing here summarizes its methods. Once the file is committed, replace this paragraph
> with the specifics ARTEMIS is meant to follow — how alternatives are constructed per
> stand, which keyword sets are used, how cycles and horizons are configured, and how
> results are aggregated to the landscape. Do not paraphrase it from memory or from the
> title; the whole point of committing the file is that the methods claim is checkable.

Expected relevance, stated as the reason it was chosen rather than as a finding: it is the
worked example of **running FVS to produce alternative management trajectories per stand**,
which is exactly the library-generation half of the ARTEMIS architecture (`PLAN.md` §4c).
Its Climate-FVS component also bears on the "climate scenario" axis sketched in
[`artemis.txt`](../../artemis.txt), which is currently **out of v1 scope** (`PLAN.md`
scope notes) — reading it should settle whether that stays out.

## Citing these in the docs

Use the ref keys `LAMPS` and `CLIMATE-FVS` and link back to this README, so there is one
place to fix when the files land and the citations are verified. Notes that currently
point here:

- [`notes/trajectory-library-and-annealing.md`](../../notes/trajectory-library-and-annealing.md) — architecture of record
- [`notes/management-pipeline-plan.md`](../../notes/management-pipeline-plan.md) — build phases
- [`PLAN.md`](../../PLAN.md) — §3c, §4
- [`README.md`](../../README.md) — primary datasets and references
