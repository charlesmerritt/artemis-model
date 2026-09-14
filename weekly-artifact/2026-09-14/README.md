# Weekly artifact — 2026-09-14

## Artifact

**The trajectory library with timing in it, and the plan that falls out of it.**
Every cutting prescription re-emitted at four start times, 13,035 FVS runs instead of 3,782,
and the scheduler re-run over the result with every other input held fixed.

This is the increment `2026-08-31` ended by asking for, in its own words:

> **Add the timing-offset grid to the library** (§4). It is the direct cause of 47
> unreachable targets and of an empty final cycle, it is already specified, and it costs FVS
> runs rather than scheduler work. Nothing else in the plan will improve much until the
> decision space carries "when".

It was right about the cause. It was wrong about the empty final cycle, and finding out why
turned out to matter as much as the grid itself — see **The final cycle was never a library
gap** below.

| File | What it is |
|---|---|
| `timing_library.png` | The four-panel figure: what the library can now do, what that put back in reach, what the plan delivered, and which delay the scheduler chose. |
| `annealed_plan.csv` | **The plan.** One row per stand: `stand_id → trajectory_id`, now carrying `base_prescription` and `timing_offset_years` beside the variant id, plus county, owner class, acres and removed volume in each of the ten cycles. 11,831 rows, `trajectory_id` unique, every `fvs_run_id` joining to the library below. |
| `trajectory_index.csv` | **The library.** One row per FVS run — 13,034 of them — with §5's `harvest_cuft[cycle]` as columns `cuft_cycle_0…11` rather than as twelve rows apiece. Same content as `2026-08-31/trajectory_harvest_by_cycle.csv`, pivoted: the expanded library in long form is a 14 MB text file that is mostly zero. |
| `library_expansion.csv` | The variant accounting: every `(prescription, offset)` combination, its resolved entry years, how many entries the horizon rule dropped, and whether it collapsed. 112 rows. |
| `library_reach_by_cycle.csv` | How many trajectories can cut in each cycle at all — the mechanism, before the scheduler touches anything. |
| `options_per_stand.csv` | The menu-size distribution, against §4's target of 6–12. |
| `terminal_cycle_probe.csv` | The five FVS runs behind the final-cycle finding, every summary row of each. |
| `attainable_envelope.csv` | What each dimension × cycle can reach from this library at any selection. |
| `comparison_to_20260831.csv` | **This plan beside last week's**, on 27 measures, every "before" read from that artifact's own committed files. |
| `constraint_violations.csv` · `harvest_by_cycle.csv` · `prescription_mix.csv` · `plan_by_dimension.csv` · `timing_offsets_chosen.csv` · `seed_spread.csv` · `solution_quality.json` | The §6 quality report and the plan summarised five ways. |
| `fvs_failures.csv` | The one trajectory FVS could not simulate, and why. |
| `make_timing_library.py` · `make_annealed_plan.py` · `make_figure.py` · `probe_terminal_cycle.py` | The four drivers. |

**Why this artifact, and why it is not a repeat of `2026-08-31`.** That artifact decided over
a decision space that offered *what* and almost no *when*, and proved 47 of its 80 targets
unreachable because of it. This one changes the decision space and re-decides. The landscape,
the targets, the objective weights, the cooling schedule and the five seeds are all
deliberately unchanged, so the difference between the two plans is attributable to the menu
rather than to the search.

## Headline results

| | 2026-08-31 | 2026-09-14 | |
|---|---:|---:|---|
| **Even-flow cost** (Σ squared relative deviation) | **31.87** | **10.61** | −67%; the weight-6 term, exactly comparable |
| Objective, best of 5 seeds | 190.41 | **63.08** | lower is better |
| Objective, greedy baseline | 363.68 | 363.59 | unchanged, as it must be |
| Objective, random selection (mean of 5) | 379.75 | 276.37 | |
| **Targets proven unreachable** (of 80) | **47** | **3** | |
| Harvest in cycle 10 (2072) | 0 | 266.0 M ft³ | |
| Harvest in cycle 5 (2047) | 56.7 M ft³ | 272.5 M ft³ | |
| Removed volume, 50 years | 1.889 bn ft³ | **2.578 bn ft³** | +36% |
| Trajectories per stand | 1–4 | 1–13 | §4 targets 6–12 |
| FVS runs in the library | 3,781 | 13,034 | |
| Seed spread (max − min objective) | 0.088 | 3.48 | **worse — see the caveat** |

