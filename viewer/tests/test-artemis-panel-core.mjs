#!/usr/bin/env node
/* Validate the pure helpers behind the ARTEMIS side panel.

   Mirrors the map viewer's scripts/test-*.mjs convention so the panel can be
   checked without a browser:

       node viewer/tests/test-artemis-panel-core.mjs
*/

import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Core = require("../artemis-panel-core.js");

// ---- formatting ----

assert.equal(Core.formatPercent(0.9997, 2), "99.97%");
assert.equal(Core.formatPercent(0.5), "50.0%");
assert.equal(Core.formatPercent(null), "—");
assert.equal(Core.formatPercent(undefined), "—");

assert.equal(Core.formatArea(4.23), "4.2 ha");
assert.equal(Core.formatArea(250), "250 ha");
assert.equal(Core.formatArea(2500), "2.5k ha");
assert.equal(Core.formatArea(250000), "250k ha");
assert.equal(Core.formatArea(null), "—");

// ---- coverage badges ----

assert.deepEqual(Core.coverageBadge({ complete: true, contributing_years: [2021] }), {
  kind: "complete",
  label: "full coverage",
});

// Covered, but partly with another year's imagery — a distinct state, because
// anyone reading change between years needs to know.
assert.deepEqual(Core.coverageBadge({ complete: true, contributing_years: [2020, 2021] }), {
  kind: "filled",
  label: "gap-filled",
});

assert.deepEqual(Core.coverageBadge({ complete: false, contributing_years: [2021] }), {
  kind: "partial",
  label: "partial coverage",
});

assert.equal(Core.coverageBadge(undefined).kind, "partial");

const filled = Core.describeCoverage({
  year: 2021,
  coverage: 0.9999,
  image_count: 14,
  contributing_years: [2020, 2021, 2022],
});
assert.match(filled, /99\.99%/);
assert.match(filled, /14 images/);
assert.match(filled, /holes filled from 2020, 2022/);

const plain = Core.describeCoverage({ year: 2021, coverage: 0.5, image_count: 0,
  contributing_years: [2021] });
assert.match(plain, /no images/);
assert.doesNotMatch(plain, /holes filled/);

// ---- tile freshness ----

const now = Date.parse("2026-08-10T12:00:00Z");

const fresh = Core.tileAge("2026-08-10T11:30:00Z", now);
assert.equal(fresh.known, true);
assert.equal(fresh.stale, false);
assert.equal(fresh.label, "minted just now");

const hours = Core.tileAge("2026-08-10T04:00:00Z", now);
assert.equal(hours.label, "minted 8h ago");
assert.equal(hours.stale, false);

const stale = Core.tileAge("2026-08-07T12:00:00Z", now);
assert.equal(stale.label, "minted 3d ago");
assert.equal(stale.stale, true);

assert.equal(Core.tileAge(null, now).known, false);
assert.equal(Core.tileAge("not-a-date", now).known, false);
// Clock skew must not produce a negative age.
assert.equal(Core.tileAge("2026-08-10T13:00:00Z", now).hours, 0);

// ---- chart data ----

const clusters = [
  { cluster: 0, color: "#e69f00", inside_count: 90, outside_count: 10,
    inside_share: 0.9, outside_share: 0.1, inside_fraction: 0.9 },
  { cluster: 1, color: "#56b4e9", inside_count: 10, outside_count: 90,
    inside_share: 0.1, outside_share: 0.9, inside_fraction: 0.1 },
];

const share = Core.clusterShareChartData(clusters);
assert.deepEqual(share.labels, ["C0", "C1"]);
assert.equal(share.datasets.length, 2);
assert.equal(share.datasets[0].label, "Inside AOI");
assert.deepEqual(share.datasets[0].data, [90, 10]);
assert.deepEqual(share.datasets[1].data, [10, 90]);

const fraction = Core.insideFractionChartData(clusters);
assert.deepEqual(fraction.datasets[0].data, [90, 10]);
// Bars carry their own cluster color so the chart and the map agree.
assert.deepEqual(fraction.datasets[0].backgroundColor, ["#e69f00", "#56b4e9"]);

// A cluster with no samples has a null fraction; it must plot as 0, not NaN.
const empty = Core.insideFractionChartData([{ cluster: 0, inside_fraction: null }]);
assert.deepEqual(empty.datasets[0].data, [0]);
assert.deepEqual(empty.datasets[0].backgroundColor, ["#999999"]);

assert.deepEqual(Core.clusterShareChartData(undefined).labels, []);

// Scatter geometry is shared across methods; cluster ids arrive separately and
// are index-aligned with the points.
const scatterPoints = {
  points: [
    { x: 1, y: 2, inside: true },
    { x: 3, y: 4, inside: false },
    { x: 5, y: 6, inside: false },
  ],
};
const scatter = Core.scatterChartData(scatterPoints, ["#e69f00", "#56b4e9"], [0, 1, 0]);
assert.equal(scatter.datasets.length, 2);
assert.equal(scatter.datasets[0].label, "Inside AOI (1)");
assert.equal(scatter.datasets[1].label, "Outside AOI (2)");
assert.deepEqual(scatter.datasets[0].data, [{ x: 1, y: 2 }]);
// Outside points are hollow so overlapping sides stay readable.
assert.equal(scatter.datasets[1].backgroundColor, "transparent");
assert.deepEqual(scatter.datasets[1].borderColor, ["#56b4e9", "#e69f00"]);

