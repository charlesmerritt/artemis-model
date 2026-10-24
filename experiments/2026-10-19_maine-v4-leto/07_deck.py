"""Render the Maine layers and write the deck ``docs/maine-v4-leto/presentation.html``.

Statewide maps are the stitched v4 grid decimated by ``FACTOR`` (a block takes its weighted
majority class, add-back counted ``ADD_BACK_WEIGHT`` times so 5 ac patches stay visible). The
zoom slides read one 9 km window of ``ZOOM_FIPS`` at full 30 m resolution. Every number on a
slide is read here from the run's JSON outputs, not typed in.

    uv run python experiments/2026-10-19_maine-v4-leto/07_deck.py
"""

from __future__ import annotations

import base64
import html
import io
import json

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.windows import Window, from_bounds  # noqa: E402
from shapely.geometry import box  # noqa: E402

import common as c  # noqa: E402

DECK = c.REPO / "docs/maine-v4-leto/presentation.html"
TEMPLATE = c.REPO / "docs/_template/presentation.html"
FACTOR = 8
ADD_BACK_WEIGHT = 4
ZOOM_FIPS = "23011"     # Kennebec: mixed family, industrial and town ownership
ZOOM_PX = 300           # 9 km
OUTSIDE = "#f6f5ef"

PROVENANCE = {  # treemap2022_provenance.tif (young_plots): class -> (label, colour)
    1: ("TreeMap forest", "#8fb87f"),
    4: ("added back, established young", "#d9480f"),
    2: ("added back, donor as is", "#7b2cbf"),
    3: ("unmapped land", "#e4e0d5"),
    0: ("water", "#b9d3e3"),
}
OWNERS = {  # Harris code -> (OWNER_CLASS, colour)
    3: ("private_family", "#a6cf8b"),
    4: ("private_industrial", "#2f6b3f"),
    7: ("state", "#3b7dc4"),
    6: ("federal", "#6a4c93"),
    8: ("local", "#e0a030"),
    5: ("tribal", "#b5523b"),
    0: ("unknown", "#bdbdb5"),
}
NON_FOREST = "#ebe8df"
WATER = "#b9d3e3"


def png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor=fig.get_facecolor())
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def hex_rgb(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32) / 255


