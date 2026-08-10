# Branch triage — 2026-08-10

Sixteen refs existed besides `main`: eleven with open PRs, three with no PR at all,
and two whose PRs were already closed. This is the disposition for each, and the
order to land them in.

Everything below was measured, not guessed: pairwise `git merge-tree` across all
15 branches, then a real sequential merge in a throwaway worktree with the suite
run at the end.

## The headline

**Twelve of fifteen branches merge cleanly into `main` one at a time.** That number
is misleading and is the reason this backlog looks healthier than it is — the
branches conflict with *each other*, not with `main`. Once the first one lands, the
next one's clean merge is no longer clean.

The real dependency structure is three shared files acting as collision points:

| File | Nature | Branches touching it |
|---|---|---|
| `notes/README.md` | append-only index | 8 |
| `uv.lock` | generated | 8 (all vs. #8) |
| `tests/conftest.py` | shared fixtures | 4 |

Those produce 34 of the 50 pairwise conflicts and are all mechanical. Strip them out
and only **four real code conflicts** remain, listed under "Combine" below.

## Verified merge train

Merged in this order into a scratch branch off `main`, each one clean, no manual
resolution anywhere:

1. `claude/stoic-feynman-wylkee` — PR #12, weekly artifact 2026-08-03
2. `claude/harvest-scheduling-1` — no PR, weekly artifact 2026-08-10
3. `claude/treemap-raster-correction-summary-cis9c2` — PR #13, one HTML deck
4. `claude/naip-imagery-embeddings-viewer-exzd38` — no PR, new `pipeline/s5_imagery/` + `viewer/`
5. `r/fia-treemap-fortype-validation` — PR #25, per-forest-type FIA/TreeMap validation
6. `claude/artemis-join-key-rounding-ajtk4k` — PR #15, identifier precision guards
7. `claude/review-main-pipeline-scripts-sin7r1` — PR #22, FVS keyword register
8. `management-units/riparian-accounting` — PR #23, sliver best-neighbour merge

Result: **368 passed, 22 skipped.** Green.

That is eight of sixteen refs retired in one pass, and it does not require resolving
a single conflict. Land this first — it shrinks the collision surface for everything
that follows, because four of these eight are the additive-only branches that were
inflating the conflict count.

Caveat on ordering: #23 merges clean *only if* #14 has not landed yet. The two
collide (see below), and #23 is the smaller change.

## Merge as-is

Additive, isolated, CI green, nothing else depends on them.

- **PR #12** `stoic-feynman-wylkee` — 4 files, one directory. Ships 14 MB of GeoTIFF;
  under the 99 MB guard in `scripts/check-staged-large-files.sh`, so it passes, but
  worth a deliberate yes rather than a default one.
- **PR #13** `treemap-raster-correction-summary-cis9c2` — a single self-contained
  `docs/treemap-raster-correction/presentation.html`. Zero risk.
- **PR #25** `r/fia-treemap-fortype-validation` — touches `r/07_FL_FIA_TreeMap_comparison.R`,
  which PR #15 also edits, but in disjoint regions. No conflict, either order.
- **PR #22** `review-main-pipeline-scripts-sin7r1` — largest rewrite of
  `regime_templates.py` (+357/−26). Land it *before* #19, which makes a 10-line edit
  to the same file; rebasing the small change onto the big one is the cheap direction.
- **PR #15** `artemis-join-key-rounding-ajtk4k` — no CI run recorded. Passed as part
  of the merge-train run above, so this is a missing check rather than a failing one.

## Open with no PR — open one or delete

- `claude/harvest-scheduling-1` — tip `105650f` is byte-identical to the head of
  **PR #26, which was closed unmerged today** with no review comments and no CI
  failure. The PR branch (`claude/gifted-ritchie-0fpgkk`) was deleted; this ref is
  the same commit surviving under a different name. Either #26 was closed by mistake
  and this should be reopened as a fresh PR, or it was closed deliberately and this
  branch should be deleted. **This is the one item that needs a human answer** — the
  content merges clean and passes tests, so nothing in the repo explains the closure.
- `claude/naip-imagery-embeddings-viewer-exzd38` — two commits, 20 files, a whole new
  `pipeline/s5_imagery/` stage plus a JS viewer, with tests. Substantial work sitting
  entirely unreviewed. Open a PR.

## Delete

- **`scripts/leto-workflow`** — 60 commits behind `main`, last touched 2026-07-20.
  Its `notes/notebooks.md` is now byte-identical to `main`'s, and it renames
  `notebooks/FVS_5county_growth_smoke.ipynb` to `.old` — a file that no longer exists
  on `main`, so the rename is dead on arrival.

  Do not delete blind: the four `scripts/*.txt` LETO ArcGIS files
  (`LETO.V1.1.txt`, `Create_FVS_Database.txt`, `LETO_CSV_PIPELINE.txt`,
  `Join_FVS_output_to_arc.txt`) exist **nowhere else in the repo**. Cherry-pick those
  onto a fresh branch, then delete this one. Everything else on it has been overtaken.

## Rebase, don't merge

- **PR #24** `environment-configuration-kci3xv` — 22 commits behind, and it conflicts
  with *eleven of the other fourteen branches*, more than anything else in the repo.
  The cause is not the R2 work: it re-adds `notes/claude-code-web-environment.md`,
  which already landed on `main` via PR #10, producing an add/add against everything.
  No CI run recorded. Rebase onto `main`, drop the duplicate file, re-run CI. The
  actual content (R2 bucket mirror, `pipeline/data_access.py`) is worth keeping.
- **PR #8** `r2-harvest-scheduling-viz-8yi8pd` — 32 behind. Its only conflict with
  anything is `uv.lock`, which is generated. Rebase and regenerate the lock; the
  weekly artifact itself is isolated. No CI run recorded.
- **PR #9** `treemap/hole-rectification` (draft) — 18 commits, 40 files, CI green,
  and its sole collision after the merge train is a two-line append to
  `notes/README.md`. The size is the only reason to be careful; the merge is not hard.
  Worth taking out of draft.

## Combine — the four real conflicts

These are genuine content disagreements where two branches independently solved the
same problem. None of them resolve mechanically.

### 1. `config/management_regimes.yaml` — PR #14 vs PR #19

Both branches **add the same new file**, with different schemas and no shared history:

- **#14** declares `version: 1`, and splits the file into `prescriptions` (a library
  of eight silvicultural prescriptions) and `owner_classes` (eligibility + one default
  each). Its stated design driver is trajectory-library cost — prescriptions are shared
  across owner classes so the library does not grow owner-by-owner.