**The plan is flat now.** Every cycle lands between 27% and 45% below the TPO target, where
last week ran from level with it to 100% short:

| Cycle | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-08-31, % of target | −19 | −36 | −19 | −35 | **−85** | −16 | −68 | **−92** | −42 | **−100** |
| 2026-09-14, % of target | −45 | −41 | −35 | −31 | −30 | −29 | −30 | −37 | −27 | −31 |

That is what even flow looks like against a target the landscape cannot fully supply, and it
is a different failure from last week's. The plan no longer misses its target by *oscillating*;
it misses by a roughly constant shortfall, which is a supply statement about the five-county
pilot rather than a scheduling artifact.

### The finding: 44 of the 47 unreachable targets were a timing artifact

47 targets were provably outside the library's reach last week. Three are now — and all three
fail the same way, with the library's **maximum** below the target:

| Dimension | Key | Cycle | Ceiling as % of target |
|---|---|---:|---:|
| county | Suwannee | 1 (2027) | 81% |
| county | Suwannee | 10 (2072) | 88% |
| owner_group | Other public | 1 (2027) | 62% |

(`Suwanee`, one 'n', in the tables — the TPO workbook's spelling, carried through
`pipeline.s3_management.tpo_targets`.)

Two of the three are in **cycle 1**, and that is exactly what an offsets-only grid predicts:
the grid can delay an entry, never advance one. Cycle 1's supply is whatever the
deterministically resolved schedules already put there, and no variant this artifact adds can
increase it. Making cycle 1 reachable needs *negative* offsets — entries earlier than stand
age and the config's fixed offsets resolve to — which is a policy question (is it credible to
cut these stands sooner than the prescription says?) rather than a library-budget one.

The count is a proof in one direction only, unchanged from last week and for the same reason:
the envelope comes from a relaxation, so a target *outside* it is unreachable by any
selection, while a target *inside* it is merely not proven unreachable. `attainable_envelope.csv`
still names the column `target_within_envelope` for that reason.

### The final cycle was never a library gap

`2026-08-31` reported cycle 10 empty and read it as a gap the timing grid would fill:

> The ceiling … swings to **zero everywhere in cycle 10**. No prescription in the enumerated
> library schedules an entry in 2072 at all.

The second sentence is not true, and the first has a different cause. The 2026-08-17 library
*did* schedule entries in 2072 — ten distinct `hardwood_clearcut_regen` entry years run from
2027 to 2072. They produced no harvest because **FVS does not execute an activity scheduled in
the final year of the projection.** `probe_terminal_cycle.py` runs the same stand and the same
committed keyfile renderer three ways:

| Clearcut scheduled in | Projection | Removal reported |
|---|---|---:|
| 2067 | 10 cycles (2022→2072) | 5,061.69 ft³/ac |
| **2072** | **10 cycles (2022→2072)** | **0.00** |
| 2072 | 11 cycles (2022→2077) | 5,274.74 ft³/ac |

So cycle 10 was unreachable *by any library* under a ten-cycle projection, and no timing
offset could have fixed it. The library now runs **eleven** cycles and scores ten: the extra
cycle exists only so that an entry in the horizon's last year is carried out inside the
projection instead of falling off its end. The 50-year harvest horizon is unchanged, and so is
every cycle in it — the probe checks that the 2022–2072 rows of a ten- and an eleven-cycle run
are identical, and the base-library check below repeats that test across 3,775 real runs.

