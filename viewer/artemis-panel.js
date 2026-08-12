/* ----------------------------------------------------------------
   artemis-panel.js — ARTEMIS side panel for the PERSEUS map viewer.

   A right-docked, collapsible panel driven by artemis/catalog.json
   (written by pipeline/s5_imagery/viewer_catalog.py). It adds:

     - NAIP year layers, with per-year extent-coverage provenance
     - the imagery-extent and area-of-interest vector layers
     - the embedding cluster raster
     - inside-vs-outside clustering charts

   Drop-in: it builds its own DOM, so index.html only needs the
   stylesheet and these two scripts. Nothing in the stock viewer is
   modified; the panel talks to it through window.AppState and
   window.Layers only.

   Depends on: artemis-panel-core.js, Chart.js (already loaded by the
   viewer for zonal stats), MapLibre via window.AppState.
   ---------------------------------------------------------------- */

(function () {
  "use strict";

  const Core = window.ArtemisPanelCore;
  const State = window.AppState;

  const DEFAULT_CATALOG_URL = "artemis/catalog.json";
  const COLLAPSE_KEY = "artemis.panel.collapsed";
  const METHOD_KEY = "artemis.panel.method";

  let catalog = null;
  const charts = Object.create(null);

  // ---- Configuration ----

  /**
   * Where to load the catalog from. Overridable the same way the viewer's
   * other endpoints are, so a dev copy can be pointed at without a rebuild.
   */
  function catalogUrl() {
    try {
      const fromQuery = new URLSearchParams(location.search).get("artemisCatalog");
      if (fromQuery) return fromQuery;
      const fromStorage = localStorage.getItem("artemisCatalog");
      if (fromStorage) return fromStorage;
    } catch (_) {
      /* private-mode storage access can throw; the default is fine */
    }
    return DEFAULT_CATALOG_URL;
  }

  // ---- DOM scaffold ----

  function buildScaffold() {
    const region = document.getElementById("map-region");
    if (!region) return null;

    const panel = document.createElement("aside");
    panel.id = "artemis-panel";
    panel.setAttribute("aria-label", "ARTEMIS imagery and embeddings");
    panel.innerHTML = `
      <button id="artemis-tab" class="artemis-tab" type="button"
              aria-expanded="true" aria-controls="artemis-panel-body"
              title="Show or hide the ARTEMIS panel">
        <span class="artemis-tab-chevron" aria-hidden="true">›</span>
        <span class="artemis-tab-label">Embeddings</span>
      </button>
      <div id="artemis-panel-body" class="artemis-body">
        <header class="artemis-header">
          <div>
            <div class="artemis-title">ARTEMIS</div>
            <div class="artemis-sub" id="artemis-subtitle">Imagery &amp; embeddings</div>
          </div>
        </header>
        <div class="artemis-scroll" id="artemis-sections"></div>
      </div>
    `;
    region.appendChild(panel);
    return panel;
  }

  function section(title, subtitle) {
    const element = document.createElement("section");
    element.className = "artemis-section";
    const heading = document.createElement("h3");
    heading.className = "artemis-section-title";
    heading.textContent = title;
    element.appendChild(heading);
    if (subtitle) {
      const sub = document.createElement("p");
      sub.className = "artemis-section-sub";
      sub.textContent = subtitle;
      element.appendChild(sub);
    }
    return element;
  }

  function button(label, title, onClick) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "artemis-btn";
    element.textContent = label;
    if (title) element.title = title;
    element.addEventListener("click", () => onClick(element));
    return element;
  }

  async function withBusy(element, label, work) {
    const original = element.textContent;
    element.disabled = true;
    element.textContent = label;
    try {
      await work();
    } catch (err) {
      State.toast(`ARTEMIS: ${err.message || err}`, "error");
      console.error("[artemis-panel]", err);
    } finally {
      element.disabled = false;
      element.textContent = original;
    }
  }

  // ---- Layer helpers ----

  function requireLayers() {
    if (!window.Layers) throw new Error("Viewer layer module not loaded");
    return window.Layers;
  }

  function addTileLayer(name, tileUrl, bounds, sourceDesc) {
    return requireLayers().addD2STileLayer({
      name,
      tileUrl,
      bounds,
      sourceDesc: sourceDesc || "Earth Engine tiles",
    });
  }

  /**
   * Add a GeoJSON layer and repaint it in its role color.
   *
   * The viewer paints every vector the same green, which is exactly wrong here:
   * the whole panel is about telling the imagery extent apart from the AOI, so
   * the two have to be distinguishable on the map at a glance.
   */
  async function addVectorLayer(spec, fallbackName) {
    if (!spec || !spec.url) throw new Error("No vector layer configured");

    const entry = await requireLayers().addLayerFromConfig({
      name: spec.name || fallbackName,
      type: "geojson",
      source: { kind: "builtin", url: spec.url },
    });

    const style = spec.style || {};
    const map = State.getMap();
    if (!map || !entry || !entry.layerId || !style.color) return entry;

    const paint = [
      [entry.layerId, "fill-color", style.color],
      [entry.layerId, "fill-opacity", style.fillOpacity ?? 0.1],
      [`${entry.layerId}_line`, "line-color", style.color],
      [`${entry.layerId}_line`, "line-width", style.lineWidth ?? 2],
      [`${entry.layerId}_circle`, "circle-color", style.color],
    ];
    paint.forEach(([layerId, property, value]) => {
      try {
        if (map.getLayer(layerId)) map.setPaintProperty(layerId, property, value);
      } catch (_) {
        /* a style that will not apply is not worth failing the add over */
      }
    });
    return entry;
  }

  /**
   * Add one NAIP year. Prefers an exported COG (durable, and the viewer can
   * compute zonal statistics over it) and falls back to Earth Engine tiles.
   */
  async function addNaipYear(entry, name) {
    const label = `NAIP ${entry.year} — ${name}`;
    if (entry.cog_url) {
      return requireLayers().addLayerFromConfig({
        name: label,
        type: "cog",
        source: { kind: "builtin", url: entry.cog_url },
      });
    }
    if (entry.tile_url) {
      return addTileLayer(label, entry.tile_url, entry.bounds, "Earth Engine · NAIP");
    }
    throw new Error(`${entry.year} has neither an exported COG nor a tile URL`);
  }

  /**
   * Add every year and wire them to the time bar.
   *
   * When every year has a COG, one native time-series layer is enough. Earth
   * Engine tile layers cannot be timesteps of a single layer, so they are added
   * individually and bound into a layer group, whose shared time widget scrubs
   * across members — the same playback either way.
   */
  async function addAllNaipYears(years, name) {
    const usable = years.filter((entry) => entry.cog_url || entry.tile_url);
    if (!usable.length) throw new Error("No year has an exported COG or a tile URL");

    if (usable.every((entry) => entry.cog_url)) {
      return requireLayers().addLayerFromConfig({
        name: `NAIP — ${name}`,
        type: "cog",
        times: usable.map((entry) => ({ label: entry.label, url: entry.cog_url })),
      });
    }

    const added = [];
    for (const entry of usable) {
      added.push(await addNaipYear(entry, name));
    }

    const layerIds = added.filter(Boolean).map((layer) => layer.id);
    if (layerIds.length > 1) {
      const group = State.createLayerGroup({
        name: `NAIP — ${name}`,
        layerIds,
        timeWidget: true,
      });
      if (group) State.setActiveTimeGroup(group.id);
    }
    return added;
  }

  // ---- Sections ----

  function renderVectors(container, data) {
    const { extent, aoi } = data.vectors;
    if (!extent && !aoi) return;

    const element = section(
      "Vector layers",
      "The extent is what imagery must cover; the AOI is what the embeddings are about."
    );

    const row = document.createElement("div");
    row.className = "artemis-row";

    if (extent) {
      row.appendChild(
        button("Add extent", extent.name, (btn) =>
          withBusy(btn, "Adding…", () => addVectorLayer(extent, "Imagery extent"))
        )
      );
    }
    if (aoi) {
      row.appendChild(
        button("Add AOI", aoi.name, (btn) =>
          withBusy(btn, "Adding…", () => addVectorLayer(aoi, "Area of interest"))
        )
      );
    }
    element.appendChild(row);

    const facts = document.createElement("dl");
    facts.className = "artemis-facts";
    const addFact = (term, value) => {
      const dt = document.createElement("dt");
      dt.textContent = term;
      const dd = document.createElement("dd");
      dd.textContent = value;
      facts.appendChild(dt);
      facts.appendChild(dd);
    };
    if (extent) addFact("Extent area", Core.formatArea(extent.area_ha));
    if (aoi) addFact("AOI area", Core.formatArea(aoi.area_ha));
    if (aoi && aoi.containment_in_extent !== undefined && aoi.containment_in_extent !== null) {
      addFact("AOI inside extent", Core.formatPercent(aoi.containment_in_extent, 2));
    }
    element.appendChild(facts);

    if (aoi && aoi.containment_in_extent !== undefined && aoi.containment_in_extent < 0.999) {
      element.appendChild(
        note(
          "warn",
          "Part of the AOI falls outside the imagery extent. That area has no imagery and " +
            "contributed no embedding samples."
        )
      );
    }

    container.appendChild(element);
  }

  function note(kind, text) {
    const element = document.createElement("p");
    element.className = `artemis-note artemis-note-${kind}`;
    element.textContent = text;
    return element;
  }

  function renderNaip(container, data) {
    if (!data.naip) return;

    const naip = data.naip;
    const element = section(
      "NAIP imagery",
      `${naip.collection}${naip.scaleM ? ` · ${naip.scaleM} m` : ""}${
        naip.bands.length ? ` · ${naip.bands.join("")}` : ""
      }`
    );

    if (naip.years.length > 1) {
      const row = document.createElement("div");
      row.className = "artemis-row";
      row.appendChild(
        button("Add all years to time bar", "Add every year and scrub with the time slider", (btn) =>
          withBusy(btn, "Adding…", () => addAllNaipYears(naip.years, data.name))
        )
      );
      element.appendChild(row);
    }

    const list = document.createElement("ul");
    list.className = "artemis-year-list";

    naip.years.forEach((year) => {
      const badge = Core.coverageBadge(year);
      const item = document.createElement("li");
      item.className = "artemis-year";

      const meta = document.createElement("div");
      meta.className = "artemis-year-meta";

      const heading = document.createElement("div");
      heading.className = "artemis-year-head";
      const label = document.createElement("span");
      label.className = "artemis-year-label";
      label.textContent = year.label;
      const chip = document.createElement("span");
      chip.className = `artemis-chip artemis-chip-${badge.kind}`;
      chip.textContent = badge.label;
      heading.appendChild(label);
      heading.appendChild(chip);

      const detail = document.createElement("div");
      detail.className = "artemis-year-detail";
      detail.textContent = Core.describeCoverage(year);

      meta.appendChild(heading);
      meta.appendChild(detail);

      if (!year.cog_url && year.tile_url) {
        const age = Core.tileAge(year.tile_url_generated_utc);
        const source = document.createElement("div");
        source.className = `artemis-year-source${age.stale ? " artemis-stale" : ""}`;
        source.textContent = age.stale
          ? `Earth Engine tiles, ${age.label} — may have expired`
          : `Earth Engine tiles, ${age.label}`;
        meta.appendChild(source);
      }

      item.appendChild(meta);

      const hasSource = Boolean(year.cog_url || year.tile_url);
      const add = button("Add", `Add NAIP ${year.label} to the map`, (btn) =>
        withBusy(btn, "…", () => addNaipYear(year, data.name))
      );
      add.disabled = !hasSource;
      if (!hasSource) add.title = "No exported COG and no tile URL for this year";
      item.appendChild(add);

      list.appendChild(item);
    });

    element.appendChild(list);

    if (naip.incompleteYears.length) {
      element.appendChild(
        note(
          "warn",
          `Incomplete coverage: ${naip.incompleteYears.join(", ")}. These mosaics have holes ` +
            "inside the extent — treat any comparison against them with care."
        )
      );
    }

    container.appendChild(element);
  }

  function renderEmbeddings(container, data) {
    const embeddings = data.embeddings;
    if (!embeddings) {
      const element = section("Embeddings");
      element.appendChild(
        note(
          "info",
          "No embedding run in this catalog. Generate one with " +
            "pipeline/s5_imagery/embeddings.py, then rebuild the catalog."
        )
      );
      container.appendChild(element);
      return;
    }

    const runs = embeddings.runs || [];
    if (!runs.length) {
      const element = section("Embedding clusters");
      element.appendChild(
        note(
          "warn",
          "This catalog has an embeddings block but no clustering runs. It was probably " +
            "written by an older embeddings.py — regenerate it."
        )
      );
      container.appendChild(element);
      return;
    }

    const summary = Core.summarizeSeparability(embeddings, runs[0]);
    const element = section(
      "Embedding clusters",
      `${embeddings.collection} ${embeddings.year} · ` +
        `${summary.insideCount} inside / ${summary.outsideCount} outside samples`
    );

    // ---- Method selector ----

    const field = document.createElement("label");
    field.className = "artemis-field";
    const fieldLabel = document.createElement("span");
    fieldLabel.textContent = "Clustering method";
    const select = document.createElement("select");
    select.id = "artemis-method";
    select.className = "artemis-select";
    runs.forEach((run) => {
      const option = document.createElement("option");
      option.value = run.method;
      option.textContent = `${run.label} · k=${run.k_observed}`;
      select.appendChild(option);
    });
    field.appendChild(fieldLabel);
    field.appendChild(select);
    element.appendChild(field);

    const methodNote = document.createElement("p");
    methodNote.className = "artemis-method-desc";
    element.appendChild(methodNote);

    // ---- Layer button ----

    const layerRow = document.createElement("div");
    layerRow.className = "artemis-row";
    element.appendChild(layerRow);

    const layerNote = document.createElement("div");
    element.appendChild(layerNote);

    // ---- Separability readout ----

    const readout = document.createElement("div");
    readout.className = "artemis-readout";
    const metric = document.createElement("div");
    metric.className = "artemis-metric";
    const metricValue = document.createElement("div");
    metricValue.className = "artemis-metric-value";
    const metricLabel = document.createElement("div");
    metricLabel.className = "artemis-metric-label";
    metricLabel.textContent = "Inside vs outside divergence";
    metric.appendChild(metricValue);
    metric.appendChild(metricLabel);
    readout.appendChild(metric);
    element.appendChild(readout);

    const interpretation = document.createElement("p");
    interpretation.className = "artemis-interpretation";
    element.appendChild(interpretation);

    element.appendChild(
      note(
        "info",
        "Jensen-Shannon divergence, 0 to 1, between the inside-AOI and outside-AOI cluster " +
          "distributions. 0 means the boundary is invisible in embedding space; 1 means the " +
          "two sides share no cluster."
      )
    );

    container.appendChild(element);

    if (runs.length > 1) renderMethodComparison(container, embeddings, select);

    const chartRefs = renderCharts(container, embeddings, summary);
    const tableBody = renderClusterTable(container);

    // ---- Method switching ----

    function applyRun(method) {
      const run = Core.selectRun(embeddings, method);
      if (!run) return;

      select.value = run.method;
      methodNote.textContent = run.description || "";
      metricValue.textContent = (run.separability || {}).jensen_shannon_divergence !== undefined
        ? Number(run.separability.jensen_shannon_divergence).toFixed(3)
        : "—";
      interpretation.textContent = (run.separability || {}).interpretation || "";

      updateLayerRow(layerRow, layerNote, embeddings, run);
      updateCharts(chartRefs, embeddings, run);
      updateClusterTable(tableBody, run);

      document
        .querySelectorAll(".artemis-method-row")
        .forEach((row) => row.classList.toggle("active", row.dataset.method === run.method));
    }

    select.addEventListener("change", () => {
      applyRun(select.value);
      try {
        localStorage.setItem(METHOD_KEY, select.value);
      } catch (_) {
        /* preference is best-effort */
      }
    });

    let initial = embeddings.default_method;
    try {
      const saved = localStorage.getItem(METHOD_KEY);
      if (saved && runs.some((run) => run.method === saved)) initial = saved;
    } catch (_) {
      /* fall back to the catalog's default */
    }
    applyRun(initial);
  }

  /**
   * A ranked list of every method that was run, by how strongly it separates
   * inside from outside on the same samples.
   *
   * This is the point of running more than one: the rows are directly comparable
   * because every method clustered an identical sample.
   */
  function renderMethodComparison(container, embeddings, select) {
    const rows = Core.methodSummaries(embeddings);
    const element = section(
      "Method comparison",
      "Same samples, same embeddings — only the clustering differs. Higher divergence " +
        "means that method found a sharper inside/outside split."
    );

    const list = document.createElement("ul");
    list.className = "artemis-method-list";

    rows
      .slice()
      .sort((a, b) => b.divergence - a.divergence)
      .forEach((row) => {
        const item = document.createElement("li");
        item.className = "artemis-method-row";
        item.dataset.method = row.method;
        item.tabIndex = 0;
        item.title = row.description;

        const meta = document.createElement("div");
        meta.className = "artemis-method-meta";
        const name = document.createElement("div");
        name.className = "artemis-method-name";
        name.textContent = row.label;
        const detail = document.createElement("div");
        detail.className = "artemis-method-detail";
        detail.textContent = `k=${row.k}${row.autoK ? " (auto)" : ""}`;
        meta.appendChild(name);
        meta.appendChild(detail);

        const value = document.createElement("div");
        value.className = "artemis-method-value";
        value.textContent = row.divergenceLabel;
        if (row.best) {
          const badge = document.createElement("span");
          badge.className = "artemis-chip artemis-chip-complete";
          badge.textContent = "strongest";
          detail.appendChild(document.createTextNode(" "));
          detail.appendChild(badge);
        }

        item.appendChild(meta);
        item.appendChild(value);

        const choose = () => {
          select.value = row.method;
          select.dispatchEvent(new Event("change"));
        };
        item.addEventListener("click", choose);
        item.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            choose();
          }
        });

        list.appendChild(item);
      });

    element.appendChild(list);
    element.appendChild(
      note(
        "info",
        "A stronger split is not automatically the right answer — it says the method is " +
          "responding to something that lines up with the boundary, not what that something is."
      )
    );
    container.appendChild(element);
  }

  function updateLayerRow(row, noteHost, embeddings, run) {
    row.innerHTML = "";
    noteHost.innerHTML = "";

    const layers = run.layers || {};
    if (!layers.cluster_cog_url && !layers.cluster_tile_url) {
      noteHost.appendChild(
        note("info", `No cluster raster for ${run.label}. Re-run with --export-clusters to add one.`)
      );
      return;
    }

    row.appendChild(
      button("Add cluster layer", `Add the ${run.label} raster`, (btn) =>
        withBusy(btn, "Adding…", async () => {
          const name = `Clusters ${embeddings.year} · ${run.label}`;
          if (layers.cluster_cog_url) {
            await requireLayers().addLayerFromConfig({
              name,
              type: "cog",
              source: { kind: "builtin", url: layers.cluster_cog_url },
              style: { colormap: "viridis", min: 0, max: Math.max(0, run.k_observed - 1) },
            });
          } else {
            await addTileLayer(
              name,
              layers.cluster_tile_url,
              (embeddings.extent || {}).bounds,
              `Earth Engine · ${run.label}`
            );
          }
        })
      )
    );

    if (!layers.cluster_cog_url && layers.cluster_tile_url) {
      const age = Core.tileAge(layers.tile_url_generated_utc);
      if (age.stale) {
        noteHost.appendChild(
          note("warn", `Cluster tiles ${age.label} — the Earth Engine URL may have expired.`)
        );
      }
    }
  }

  function chartBlock(container, title, subtitle, canvasId) {
    const element = section(title, subtitle);
    const wrap = document.createElement("div");
    wrap.className = "artemis-chart";
    const canvas = document.createElement("canvas");
    canvas.id = canvasId;
    wrap.appendChild(canvas);
    element.appendChild(wrap);
    container.appendChild(element);
    return canvas;
  }

  /**
   * Build the three chart shells once.
   *
   * They are created empty and filled by updateCharts, so switching methods
   * mutates data in place rather than tearing down and rebuilding canvases —
   * no flicker, and the scatter's zoom/axis scale stays put between methods.
   */
  function renderCharts(container, embeddings, summary) {
    if (typeof Chart === "undefined") {
      container.appendChild(note("warn", "Chart.js is not loaded; charts unavailable."));
      return null;
    }

    const gridColor = "rgba(154, 164, 178, 0.15)";
    const tickColor = "#9aa4b2";
    const baseScales = {
      x: { ticks: { color: tickColor }, grid: { color: gridColor } },
      y: { ticks: { color: tickColor }, grid: { color: gridColor } },
    };
    const legend = { labels: { color: "#e6edf3", boxWidth: 12, usePointStyle: true } };

    const shareCanvas = chartBlock(
      container,
      "Cluster share: inside vs outside",
      "Share of each side's samples falling in each cluster. Equal bars mean the AOI " +
        "boundary does not register in embedding space.",
      "artemis-chart-share"
    );
    charts.share = new Chart(shareCanvas, {
      type: "bar",
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend },
        scales: {
          ...baseScales,
          y: {
            ...baseScales.y,
            beginAtZero: true,
            title: { display: true, text: "% of side's samples", color: tickColor },
          },
        },
      },
    });

    const fractionCanvas = chartBlock(
      container,
      "Composition of each cluster",
      "Of the samples in a cluster, how many sit inside the AOI. Bars far from 50% mark " +
        "clusters that belong to one side.",
      "artemis-chart-fraction"
    );
    charts.fraction = new Chart(fractionCanvas, {
      type: "bar",
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false } },
        scales: {
          ...baseScales,
          y: {
            ...baseScales.y,
            beginAtZero: true,
            max: 100,
            title: { display: true, text: "% inside AOI", color: tickColor },
          },
        },
      },
    });

    const points = (embeddings.scatter && embeddings.scatter.points) || [];
    if (points.length) {
      const scatterCanvas = chartBlock(
        container,
        "Embedding space (PCA)",
        `Sampled embeddings on their first two principal components${
          summary.varianceLabel ? ` · ${summary.varianceLabel}` : ""
        }. Color is cluster; filled circles are inside the AOI, outlined triangles outside. ` +
          "The cloud is identical across methods — only the coloring changes.",
        "artemis-chart-scatter"
      );
      charts.scatter = new Chart(scatterCanvas, {
        type: "scatter",
        data: { datasets: [] },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: {
            legend: {
              ...legend,
              labels: {
                ...legend.labels,
                // Point color carries cluster identity here, so the legend must
                // not imply it means inside/outside. Neutral swatches leave the
                // legend saying only what the shapes mean.
                generateLabels: (chart) =>
                  chart.data.datasets.map((dataset, index) => ({
                    text: dataset.label,
                    pointStyle: dataset.pointStyle,
                    fillStyle: index === 0 ? "#9aa4b2" : "transparent",
                    strokeStyle: "#9aa4b2",
                    lineWidth: 1.5,
                    fontColor: "#e6edf3",
                    datasetIndex: index,
                    hidden: !chart.isDatasetVisible(index),
                  })),
              },
            },
          },
          scales: {
            x: { ...baseScales.x, title: { display: true, text: "PC1", color: tickColor } },
            y: { ...baseScales.y, title: { display: true, text: "PC2", color: tickColor } },
          },
        },
      });
    }

    return charts;
  }

  function updateCharts(chartRefs, embeddings, run) {
    if (!chartRefs) return;

    if (chartRefs.share) {
      chartRefs.share.data = Core.clusterShareChartData(run.clusters);
      chartRefs.share.update();
    }
    if (chartRefs.fraction) {
      chartRefs.fraction.data = Core.insideFractionChartData(run.clusters);
      chartRefs.fraction.update();
    }
    if (chartRefs.scatter) {
      chartRefs.scatter.data = Core.scatterChartData(
        embeddings.scatter,
        run.palette,
        run.cluster_by_point
      );
      chartRefs.scatter.update();
    }
  }

  function renderClusterTable(container) {
    const element = section("Cluster detail");
    const table = document.createElement("table");
    table.className = "artemis-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th>Cluster</th><th>Inside</th><th>Outside</th><th>% inside</th>
        </tr>
      </thead>
    `;
    const body = document.createElement("tbody");
    table.appendChild(body);
    element.appendChild(table);
    container.appendChild(element);
    return body;
  }

  function updateClusterTable(body, run) {
    if (!body) return;
    body.innerHTML = "";

    (run.clusters || []).forEach((cluster) => {
      const row = document.createElement("tr");

      const idCell = document.createElement("td");
      const swatch = document.createElement("span");
      swatch.className = "artemis-swatch";
      swatch.style.background = cluster.color || "#999999";
      idCell.appendChild(swatch);
      idCell.appendChild(document.createTextNode(`C${cluster.cluster}`));

      const insideCell = document.createElement("td");
      insideCell.textContent = cluster.inside_count;
      const outsideCell = document.createElement("td");
      outsideCell.textContent = cluster.outside_count;
      const fractionCell = document.createElement("td");
      fractionCell.textContent = Core.formatPercent(cluster.inside_fraction, 0);

      [idCell, insideCell, outsideCell, fractionCell].forEach((cell) => row.appendChild(cell));
      body.appendChild(row);
    });
  }

  // ---- Render ----

  function render(data) {
    const container = document.getElementById("artemis-sections");
    if (!container) return;

    Object.values(charts).forEach((chart) => chart && chart.destroy());
    Object.keys(charts).forEach((key) => delete charts[key]);
    container.innerHTML = "";

    const subtitle = document.getElementById("artemis-subtitle");
    if (subtitle) subtitle.textContent = data.name;

    if (data.schema && !data.schemaMatches) {
      container.appendChild(
        note(
          "warn",
          `Catalog schema is ${data.schema}, expected ${Core.CATALOG_SCHEMA}. Some fields may ` +
            "be missing — regenerate with the current viewer_catalog.py."
        )
      );
    }

    renderVectors(container, data);
    renderNaip(container, data);
    renderEmbeddings(container, data);
  }

  function renderMissingCatalog(url, error) {
    const container = document.getElementById("artemis-sections");
    if (!container) return;
    container.innerHTML = "";
    container.appendChild(note("warn", `Could not load ${url}: ${error}`));
    const help = document.createElement("pre");
    help.className = "artemis-code";
    help.textContent =
      "uv run python -m pipeline.s5_imagery.viewer_catalog \\\n" +
      "  --naip-manifest data/interim/naip/<slug>/naip_manifest.json \\\n" +
      "  --clusters data/interim/embeddings/<slug>/clusters.json";
    container.appendChild(help);
    container.appendChild(
      note("info", "Then serve the viewer with viewer/serve_viewer.py so the catalog is in place.")
    );
  }

  // ---- Collapse ----

  function initTab(panel) {
    const tab = document.getElementById("artemis-tab");
    const region = document.getElementById("map-region");
    if (!tab) return;

    function apply(collapsed) {
      panel.classList.toggle("collapsed", collapsed);
      if (region) region.classList.toggle("artemis-open", !collapsed);
      tab.setAttribute("aria-expanded", String(!collapsed));
      tab.title = collapsed ? "Show the ARTEMIS panel" : "Hide the ARTEMIS panel";
      // Charts sized while the panel was off-screen need a nudge once it is back.
      if (!collapsed) {
        requestAnimationFrame(() => {
          Object.values(charts).forEach((chart) => chart && chart.resize());
        });
      }
    }

    let collapsed = false;
    try {
      collapsed = localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch (_) {
      /* default to open */
    }
    apply(collapsed);

    tab.addEventListener("click", () => {
      collapsed = !panel.classList.contains("collapsed");
      apply(collapsed);
      try {
        localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
      } catch (_) {
        /* preference is best-effort */
      }
    });
  }

  // ---- Bootstrap ----

  async function init() {
    if (!Core) {
      console.error("[artemis-panel] artemis-panel-core.js must load first");
      return;
    }
    if (!State) {
      console.error("[artemis-panel] map viewer state not found");
      return;
    }

    const panel = buildScaffold();
    if (!panel) {
      console.error("[artemis-panel] #map-region not found; is this the PERSEUS viewer?");
      return;
    }
    initTab(panel);

    const url = catalogUrl();
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      catalog = Core.normalizeCatalog(await response.json());
      render(catalog);
    } catch (err) {
      console.warn("[artemis-panel] no catalog:", err);
      renderMissingCatalog(url, err.message || String(err));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