- **#19** declares `version: 2`, keyed on LETO `OWN_CODE`, and carries a prominent
  warning that two ownership code systems share the column name `OWN_CODE` with
  different meanings (LETO 3 = Federal, Harris 3 = Family), referencing issue #20.

This is not a merge conflict, it is an unmade decision about the schema. #14 has the
better cost argument; #19 has the ownership-vocabulary safety work that #14 lacks
entirely, and that hazard is real. **Recommendation: take #14's
`prescriptions`/`owner_classes` structure as the skeleton and port #19's `sources`
block, `harris_*` disambiguation fields, and the vocabulary warning into it.** Neither
branch should land as-is.

### 2. `sketch_management_units.py` + `sliver_merge.py` — PR #14 vs PR #23

Six-file conflict. Both implement riparian retention independently — #14 via commits
"Retain riparian buffers as units instead of erasing them" and "Apply the riparian
overlay last"; #23 via "preserve riparian management units".

They are **not** redundant, and #23 is not simply superseded:

- On riparian, **#14 is better factored** — it extracts a separate
  `pipeline/s3_management/riparian_overlay.py` (12 riparian references left in
  `sketch_management_units.py`) where #23 does it inline (39 references). #14 has also
  been through a review round.
- On `sliver_merge.py`, **#23 is far ahead and #14 has almost nothing** — #23 adds
  best-same-class-neighbour ranking, same-parcel preference, shared-boundary edge
  computation, and orphan handling. #14's only change to that file is
  `split_exempt_units`.

**Recommendation: take #23's `sliver_merge.py` wholesale and #14's `riparian_overlay.py`
factoring.** Land #23 first (it is one commit, and merges clean today), then rebase #14
on top and drop #14's inline riparian handling in favour of its own overlay module.

### 3. `pipeline/s4_fvs/regime_templates.py` — PR #22 vs PR #19 vs PR #21

Three branches edit this file: #22 (+357/−26), #19 (+10/−2), #21 (+8). Only #22↔#19
actually conflict. Land #22 first, then #19 and #21 rebase onto it as small edits.
Note that #22 also carries `notes/pr21-review-2026-08-07.md` — a review of #21 — so
these two are already entangled by intent, not just by file.

### 4. `pipeline/s4_fvs/build_fvs_inputs.py` — PR #14 vs PR #15

Single-file conflict, both editing the FVS input builder. #15 is in the verified merge
train and lands clean; #14 rebases onto it. Smaller of the two problems on #14.

## Recommended sequence

1. Land the eight-branch merge train above. Verified green, no resolution needed.
2. Delete `scripts/leto-workflow` after cherry-picking the four LETO `.txt` files.
3. Decide PR #26 / `claude/harvest-scheduling-1`: reopen or delete.
4. Open a PR for `claude/naip-imagery-embeddings-viewer-exzd38`.
5. Rebase #24 (drop the duplicate note), #8 (regenerate `uv.lock`), un-draft #9.
6. Resolve the `management_regimes.yaml` schema question — it blocks both #14 and #19
   and is the only decision here that needs a modelling judgement rather than a merge.
7. Rebase #14 onto #23 + #15; rebase #19 and #21 onto #22.

After steps 1–5 the backlog is sixteen refs down to five, and every remaining one is a
single deliberate decision rather than a merge puzzle.

## Convention worth adopting

`notes/README.md` caused 8 of 50 conflicts and `uv.lock` another 8, for the same reason
each time: every branch appends one line to a shared index, or regenerates a lockfile.
Both are avoidable — set `uv.lock` to `merge=binary` with a regenerate-on-conflict rule,
and either split the notes index by section or accept that it is append-only and merge
with `union`. That single `.gitattributes` change would have made 16 of these 50
conflicts disappear.
