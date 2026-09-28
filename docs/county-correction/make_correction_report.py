"""Render Union County correction rasters using the existing ARTEMIS deck shell.

Run after pipeline.s1_initial_state.run_county_correction. Figures contain source raster cells;
no basemap imagery or example observations are synthesized.
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import json
from pathlib import Path
import re
import sys
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Rectangle
import numpy as np
import rasterio
from rasterio.warp import transform as transform_xy
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pipeline.s1_initial_state.stratify_treemap_holes import NON_FIA_TREE_PREFIXES  # noqa: E402

HISTORY = ["#d5d5d5", "#155d27", "#c7a35d"]
FOREST = ["#fff2c6", "#243b80", "#e3342f", "#00a6a6"]
FOREST_LABELS = ["No TreeMap ID / no recovery", "Existing TreeMap ID", "Provisional donor proposal", "Recovered forest, no donor"]
HISTORY_LABELS = ["Other vegetation", "FIA-compatible tree", "Recently logged"]
OWNERS = ["#404040", "#d9cfbe", "#63d3f2", "#b5533c", "#f5d000", "#642a89", "#788c00", "#f27600", "#753b16"]
OWNER_LABELS = ["0 Unknown forest", "1 Nonforest", "2 Water", "3 Family", "4 Corporate", "5 Tribal", "6 Federal", "7 State", "8 Local"]
STRATA_COLORS = ["#edf0f4", "#a6d854", "#5648c8", "#e89086", "#00635a", "#8394a7"]
STRATA_LABELS = ["Outside hole strata", "S1 Logged then regrown", "S2 Tree in both bookends", "S3 Tree then open", "S4 Regrown only", "S5 No historical evidence"]


def read(path, reference=None):
    with rasterio.open(path) as src:
        if reference and (src.shape != reference["shape"] or src.crs != reference["crs"] or not np.allclose(tuple(src.transform), tuple(reference["transform"]), rtol=0, atol=1e-8)):
            raise ValueError(f"Raster does not share output grid: {path}")
        data = src.read(1, masked=True)
        grid = {"shape": src.shape, "crs": src.crs, "transform": src.transform}
    return data, grid


def example_windows(accepted, count=3, half_width=65):
    """Select largest 8-connected patches with deterministic row-major tie breaks."""
    labels, _ = ndimage.label(accepted, structure=np.ones((3, 3)))
    sizes = np.bincount(labels.ravel())
    ordered = sorted(range(1, len(sizes)), key=lambda i: (-sizes[i], i))
    selected = []
    for label in ordered:
        cells = np.argwhere(labels == label)
        center = cells[np.argmin(((cells - cells.mean(axis=0)) ** 2).sum(axis=1))]
        row, col = map(int, center)
        if any(abs(row - e["row"]) < half_width and abs(col - e["col"]) < half_width for e in selected):
            continue
        selected.append({"row": row, "col": col, "component_pixels": int(sizes[label]),
                         "window": [max(0, row-half_width), min(accepted.shape[0], row+half_width+1),
                                    max(0, col-half_width), min(accepted.shape[1], col+half_width+1)]})
        if len(selected) == count:
            break
    return selected


def extent(grid, window):
    r0, r1, c0, c1 = window
    t = grid["transform"]
    left, top = t * (c0, r0)
    right, bottom = t * (c1, r1)
    return left, right, bottom, top


def map_panel(ax, data, title, grid, colors, window=None, *, vmin=0, vmax=None):
    window = window or [0, data.shape[0], 0, data.shape[1]]
    r0, r1, c0, c1 = window
    ext = extent(grid, window)
    if colors is None:
        ax.imshow(data[r0:r1, c0:c1], extent=ext, interpolation="nearest")
    elif isinstance(colors, list):
        cmap = ListedColormap(colors)
        norm = BoundaryNorm(np.arange(len(colors)+1)-.5, len(colors))
        ax.imshow(data[r0:r1, c0:c1], extent=ext, cmap=cmap, norm=norm, interpolation="nearest")
    else:
        ax.imshow(data[r0:r1, c0:c1], extent=ext, cmap=colors, vmin=vmin, vmax=vmax, interpolation="nearest")
    ax.set_title(title, fontsize=12, loc="left", fontweight="bold", pad=9)
    ax.set_xticks(np.linspace(ext[0], ext[1], 3))
    ax.set_yticks(np.linspace(ext[2], ext[3], 3))
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.tick_params(labelsize=7)
    ax.set_xlabel("Easting, m · EPSG:5070", fontsize=8)
    ax.set_ylabel("Northing, m", fontsize=8)
    width = ext[1] - ext[0]
    scale = 1000 if width < 12000 else 5000
    x, y = ext[0]+width*.06, ext[2]+(ext[3]-ext[2])*.07
    ax.plot([x, x+scale], [y, y], color="#202820", lw=3)
    ax.text(x, y+(ext[3]-ext[2])*.02, f"{scale/1000:g} km", fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": .8})
    ax.text(.96, .96, "N ↑", transform=ax.transAxes, ha="right", va="top", fontsize=9)
    for spine in ax.spines.values():
        spine.set_color("#cccfc7")


def key_card(groups):
    """HTML legend rendered beside a figure in the deck, not inside the PNG.

    groups: (title, colors, names) tuples; one titled section per group.
    """
    sections = ''.join(
        f'<h4>{html.escape(title)}</h4><ul>'
        + ''.join(f'<li><span class="sw" style="background:{c}"></span>{html.escape(n)}</li>' for c, n in zip(colors, names))
        + '</ul>'
        for title, colors, names in groups)
    return f'<aside class="key">{sections}</aside>'


def save(fig, path):
    fig.savefig(path, dpi=180, facecolor="#ffffff", bbox_inches="tight")
    plt.close(fig)


def anchor_diagnostics(path, metrics):
    """Horizontal bars for the anchor-proxy diagnostics with a chance-level line."""
    rows = [("6 km block cross-validation AUC", metrics["auc_block_cv"], "#243b80"),
            ("Shuffled-label AUC control", metrics["auc_label_shuffle"], "#b9bdb4"),
            ("Full-funnel balanced accuracy", metrics["pipeline_balanced_accuracy"], "#155d27")]
    fig, ax = plt.subplots(figsize=(13, 3.4))
    y = np.arange(len(rows))
    for i, (label, value, color) in enumerate(rows):
        ax.barh(i, value, height=.58, color=color)
        ax.text(value + .012, i, f"{value:.3f}", va="center", fontsize=10, color="#1b221c")
    ax.axvline(.5, color="#a4562a", lw=1.4, ls="--")
    ax.text(.505, len(rows) - .32, "chance (0.5)", fontsize=9, color="#a4562a")
    ax.set_yticks(y, [r[0] for r in rows], fontsize=11)
    ax.set_xlim(0, 1.0)
    ax.set_xticks(np.linspace(0, 1, 6))
    ax.tick_params(labelsize=9)
    ax.set_xlabel("Score, 0–1", fontsize=10)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#cccfc7")
    fig.tight_layout()
    save(fig, path)


def union_fia_figure(path, fia):
    """Before/after forest-acreage bars with the FIA estimate and 95% CI overlayed."""
    before, after = fia["treemap_forest_acres"], fia["corrected_acres"]
    low, high, estimate = fia["ci95_low"], fia["ci95_high"], fia["evalidator_estimate_acres"]
    fig, ax = plt.subplots(figsize=(13, 6.0))
    ax.bar([0, 1], [before, after], width=.5, color=["#c7a35d", "#155d27"])
    for x, value, note in [(0, before, f'{fia["shortfall_se_multiples"]:.2f} SE below FIA'),
                           (1, after, f'{fia["remaining_se_multiples"]:.2f} SE below FIA')]:
        ax.text(x, value + 3200, f"{value:,.0f} ac\n{note}", ha="center", fontsize=10,
               color="#1b221c", linespacing=1.35)
    # Rotated box-and-whisker: the box spans the 95% interval, the estimate is
    # its median line; the CI is all EVALIDator returns, so the whiskers ARE the box.
    span = (-0.42, 1.42)
    ax.fill_between(span, low, high, color="#e3342f", alpha=.10)
    ax.plot(span, [estimate, estimate], color="#e3342f", lw=2)
    for edge in (low, high):
        ax.plot(span, [edge, edge], color="#e3342f", lw=1.3)
        for x in span:  # caps
            ax.plot([x, x], [edge - high * .012, edge + high * .012], color="#e3342f", lw=1.3)
    ax.annotate(f'FIA EVALIDator estimate {estimate:,.0f} ac\nSE {fia["se_percent"]:.2f}% ({fia["plots"]} plots) · 95% CI {low:,.0f} – {high:,.0f} ac',
                (span[1], high), xytext=(6, 4), textcoords="offset points", ha="right",
                va="bottom", fontsize=10, color="#a12f27", linespacing=1.4)
    ax.set_xticks([0, 1], ["TreeMap forest, as published", "After correction (+ recovered forest)"], fontsize=11)
    ax.set_xlim(-0.6, 1.6)
    ax.set_ylim(0, high * 1.14)
    ax.get_yaxis().set_major_formatter(lambda v, _pos: f"{v:,.0f}")
    ax.tick_params(labelsize=9)
    ax.set_ylabel("Forest land, acres", fontsize=10)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#cccfc7")
    fig.tight_layout()
    save(fig, path)


def historical(path, lookup, grid):
    data, _ = read(path, grid)
    classes = np.zeros(data.shape, dtype=np.uint8)
    mapped = np.zeros(data.shape, dtype=bool)
    with lookup.open(newline="") as stream:
        for row in csv.DictReader(stream):
            code = int(row["VALUE"])
            if code < 0:
                continue
            cells = data.data == code
            mapped |= cells
            name = row["EVT_NAME"]
            if row["EVT_LF"] == "Tree" and not name.startswith(NON_FIA_TREE_PREFIXES):
                classes[cells] = 1
            if name.startswith("Recently Logged"):
                classes[cells] = 2
    return np.ma.array(classes, mask=np.ma.getmaskarray(data) | ~mapped)


def figure(path, caption):
    image = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<figure class="fig"><div class="map-window"><img src="data:image/png;base64,{image}" alt="{html.escape(caption, quote=True)}"></div><figcaption>{html.escape(caption)} <a href="{html.escape(path.name)}" target="_blank" rel="noopener">Open full-size map</a>. On narrow screens, swipe within the map to pan.</figcaption></figure>'


def slide(identifier, title, body, source, *, title_slide=False):
    cls = "slide title-slide" if title_slide else "slide"
    return f'<section class="{cls}" id="{identifier}"><div class="inner"><p class="eyebrow">ARTEMIS · Union County · 14 September 2026</p><h2>{html.escape(title)}</h2>{body}<p class="srcline">{html.escape(source)}</p></div></section>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/county_correction")
    parser.add_argument("--artifact-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    output = args.data_dir / "output"
    manifest = json.loads((output / "manifest.json").read_text())
    dest = args.artifact_dir
    dest.mkdir(parents=True, exist_ok=True)
    fia = json.loads((dest / "union_fia_check.json").read_text())
    rasters = {}
    grid = None
    for name in ("treemap_original", "treemap_donor_proposal", "treemap_status", "forest_recovery",
                 "ownership_original", "ownership_corrected", "ownership_status", "strata", "probability", "similarity"):
        rasters[name], grid = read(output / f"{name}.tif", grid)
    if str(grid["crs"]) != "EPSG:5070":
        raise ValueError("The presentation requires the county EPSG:5070 metric grid")
    area_ha = abs(grid["transform"].a * grid["transform"].e) / 10000
    county_mask = np.ma.getmaskarray(rasters["forest_recovery"])
    accepted = rasters["forest_recovery"].filled(0) == 1
    examples = example_windows(accepted)
    features = []
    for i, example in enumerate(examples, 1):
        x, y = grid["transform"] * (example["col"]+.5, example["row"]+.5)
        lon, lat = transform_xy(grid["crs"], "EPSG:4326", [x], [y])
        example.update({"example": i, "longitude": lon[0], "latitude": lat[0]})
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon[0], lat[0]]}, "properties": example})
    (output / "examples.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2)+"\n")
    forest_maps = []
    for name in ("treemap_original", "treemap_donor_proposal"):
        raster = rasters[name]
        classes = ((raster.data > 0) & ~np.ma.getmaskarray(raster)).astype(np.uint8)
        if name == "treemap_donor_proposal":
            classes[accepted] = 3
            classes[rasters["treemap_status"].filled(0) == 2] = 2
        forest_maps.append(np.ma.array(classes, mask=county_mask))
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.5))
    for ax, data, title in zip(axes, forest_maps, ["Original TreeMap forest extent", "Proposed forest extent + donor assignments"]):
        map_panel(ax, data, title, grid, FOREST)
        for example in examples:
            left, right, bottom, top = extent(grid, example["window"])
            ax.add_patch(Rectangle((left, bottom), right-left, top-bottom, fill=False, ec="#121212", lw=1))
            ax.text(left, top, str(example["example"]), fontsize=9, bbox={"facecolor": "white", "edgecolor": "none"})
    fig.subplots_adjust(bottom=.06, top=.97, wspace=.3)
    save(fig, dest / "treemap_comparison.png")
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.5))
    for ax, name, title in zip(axes, ["ownership_original", "ownership_corrected"], ["Original woodland ownership", "Forest domain + same-parcel recovery"]):
        map_panel(ax, rasters[name], title, grid, OWNERS)
    fig.subplots_adjust(bottom=.06, top=.97, wspace=.3)
    save(fig, dest / "ownership_comparison.png")
    history = {year: historical(args.data_dir / f"evt_{year}.tif", args.data_dir / f"evt_{year}.csv", grid) for year in (2016, 2022, 2024)}
    for example in examples:
        fig, axes = plt.subplots(2, 3, figsize=(14, 8.4))
        for ax, year in zip(axes[0], history):
            map_panel(ax, history[year], f"LANDFIRE EVT {year}", grid, HISTORY, example["window"])
        map_panel(axes[1, 0], rasters["strata"], "Historical evidence stratum", grid, STRATA_COLORS, example["window"])
        map_panel(axes[1, 1], forest_maps[1], "TreeMap donor proposal", grid, FOREST, example["window"])
        map_panel(axes[1, 2], rasters["ownership_corrected"], "Ownership after recovery", grid, OWNERS, example["window"])
        fig.subplots_adjust(bottom=.07, top=.95, wspace=.33, hspace=.22)
        save(fig, dest / f'example_{example["example"]}.png')
    summaries = {row["metric"]: row["pixels"] for row in manifest["summary"]}
    # The decoded first three latent coordinates are a visualization, not optical RGB.
    from research.county_correction.acquire_embeddings import decode_embeddings
    embedding_path = args.data_dir / "embeddings_2022.tif"
    read(embedding_path, grid)
    with rasterio.open(embedding_path) as src:
        bands = decode_embeddings(src.read())[:3]
    valid_embedding = np.isfinite(bands).all(axis=0) & ~county_mask
    rgb = np.zeros((*county_mask.shape, 4), dtype=np.float32)
    for channel in range(3):
        low, high = np.quantile(bands[channel, valid_embedding], [.02, .98])
        if high <= low:
            raise ValueError("Embedding color channel has no usable range")
        rgb[..., channel] = np.nan_to_num(np.clip((bands[channel]-low)/(high-low), 0, 1))
    rgb[..., 3] = valid_embedding.astype(np.float32)
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.5))
    map_panel(axes[0], rgb, "2022 embedding coordinates A00/A01/A02", grid, None)
    map_panel(axes[1], rasters["probability"], "Model forest probability", grid, "viridis", vmax=1)
    # The colorbar gets its own axes below the right panel instead of stealing
    # height from it, so both maps stay level with each other vertically.
    fig.subplots_adjust(bottom=.16, top=.97, left=.07, right=.99, wspace=.3)
    pos = axes[1].get_position()
    cax = fig.add_axes([pos.x0, pos.y0 - .075, pos.width, .015])
    fig.colorbar(axes[1].images[0], cax=cax, orientation="horizontal")
    save(fig, dest / "embedding_evidence.png")
    recovered = int(accepted.sum())
    proposed = int((rasters["treemap_status"].filled(0) == 2).sum())
    unknown = int((rasters["ownership_status"].filled(0) == 1).sum())
    parcel = int((rasters["ownership_status"].filled(0) == 2).sum())
    for metric, actual in [("forest_recovered", recovered), ("treemap_donor_proposals", proposed), ("ownership_unknown_forest", unknown), ("ownership_same_parcel", parcel)]:
        if summaries[metric] != actual:
            raise ValueError(f"Manifest metric does not match raster: {metric}")
    tiles = '<div class="tiles">' + ''.join(f'<div class="tile"><div class="n">{n:,}</div><div class="l">{label}</div></div>' for n, label in [(recovered, "accepted forest-recovery pixels"), (proposed, "provisional TreeMap donor pixels"), (unknown, "forest pixels with unknown owner"), (parcel, "same-parcel owner assignments")]) + '</div>'
    slides = [slide("title", "Separate repairs for forest extent and ownership", '<p class="lede">Union County, Florida, FIPS 12125. Historical vegetation classifications and satellite embeddings support forest recovery. Parcel evidence supplies ownership classes where available.</p>'+tiles+f'<p class="sub">30 m cells. Accepted forest recovery covers {recovered*area_ha:,.1f} hectares.</p><div class="caveat">TreeMap IDs are donor proposals. These donor proposals require regeneration verification before use in FVS. Ownership classes are inferred from agreement within a parcel; they are not legal title verification.</div>', "output rasters; manifest.json; county_correction.py", title_slide=True)]
    slides.append(slide("method", "Preserve the earlier evidence funnel", '<div class="cards"><div class="tcard"><h4>Bookended vegetation</h4><p>S1 records logged vegetation followed by tree cover. S2 records tree cover in both bookends at a TreeMap hole. S3 and S4 additionally require the configured probability and embedding similarity thresholds. S5 has no recovery evidence.</p></div><div class="tcard"><h4>Separate assignment rules</h4><p>TreeMap selects an eligible donor within the distance and embedding thresholds. Ownership retains known codes and water. Recovered forest remains unknown unless observed known-owner cells in the same parcel agree.</p></div></div><div class="caveat">LANDFIRE EVT panels are classified vegetation products, not aerial photographs. Largest accepted patches are selected for inspection; they do not estimate correction accuracy.</div>', "stratify_treemap_holes.py; county_correction.py; manifest.json"))
    slides.append(slide("obata", "Time-series change detection can date the recoveries", '<div class="cards"><div class="tcard"><h4>The Obata method</h4><p>Shingo Obata\'s detection work (charlesmerritt/GEE-raster-correct; FORESTSAT 2018) runs dense Landsat 5/7 time series through two detectors in Google Earth Engine — a spectral-threshold rule and a statistical boundary method — and emits one raster whose pixel value is the year of the last disturbance, 1984–2016. The same project pairs per-pixel harmonic regression with FIA plots to estimate growing-stock volume.</p></div><div class="tcard"><h4>Why it belongs in this funnel</h4><p>Our S1 stratum is bookend evidence: logged vegetation followed by tree cover, but LANDFIRE cannot date the disturbance and leaves a 6–9 year blind spot. An Obata-style last-disturbance-year raster would corroborate the recovery strata independently, give each accepted recovery an event date for stand age, and — on the statewide path — stand in for the absent LANDFIRE Annual Disturbance 1999–2023 layer.</p></div></div><div class="caveat">The prototype ran on Georgia WRS-2 tiles with Landsat 5/7; it has not been applied to Florida or this county\'s grid. Today\'s strata rest on LANDFIRE bookends only — Obata-style detection is the proposed upgrade, not the evidence behind these rasters.</div>', "GEE-raster-correct · DisturbanceDetectionAlgorithm; FORESTSAT_2018_Shingo_Obata.pdf; notes/2026-09-14_statewide_repair_blockers.md §4"))


    # --- slides added 2026-09-21: wayfinder frontier state and owner-class policy ---
    WAYFINDER_ISSUES = "https://github.com/charlesmerritt/artemis-model"
    wayfinder_rows = [
        ("59", "Research: what the detector reverse-engineering effort has established",
         "the port's fidelity, sensor record, output form and confidence semantics."),
        ("60", "Research: hole statistics in EVT and NWOS on the AOI",
         "measured acres and mechanisms behind both rasters' holes."),
        ("65", "Donor choice for TreeMap imputation",
         "ring-modal TM_ID vs. embedding-similar donors, dated cuts as donor evidence."),
    ]
    blocked_rows = [
        ("61", "Interface contract for the disturbance-date raster", "59"),
        ("62", "Gate policy: when a dated disturbance rescues a hole", "61"),
        ("63", "EVT write-back rule", "60"),
        ("64", "NWOS ownership repair rule", "60"),
        ("66", "Preserving the disturbance date for FVS", "61"),
        ("67", "Validation plan for the overhauled procedure", "62, 63, 64"),
    ]
    frontier = " ".join(f'<a href="{WAYFINDER_ISSUES}/issues/{n}">{t}</a> — {d}' for n, t, d in wayfinder_rows)
    blocked = " · ".join(
        (f'<a href="{WAYFINDER_ISSUES}/issues/{n}">{t}</a>' + (f' ← <a href="{WAYFINDER_ISSUES}/issues/{b}">#{b}</a>' if "," not in b else f" ← #{b}"))
        for n, t, b in blocked_rows)
    slides.append(slide("wayfinder", "A wayfinder map now plans the disturbance-dating overhaul",
        '<p class="lede">The overhaul is charted as <a href="' + WAYFINDER_ISSUES + '/issues/58">Map: overhauling raster hole repair with disturbance dating</a> on GitHub Issues — a plan, not a build: each ticket resolves one decision, and the map is done when the way to a hand-off spec is clear.</p>'
        '<div class="tiles">'
        '<div class="tile"><div class="n">9</div><div class="l">decision tickets</div></div>'
        '<div class="tile"><div class="n">3</div><div class="l">open on the frontier</div></div>'
        '<div class="tile"><div class="n">6</div><div class="l">blocked behind them</div></div>'
        '<div class="tile"><div class="n">6</div><div class="l">in the fog</div></div>'
        '</div>'
        '<div class="cards">'
        '<div class="tcard"><h4>On the frontier now</h4><p>' + frontier + '</p></div>'
        '<div class="tcard"><h4>Blocked behind them</h4><p>' + blocked + '</p></div>'
        '<div class="tcard"><h4>Settled at charting</h4><p>The disturbance raster enters the funnel as a <strong>rescue gate</strong>, not a classifier feature: a dated disturbance promotes a hole to candidacy, the AlphaEarth evidence keeps the final say, and add-back stays precision-oriented. Record window 2000–2022, five-county AOI; this map owns the detector interface contract and publishes it to the reverse-engineering effort so the two efforts stay decoupled.</p></div>'
        '</div>'
        '<div class="caveat">Plan, don\'t do: the map ends in a spec to hand off, not in code. Shingo\'s volume-estimation half and the statewide path are out of scope. The fog — thinning vs. stand-replacing detection, whether S5 no-evidence holes can be rescued, LCMS as a fallback detector, pre-2000 cuts still open in 2022, statewide scaling, FVS consuming dated establishment — stays in the map\'s Not-yet-specified until the frontier reaches it.</div>',
        "charlesmerritt/artemis-model#58 · child tickets 59–67 · charted 2026-09-21"))
    parameters = manifest["parameters"]
    model_note = (f'Probability cutoff {parameters["probability_threshold"]:.2f}; exemplar cosine cutoff '
                  f'{parameters["similarity_threshold"]:.4f}; minimum accepted patch {parameters["min_acres"]:g} acres. '
                  f'Ambiguous S3/S4 recovery enabled: {manifest["ambiguous_strata_enabled"]}. '
                  'The model is refitted on Union County proxy anchors with spatial block validation. '
                  'These are not independent field labels.')
    metrics = manifest["model_metrics"]
    anchor_diagnostics(dest / "anchor_diagnostics.png", metrics)
    union_fia_figure(dest / "union_fia_check.png", fia)
    slides.append(slide("validation", "County anchor diagnostics are not map accuracy",
        '<p class="lede">This run fits the prior model design on Union County anchors. It uses 2018 and 2022 embedding samples at TreeMap cell centers, with 6 km spatial blocks. Prior hyperparameters are retained; these are new county diagnostics.</p>'
        + figure(dest/"anchor_diagnostics.png", "Bars are anchor-proxy diagnostics. The model separates held-out 6 km spatial blocks (AUC 0.96); the shuffled-label control sits at chance (0.46), as it must; the full-funnel balanced accuracy is 0.88 on the anchor proxies.")
        + '<div class="caveat">Historical vegetation products supply the training labels. These metrics do not measure independent field accuracy, legal owner accuracy, or accuracy on ambiguous S3/S4 cells. No manually interpreted NAIP validation is included.</div>',
        "manifest.json model_metrics; hole_model.json; anchor_samples.csv; classify_holes.py; make_correction_report.py anchor_diagnostics"))
    slides.append(slide("fia-check", "The correction moves Union County toward FIA without overshooting",
        '<p class="lede">EVALIDator attribute 2, “Area of forest land, in acres”, for county code 125 as a single domain — the same design-based reconciliation the five-county correction ran. Bars are the correction rasters on the same 30 m grid, before and after.</p>'
        + figure(dest/"union_fia_check.png", "Bars: TreeMap forest on the county grid before and after the correction. The red box-and-whisker overlay is EVALIDator's 95% interval with the estimate as its median line. Both bars sit inside the interval; the correction closes part of the gap and does not overshoot.")
        + '<div class="caveat">The county domain carries only 25 plots, so its interval is wide: this check bounds overshoot; it cannot resolve the remaining difference. The five-county reconciliation (SE 6.0%) remains the stronger test of total forest area.</div>',
        "union_fia_check.json; verify_union_fia.py; treemap_original.tif; forest_recovery.tif; make_correction_report.py union_fia_figure"))
    slides.append(slide("embeddings", "Inspect the embedding evidence separately", figure(dest/"embedding_evidence.png",
        "Left: decoded AlphaEarth coordinates A00/A01/A02, each scaled to county 2nd–98th percentiles. These are embedding colors, not optical RGB imagery. Right: model forest probability; white indicates missing data. The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind.")
        + '<div class="caveat">'+html.escape(model_note)+'</div>', "embeddings_2022.tif; probability.tif; similarity.tif; manifest.json"))
    slides.append(slide("treemap", "TreeMap gains provisional donor coverage", '<div class="fig-row">'+figure(dest/"treemap_comparison.png", "Before and after on the same 30 m grid. Red cells receive provisional donor IDs; teal cells have accepted forest evidence but no eligible donor. Numbered boxes locate the detailed examples. White is raster nodata.")+key_card([("Forest legend", FOREST, FOREST_LABELS)])+'</div>', "treemap_original.tif; treemap_donor_proposal.tif; treemap_status.tif"))
    slides.append(slide("treelist", "Corrected pixels get treelists from donor plots", '<div class="cards"><div class="tcard"><h4>What exists today</h4><p>Every accepted pixel with an eligible donor received the nearest observed TreeMap cell\'s TM_ID — distance- and embedding-cosine-gated, original IDs untouched; '+f'{proposed:,}'+' pixels carry proposals. Accepted cells with no eligible donor stay the explicit “recovered forest, no donor” class. Neither case is a young-stand inventory yet.</p></div><div class="tcard"><h4>The planned synthesis</h4><p>Donor IDs seed nearest-neighbor imputation: the donor plot\'s treelist establishes each corrected unit at age 0 — species, density, diameters — adjusted by owner-class establishment rules (planted pine density and species for corporate land, natural regeneration elsewhere). FVS consumes these as StandInit/TreeInit inputs; riparian units grow only and are never harvested.</p></div></div><div class="caveat">Donor proposals require regeneration verification before FVS use. An imputed ID carries the donor plot\'s weight and species mix; it is not an observed young stand.</div>', "county_correction.py correct_treemap; notes/2026-09-14_statewide_repair_blockers.md §3; AGENTS.md imputation policy"))


    # --- slide added 2026-09-21: the Harris owner-class policy the FVS management runs on ---
    owner_rows = [
        ("Corporate / other private (4)", "Private",
         "pine: <code>pine_plantation_short_rotation</code>; hardwood/other: <code>hardwood_clearcut_regen</code>",
         "short rotation · long rotation · hardwood clearcut regen",
         "Rotation forestry; Harris folds not-for-profits and institutions into this one class, so all share the menu"),
        ("Family forest (3)", "Private",
         "<code>family_light_thin</code> on every type",
         "family light thin · family uneven-aged selection · pine plantation long rotation",
         "Light entries by default; the sawtimber rotation stays eligible for the managed minority of family pine"),
        ("Tribal forest (5)", "Private (FIA group 40)",
         "<code>public_selection_light</code>",
         "public selection light · family light thin",
         "Placeholder policy — pilot acreage sits below the 500-event floor, so no class-specific behaviour is claimed"),
        ("Federal forest (6)", "Federal (NF)",
         "<code>public_selection_light</code>",
         "public selection light · public thin restore",
         "Effectively Osceola NF in the pilot; density reduction in pine is the characteristic treatment"),
        ("State forest (7)", "Other public",
         "pine: <code>public_thin_restore</code>; hardwood/other: <code>public_selection_light</code>",
         "public thin restore · public selection light · pine plantation long rotation",
         "Active timber program alongside conservation, so the pine default is the thin, and a sawtimber rotation stays on the menu"),
        ("Local forest (8)", "Other public",
         "<code>no_management</code>",
         "public selection light · public thin restore",
         "Parks, watershed and school land; entry is the exception the scheduler must opt into"),
        ("Unknown forest (0)", "Private",
         "<code>family_light_thin</code>",
         "family light thin only — deliberately the narrowest menu",
         "Not an owner but missing information; every unit carries the unknown_ownership flag and no rotation clearcut is invented here to reach a volume target"),
    ]
    owner_table_rows = "".join(
        '<tr><td class="name">' + n + '</td><td>' + tpo + '</td><td>' + d + '</td><td>' + m + '</td><td>' + r + '</td></tr>'
        for n, tpo, d, m, r in owner_rows)
    slides.append(slide("owner-classes", "How each Harris owner class is managed in FVS",
        '<p class="lede">The seven Harris classes (RDS-2025-0045) are the only owner vocabulary. Each has a declared default and a menu the landscape scheduler may choose from; <code>no_management</code> is universally eligible, and every prescription can be offset in 5-year steps.</p>'
        '<div class="tbl-wrap"><table>'
        '<thead><tr><th>Owner class (Harris value)</th><th>TPO group</th><th>Default prescription</th><th>Eligible menu beyond no_management</th><th>Constraint / rationale</th></tr></thead>'
        '<tbody>' + owner_table_rows + '</tbody></table></div>'
        '<div class="caveat">The riparian/SMZ override outranks every ownership rule: absolute <code>no_management</code>, grow-only, never harvested. AGENTS.md floors apply to every class: minimum harvest age 15, minimum harvestable share 25%. Volume caps bind per TPO group (Private / Federal (NF) / Other public), not per class; non-forest (1) and water (2) never enter the FVS pipeline. The offset grid itself is being settled by <a href="' + WAYFINDER_ISSUES + '/issues/50">Map: how time is represented in the trajectory library</a>.</div>',
        "config/management_regimes.yaml owner_classes; config/ownership_policy.yaml (Harris RDS-2025-0045); pipeline/s3_management/regime_assignment.py; pipeline/s4_fvs/regime_templates.py"))
    slides.append(slide("ownership", "Ownership recovery retains unknown forest explicitly", '<div class="fig-row">'+figure(dest/"ownership_comparison.png", "The same ownership palette applies to both maps. Known owners and water remain unchanged. Missing ownership over existing or recovered forest becomes code 0 unless same-parcel evidence supplies a known owner.")+key_card([("Ownership legend", OWNERS, OWNER_LABELS)])+'</div>', "ownership_original.tif; ownership_corrected.tif; ownership_status.tif"))
    for example in examples:
        i = example["example"]
        caption = f'Example {i} at {example["latitude"]:.5f}° N, {abs(example["longitude"]):.5f}° W. Selected patch has {example["component_pixels"]:,} accepted cells. Panels share an extent. EVT colors represent tree, recently logged, and other vegetation. Ownership colors follow the county map.'
        key = key_card([
            ("LANDFIRE EVT", HISTORY, HISTORY_LABELS),
            ("Recovery strata", STRATA_COLORS, STRATA_LABELS),
            ("TreeMap status", FOREST, FOREST_LABELS),
            ("Ownership", OWNERS, OWNER_LABELS),
        ])
        slides.append(slide(f"example-{i}", f"Example {i}: inspect the historical bookends", '<div class="fig-row">'+figure(dest/f"example_{i}.png", caption)+key+'</div>', "evt_2016/2022/2024.tif + CSV class tables; output rasters; examples.geojson"))
    package = dest / "rasters"
    package.mkdir(exist_ok=True)
    products = [f"{name}.tif" for name in rasters]
    products += ["manifest.json", "summary.csv", "examples.geojson", "hole_model.json"]
    if (output / "validation.json").is_file():
        products.append("validation.json")
    for name in products:
        shutil.copy2(output / name, package / name)
    # The FIA reconciliation and its script travel with the package.
    for name in ("union_fia_check.json", "verify_union_fia.py"):
        shutil.copy2(dest / name, package / name)
        products.append(name)
    provenance = package / "sources"
    provenance.mkdir(exist_ok=True)
    provenance_names = ["r2_sources.json"] + [f"embeddings_{year}.json" for year in manifest["embedding_years"]]
    for name in provenance_names:
        shutil.copy2(args.data_dir / name, provenance / name)
    links = ''.join(f'<li><a href="rasters/{name}" download>{name}</a></li>' for name in products)
    source_links = ''.join(f'<li><a href="rasters/sources/{name}" download>{name}</a></li>' for name in provenance_names)
    slides.append(slide("sources", "Inspect the rasters and repeat the run", '<p>The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind. <a href="https://developers.google.com/earth-engine/guides/aef_on_gcs_readme">Dataset documentation and attribution</a>.</p><p class="lede">All maps embed their PNGs and open without a web server. Downloadable maps, model parameters and provenance travel in the adjacent rasters directory.</p><ul class="take">'+links+'</ul><h3>Source provenance</h3><ul class="take">'+source_links+'</ul><p>The raw anchor sample table remains in the pipeline output directory and is excluded from this package.</p><details><summary>Run manifest</summary><pre class="run">'+html.escape(json.dumps(manifest, indent=2))+'</pre></details>', "make_correction_report.py; rasters/manifest.json; rasters/sources/"))
    template = (ROOT / "docs/_template/presentation.html").read_text()
    start = template.index('<div class="deck" id="deck">')
    end = template.index('<nav class="hud"')
    deck = template[:start] + '<div class="deck" id="deck">\n' + '\n'.join(slides) + '\n</div>\n' + template[end:]
    deck = re.sub(r"<title>.*?</title>", "<title>ARTEMIS · Union County raster correction</title>", deck)
    deck = deck.replace('</style>', 'figure.fig img{max-height:65vh}.slide[id^="example-"] figure.fig img{max-height:none}.slide{padding-bottom:85px}details{margin-top:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>')
    deck = deck.replace('</style>', '.map-window{overflow-x:auto}@media(max-width:760px){.map-window img{min-width:1100px;max-height:none!important}}</style>')
    deck = deck.replace('</style>', '.fig-row{display:flex;align-items:flex-start;gap:24px;margin-top:16px}.fig-row figure.fig{flex:1 1 auto;min-width:0;margin:0}.key{flex:0 0 225px;margin-top:4px;border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--card)}.key h4{margin:14px 0 8px}.key h4:first-child{margin-top:0}.key h4 + ul{padding-top:0}.key h4{margin:14px 0 8px;font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-faint)}.key ul{list-style:none;margin:0;padding:0;display:grid;gap:6px}.key li{display:flex;align-items:center;gap:9px;font-size:12.5px;color:var(--ink-soft)}.key .sw{width:18px;height:13px;border-radius:3px;flex:0 0 auto;box-shadow:inset 0 0 0 1px rgba(0,0,0,.18)}@media(max-width:900px){.fig-row{flex-direction:column}.key{flex:1 1 auto}}@media(prefers-color-scheme:dark){.key .sw{box-shadow:inset 0 0 0 1px rgba(255,255,255,.25)}}</style>')
    deck = deck.replace('</script>', "document.querySelectorAll('.map-window').forEach(el=>['touchstart','touchend'].forEach(name=>el.addEventListener(name,e=>e.stopPropagation(),{passive:true})));</script>")
    (dest / "presentation.html").write_text(deck)
    print(json.dumps({"presentation": str(dest/"presentation.html"), "examples": len(examples), "accepted_pixels": recovered, "proposed_pixels": proposed}))


if __name__ == "__main__":
    main()
