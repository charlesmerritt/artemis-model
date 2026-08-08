# Scenario Framework — Following Diaz et al. (2018)

Aligning ARTEMIS with the Ecotrust tradeoff-analysis design. Machine-readable form:
[`config/scenarios.yaml`](../config/scenarios.yaml).

**Reference.** Diaz, D.D., Loreno, S., Ettl, G.J., Davies, B. (2018). *Tradeoffs in
Timber, Carbon, and Cash Flow under Alternative Management Systems for Douglas-Fir in the
Pacific Northwest.* Forests 9(8), 447. [doi:10.3390/f9080447](https://doi.org/10.3390/f9080447)

> **Sourcing caveat.** Every full-text mirror of this paper — mdpi.com, ecotrust.org,
> semanticscholar.org, researchgate.net, crossref, the Semantic Scholar API — is blocked
> by this environment's egress policy. The structure below is reconstructed from two
> independent search-result summaries that agree with each other. **Exact rotation ages,
> retention percentages, and buffer widths from the paper were not retrievable**, so the
> numbers in our config are our own choices, not transcriptions of theirs. Someone with
> the PDF should check §2 against it before this framing goes into a writeup.

---

## What they did

Three key performance indicators — **average carbon storage** (in forest *and* harvested
wood products), **cumulative timber output**, and **discounted cash flow** — computed for
**four management scenarios** across 64 parcels in western Oregon and Washington.

The four scenarios are a 2×2 over two design axes:

| | **FPA minimum** | **FSC certified** |
|---|---|---|
| **Maximise NPV** (short rotation) | BAU | SHORT~FSC |
| **Maximise sustained yield** (long rotation) | LONG~FPA | LONG~FSC |

- **Objective axis** — maximise net present value, or maximise sustained timber yield.
  In practice this is rotation length.
- **Constraint axis** — comply with the minimum Oregon/Washington Forest Practices Act
  rules, or meet FSC certification requirements: more green-tree retention and wider
  riparian buffers.

Headline finding: practices that leave more and bigger trees standing for longer deliver
measurable carbon benefits, but generally at the cost of NPV and timber yield. The FSC
scenarios showed consistently higher carbon storage *per unit of timber produced*.

## The structural difference from what we built

This is the part worth absorbing.

**What we have now:** each ownership class gets one regime. Ownership determines
management. The output is a single projected landscape.

**What they do:** scenarios are alternatives applied to *everything*, and every parcel is
run under *every* scenario. Ownership is not the organising principle — the scenario is.
That is what produces a tradeoff frontier instead of a point estimate. You cannot say
"extending rotations costs X NPV and gains Y carbon" from one projection; you need the
counterfactual on the same ground.

The good news is that the repo was already heading here and the pieces line up:

- `PLAN.md` §4c — trajectory library keyed on `(plot, regime, site-index bin)`, i.e. run
  every stand under every regime
- `artemis.txt` — "stand × eligible management prescription × treatment timing/offset ×
  climate scenario", then a landscape policy layer selects one trajectory per stand
- `config/management_regimes.yaml` already carries `eligible_regimes` per owner class,
  which is exactly "which prescriptions may this stand consider"

So the owner-class work is not wasted, and it does not become the wrong abstraction. It
becomes **the BAU scenario** — the reference case describing what is plausibly happening
now — and the other three arms are counterfactuals against it.

---

## The Florida translation

Both axes carry over. The constraint axis needs a vocabulary swap, since the Oregon
Forest Practices Act and FSC do not apply here.

| | **Florida BMP minimum** | **Certified** |
|---|---|---|
| **Short rotation** | `bau` | `short_certified` |
| **Extended rotation** | `long_bmp` | `long_certified` |

- **Objective** — corporate rotations at 25 (pine) / 20 (hardwood), versus extended to 40
  with a second commercial thin.
- **Practice constraint** — SMZs at the Florida Forest Service 2020 minimums already in
  `config/bmp_rules.yaml` (35/50/75 ft) with full-removal regeneration harvests, versus
  15% green-tree retention and roughly doubled SMZ widths.

`short_certified` and `long_bmp` each differ from BAU on exactly one axis, so each
isolates its own effect; `long_certified` moves both. `tests/test_s4_scenarios.py`
enforces that, and enforces that the "certified" arms actually retain trees while the
"BMP minimum" arms actually do not — a scenario table that has quietly stopped varying
what it claims to vary is worse than none.

### Retention is expressible today; buffer width is not

Green-tree retention needs no new FVS keyword. A regeneration harvest that removes 85%
across the full diameter range *is* 15% retention, and `ThinDBH` renders it — so the
retention axis costs nothing beyond the four new regime definitions in
`config/regimes.yaml`.

**The buffer axis is different and more expensive than it looks.** Widening SMZs changes
which acres are riparian, so it changes the stand polygons themselves.
`sketch_management_units.py` has to re-run and LETO has to re-segment before that arm
means anything — it cannot be evaluated from the existing trajectory library. And it is
likely a large part of why the certified arm stores more carbon, since wider buffers move
area out of a managed owner class into unconditional `no_management`. Reporting a
certified scenario with BMP-minimum geometry would understate the effect while looking
complete.

---

## Two of the three KPIs are blocked

We can compute one of Diaz et al.'s three indicators. This is the honest state:

| KPI | Status | Why |
|---|---|---|
| Cumulative timber output | **available** | Falls out of the FVS cut list; the scheduler already tracks `volume_removed` |
| Average carbon storage | **blocked** | Two independent blockers, below |
| Discounted cash flow | **blocked** | No stumpage prices, no cost model, no agreed discount rate |

**Carbon is blocked twice over, and the second one matters more than it looks:**

1. **In-forest carbon is switched off.** `config/projection.yaml` sets
   `carbon_extension: false` as a deliberate tripwire — FVS stop/restart silently resets
   FFE live-fuel state and understates total stand carbon by ~8% per barrier
   ([`restart-fidelity-findings.md`](restart-fidelity-findings.md)). Continuous
   single-pass runs are unaffected, which is probably the route to this KPI first.
2. **There is no harvested wood products pool.** Diaz et al. count carbon in products and
   landfill with decay over time. We model none of it. Without an HWP pool, *every*
   harvesting scenario looks like pure loss, so an in-forest-only comparison would not
   reproduce their finding — it would exaggerate it in the direction we happen to expect.
   That is the most dangerous kind of wrong.

Until both are fixed, the scenario factorial produces a timber-output comparison and a
structural comparison, not the carbon-versus-cash tradeoff the paper is about. Worth
being explicit about that before anyone reads a carbon number off this pipeline.

`tests/test_s4_scenarios.py::test_carbon_kpi_stays_blocked_while_the_fvs_flag_is_off`
ties the KPI status to the FVS flag, so re-enabling carbon forces a decision about HWP
rather than letting a half-built carbon KPI look finished.

---

## Run cost

Trajectory library is `unique(donor plot × regime × SI bin)`. The LETO run resolves
57,527 stands from **529 donor plots**, and the four scenarios reference 15 distinct
regimes, so the ceiling is **529 × 15 = 7,935 runs** against the no-management baseline's
693. Only regimes actually reachable from some owner class in some scenario need running,
so the real number is lower.

The buffer axis is *not* in that count — it needs a re-segmented landscape, not more FVS
runs.

---

## What is still ours to decide

- **Whether the objective axis should be rotation length at all.** Diaz et al. derive
  rotation from an optimisation (maximise NPV vs. sustained yield); we have hardcoded two
  rotation lengths, which is the *result* of that optimisation, not the optimisation. With
  no price data we cannot do it their way, and the shortcut should be stated as such.
- **Whether BAU should stay ownership-differentiated.** Theirs is a single industrial
  default across 64 like parcels. Ours varies by owner class, which is more realistic for
  a mixed-ownership landscape but makes "the BAU scenario" a composite rather than a
  practice.
- **Whether non-corporate classes should vary across scenarios at all.** Currently only
  `corporate` gets overrides — a family owner's behaviour does not obviously change
  because a corporate objective changed. But if the scenarios are meant as *policy*
  alternatives rather than *owner* alternatives, private and public classes should move
  too.
- **A treatment-timing offset axis.** `artemis.txt` sketches "delayed treatment by 5, 10,
  or 15 years" as a trajectory dimension. That is a third axis Diaz et al. did not have
  and it multiplies run count directly.

---

Related: [[management-regimes-by-owner]], [[management-pipeline-plan]],
[[restart-fidelity-findings]] (the carbon blocker), [[methodology-directions]].