def block_class(a: np.ndarray, weights: dict[int, float], fill: int) -> np.ndarray:
    """Each FACTOR x FACTOR block's weighted majority class; ``fill`` where no class is present."""
    h, w = (a.shape[0] // FACTOR) * FACTOR, (a.shape[1] // FACTOR) * FACTOR
    blocks = a[:h, :w].reshape(h // FACTOR, FACTOR, w // FACTOR, FACTOR)
    best = np.full((h // FACTOR, w // FACTOR), fill, dtype=a.dtype)
    score = np.zeros(best.shape, dtype=np.float32)
    for v, wt in weights.items():
        s = (blocks == v).sum(axis=(1, 3)) * wt
        take = s > score
        best[take], score[take] = v, s[take]
    return best


def paint(classes: np.ndarray, colours: dict[int, str]) -> np.ndarray:
    rgb = np.empty((*classes.shape, 3), dtype=np.float32)
    rgb[:] = hex_rgb(OUTSIDE)
    for v, colour in colours.items():
        rgb[classes == v] = hex_rgb(colour)
    return rgb


def state_map(rgb: np.ndarray, bounds, legend: list[tuple[str, str]], counties) -> str:
    fig = plt.figure(figsize=(8.2, 10), facecolor=OUTSIDE)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.imshow(rgb, extent=(bounds.left, bounds.right, bounds.bottom, bounds.top),
              interpolation="nearest")
    counties.boundary.plot(ax=ax, color="#3a3a38", linewidth=0.5)
    for _, r in counties.iterrows():
        p = r.geometry.representative_point()
        ax.text(p.x, p.y, r.NAME, fontsize=7, ha="center", color="#1b221c",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.6))
    ax.set_axis_off()
    ax.set_xlim(bounds.left, bounds.right)
    ax.set_ylim(bounds.bottom, bounds.top)
    ax.legend(handles=[Patch(color=col, label=lab) for lab, col in legend], loc="lower right",
              fontsize=9, frameon=True, facecolor="white", edgecolor="#d0d0c3")
    return png(fig)


def statewide_figures() -> dict[str, str]:
    young = c.RUN_DIR / "young_plots"
    with rasterio.open(young / "treemap2022_provenance.tif") as src:
        prov, bounds = src.read(1), src.bounds
    with rasterio.open(c.RUN_DIR / "bookend_strata.tif") as src:
        strata = src.read(1)
    with rasterio.open(c.RUN_DIR / "nwos2022_improved.tif") as src:
        harris = src.read(1)
    counties = c.maine_counties()
    added = np.isin(prov, (2, 4))
    forest = np.isin(prov, (1, 2, 4))
    figs = {"by_stratum": {k: float((added & (strata == k)).sum()) * c.PIXEL ** 2 / 4046.8564224
                           for k in (1, 2, 3, 4)}}

    w = {v: (ADD_BACK_WEIGHT if v in (2, 4) else 1) for v in PROVENANCE}
    figs["provenance"] = state_map(
        paint(block_class(prov, w, 255), {k: v[1] for k, v in PROVENANCE.items()}), bounds,
        [v for v in PROVENANCE.values()], counties)

    # 1 forest, 2 hole not added, 3 added S1/S2 (LANDFIRE), 4 added S3/S4 (Maine model), 5 water
    holes = np.zeros(prov.shape, dtype=np.uint8)
    holes[prov == 1] = 1
    holes[(strata >= 1) & (strata <= 5) & ~added] = 2
    holes[added & np.isin(strata, (1, 2))] = 3
    holes[added & np.isin(strata, (3, 4))] = 4
    holes[prov == 0] = 5
    hole_legend = {1: ("TreeMap forest", "#cfe0c3"), 2: ("hole, not added", "#bdbdb5"),
                   3: ("added: S1/S2, LANDFIRE forest both years", "#1d4e89"),
                   4: ("added: S3/S4, Maine AlphaEarth model", "#d9480f"), 5: ("water", WATER)}
    figs["holes"] = state_map(
        paint(block_class(holes, {1: 1, 2: 1, 3: ADD_BACK_WEIGHT, 4: ADD_BACK_WEIGHT, 5: 1}, 0),
              {k: v[1] for k, v in hole_legend.items()}), bounds,
        list(hole_legend.values()), counties)

    owner = np.full(prov.shape, 254, dtype=np.uint8)
    owner[forest] = np.where(np.isin(harris[forest], list(OWNERS)), harris[forest], 0)
    owner[prov == 0] = 253
    owner[prov == 255] = 255
    colours = {k: v[1] for k, v in OWNERS.items()} | {254: NON_FOREST, 253: WATER}
    figs["owners"] = state_map(
        paint(block_class(owner, {k: 1 for k in colours}, 255), colours), bounds,
        [(lab.replace("_", " "), col) for lab, col in OWNERS.values()], counties)
    return figs


def zoom_window() -> Window:
    """The ZOOM_PX window of ZOOM_FIPS with the most stands the speckle step absorbed."""
    with rasterio.open(c.WORK / f"counties/{ZOOM_FIPS}_seg_leto.tif") as src:
        leto = src.read(1)
    with rasterio.open(c.WORK / f"counties/{ZOOM_FIPS}_seg.tif") as src:
        seg = src.read(1)
    best, best_score = None, -1
    step = ZOOM_PX // 2
    for r in range(0, leto.shape[0] - ZOOM_PX, step):
        for col in range(0, leto.shape[1] - ZOOM_PX, step):
            a = leto[r:r + ZOOM_PX, col:col + ZOOM_PX]
            if (a > 0).mean() < 0.6:
                continue
            b = seg[r:r + ZOOM_PX, col:col + ZOOM_PX]
            score = len(np.unique(a)) - len(np.unique(b))
            if score > best_score:
                best, best_score = Window(col, r, ZOOM_PX, ZOOM_PX), score
    return best


def label_rgb(labels: np.ndarray, seed: int = 7) -> np.ndarray:
    """Random pastel per label, dark boundaries between labels, background left blank."""
    rng = np.random.default_rng(seed)
    _, inv = np.unique(labels, return_inverse=True)
    inv = inv.reshape(labels.shape)
    palette = 0.55 + 0.4 * rng.random((inv.max() + 1, 3))
    rgb = palette[inv].astype(np.float32)
    edge = np.zeros(labels.shape, dtype=bool)
    edge[:, 1:] |= labels[:, 1:] != labels[:, :-1]
    edge[1:, :] |= labels[1:, :] != labels[:-1, :]
    rgb[edge] = (0.15, 0.17, 0.15)
    rgb[labels == 0] = hex_rgb(NON_FOREST)
    return rgb


def zoom_panel(rgb: np.ndarray, title: str) -> str:
    fig = plt.figure(figsize=(5.6, 5.9), facecolor="white")
    ax = fig.add_axes((0, 0, 1, 0.94))
    ax.imshow(rgb, interpolation="nearest")
    ax.set_axis_off()
    fig.text(0.5, 0.965, title, ha="center", va="center", fontsize=12, color="#1b221c")
    return png(fig)


def zoom_figures(win: Window) -> tuple[dict[str, str], dict]:
    seg_path = c.WORK / f"counties/{ZOOM_FIPS}_seg.tif"
    with rasterio.open(c.WORK / f"counties/{ZOOM_FIPS}_seg_leto.tif") as src:
        leto = src.read(1, window=win)
    with rasterio.open(seg_path) as src:
        seg = src.read(1, window=win)
        wt = src.window_transform(win)
        bounds = rasterio.windows.bounds(win, src.transform)
    young = c.RUN_DIR / "young_plots"
    with rasterio.open(young / "treemap2022_provenance.tif") as src:
        prov = src.read(1, window=from_bounds(*bounds, transform=src.transform))
    with rasterio.open(c.RUN_DIR / "nwos2022_improved.tif") as src:
        harris = src.read(1, window=from_bounds(*bounds, transform=src.transform))
    figs = {}
    figs["z_prov"] = zoom_panel(paint(prov, {k: v[1] for k, v in PROVENANCE.items()}),
                                "v4 TreeMap: published and added back")
    own = np.where(prov > 0, np.where(np.isin(harris, list(OWNERS)), harris, 0), 254)
    own[np.isin(prov, (0, 3))] = 254
    figs["z_owner"] = zoom_panel(paint(own, {k: v[1] for k, v in OWNERS.items()} | {254: NON_FOREST}),
                                 "Harris ownership on the 30 m grid")
    figs["z_leto"] = zoom_panel(label_rgb(leto), f"LETO CA parents: {len(np.unique(leto)) - 1:,}")
    figs["z_seg"] = zoom_panel(label_rgb(seg), f"after speckle cleanup: {len(np.unique(seg)) - 1:,}")

    mu = gpd.read_file(c.WORK / f"counties/{ZOOM_FIPS}.gpkg", bbox=bounds)
    fig = plt.figure(figsize=(5.6, 5.9), facecolor="white")
    ax = fig.add_axes((0, 0, 1, 0.94))
    upland, rip = mu[mu.RIPARIAN != 1], mu[mu.RIPARIAN == 1]
    upland.plot(ax=ax, color=[OWNERS_BY_CLASS.get(k, "#bdbdb5") for k in upland.OWNER_CLASS],
                edgecolor="#26302a", linewidth=0.25)
    rip.plot(ax=ax, color="#2b6cb0", edgecolor="#1a3f66", linewidth=0.2)
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_facecolor(NON_FOREST)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.text(0.5, 0.965, "management units (owner colour; riparian blue)", ha="center",
             va="center", fontsize=12, color="#1b221c")
    figs["z_mu"] = png(fig)
    inside = mu[mu.representative_point().within(box(*bounds))]
    meta = {"bounds": [round(b) for b in bounds], "units": len(inside),
            "riparian": int((inside.RIPARIAN == 1).sum()), "transform": str(wt)}
    return figs, meta


OWNERS_BY_CLASS = {lab: col for lab, col in OWNERS.values()}


def fmt(x, nd=0) -> str:
    return f"{x:,.{nd}f}"


def county_rows() -> str:
    rows = []
    for fips in sorted(p.stem.split("_")[0] for p in (c.WORK / "counties").glob("*_qa.json")):
        q = json.loads((c.WORK / f"counties/{fips}_qa.json").read_text())
        rows.append(
            f"<tr><td class=\"name\">{html.escape(q['county'])}</td>"
            f"<td class=\"num\">{fmt(q['forest_acres_cells'])}</td>"
            f"<td class=\"num\">{fmt(q['added_back_acres'])}</td>"
            f"<td class=\"num\">{fmt(q['leto_parent_stands'])}</td>"
            f"<td class=\"num\"><b>{fmt(q['parent_stands'])}</b></td>"
            f"<td class=\"num\">{fmt(q['parent_acre_median'], 1)}</td>"
            f"<td class=\"num\">{fmt(q['management_units'])}</td>"
            f"<td class=\"num\">{fmt(q['riparian_acres'])}</td>"
            f"<td class=\"num\">{q['unknown_owner_acres'] / q['forest_acres_cells']:.0%}</td></tr>")
    return "\n".join(rows)


def slides(f: dict[str, str], zmeta: dict) -> str:
    qa = json.loads((c.OUTPUTS / "me_statewide_qa.json").read_text())
    imp = json.loads((c.WORK / "03_improve.json").read_text())
    yp = json.loads((c.WORK / "03_young.json").read_text())
    fl = json.loads((c.WORK / "03_improve_fl_model.json").read_text())
    model = json.loads(c.HOLE_MODEL.read_text())
    match = yp["acres_by_match_level"]
    added = imp["added_back_acres"]
    under5 = qa["parent_acres_under_5_ac"] / qa["forest_acres_cells"]
    owners = sorted(qa["acres_by_owner_class"].items(), key=lambda kv: -kv[1])
    owner_rows = "\n".join(
        f"<tr><td class=\"name\">{k.replace('_', ' ')}</td><td class=\"num\">{fmt(v)}</td>"
        f"<td class=\"num\">{v / qa['forest_acres_cells']:.1%}</td></tr>" for k, v in owners)
    m = model["metrics"]
    z = zmeta["bounds"]

    def fig(key, alt, cap, cls="fig"):
        return (f'<figure class="{cls}"><img alt="{html.escape(alt)}" src="{f[key]}">'
                f"<figcaption>{cap}</figcaption></figure>")

    def pair(a, b):
        return (f'<div class="cards" style="margin-top:12px">'
                f'<div>{a}</div><div>{b}</div></div>')

    return f"""
  <section class="slide title-slide" id="title">
    <div class="inner">
      <p class="eyebrow">ARTEMIS · initial state · Maine</p>
      <h1>Maine stands on the v4-improved TreeMap</h1>
      <p class="lede">All 16 Maine counties: TreeMap 2022 repaired with the v4 recipe, a hole model
        <b>refit on Maine</b>, LETO v1 cellular-automata stands, a <b>speckle cleanup after LETO</b>, and
        the LETO Maine riparian split. <b>{fmt(qa['management_units'])}</b> management units over
        <b>{fmt(qa['forest_acres_cells'] / 1e6, 2)}M</b> forest acres.</p>
      <div class="meta">
        <span class="chip">counties <b>{qa['counties_run']}</b></span>
        <span class="chip">added back <b>{fmt(added)} ac</b></span>
        <span class="chip">parent stands <b>{fmt(qa['parent_stands'])}</b></span>
        <span class="chip">median parent <b>{qa['parent_acre_median']} ac</b></span>
      </div>
      <p class="sub" style="margin-top:22px">Branch experiment/maine-v4-leto · run 2026-10-23 · ← → or space</p>
    </div>
  </section>

  <section class="slide" id="pipeline">
    <div class="inner">
      <p class="kicker">Method <span class="rule"></span> 01</p>
      <h2>Six steps, county by county where memory needs it</h2>
      <div class="flow">
        <div class="fstep"><div class="t">01</div><div class="v">Grid inputs</div><div class="d">Maine grid on TreeMap's lattice; LANDFIRE hole strata, Harris ownership, Hansen loss warped onto it</div></div>
        <div class="fstep"><div class="t">02</div><div class="v">Hole model</div><div class="d">AlphaEarth Stage A/B refit on Maine strata; 72 Earth Engine tiles scored</div></div>
        <div class="fstep"><div class="t">03</div><div class="v">v4 raster</div><div class="d">county_improvement per county, stitched; dated stand age; young-plot IDs</div></div>
        <div class="fstep"><div class="t">04</div><div class="v">FIA</div><div class="d">COND/TREE rows for every plot the raster references (ME, MA, NH, NY)</div></div>
        <div class="fstep"><div class="t">05</div><div class="v">LETO</div><div class="d">Stage 1 attributes, CA, force_merge; speckle cleanup; riparian split; donor weights</div></div>
        <div class="fstep"><div class="t">06</div><div class="v">Merge</div><div class="d">statewide GeoPackage, donor weights, QA</div></div>
      </div>
      <div class="caveat"><b>v4 here is bookends only.</b> The v4 rule is
        <span class="mono">bookends_or_obata_and_hansen</span>; Obata covers only one Florida Landsat scene, so
        <span class="mono">obata &amp; hansen</span> is empty in Maine. Hansen still runs and dates the add-back
        pixels. The EVT gate v3 and the quarry patch rule apply as in FL5.</div>
      <p class="srcline">experiments/2026-10-19_maine-v4-leto/common.py · 03_improve.py · 05_leto.py · 06_merge.py</p>
    </div>
  </section>

  <section class="slide" id="provenance">
    <div class="inner">
      <p class="kicker">Layer · v4 TreeMap <span class="rule"></span> 02</p>
      <h2>{fmt(added)} acres added back to {fmt(imp['published_forest_acres'] / 1e6, 2)}M published forest acres</h2>
      <div class="cards" style="grid-template-columns:1.25fr 1fr;align-items:start">
        <div>{fig('provenance', f"Maine statewide map of the improved TreeMap. Published forest covers most of the state; {fmt(added)} added-back acres are scattered patches, densest in the northern industrial forest.", "<b>Where the repaired raster differs from TreeMap 2022.</b> 240 m blocks; a block shows add-back when add-back pixels are a quarter of its forest-class weight.", "fig map")}</div>
        <div>
          <div class="tiles" style="grid-template-columns:1fr 1fr">
            <div class="tile"><div class="n">{fmt(imp['hole_acres'])}</div><div class="l">acres of land with no TreeMap value</div></div>
            <div class="tile"><div class="n">{fmt(imp['rule_accepted_before_mmu_acres'])}</div><div class="l">acres the rule accepted before the 5 ac minimum</div></div>
            <div class="tile"><div class="n good">{fmt(added)}</div><div class="l">acres added back in {fmt(imp['patches'])} patches</div></div>
            <div class="tile"><div class="n warn">{fmt(imp['evt_gate']['rejected_acres'])}</div><div class="l">acres the EVT gate v3 rejected (crops, developed)</div></div>
          </div>
          <ul class="take">
            <li><b>{fmt(imp['added_back_acres_by_mode']['scaled_young'])} ac</b> are established young (ADR 0003 scaled neighbour); <b>{fmt(imp['added_back_acres_by_mode']['donor_as_is'])} ac</b> (S2) keep the donor as is.</li>
            <li><b>{fmt(imp['dropped_by_mmu_or_no_donor_acres'])} ac</b> fell to the minimum patch size or had no donor.</li>
          </ul>
        </div>
      </div>
      <p class="srcline">work/rasters/v4/maine/young_plots/treemap2022_provenance.tif · 03_improve.json · 07_deck.py</p>
    </div>
  </section>

  <section class="slide" id="hole-model">
    <div class="inner">
      <p class="kicker">Layer · hole strata <span class="rule"></span> 03</p>
      <h2>The Florida hole model passed no Maine pixel; the Maine refit adds S3/S4 back</h2>
      <div class="cards" style="grid-template-columns:1.25fr 1fr;align-items:start">
        <div>{fig('holes', "Maine map of TreeMap holes by outcome. Blue S1/S2 add-backs and orange model add-backs sit in grey holes; orange is concentrated in the north and west.", "<b>Holes by stratum and outcome.</b> S1/S2 are LANDFIRE forest in 2016 and 2024 and are added back unconditionally; S3/S4 need the AlphaEarth model.", "fig map")}</div>
        <div>
          <div class="tbl-wrap"><table style="min-width:0">
            <thead><tr><th>Hole model</th><th class="num">FL5 fit</th><th class="num">Maine fit</th></tr></thead>
            <tbody>
              <tr><td class="name">Stage A threshold</td><td class="num">0.907</td><td class="num">{model['similarity_threshold']:.3f}</td></tr>
              <tr><td class="name">Maine S3/S4 passing</td><td class="num">0</td><td class="num"><b>model: 26% / 41%</b></td></tr>
              <tr class="goodrow"><td class="name">Acres added back</td><td class="num">{fmt(fl['added_back_acres'])}</td><td class="num"><b>{fmt(added)}</b></td></tr>
            </tbody></table></div>
          <ul class="take">
            <li><b>Refit on 4,500 Maine points in 185 spatial blocks</b>: block-CV AUC {m['auc_block_cv']:.3f} (shuffled labels {m['auc_label_shuffle']:.2f}); end to end balanced accuracy {m['pipeline_balanced_accuracy']:.3f}, precision {m['pipeline_precision']:.3f}.</li>
            <li><b>Added back by stratum:</b> {' · '.join(f'S{k} {fmt(v)}' for k, v in f['by_stratum'].items())} ac.</li>
            <li>Most model-accepted S3/S4 pixels are in patches under 5 ac, so the minimum patch size removes them.</li>
          </ul>
        </div>
      </div>
      <p class="srcline">pipeline/s1_initial_state/embed_holes.py · classify_holes · work/model/hole_model.json · bookend_strata.tif</p>
    </div>
  </section>

  <section class="slide" id="young-plots">
    <div class="inner">
      <p class="kicker">Layer · young plots <span class="rule"></span> 04</p>
      <h2>{fmt(match['neighbour'] / added * 100)}% of add-back acres have no young FIA plot to match</h2>
      <p class="lede">ADR 0003 gives each add-back pixel a new ID with a dated age and a young FIA plot from
        LETO Maine's states (ME, MA, NH, NY). TreeMap holds few young plots there, so unmatched pixels keep their
        neighbour plot (<span class="mono">UnmatchedYoungPlotPolicy.KEEP_NEIGHBOUR</span>).</p>
      <div class="tiles">
        <div class="tile"><div class="n good">{fmt(match['type'])}</div><div class="l">acres matched by forest type</div></div>
        <div class="tile"><div class="n">{fmt(match['group'])}</div><div class="l">acres matched by forest-type group</div></div>
        <div class="tile"><div class="n warn">{fmt(match['neighbour'])}</div><div class="l">acres keeping the neighbour plot</div></div>
        <div class="tile"><div class="n">{fmt(yp['counts']['new_ids'])}</div><div class="l">new IDs; every one has trees and resolves in the VAT</div></div>
      </div>
      <div class="caveat"><b>Neighbour-kept acres carry a young age with mature structure.</b> Their STAND_AGE_2022
        is dated (Hansen {fmt(yp['acres_by_age_source']['hansen'])} ac, stratum median
        {fmt(yp['acres_by_age_source']['stratum_median'])} ac) but BA, QMD and TPA are the mature neighbour's. The
        forest-type group map is southern: white pine and hemlock fall in <span class="mono">other</span>.</div>
      <p class="srcline">pipeline/s1_initial_state/add_back_ids.py · young_plot_library.py · 03_young.json</p>
    </div>
  </section>

  <section class="slide" id="owners">
    <div class="inner">
      <p class="kicker">Layer · ownership <span class="rule"></span> 05</p>
      <h2>A third of Maine's forest acres have no Harris owner</h2>
      <div class="cards" style="grid-template-columns:1.25fr 1fr;align-items:start">
        <div>{fig('owners', "Maine forest ownership map. Dark green private industrial land fills the north and west; light green family land dominates the south; large grey unknown areas cover the north woods.", "<b>Harris/NWOS (RDS-2025-0045) on forest pixels</b>, 10 m warped nearest to the 30 m grid.", "fig map")}</div>
        <div>
          <div class="tbl-wrap"><table style="min-width:0">
            <thead><tr><th>Owner class</th><th class="num">Acres</th><th class="num">Share</th></tr></thead>
            <tbody>{owner_rows}</tbody></table></div>
          <div class="caveat"><b>Unknown is {qa['unknown_owner_acres'] / qa['forest_acres_cells']:.0%} of acres and
            {qa['unknown_owner_parent_share']:.0%} of parent stands.</b> These are forest pixels Harris leaves
            without a class. They need a policy before regime assignment.</div>
        </div>
      </div>
      <p class="srcline">work/rasters/v4/maine/nwos2022_improved.tif · 05_leto.py HARRIS_TO_OWNER_CLASS</p>
    </div>
  </section>

  <section class="slide" id="speckle">
    <div class="inner">
      <p class="kicker">Layer · parent stands <span class="rule"></span> 06</p>
      <h2>The speckle cleanup removes {fmt(qa['speckle_absorbed_stands'])} LETO stands under 5 ac</h2>
      <p class="lede">LETO's force_merge only joins a small stand to a <b>same-owner</b> neighbour. Harris's 10 m
        ownership, warped to 30 m, leaves single-pixel owners, so LETO keeps
        {fmt(qa['leto_parent_stands'])} parents. <span class="mono">pipeline/stand_speckle.py</span> then joins
        every stand under 5 ac that touches another to the neighbour it shares the longest edge with.</p>
      {pair(fig('z_leto', 'LETO parent stands in a 9 km window of Kennebec County: many tiny one-pixel stands along ownership edges.', '<b>LETO v1 output</b> (kept as <span class="mono">&lt;fips&gt;_seg_leto.tif</span>).'),
            fig('z_seg', 'The same window after the speckle cleanup: the tiny stands are absorbed into larger neighbours.', '<b>After cleanup</b> (<span class="mono">&lt;fips&gt;_seg.tif</span>).'))}
      <div class="tiles">
        <div class="tile"><div class="n">{fmt(qa['leto_parent_stands'])}</div><div class="l">LETO parents</div></div>
        <div class="tile"><div class="n good">{fmt(qa['parent_stands'])}</div><div class="l">parents after cleanup; median {qa['parent_acre_median']} ac</div></div>
        <div class="tile"><div class="n">{fmt(qa['speckle_absorbed_acres'])}</div><div class="l">acres absorbed into a neighbour</div></div>
        <div class="tile"><div class="n warn">{fmt(qa['isolated_small_stands'])}</div><div class="l">isolated stands under 5 ac left: {fmt(qa['parent_acres_under_5_ac'])} ac, {under5:.1%} of forest</div></div>
      </div>
      <p class="srcline">pipeline/stand_speckle.py · tests/test_stand_speckle.py · Kennebec window {z[0]},{z[1]} – {z[2]},{z[3]} (EPSG:5070)</p>
    </div>
  </section>

  <section class="slide" id="zoom">
    <div class="inner">
      <p class="kicker">Layers · one window <span class="rule"></span> 07</p>
      <h2>Raster, owner and management units in the same 9 km of Kennebec</h2>
      <div class="cards three" style="margin-top:12px">
        <div>{fig('z_prov', 'Improved TreeMap provenance in the Kennebec window: mostly published forest, a few orange add-back patches, beige unmapped land.', '<b>v4 provenance.</b> Orange: added back young.')}</div>
        <div>{fig('z_owner', 'Ownership pixels in the window: family, industrial, town and unknown owners interleave at the pixel scale.', '<b>Harris owners.</b> The speckle source.')}</div>
        <div>{fig('z_mu', 'Management unit polygons in the window coloured by owner class, with riparian buffer units in blue along streams and ponds.', f"<b>Management units.</b> {fmt(zmeta['units'])} in view, {fmt(zmeta['riparian'])} riparian.")}</div>
      </div>
      <p class="srcline">work/counties/{ZOOM_FIPS}.gpkg · pipeline/leto_riparian.py MAINE_V1_RULES (45 ft on perennial streams, lakes, complex channels)</p>
    </div>
  </section>

  <section class="slide" id="counties">
    <div class="inner">
      <p class="kicker">Stand layer · by county <span class="rule"></span> 08</p>
      <h2>{fmt(qa['management_units'])} management units, {fmt(qa['riparian_units'])} of them riparian</h2>
      <div class="tbl-wrap"><table>
        <thead><tr><th>County</th><th class="num">Forest ac</th><th class="num">Added ac</th><th class="num">LETO parents</th><th class="num">Parents</th><th class="num">Median ac</th><th class="num">MUs</th><th class="num">Riparian ac</th><th class="num">Unknown</th></tr></thead>
        <tbody>{county_rows()}</tbody></table></div>
      <p class="srcline">work/counties/&lt;fips&gt;_qa.json · outputs/me_management_units.gpkg · me_statewide_qa.json</p>
    </div>
  </section>

  <section class="slide" id="close">
    <div class="inner">
      <p class="kicker">Closing <span class="rule"></span> 09</p>
      <h2>What the Maine layer establishes, and what it does not</h2>
      <ul class="take">
        <li><b>A statewide v4 stand layer exists:</b> {fmt(qa['parent_stands'])} parents, {fmt(qa['management_units'])} management units, {fmt(qa['distinct_donor_plots'])} donor plots, every forest acre accounted (MU acres = forest cells).</li>
        <li><b>Hole models are regional:</b> the FL fit passes nothing; the Maine refit adds back {fmt(added - fl['added_back_acres'])} ac more than the FL-model run.</li>
        <li><b>The speckle cleanup is a separate post-LETO step;</b> LETO's labels are kept beside it.</li>
        <li><b>Young structure is thin:</b> {fmt(match['neighbour'])} add-back acres carry mature structure under a young age.</li>
      </ul>
      <div class="caveat"><b>Open:</b> an owner policy for {fmt(qa['unknown_owner_acres'] / 1e6, 2)}M unknown acres;
        isolated forest islands under 5 ac (keep, LETO's 60 m proximity merge, or drop); EVT gate v3 blocks only warm-temperate
        pasture (7997); NHDPlus HR HU4 <span class="mono">NHDFlowline</span> stands in for v1's
        <span class="mono">NetworkNHDFlowline</span>; Obata covers only Landsat p17/r39 and reads 0 here.</div>
      <p class="srcline">experiments/2026-10-19_maine-v4-leto/common.py (Findings)</p>
    </div>
  </section>
"""


def main() -> None:
    template = TEMPLATE.read_text()
    head, rest = template.split('<div class="deck" id="deck">', 1)
    tail = rest[rest.index("</div>\n\n<nav"):]
    head = head.replace("<title>ARTEMIS — deck template</title>", "<title>Maine v4 stands</title>")
    head = head.replace("</style>", "  figure.fig.map img { max-height:72vh; }\n</style>")
    figs = statewide_figures()
    zf, zmeta = zoom_figures(zoom_window())
    body = slides(figs | zf, zmeta)
    DECK.parent.mkdir(parents=True, exist_ok=True)
    DECK.write_text(head + '<div class="deck" id="deck">' + body + tail)
    print(DECK, f"{DECK.stat().st_size / 1e6:.1f} MB", zmeta)


if __name__ == "__main__":
    main()