**This corrected six trajectories that `2026-08-31` published as harvests that never
happened.** Six `hardwood_clearcut_regen` runs whose clearcut fell in 2072 were published with
a prescription, a scheduled harvest year, zero removed volume, and a full standing stand at the
end. They now remove 16,717 ft³/ac between them, and each one's removal is exactly the standing
volume last week reported it still carrying. They are named in
`solution_quality.json → timing_grid.base_library_check.corrected_runs`.

### What the scheduler did with the axis

It used all of it, and it spread the landscape nearly evenly across the four start times:

| Delay chosen | Stands | Acres |
|---|---:|---:|
| as resolved (+0 yr) | 1,158 | 211,805 |
| +5 yr | 1,464 | 329,325 |
| +10 yr | 1,266 | 153,996 |
| +15 yr | 1,110 | 183,011 |

And it stopped paying for even flow with abstention. `no_management` is in every non-riparian
library precisely so a binding volume target can be met by *not cutting* (§3 rule 1), and last
week the search leaned on it for **115,561 upland acres**. This week it needs **35,806** — the
other 80,000 acres are cut, just later. Timing substitutes for abstention, which is the
argument for the grid stated as an outcome rather than as a design intention.

### Riparian no-entry survived a fourfold expansion of every other menu

All 6,602 riparian stands still carry a library of exactly `{no_management}`, asserted by the
driver rather than assumed. The expansion multiplied every cutting menu by four and left these
untouched — by construction, since `no_management` has no entries and shifting "no entry"
produces no new trajectory. §3 rule 2 asks that the protection be "enforced by the absence of
an alternative rather than by a constraint the search could violate", and this is a harder test
of that than last week's: the one menu that did not grow is the one that must not.

## Caveats, stated rather than buried

**The search is measurably less converged than last week's, and that is the cost of the bigger
space.** Seed spread went from 0.088 (0.05% of the objective) to 3.48 (5.5%). Five seeds now
land at 63.08, 63.58, 63.63, 64.16 and 66.56 rather than on top of each other. The plan
reported is the best of the five, as §6 requires, and every seed is in `seed_spread.csv` — but
"converged" is no longer the right word for it. A decision space that is 3.5× larger with up to
13 options per stand needs more restarts or a slower cooling schedule, and neither was changed
here, deliberately: changing the search and the library in the same week would have made the
comparison meaningless. **This is the first thing to fix next week**, and it is cheap.

**The relaxation bound has gone nearly vacuous.** It fell from 153.74 to 0.195, because the
bound scores only the distance from targets to the *outside* of the relaxed interval, and
almost every target is now inside one. The gap between the plan (63.08) and the bound (0.20) is
therefore dominated by the relaxation's own looseness rather than by search error, and it no
longer certifies much. It is still a valid lower bound — it is reported because §6.2 asks for
one with its strategy declared, not because it is informative this week.

**The objective totals are not perfectly commensurable between the two weeks**, and the
comparison leads with the term that is. The objective's second term (`standing_volume`, weight
1) is normalised by the landscape's own attainable maximum, which a larger menu can raise, so
the same plan can score slightly differently under two libraries. The even-flow term (weight 6)
has no such denominator: 31.87 → 10.61 is exact. The greedy baseline is the control —
363.68 → 363.59, a difference of 0.08 on an identical set of trajectories.

**The two spatial penalties are still unavailable, and this week did nothing about them.**
`adjacency_greenup` and `max_opening_size` need a neighbour relation between stands, and a
"stand" here is still a pixel class (`TreeMap plot × county × ownership`) rather than a compact
polygon. The `block` move goes with them and the mixture renormalises over `single_stand` and
`period_swap` (0.875 / 0.125). **This remains the single largest caveat on the plan**, it is a
property of the input rather than of the scheduler, and it clears when the Phase 2.3 unit ×
stand crosswalk lands.