// Switching methods recolors the same points; the coordinates must not move,
// which is what makes two methods visually comparable.
const recolored = Core.scatterChartData(scatterPoints, ["#e69f00", "#56b4e9"], [1, 0, 1]);
assert.deepEqual(recolored.datasets[0].data, scatter.datasets[0].data);
assert.deepEqual(recolored.datasets[1].data, scatter.datasets[1].data);
assert.deepEqual(recolored.datasets[0].backgroundColor, ["#56b4e9"]);

// Palette shorter than the cluster count must wrap rather than yield undefined.
const wrapped = Core.scatterChartData(
  { points: [{ x: 0, y: 0, inside: true }] },
  ["#111111", "#222222"],
  [3]
);
assert.equal(wrapped.datasets[0].backgroundColor[0], "#222222");

// Missing labels fall back to grey instead of crashing.
const unlabelled = Core.scatterChartData(scatterPoints, ["#e69f00"], []);
assert.equal(unlabelled.datasets[0].backgroundColor[0], "#999999");

assert.equal(Core.scatterChartData(undefined, [], []).datasets[0].data.length, 0);

// ---- separability summary ----

// Sample counts and PCA variance belong to the embedding run; divergence
// belongs to the selected clustering method.
const summary = Core.summarizeSeparability(
  {
    sample: { inside: 1500, outside: 1500, total: 3000 },
    scatter: { explained_variance_ratio: [0.42, 0.19] },
  },
  { separability: { jensen_shannon_divergence: 0.4231, interpretation: "Strong separation" } }
);
assert.equal(summary.divergenceLabel, "0.423");
assert.equal(summary.insideCount, 1500);
assert.equal(summary.varianceLabel, "PC1 42% · PC2 19%");
assert.equal(summary.interpretation, "Strong separation");

const missing = Core.summarizeSeparability({}, {});
assert.equal(missing.divergence, null);
assert.equal(missing.divergenceLabel, "—");
assert.equal(missing.varianceLabel, "");

// ---- run selection ----

const embeddingsPayload = {
  default_method: "xmeans",
  runs: [
    {
      method: "kmeans",
      label: "k-means (Euclidean)",
      auto_k: false,
      k_observed: 6,
      separability: { jensen_shannon_divergence: 0.21 },
    },
    {
      method: "xmeans",
      label: "X-means (auto k, BIC)",
      auto_k: true,
      k_observed: 9,
      separability: { jensen_shannon_divergence: 0.44 },
    },
    {
      method: "lvq",
      label: "Learning vector quantization",
      auto_k: false,
      k_observed: 6,
      separability: { jensen_shannon_divergence: 0.33 },
    },
  ],
};

assert.equal(Core.selectRun(embeddingsPayload, "lvq").method, "lvq");
// An unknown or stale saved method falls back to the declared default.
assert.equal(Core.selectRun(embeddingsPayload, "cobweb").method, "xmeans");
assert.equal(Core.selectRun(embeddingsPayload, undefined).method, "xmeans");
// With no default either, the first run wins rather than rendering nothing.
assert.equal(
  Core.selectRun({ runs: [{ method: "lvq" }] }, "nope").method,
  "lvq"
);
assert.equal(Core.selectRun({ runs: [] }, "kmeans"), null);
assert.equal(Core.selectRun(null, "kmeans"), null);

// ---- method comparison ----

const methods = Core.methodSummaries(embeddingsPayload);
assert.equal(methods.length, 3);
assert.deepEqual(
  methods.map((row) => row.method),
  ["kmeans", "xmeans", "lvq"]
);
assert.equal(methods[1].divergenceLabel, "0.440");
assert.equal(methods[1].autoK, true);
assert.equal(methods[1].k, 9);
// Exactly one method is flagged strongest, and it is the highest divergence.
assert.deepEqual(
  methods.map((row) => row.best),
  [false, true, false]
);

// A lone method is not "strongest" — there is nothing to be stronger than.
const single = Core.methodSummaries({
  runs: [{ method: "kmeans", separability: { jensen_shannon_divergence: 0.5 } }],
});
assert.equal(single[0].best, false);

assert.deepEqual(Core.methodSummaries(null), []);
assert.deepEqual(Core.methodSummaries({ runs: [] }), []);

// A run missing its separability block must not produce NaN in the list.
const partial = Core.methodSummaries({ runs: [{ method: "kmeans" }] });
assert.equal(partial[0].divergenceLabel, "0.000");

// ---- catalog normalization ----

const normalized = Core.normalizeCatalog({
  schema: Core.CATALOG_SCHEMA,
  name: "Test Stands",
  vectors: { extent: { url: "artemis/extent.geojson" } },
  naip: { collection: "USDA/NAIP/DOQQ", years: [{ year: 2021 }] },
});
assert.equal(normalized.schemaMatches, true);
assert.equal(normalized.vectors.aoi, null);
assert.deepEqual(normalized.naip.incompleteYears, []);
assert.equal(normalized.embeddings, null);

// A mismatched schema is surfaced, not silently rendered with missing fields.
assert.equal(Core.normalizeCatalog({ schema: "artemis.viewer.catalog/0" }).schemaMatches, false);

const bare = Core.normalizeCatalog(null);
assert.equal(bare.name, "ARTEMIS");
assert.equal(bare.naip, null);
assert.deepEqual(bare.vectors, { extent: null, aoi: null });

const withEmbeddings = Core.normalizeCatalog({ embeddings: { default_method: "kmeans" } });
assert.deepEqual(withEmbeddings.embeddings.runs, []);
assert.deepEqual(withEmbeddings.embeddings.scatter.points, []);

const withRuns = Core.normalizeCatalog({ embeddings: embeddingsPayload });
assert.equal(withRuns.embeddings.runs.length, 3);
assert.equal(withRuns.embeddings.default_method, "xmeans");

console.log("artemis-panel-core: all assertions passed");
