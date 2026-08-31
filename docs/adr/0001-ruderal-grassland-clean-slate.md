# Redo ruderal grassland rectification from scratch, generative-first, human-validated

**Status:** accepted

An earlier investigation (`notes/clearcut-vs-agriculture-embeddings.md`) already scored
ruderal grassland as a confused class, using LCMS tree-removal as the ground-truth anchor
for a supervised classifier (spatial-CV AUC 0.99+). We are discarding that as the basis for
production rectification and starting over: LCMS is not trusted as ground truth on its own,
the sampling engineering (MMU filter, erosion, spatial blocks) is being kept only as
optionally-reusable geometry, and the classification method is deliberately left
unspecified pending first-principles design.

Decided in its place:
- **Mask** = LF-EVT 2022 categorical membership alone (not the earlier multi-year
  hole-stratification design). Multi-year AlphaEarth embeddings are used only as
  classifier *features*, because the temporal signature is expected to be the actual
  separator between true grassland and recent silviculture.
- **LCMS is admissible only when corroborated** against independent raster evidence (e.g.
  LCMS flags a disturbance that precedes a later TreeMap hole, or aligns with a
  grass-like LF-EVT transition) — never taken alone.
- **Validation moves from automated statistical checks to human review**: a labeling
  webapp (built on the existing `viewer/` + `pipeline/s5_imagery` NAIP/time-slider/cluster
  panel) collects approve/disapprove/ambiguous verdicts from the user and collaborators,
  stored in a database, rather than reusing `validate_s3_lcms.py`-style LCMS/EVALIDator
  validation.
- **Rollout is staged by AOI**: the existing 5-county extent first, then statewide
  Florida, then a further state once Florida is a lock.

The specific generative model for characterizing the ruderal-grassland spectral
distribution (e.g. Dirichlet-process vs. finite mixture, dimensionality reduction) is an
open question, deliberately punted to a future session.

See `notes/ruderal-grassland-agent-feedback-loops.md` for how each stage should expose a
fast, self-inspectable path (sample-scale runs, printed sanity stats, PNG diagnostics,
Playwright-driven checks on the review webapp) so the implementing agent can verify its
own output as it goes, rather than only at a human review round.