**938 stands carry 13 trajectories, against §4's target of 6–12.** The distribution is 6,602
riparian stands with one option, 3,381 with nine, 938 with thirteen, and 910 spread over 2–5
where the horizon rule collapsed some variants. The median upland stand sits at nine, inside
the target; the three-cutting-prescription stands sit one over it. §4 asks that library growth
be treated as a standing budget question, so, stated: the carved library holds 3,106 cutting
runs and 676 `no_management` runs; the three added offsets contribute 3,100 + 3,088 + 3,065
rather than 3 × 3,106, because 65 variants lose every entry to the horizon rule. That is 13,035
runs and 22 minutes on four cores, and a fifth offset would add roughly 3,000 more.

## Not fabricated

Every number above comes from committed repository code or from FVS output.

- **The expansion moves entry years and nothing else.** Each variant's operations come from the
  repository's own `build_thins` and `build_regeneration`, applied to the parameters
  `2026-08-17` resolved, with only the years shifted — same proportions, same DBH windows, same
  regeneration rule and delay. The shifted operations go to `render_keyfile`'s documented
  `thins`/`regen` override, so the timing grid never touches the keyfile renderer.
- **Two rules decide what survives, and both are the config's own.** An entry past 2072 is
  dropped (`config/management_regimes.yaml`: "a later entry is noise in the keyfile and a lie in
  the schedule"), and a variant that loses every entry "resolves to `no_management` for that
  stand" and is not published as an option, because it would duplicate the `no_management`
  already in the menu. Six `(prescription, offset)` combinations collapsed that way.
- **The carved landscape reproduces `2026-08-24` exactly**, asserted before anything runs:
  5,240 pre-carve units and 925,097.8 acres in; 11,831 stands, 22,317 pre-expansion library
  rows, 6,602 riparian, 5,229 upland, 913,943.2 harvestable acres out. Every check an equality
  against the committed `library_riparian_delta.csv`, not a tolerance.
- **The base library is verified run by run, not spot-checked.** `check_base_library` compares
  all 3,781 offset-0 runs against `2026-08-31/trajectory_index.csv` and splits them by whether
  the prescription schedules an entry in 2072:
  - **3,775 runs must be identical** — total removed volume, ending standing volume and
    harvest-cycle count. Max absolute difference: **9.1 × 10⁻¹³ ft³/ac**, a CSV round-trip.
    This is what proves the timing grid did not perturb the base prescriptions and the eleventh
    cycle did not reach back inside the horizon.
  - **6 runs must differ by exactly the cycle-10 removal last week dropped** — `total_now ==
    total_20260831 + cuft_cycle_10`, one more harvest cycle than before, and an ending standing
    volume no higher than before. All six pass.
  The check fails the run rather than tolerating a discrepancy in either group.
- **The greedy baseline plans over the same trajectories it planned over last week.**
  `assign_prescription` names a prescription, not a timing — it resolves entry years from stand
  age and the config's fixed offsets, which is the `@+0` variant — so the baseline is given that
  variant and no free choice of timing. It converges in 31 passes, as last week, and lands at
  363.59 against last week's 363.68.
- **The batch fails closed, and did.** Of 13,035 runs, 13,034 completed and one was killed by
  SIGFPE and excluded. The driver refused to publish until the exclusion count was stated
  exactly (`--allow-excluded-runs 1`), which is how this artifact was produced. What is
  acknowledged is the exclusion **set**, not its size: the committed `fvs_failures.csv` is
  read before it is rewritten and compared key by key, so a run in which this failure starts
  passing and a different one starts failing is refused rather than waved through on an
  unchanged count of one.
- **The excluded run is the same one `2026-08-31` excluded**: `473803917489998 /
  hardwood_clearcut_regen`, SIGFPE in `varmrt.f:176` (`ADJUST = TEMKIL/TEMSUM`, a division by
  zero in the SN mortality routine on a nearly-empty post-clearcut hardwood stand). Only the
  `@+0` variant dies: at +5, +10 and +15 the same prescription on the same plot completes, so
  the stand keeps a choice and `stands_with_a_choice` is 5,229 rather than last week's 5,228.
  The `zero` trap is kept and the run dropped, as before — it is a genuine numerical error.
- **The harvest-year row merge now matters.** `2026-08-31` fixed FVS_Summary2's two-row harvest
  year (removals from the `RmvCode = 1` row, state from the `RmvCode = 2` row) and noted it
  changed none of its published numbers, because nothing in its library harvested in the final
  cycle. This week 1,637 trajectories do, and `ending_merch_cuft_per_ac` — what the
  `standing_volume` objective reads — is that cycle's post-cut state.
- **The new logic carries its own tests.** `tests/test_weekly_artifact_20260914_timing.py`
  (20 tests) pins the offset grid on operations small enough to check by inspection: that an
  offset moves years and nothing else, that offset 0 is the identity, that an entry past 2072 is
  dropped and counted, that a variant losing every entry collapses, that a delayed rotation
  keeps its thin when its clearcut falls out, that regeneration is dropped with the harvest that
  created it, that `no_management` gains no variants so riparian menus stay `{no_management}`,
  and that both drivers decode a variant id the same way.
- `uv run ruff check .` clean. `uv run pytest tests/` → **924 passed, 9 failed, 10 skipped**.
  All nine failures are in `tests/test_restart_fidelity.py` and all nine are the same
  environment fault, unrelated to this artifact: the sandbox cannot download DuckDB's
  `sqlite_scanner` extension (`HTTP 403` from `extensions.duckdb.org`), which
  `scripts/setup-env.sh` installs in a normal environment. The 20 new tests pass.

## Eight corrections made after review

Raised on the PR by Devin Review, Codex, and Claude Code review comments. Each was
reproduced against the code before being fixed, and **all 13,035 keyfiles are byte-for-byte
identical before and after**, so every published volume in this artifact is unchanged by
them: the only committed file that moved is `library_expansion.csv`, whose collapsed rows
now report what they dropped.

1. **The raw-output cache ignored the simulation inputs.** `--reuse-raw` keyed the cached
   `FVS_Summary2` on run ids and keyfile hashes alone. A changed
   `FIA_5county_consolidated.db` or a rebuilt `FVSsn` alters no keyfile, so the cache would
   have been reused across either and stale volumes published under a fresh manifest. The
   key now covers a content fingerprint of the tree lists actually written, the binary's own
   hash, and the cycle count. The fingerprint is computed from the stand and tree rows rather
   than the SQLite file, whose bytes need not be stable between two builds of identical
   content.
2. **Collapsed variants reported dropping nothing.** When every entry fell outside the
   horizon, `shift_variant` returned `None` and the accounting row recorded
   `entries_dropped = 0` — for rows whose whole reason for existing is that their entries
   were dropped. `Variant` now carries a `collapsed` flag and is always returned, so the
   count survives; the six collapsed rows in `library_expansion.csv` now report 1 or 3
   dropped entries rather than 0.
3. **Regeneration was re-parented by proximity after the shift.** A record's parent was taken
   to be the nearest surviving entry preceding it. That agrees with the truth for every
   template in the library today — `clearcut` and `plantation_rotation` each have a single
   stand-replacing entry — but would silently re-attach a record to an unrelated earlier
   entry the moment a template had two and the later one was dropped, re-initializing a stand
   from a planting list for a harvest that never happened. The parent is now resolved on the
   unshifted schedule, where `_regen_after` placed the record exactly `delay_years` after its
   own harvest. The logic is split into `_shift_operations` so the two-stand-replacing-entry
   case can be tested at all: no prescription can currently produce it.
4. **`stand_sdi_tables` keyed its lookup with `str()`.** The batch's own frame is normalised
   upstream, but the helper is unguarded for any other caller, and `AGENTS.md` is explicit
   that an ID column goes through `as_id_series`. A numerically-typed `STAND_CN` would have
   keyed the table `"4.4894e+14"`, missed every lookup, and sent all natural regeneration to
   the single-species fallback — silently, since that fallback only warns.
5. **The exclusion gate acknowledged a count, not a set.** `--allow-excluded-runs 1` accepted
   any single failure, so a run in which the known SIGFPE started passing and a different
   trajectory started failing would have published a newly missing trajectory unreviewed. The
   committed `fvs_failures.csv` is now read before being rewritten and compared key by key,
   and it is left untouched when the gate refuses — overwriting it first would have made the
   *next* run compare against the very set nobody had reviewed.
6. **A code comment stated a number that was not a count of anything.** It claimed "5,240
   stands share 3,788 of them" of a dedup keyed on `(prescription, template, params)`: 5,240
   is the pre-carve unit count and 3,788 is a distinct-FVS-run count on a different key. The
   22,317 carved library rows carry 29 such combinations — 28 cutting plus `no_management`.
   In an artifact whose "Not fabricated" section is about numbers being checked rather than
   asserted, a comment inventing one is worth the fix.

A second review round raised two more, both about what "fails closed" covers:

7. **A missing cache sidecar read as "no failures".** A run that fails outright contributes
   no summary rows at all, so `raw_failures.csv` is the only record it ever existed. Treating
   a missing one as an empty frame would have turned a lost file into a smaller decision
   space, with the failed trajectory's option vanishing before it reached the exclusion gate.
   All three cache components are now required for a hit; any missing one is a cache miss.
   Behind that, **every rendered run must now be published or named as excluded, exactly
   one of the two** — `reconcile_run_ledger` is a partition check, so a run that produced no
   rows and was never recorded as a failure stops publication instead of disappearing.
8. **`--limit` published.** The smoke-test flag wrote the library tables, the artifact CSVs
   and `batch_manifest.json` — the very marker `make_annealed_plan.py` reads as "this library
   is complete and safe to plan over". A truncated run set is a different library, and every
   check downstream would have validated it as though it were this one. `--limit` now
   publishes nothing at all: no tables, no CSVs, no raw cache (whose key belongs to the
   truncated set), no manifest, and it leaves the exclusion acknowledgement untouched. It
   still invalidates the previous manifest at startup, as every run does, so a smoke test
   leaves the library unplannable until a full run republishes it — the safe direction.

Four further tests cover the new behaviour (20 in total): that a collapsed variant still
reports its dropped entries and carries no regeneration, that a regeneration record is
dropped rather than adopted when its own parent falls outside the horizon, and that the run
ledger refuses both a vanished run and one counted on both sides.

## R2 inputs pulled

**One file**, the same one `2026-08-31` needed. The carved landscape and the enumerated library
are committed artifacts, so nothing spatial had to be re-derived — no NHD, no parcels, no
ownership raster, no county geometry.

| R2 key | Local path | Size |
|---|---|---|
| `data/Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db` | `data/interim/stage/` | 1.004 GiB |

It supplies `FVS_STANDINIT_PLOT` / `FVS_TREEINIT_PLOT` for the 676 donor plots — the tree lists
FVS grows, and the species SDI tables natural regeneration is apportioned by. All 676 were
present; none was missing or imputed. No downloaded data is committed; everything lands under
gitignored `data/`.

## Exact commands

```bash
# 1. Build FVS Southern variant (the container has no FVS; the repo expects one at fvs/bin/)
apt-get install -y gfortran
git clone --depth 1 https://github.com/USDAForestService/ForestVegetationSimulator.git fvs/src
git -C fvs/src submodule update --init --depth 1 volume/NVEL      # NVEL volume library
sed -i 's/FVSbc_sourceList.txt/FVSsn_sourceList.txt/' fvs/src/bin/CMakeLists.txt
# Drop the underflow/denormal traps: the makefile promotes benign gradual-underflow-to-zero
# into a fatal trap in NVEL's own documented underflow site. 2026-08-31 correction 1.
sed -i 's/-ffpe-trap=invalid,zero,underflow,overflow,denormal/-ffpe-trap=invalid,zero,overflow/' \
  fvs/src/bin/makefile
make -C fvs/src/bin FVSsn -j4
mkdir -p fvs/bin && cp fvs/src/bin/FVSsn fvs/src/bin/FVSsn.so fvs/bin/

# 2. The one input (rclone remote `r2` is preconfigured via RCLONE_CONFIG_R2_* env vars)
rclone copyto r2:artemis-r2/data/Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db \
  data/interim/stage/FIA_5county_consolidated.db

# 3. The expanded library. ~22 min for 13,035 runs on four cores.
#    Fails closed: the first run stops at the exclusion gate reporting one SIGFPE, and
#    --reuse-raw then republishes from cached FVS output rather than re-running the batch.
uv run python weekly-artifact/2026-09-14/make_timing_library.py --workers 4
uv run python weekly-artifact/2026-09-14/make_timing_library.py --workers 4 \
    --reuse-raw --allow-excluded-runs 1
uv run python weekly-artifact/2026-09-14/make_timing_library.py --dry-run   # sizing only, no FVS

# 4. The evidence for the eleventh cycle (5 FVS runs; reuses the batch's input database)
uv run python weekly-artifact/2026-09-14/probe_terminal_cycle.py

# 5. The plan (~10 min, 5 restarts) and the figure (committed CSVs only)
uv run python weekly-artifact/2026-09-14/make_annealed_plan.py
uv run python weekly-artifact/2026-09-14/make_figure.py
```

## Dependencies

`gfortran` (apt) to compile FVS; nothing else new, and no change to `pyproject.toml` or
`uv.lock`. The committed environment was used as-is: Python 3.14, pandas, PyYAML, matplotlib.
`uv sync` reproduces it. `FVSSN_BIN` overrides the binary location if it is not at
`fvs/bin/FVSsn`.

## How to regenerate

```bash
uv sync
# build FVS and stage the one input per the commands above, then run the four drivers.
```

Output is deterministic: FVS is deterministic, and the annealer takes its seed
(`harvest.random_seed: 42`) and its restart count from `config/projection.yaml`, running seeds
42–46 and reporting all five. Same seed + same library + same weights gives the same plan.

Intermediates, all gitignored, under `data/interim/timing_library/`: the FVS input database,
13,035 keyfiles, the raw `FVS_Summary2` cache, the full `trajectory_cycles` state table, the
expanded per-stand library, and `excluded_runs.csv` (written before the exclusion gate, so a
refused run still leaves its failures somewhere readable). Delete that directory to force the
batch to re-run. `data/interim/stage/` holds the FIA database.

The raw cache is keyed on everything that can change FVS's output — the rendered keyfiles, a
content fingerprint of the tree lists actually written to the input database, the `FVSsn`
binary, and the cycle count — so `--reuse-raw` cannot serve trajectories simulated against a
different FIA database or a rebuilt executable, neither of which alters a keyfile.

## What this hands the next run

1. **Give the search the restarts the bigger space needs.** Seed spread went from 0.05% to 5.5%
   of the objective. Nothing about the library needs to change — more restarts, or a cooling
   factor nearer 1, or both, and the numbers above are a floor on what this decision space can
   do rather than its best.
2. **Decide whether negative offsets are admissible.** Two of the three remaining unreachable
   targets are in cycle 1, where an offsets-only grid is powerless by construction. Entries
   *earlier* than the deterministic schedule would reach them, and whether that is credible
   silviculture or target-chasing is a policy question worth answering explicitly rather than
   by default.
3. **Land the Phase 2.3 unit × stand crosswalk**, unchanged from last week's list and now the
   only large caveat left: it turns the pixel classes into polygons and switches on adjacency,
   green-up, opening size and the `block` move — the whole LAMPS half of the architecture,
   still dark.
4. **Re-check `pipeline/`'s status lines**, also unchanged from last week. `harvest_scheduler.py`,
   the README's "Current implementation" and `notes/trajectory-library-and-annealing.md` all
   still say the annealer is not built, and there are now two weekly drivers that build it. The
   design note's §4 line "Adding alternative timing parameterizations remains a library-budget
   decision" is likewise no longer the state of things. Promoting the drivers into `pipeline/`
   was again deliberately not done here: an artifact PR should not quietly become an
   architecture PR.
