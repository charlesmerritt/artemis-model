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

- `claude/harvest-scheduling-1` — the branch was **renamed** from
  `claude/gifted-ritchie-0fpgkk`. GitHub reports PR #26 as `closed`, but that is an
  artifact of the rename: deleting a PR's head branch auto-closes it, and a closed
  PR cannot be reopened once its head is gone. The work was never abandoned — tip
  `105650f` is the same commit, it merges clean, and it passes the suite. Open a
  fresh PR for it under the new name; #26 is just a dead reference.
- `claude/naip-imagery-embeddings-viewer-exzd38` — two commits, 20 files, a whole new
  `pipeline/s5_imagery/` stage plus a JS viewer, with tests. Substantial work sitting
  entirely unreviewed. Open a PR.

## Delete

- **`scripts/leto-workflow`** — 60 commits behind `main`, last touched 2026-07-20.
  Its `notes/notebooks.md` is now byte-identical to `main`'s, and it renames
  `notebooks/FVS_5county_growth_smoke.ipynb` to `.old` — a file that no longer exists
  on `main`, so the rename is dead on arrival.

  Do not delete blind: **five** `scripts/*.txt` LETO ArcGIS files (`LETO.V1.1.txt`,
  `LETO_CSV_PIPELINE.txt`, `Create_FVS_Database.txt`, `Join_FVS_output_to_arc.txt`,
  and `README.txt`, which is the run-order guide for the other four) exist **nowhere
  else in the repo**. Rescue those, then delete the branch. Everything else on it has
  been overtaken — all four of its note edits are already byte-identical on `main`,
  and `main` is one line *ahead* on `treemap-cog-county-summary.md`.

  Rescued 2026-08-10. This mattered more than "keep a copy": `pipeline/s3_management/
  assign_plt_cn.py` and `sliver_merge.py` both cite `scripts/LETO.V1.1.txt` in their
  docstrings for the procedure they reimplement, and that path did not resolve
  anywhere on `main`. The rescue fixes two live dangling references, and
  `notes/pipeline-review-2026-08-06.md` no longer points at a deleted branch.

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

## Executed — 2026-08-10

The train ran. Nine branches landed on `main` as `--no-ff` merges (each one
individually revertable), plus the `.gitattributes` fix that had to go first for the
train to behave as tested:

