/* ----------------------------------------------------------------
   artemis-panel-core.js — pure helpers for the ARTEMIS side panel.

   No DOM, no MapLibre, no Chart.js. Everything here is a plain data
   transform so it can be unit-tested under node, following the same
   pattern as the viewer's zonal-core.js.

   Public surface (window.ArtemisPanelCore / module.exports):
     normalizeCatalog(raw)
     formatPercent(fraction, digits)
     formatArea(hectares)
     coverageBadge(yearEntry)
     describeCoverage(yearEntry)
     tileAge(isoString, now)
     clusterShareChartData(clusters)
     insideFractionChartData(clusters)
     scatterChartData(scatter, palette, clusterIds)
     summarizeSeparability(embeddings, run)
     selectRun(embeddings, method)
     methodSummaries(embeddings)
   ---------------------------------------------------------------- */

(function (root) {
  "use strict";

  // Earth Engine map IDs are not permanent. There is no published TTL, so this
  // is a caution threshold rather than an expiry: past a day, a tile layer that
  // fails to load is far more likely to be expired than misconfigured.
  const TILE_STALE_HOURS = 24;

  const CATALOG_SCHEMA = "artemis.viewer.catalog/1";

  // ---- Formatting ----

  function formatPercent(fraction, digits) {
    if (fraction === null || fraction === undefined || Number.isNaN(Number(fraction))) {
      return "—";
    }
    const places = digits === undefined ? 1 : digits;
    return `${(Number(fraction) * 100).toFixed(places)}%`;
  }

  function formatArea(hectares) {
    if (hectares === null || hectares === undefined || Number.isNaN(Number(hectares))) {
      return "—";
    }
    const value = Number(hectares);
    if (value >= 100000) return `${(value / 1000).toFixed(0)}k ha`;
    if (value >= 1000) return `${(value / 1000).toFixed(1)}k ha`;
    if (value >= 10) return `${value.toFixed(0)} ha`;
    return `${value.toFixed(1)} ha`;
  }

  // ---- Coverage ----

  /**
   * Classify how well a year covers the imagery extent.
   *
   * "gap-filled" is called out separately from "complete" on purpose: the mosaic
   * does cover the extent, but part of it is imagery from another year, and
   * anyone reading change between years needs to know that.
   */
  function coverageBadge(yearEntry) {
    const entry = yearEntry || {};
    const contributing = entry.contributing_years || [];
    if (!entry.complete) {
      return { kind: "partial", label: "partial coverage" };
    }
    if (contributing.length > 1) {
      return { kind: "filled", label: "gap-filled" };
    }
    return { kind: "complete", label: "full coverage" };
  }

  function describeCoverage(yearEntry) {
    const entry = yearEntry || {};
    const contributing = (entry.contributing_years || []).slice();
    const covered = formatPercent(entry.coverage, 2);
    const images = entry.image_count ? `${entry.image_count} images` : "no images";

    if (contributing.length > 1) {
      const others = contributing.filter((year) => year !== entry.year);
      return `${covered} of extent, ${images}; holes filled from ${others.join(", ")}`;
    }
    return `${covered} of extent, ${images}`;
  }

  // ---- Tile freshness ----

  function tileAge(isoString, now) {
    if (!isoString) return { known: false, hours: null, label: "age unknown", stale: false };

    const minted = Date.parse(isoString);
    if (Number.isNaN(minted)) {
      return { known: false, hours: null, label: "age unknown", stale: false };
    }

    const reference = now === undefined ? Date.now() : now;
    const hours = Math.max(0, (reference - minted) / 3_600_000);
    const stale = hours >= TILE_STALE_HOURS;

    let label;
    if (hours < 1) label = "minted just now";
    else if (hours < 24) label = `minted ${Math.round(hours)}h ago`;
    else label = `minted ${Math.round(hours / 24)}d ago`;

    return { known: true, hours, label, stale };
  }

  // ---- Chart data ----

  /**
   * Grouped bar: what share of inside samples, and of outside samples, each
   * cluster holds.
   *
   * Shares are normalized within each side, so the two series are comparable
   * even though the sides rarely have identical sample counts. Equal bars mean
   * the AOI boundary is invisible to the clustering.
   */
  function clusterShareChartData(clusters) {
    const list = clusters || [];
    return {
      labels: list.map((entry) => `C${entry.cluster}`),
      datasets: [
        {
          label: "Inside AOI",
          data: list.map((entry) => Number(entry.inside_share || 0) * 100),
          backgroundColor: "#e69f00",
        },
        {
          label: "Outside AOI",
          data: list.map((entry) => Number(entry.outside_share || 0) * 100),
          backgroundColor: "#56b4e9",
        },
      ],
    };
  }

  /**
   * Bar: of everything in a cluster, how much sits inside the AOI.
   *
   * Bars are tinted by their own cluster color so this chart and the map agree.
   * The 50% line is the reference — a cluster there is split evenly between
   * inside and outside and therefore tells you nothing about the boundary.
   */
  function insideFractionChartData(clusters) {
    const list = clusters || [];
    return {
      labels: list.map((entry) => `C${entry.cluster}`),
      datasets: [
        {
          label: "% of cluster inside AOI",
          data: list.map((entry) =>
            entry.inside_fraction === null || entry.inside_fraction === undefined
              ? 0
              : Number(entry.inside_fraction) * 100
          ),
          backgroundColor: list.map((entry) => entry.color || "#999999"),
          borderColor: list.map((entry) => entry.color || "#999999"),
          borderWidth: 1,
        },
      ],
    };
  }

  /**
   * Scatter: sampled embeddings in their first two principal components.
   *
   * Two datasets, not one per cluster — inside is filled circles, outside is
   * hollow triangles, and cluster identity rides on the per-point color. That
   * keeps the legend to the comparison being made while still showing where the
   * clusters fall.
   *
   * Point geometry is shared across clustering methods (same samples, same
   * embeddings, so the same PCA), and `clusterIds` supplies the selected run's
   * labels. Switching methods therefore only recolors — the cloud does not move,
   * which is what makes two methods visually comparable.
   */
  function scatterChartData(scatter, palette, clusterIds) {
    const points = (scatter && scatter.points) || [];
    const colors = palette || [];
    const labels = clusterIds || [];
    const colorFor = (cluster) =>
      cluster === undefined || cluster === null
        ? "#999999"
        : colors[cluster % (colors.length || 1)] || "#999999";

    const inside = [];
    const insideColors = [];
    const outside = [];
    const outsideColors = [];

    points.forEach((point, index) => {
      const coordinate = { x: Number(point.x), y: Number(point.y) };
      const color = colorFor(labels[index]);
      if (point.inside) {
        inside.push(coordinate);
        insideColors.push(color);
      } else {
        outside.push(coordinate);
        outsideColors.push(color);
      }
    });

    return {
      datasets: [
        {
          label: `Inside AOI (${inside.length})`,
          data: inside,
          pointStyle: "circle",
          pointRadius: 3,
          backgroundColor: insideColors,
          borderColor: insideColors,
        },
        {
          label: `Outside AOI (${outside.length})`,
          data: outside,
          pointStyle: "triangle",
          pointRadius: 4,
          backgroundColor: "transparent",
          borderColor: outsideColors,
          borderWidth: 1.5,
        },
      ],
    };
  }

  // ---- Summaries ----

  /**
   * Combine the shared sample/scatter facts with the selected run's separability.
   *
   * Sample counts and PCA variance belong to the whole embedding run and do not
   * change when the method changes; divergence and interpretation do.
   */
  function summarizeSeparability(embeddings, run) {
    const payload = embeddings || {};
    const selected = run || {};
    const separability = selected.separability || {};
    const sample = payload.sample || {};
    const variance = (payload.scatter && payload.scatter.explained_variance_ratio) || [];

    const divergence =
      separability.jensen_shannon_divergence === undefined
        ? null
        : Number(separability.jensen_shannon_divergence);

    return {
      divergence,
      divergenceLabel: divergence === null ? "—" : divergence.toFixed(3),
      interpretation: separability.interpretation || "",
      insideCount: sample.inside || 0,
      outsideCount: sample.outside || 0,
      totalCount: sample.total || 0,
      varianceLabel: variance.length
        ? `PC1 ${formatPercent(variance[0], 0)} · PC2 ${formatPercent(variance[1], 0)}`
        : "",
    };
  }

  /**
   * Pick a run out of an embeddings payload by method name.
   *
   * Falls back to the declared default, then to the first run, so a stale saved
   * method preference never leaves the panel with nothing to show.
   */
  function selectRun(embeddings, method) {
    const runs = (embeddings && embeddings.runs) || [];
    if (!runs.length) return null;
    return (
      runs.find((run) => run.method === method) ||
      runs.find((run) => run.method === (embeddings || {}).default_method) ||
      runs[0]
    );
  }

  /**
   * One row per method for the comparison list, ranked by how strongly each
   * separates inside from outside.
   *
   * This is the answer to "which clustering technique should we use" — a method
   * that finds a sharper inside/outside split on the same samples is the one
   * responding to whatever the AOI boundary actually marks.
   */
  function methodSummaries(embeddings) {
    const runs = (embeddings && embeddings.runs) || [];
    const rows = runs.map((run) => {
      const divergence = Number(
        (run.separability || {}).jensen_shannon_divergence ?? 0
      );
      return {
        method: run.method,
        label: run.label || run.method,
        description: run.description || "",
        autoK: !!run.auto_k,
        k: run.k_observed ?? null,
        divergence,
        divergenceLabel: divergence.toFixed(3),
      };
    });

    const best = rows.reduce(
      (top, row) => (top === null || row.divergence > top.divergence ? row : top),
      null
    );
    rows.forEach((row) => {
      row.best = best !== null && row.method === best.method && rows.length > 1;
    });
    return rows;
  }

  // ---- Catalog ----

  /**
   * Fill in a catalog's optional branches so the panel never has to null-check
   * its way through rendering. Unknown schema versions are surfaced rather than
   * silently rendered, since a mismatched producer means missing fields.
   */
  function normalizeCatalog(raw) {
    const catalog = raw || {};
    const vectors = catalog.vectors || {};
    const naip = catalog.naip || null;
    const embeddings = catalog.embeddings || null;

    return {
      schema: catalog.schema || null,
      schemaMatches: catalog.schema === CATALOG_SCHEMA,
      name: catalog.name || "ARTEMIS",
      generated: catalog.generated_utc || null,
      vectors: {
        extent: vectors.extent || null,
        aoi: vectors.aoi || null,
      },
      naip: naip
        ? {
            collection: naip.collection || "",
            bands: naip.bands || [],
            scaleM: naip.scale_m || null,
            incompleteYears: naip.incomplete_years || [],
            coverageSettings: naip.coverage_settings || {},
            years: naip.years || [],
          }
        : null,
      embeddings: embeddings
        ? {
            ...embeddings,
            runs: embeddings.runs || [],
            scatter: embeddings.scatter || { points: [], explained_variance_ratio: [] },
          }
        : null,
    };
  }

  const api = {
    CATALOG_SCHEMA,
    TILE_STALE_HOURS,
    normalizeCatalog,
    formatPercent,
    formatArea,
    coverageBadge,
    describeCoverage,
    tileAge,
    clusterShareChartData,
    insideFractionChartData,
    scatterChartData,
    summarizeSeparability,
    selectRun,
    methodSummaries,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.ArtemisPanelCore = api;
})(typeof window !== "undefined" ? window : globalThis);