`stoic-feynman` (#12) → `harvest-scheduling-1` → `treemap-raster-correction` (#13) →
`naip-imagery-viewer` → `fia-treemap-fortype` (#25) → `join-key-rounding` (#15) →
`review-main-pipeline` (#22) → `riparian-accounting` (#23) → `r2-harvest-scheduling-viz` (#8)

All seven PRs among them auto-closed as **merged** (GitHub marks a PR merged once its
head is reachable from the base). Sixteen refs are now five open PRs: #24, #21, #19,
#14, #9.

`scripts/leto-workflow` was deliberately **left out** despite merging clean in the
regression. Its disposition is delete-after-rescue, not merge — it still renames a
notebook that no longer exists on `main`, and merging it would resurrect the file as
`.ipynb.old`. The four LETO `.txt` files still need cherry-picking first.

### Postmortem: CI went red on the first push

Worth recording, because every local check passed and the failure was still real.

`uv sync --locked` failed on `main` immediately. Cause: `.githooks/post-merge` did its
job — it re-resolved `uv.lock` after the `r2-harvest-scheduling-viz` merge — but the
hook is *advisory* and leaves the result unstaged. So the regeneration existed on
disk and in no commit.

Every verification then passed while checking the wrong thing. `uv sync --locked`,
`uv lock --check`, `ruff`, and `pytest` all read the **working tree**, where the fix
was sitting. The **committed** lockfile was still HEAD's, and that is what got pushed.
Working tree green, committed tree red, and nothing local disagreed.

Fixed by committing the regeneration (`973f432`). Guarded by `.githooks/pre-push`,
which refuses to push when `uv.lock` has uncommitted changes, or when `uv lock --check`
reports it stale — the first condition is precisely this bug. Bypass with
`ARTEMIS_SKIP_LOCK_CHECK=1`.

The general lesson is not about lockfiles: **verifying a merge by running commands in
the working tree does not verify what you are about to push.** For anything a hook may
have touched, check out the committed tree and test that instead.

## What is left

1. ~~Land the merge train.~~ Done — nine branches, `main` green.
2. ~~Delete the nine merged branches.~~ Done — their commits are permanently in `main`.
3. ~~`scripts/leto-workflow`: rescue the LETO files, then delete it.~~ Done — five
   files, byte-identical, under `scripts/`.
4. Rebase #24 onto `main` and drop the duplicate `notes/claude-code-web-environment.md`.
   It re-adds a file that landed via PR #10, which is why it collided with eleven
   branches. (#8 no longer needs its rebase — it landed in the train.)
5. Un-draft #9. Its only remaining collision is `tests/conftest.py`.
6. Resolve the `management_regimes.yaml` schema question — it blocks both #14 and #19
   and is the only decision here that needs a modelling judgement rather than a merge.
7. Rebase #14 onto #15 + the landed #23; rebase #19 and #21 onto the landed #22.

The backlog is sixteen refs down to five open PRs, and every remaining one is a single
deliberate decision rather than a merge puzzle.

## The `.gitattributes` fix — implemented

`notes/README.md` caused 8 of the 50 conflicts and `uv.lock` another 8, for the same
reason each time: every branch appends one line to a shared index, or regenerates a
lockfile. Neither was ever a real disagreement. Both are now handled in
`.gitattributes`.

`notes/README.md merge=union` is the easy half — entries are independent, so taking
both sides is always right.

`uv.lock` needed two pieces, and the reason is a genuine constraint rather than a
design preference. The obvious implementation — a merge driver that runs `uv lock` —
does not work, and fails *silently*, which is worse than not working:

> At the moment a merge driver runs, git has not yet written the merged files to the
> working tree, and `MERGE_HEAD` does not yet exist. Both were verified directly with
> a probe driver. So the driver cannot see the merged `pyproject.toml` and cannot
> reconstruct it — the only inputs it gets are the three versions of `uv.lock`.

The first version of this fix did exactly that, and produced a lockfile that merged
cleanly, parsed as valid TOML, passed the test suite — and was stale by four packages,
because it had resolved against the pre-merge manifest. `uv lock --check` caught it.

So the work is split:

- `scripts/merge-uv-lock.sh` (merge driver) takes HEAD's lockfile verbatim, after
  checking it parses. The result is always a resolution some branch really produced,
  never an interleave of two.
- `.githooks/post-merge` re-resolves against the merged `pyproject.toml`, which it
  *can* see because it runs after the working tree is updated. Advisory: it reports
  and leaves the change unstaged rather than amending your merge commit.

Both need per-clone registration (git will not take a driver command from a tracked
file — that would let a fetched branch run code on merge). `.claude/hooks/session-start.sh`
does it automatically; the README quickstart lists it for fresh clones. Without
registration git falls back to the ordinary 3-way merge, so this is a cleanup, never
a correctness dependency.

### Measured effect

Re-ran the full sequential merge of all fifteen branches with the fix active:

- `notes/README.md` and `uv.lock` disappeared from **every** conflict list.
- The clean merge train grew from **8 branches to 10** — `r2-harvest-scheduling-viz`
  (#8) and `scripts/leto-workflow` now merge with no intervention, since their only
  collisions were those two files.
- Final state: **368 passed, 22 skipped**, zero conflict markers, and `uv lock --check`
  clean.

The remaining conflicts are all real: `tests/conftest.py`, `regime_templates.py`,
`build_fvs_inputs.py`, the riparian pair, and genuine prose disagreements in
`README.md` / `methodology-directions.md`. Those are the four combine decisions above,
and they should conflict.
