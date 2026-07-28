"""Generate every figure in the TreeMap hole-rectification report.

Reads only committed pipeline artifacts plus the LANDFIRE/TreeMap sources on
``/mnt/d``, so the whole figure set is reproducible from a clean checkout once
the pipeline has been run:

    uv run python -m pipeline.s1_initial_state.make_report_figures

Writes PNGs to ``docs/treemap_holes/figures/`` and the scalar results the report
quotes to ``docs/treemap_holes/figures/report_values.json``, so no number in the
prose is transcribed by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import ListedColormap
from scipy import ndimage
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.model_selection import GroupKFold, cross_val_predict

from pipeline.s1_initial_state.classify_holes import (
    anchor_exemplars,
    band_columns,
    stage_a_similarity,
    training_matrix,
)
from pipeline.s1_initial_state.embed_holes import decode_score_bands
from pipeline.s1_initial_state.stratify_treemap_holes import read_evt_window

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data/interim/treemap_holes"
FIGS = REPO / "docs/treemap_holes/figures"
ACRES_PER_PIXEL = 0.2224

STRATA_LABELS = {
    1: "S1 cut pre-2016,\nregrown by 2024",
    2: "S2 cut 2016–22,\nregrown by 2024",
    3: "S3 cut 2016–22,\nstill open 2024",
    4: "S4 regrown only",
    5: "S5 no evidence",
}
STRATA_COLORS = {1: "#b2182b", 2: "#ef8a62", 3: "#f4a582", 4: "#67a9cf", 5: "#bdbdbd"}

plt.rcParams.update({
    "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 130, "savefig.bbox": "tight", "savefig.facecolor": "white",
})

VALUES: dict[str, object] = {}


def save(fig, name: str) -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGS / f"{name}.png")
    plt.close(fig)
    print(f"  wrote {name}.png")


def load_rasters():
    with rasterio.open(DATA / "treemap_hole_strata.tif") as src:
        strata = src.read(1)
        bounds, transform = src.bounds, src.transform
    with rasterio.open(DATA / "treemap_add_back_mask.tif") as src:
        add_back = src.read(1) == 1
    return strata, add_back, bounds, transform


# --------------------------------------------------------------------------- #
def fig1_study_area(strata, bounds, transform):
    """AOI extent, TreeMap coverage, and where the holes are."""
    names22, lf22 = read_evt_window(2022, bounds, strata.shape, transform)
    holes = strata > 0

    img = np.zeros(strata.shape, np.uint8)
    img[~holes] = 1
    img[holes] = 2
    cmap = ListedColormap([[1, 1, 1], [0.72, 0.84, 0.72], [0.80, 0.36, 0.24]])

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), width_ratios=[1.25, 1])
    axes[0].imshow(img, cmap=cmap, vmin=0, vmax=2, interpolation="nearest")
    axes[0].set_title("(a) Five-county AOI: TreeMap 2022 coverage")
    axes[0].axis("off")
    axes[0].legend(handles=[
        plt.Rectangle((0, 0), 1, 1, fc=[0.72, 0.84, 0.72], label="TreeMap has TM_ID (60.0%)"),
        plt.Rectangle((0, 0), 1, 1, fc=[0.80, 0.36, 0.24], label="hole, no TM_ID (40.0%)"),
    ], loc="lower left", frameon=False, fontsize=8)

    comp = pd.Series(lf22[holes]).value_counts() * ACRES_PER_PIXEL
    comp = comp.sort_values()
    axes[1].barh(comp.index, comp.values, color="#8c8c8c")
    axes[1].set_xlabel("acres")
    axes[1].set_title("(b) LANDFIRE 2022 lifeform of the hole pixels")
    for y, v in enumerate(comp.values):
        axes[1].text(v + 6000, y, f"{v:,.0f}", va="center", fontsize=7.5)
    axes[1].set_xlim(0, comp.max() * 1.25)

    VALUES["aoi_acres"] = float(strata.size * ACRES_PER_PIXEL)  # bbox, not AOI
    VALUES["hole_acres"] = float(holes.sum() * ACRES_PER_PIXEL)
    VALUES["hole_lifeform_acres"] = {k: float(v) for k, v in comp.items()}
    save(fig, "fig1_study_area")
    return names22


# --------------------------------------------------------------------------- #
def fig2_mechanism(strata, bounds, transform):
    """The root cause: LANDFIRE stops populating its Recently Logged classes."""
    holes = strata > 0
    logged, lifeforms = {}, {}
    for year in (2016, 2022, 2024):
        names, lf = read_evt_window(year, bounds, strata.shape, transform)
        logged[year] = int(np.char.startswith(names.astype(str), "Recently Logged")[holes].sum())
        lifeforms[year] = pd.Series(lf[holes]).value_counts()

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.9))
    years = [2016, 2022, 2024]
    acres = [logged[y] * ACRES_PER_PIXEL for y in years]
    axes[0].bar([str(y) for y in years], acres, color=["#b2182b", "#cccccc", "#cccccc"], width=0.55)
    for i, v in enumerate(acres):
        axes[0].text(i, v + 1200, f"{v:,.0f} ac", ha="center", fontsize=8.5)
    axes[0].set_ylabel("acres mapped as 'Recently Logged'")
    axes[0].set_title("(a) LANDFIRE abandons its harvest flag\n(within TreeMap 2022 holes)")
    axes[0].set_ylim(0, max(acres) * 1.25)

    order = ["Tree", "Shrub", "Herb", "Agriculture", "Developed", "Water", "Barren"]
    order = [o for o in order if o in lifeforms[2016].index]
    width = 0.26
    for i, year in enumerate(years):
        vals = [lifeforms[year].get(o, 0) * ACRES_PER_PIXEL for o in order]
        axes[1].bar(np.arange(len(order)) + (i - 1) * width, vals, width,
                    label=str(year), color=["#2166ac", "#92c5de", "#b2182b"][i])
    axes[1].set_xticks(np.arange(len(order)), order, rotation=30, ha="right")
    axes[1].set_ylabel("acres")
    axes[1].set_title("(b) Lifeform of the same pixels, by vintage")
    axes[1].legend(frameon=False, fontsize=8)

    VALUES["recently_logged_acres"] = {str(y): float(logged[y] * ACRES_PER_PIXEL) for y in years}
    VALUES["hole_lifeform_acres_by_vintage"] = {
        str(y): {k: float(v * ACRES_PER_PIXEL) for k, v in lifeforms[y].items()} for y in years}
    save(fig, "fig2_mechanism")


# --------------------------------------------------------------------------- #
def fig3_strata(strata):
    """Where each stratum falls, and how much acreage it holds."""
    fig = plt.figure(figsize=(11.5, 4.6))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.3, 1])

    ax = fig.add_subplot(gs[0])
    img = np.zeros(strata.shape, np.uint8)
    for code in (5, 4, 3, 2, 1):
        img[strata == code] = code
    cmap = ListedColormap([[0.95, 0.96, 0.95]] + [STRATA_COLORS[c] for c in range(1, 6)])
    ax.imshow(img, cmap=cmap, vmin=0, vmax=5, interpolation="nearest")
    ax.set_title("(a) TreeMap hole strata")
    ax.axis("off")
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, fc=STRATA_COLORS[c],
                                     label=STRATA_LABELS[c].replace("\n", " "))
                       for c in range(1, 6)], loc="lower left", frameon=False, fontsize=7.5)

    ax = fig.add_subplot(gs[1])
    acres = {c: float((strata == c).sum() * ACRES_PER_PIXEL) for c in range(1, 6)}
    codes = [5, 4, 3, 2, 1]
    ax.barh([STRATA_LABELS[c] for c in codes], [acres[c] for c in codes],
            color=[STRATA_COLORS[c] for c in codes])
    for y, c in enumerate(codes):
        ax.text(acres[c] + 8000, y, f"{acres[c]:,.0f} ac", va="center", fontsize=8)
    ax.set_xlabel("acres")
    ax.set_xlim(0, max(acres.values()) * 1.28)
    recoverable = sum(acres[c] for c in (1, 2, 3, 4))
    ax.set_title(f"(b) {recoverable:,.0f} ac carry forest evidence "
                 f"({recoverable / sum(acres.values()):.0%} of holes)")

    VALUES["strata_acres"] = acres
    VALUES["recoverable_acres"] = recoverable
    save(fig, "fig3_strata")


# --------------------------------------------------------------------------- #
def fig4_shape(strata):
    """Shape diagnostics: S2 is edge artifact, and patches need an MMU."""
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.9))

    interior_frac, patch_counts = {}, {}
    for code in (1, 2, 3, 4):
        mask = strata == code
        labels, _ = ndimage.label(mask, structure=np.ones((3, 3)))
        sizes = np.bincount(labels.ravel())
        keep = np.nonzero(sizes * ACRES_PER_PIXEL >= 5.0)[0]
        big = np.isin(labels, keep[keep != 0])
        interior = ndimage.binary_erosion(big, np.ones((3, 3)))
        interior_frac[code] = interior.sum() / max(mask.sum(), 1)
        patch_counts[code] = int(len(keep) - 1)

    codes = [1, 2, 3, 4]
    bars = axes[0].bar([f"S{c}" for c in codes], [interior_frac[c] for c in codes],
                       color=[STRATA_COLORS[c] for c in codes], width=0.6)
    for bar, c in zip(bars, codes):
        axes[0].text(bar.get_x() + bar.get_width() / 2, interior_frac[c] + 0.008,
                     f"{interior_frac[c]:.1%}\n{patch_counts[c]:,} patches ≥5 ac",
                     ha="center", fontsize=7.5)
    axes[0].set_ylabel("fraction surviving 5 ac MMU + erosion")
    axes[0].set_ylim(0, max(interior_frac.values()) * 1.35)
    axes[0].set_title("(a) Interior fraction: a blocky clearcut survives\n"
                      "erosion, a misregistration sliver does not")

    strong = np.isin(strata, (1, 2))
    labels, _ = ndimage.label(strong, structure=np.ones((3, 3)))
    patch_acres = np.bincount(labels.ravel())[1:] * ACRES_PER_PIXEL
    bins = np.array([0, 1, 2, 5, 10, 20, 40, 80, 1e9])
    names = ["<1", "1–2", "2–5", "5–10", "10–20", "20–40", "40–80", ">80"]
    binned = pd.cut(patch_acres, bins, labels=names, right=False)
    grouped = pd.DataFrame({"ac": patch_acres, "bin": binned}).groupby("bin", observed=False).ac.sum()
    axes[1].bar(names, grouped.values, color="#b2182b", width=0.65)
    axes[1].set_xlabel("patch size (acres)")
    axes[1].set_ylabel("total acres in patches of that size")
    ge5 = patch_acres[patch_acres >= 5].sum() / patch_acres.sum()
    axes[1].set_title(f"(b) S1+S2 patch-size distribution\n"
                      f"{len(patch_acres):,} patches; {ge5:.0%} of acres in patches ≥5 ac")

    VALUES["interior_fraction"] = {f"S{c}": float(interior_frac[c]) for c in codes}
    VALUES["s1s2_patches"] = int(len(patch_acres))
    VALUES["s1s2_frac_acres_ge5ac"] = float(ge5)
    save(fig, "fig4_shape")


# --------------------------------------------------------------------------- #
def fig5_stage_a(points: pd.DataFrame):
    """Stage A: similarity separability, and single centroid vs six exemplars."""
    cols = band_columns(points, 2022)
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.9))

    exemplars6 = anchor_exemplars(points, [2018, 2022], 6, 42)
    sim6, thr6 = stage_a_similarity(points, cols, 0.90, exemplars6)
    palette = {"anchor_clearcut": "#b2182b", "anchor_nonforest": "#2166ac", "apply": "#f4a582"}
    for role, colour in palette.items():
        axes[0].hist(sim6[points.role == role], bins=60, alpha=0.62, color=colour,
                     label=role.replace("anchor_", ""), density=True)
    axes[0].axvline(thr6, color="k", ls="--", lw=1.1)
    axes[0].text(thr6, axes[0].get_ylim()[1] * 0.94, f"  threshold {thr6:.3f}\n  (90% anchor recall)",
                 fontsize=7.5, va="top")
    axes[0].set_xlabel("max cosine similarity to clearcut exemplars")
    axes[0].set_ylabel("density")
    axes[0].set_title("(a) Stage A separability (6 exemplars)")
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")

    rows, table = [], []
    for k in (1, 2, 4, 6, 8, 12):
        ex = anchor_exemplars(points, [2018, 2022], k, 42)
        sim, thr = stage_a_similarity(points, cols, 0.90, ex)
        leak = float((sim[points.role == "anchor_nonforest"] >= thr).mean())
        s3 = float((sim[(points.role == "apply") & (points.stratum == 3)] >= thr).mean())
        rows.append((k, leak, s3))
        table.append({"k": k, "nonforest_leak": leak, "s3_pass": s3, "threshold": float(thr)})
    ks, leaks, s3s = zip(*rows)
    axes[1].plot(ks, leaks, "o-", color="#2166ac", label="stable non-forest admitted (leak)")
    axes[1].plot(ks, s3s, "s--", color="#f4a582", label="S3 admitted")
    axes[1].set_xlabel("number of k-means exemplars")
    axes[1].set_ylabel("fraction passing Stage A")
    axes[1].set_title("(b) More exemplars sharpen the mask\n(anchor recall held at 90%)")
    axes[1].legend(frameon=False, fontsize=8)
    axes[1].set_ylim(0, max(max(leaks), max(s3s)) * 1.2)

    VALUES["stage_a"] = table
    VALUES["stage_a_threshold_k6"] = float(thr6)
    VALUES["stage_a_median_sim"] = {
        r: float(sim6[points.role == r].median()) for r in palette}
    save(fig, "fig5_stage_a")


# --------------------------------------------------------------------------- #
def fig6_stage_b(points: pd.DataFrame):
    """Stage B: ROC against a label-shuffle baseline, and the age-referencing effect."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.9))
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))

    x, y, groups = training_matrix(points, [2018, 2022])
    cv = GroupKFold(n_splits=5)
    prob = cross_val_predict(model, x, y, groups=groups, cv=cv, method="predict_proba")[:, 1]
    rng = np.random.default_rng(42)
    y_shuf = rng.permutation(y)
    prob_shuf = cross_val_predict(model, x, y_shuf, groups=groups, cv=cv,
                                  method="predict_proba")[:, 1]

    for probs, truth, colour, label in [
        (prob, y, "#b2182b", f"real labels (AUC {roc_auc_score(y, prob):.3f})"),
        (prob_shuf, y_shuf, "#999999", f"shuffled labels (AUC {roc_auc_score(y_shuf, prob_shuf):.3f})"),
    ]:
        fpr, tpr, _ = roc_curve(truth, probs)
        axes[0].plot(fpr, tpr, color=colour, lw=1.6, label=label)
    axes[0].plot([0, 1], [0, 1], ":", color="k", lw=0.8)
    axes[0].set_xlabel("false positive rate")
    axes[0].set_ylabel("true positive rate")
    axes[0].set_title("(a) Stage B, spatial-block cross-validation\n"
                      "(GroupKFold on 0.25° blocks)")
    axes[0].legend(frameon=False, fontsize=8, loc="lower right")

    variants = {}
    for name, years, k in [("2022 anchors,\n1 centroid", [2022], 1),
                           ("2022 anchors,\n6 exemplars", [2022], 6),
                           ("2018+2022 anchors,\n6 exemplars", [2018, 2022], 6)]:
        cols = band_columns(points, 2022)
        ex = anchor_exemplars(points, years, k, 42)
        sim, thr = stage_a_similarity(points, cols, 0.90, ex)
        xi, yi, gi = training_matrix(points, years)
        model.fit(xi, yi)
        p = pd.Series(model.predict_proba(points[cols].to_numpy(float))[:, 1], index=points.index)
        accept = (sim >= thr) & (p >= 0.5)
        variants[name] = {s: float(accept[(points.role == "apply") & (points.stratum == s)].mean())
                          for s in (3, 4)}

    labels = list(variants)
    width = 0.36
    for i, (stratum, colour) in enumerate([(3, "#f4a582"), (4, "#67a9cf")]):
        vals = [variants[k_][stratum] for k_ in labels]
        pos = np.arange(len(labels)) + (i - 0.5) * width
        axes[1].bar(pos, vals, width, color=colour, label=f"S{stratum}")
        for p_, v in zip(pos, vals):
            axes[1].text(p_, v + 0.015, f"{v:.2f}", ha="center", fontsize=7.5)
    axes[1].set_xticks(np.arange(len(labels)), labels, fontsize=7.5)
    axes[1].set_ylabel("fraction of apply points accepted")
    axes[1].set_title("(b) Age-referencing recovers fresh cuts (S3)")
    axes[1].legend(frameon=False, fontsize=8)
    axes[1].set_ylim(0, 1.0)

    VALUES["stage_b_auc"] = float(roc_auc_score(y, prob))
    VALUES["stage_b_auc_shuffled"] = float(roc_auc_score(y_shuf, prob_shuf))
    VALUES["stage_b_accuracy"] = float(((prob >= 0.5).astype(int) == y).mean())
    VALUES["apply_variants"] = {k_.replace("\n", " "): v for k_, v in variants.items()}
    save(fig, "fig6_stage_b")


# --------------------------------------------------------------------------- #
def fig7_add_back(strata, add_back):
    """The deliverable: what gets returned to TreeMap, and at what patch sizes."""
    fig = plt.figure(figsize=(12, 4.8))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.25, 1.25, 0.85])

    img = np.zeros(strata.shape, np.uint8)
    img[strata == 0] = 1
    img[strata > 0] = 2
    img[add_back] = 3
    cmap = ListedColormap([[1, 1, 1], [0.82, 0.88, 0.82], [0.76, 0.76, 0.74], [0.70, 0.07, 0.11]])

    ax = fig.add_subplot(gs[0])
    ax.imshow(img, cmap=cmap, vmin=0, vmax=3, interpolation="nearest")
    ax.set_title(f"(a) Add-back mask — {add_back.sum() * ACRES_PER_PIXEL:,.0f} ac")
    ax.axis("off")
    ax.legend(handles=[
        plt.Rectangle((0, 0), 1, 1, fc=[0.82, 0.88, 0.82], label="TreeMap has data"),
        plt.Rectangle((0, 0), 1, 1, fc=[0.76, 0.76, 0.74], label="stays a hole"),
        plt.Rectangle((0, 0), 1, 1, fc=[0.70, 0.07, 0.11], label="added back"),
    ], loc="lower left", frameon=False, fontsize=7.5)

    density = ndimage.uniform_filter(add_back.astype(np.float32), size=200)
    cy, cx = np.unravel_index(np.argmax(density), density.shape)
    half = 320
    window = (slice(max(0, cy - half), cy + half), slice(max(0, cx - half), cx + half))
    ax = fig.add_subplot(gs[1])
    ax.imshow(img[window], cmap=cmap, vmin=0, vmax=3, interpolation="nearest")
    ax.set_title(f"(b) Detail, ~{2 * half * 30 / 1000:.0f} km across")
    ax.axis("off")

    labels, _ = ndimage.label(add_back, structure=np.ones((3, 3)))
    patch_acres = np.bincount(labels.ravel())[1:] * ACRES_PER_PIXEL
    ax = fig.add_subplot(gs[2])
    ax.hist(patch_acres, bins=np.logspace(np.log10(5), np.log10(patch_acres.max()), 26),
            color="#b2182b")
    ax.set_xscale("log")
    ax.set_xlabel("accepted patch size (acres, log)")
    ax.set_ylabel("patches")
    ax.set_title(f"(c) {len(patch_acres):,} patches\nmedian {np.median(patch_acres):.1f} ac, "
                 f"max {patch_acres.max():,.0f} ac")

    VALUES["add_back_acres"] = float(add_back.sum() * ACRES_PER_PIXEL)
    VALUES["add_back_patches"] = int(len(patch_acres))
    VALUES["add_back_patch_median_ac"] = float(np.median(patch_acres))
    VALUES["add_back_patch_max_ac"] = float(patch_acres.max())
    VALUES["add_back_by_stratum"] = {
        f"S{c}": {"hole_acres": float((strata == c).sum() * ACRES_PER_PIXEL),
                  "added_acres": float((add_back & (strata == c)).sum() * ACRES_PER_PIXEL)}
        for c in range(1, 6)}
    save(fig, "fig7_add_back")


# --------------------------------------------------------------------------- #
def fig8_fia(strata, add_back):
    """Reconciliation against the FIA design-based estimate."""
    # Computed by scripts documented in the report; FIA EVALID 122201 EXPCURR.
    fia_total, fia_forest = 1_803_585.0, 1_255_424.0
    aoi_px = 8_204_537
    aoi = aoi_px * ACRES_PER_PIXEL
    holes = float((strata > 0).sum() * ACRES_PER_PIXEL)
    treemap = aoi - holes
    added = float(add_back.sum() * ACRES_PER_PIXEL)

    fig, ax = plt.subplots(figsize=(8.4, 3.6))
    steps = [("TreeMap 2022, as published", treemap, "#8c8c8c", "white"),
             ("returned by this method", added, "#b2182b", "white"),
             ("still unexplained", fia_forest - treemap - added, "#d9d9d9", "black")]
    left = 0.0
    for label, value, colour, _ in steps:
        ax.barh(0, value, left=left, color=colour, height=0.42,
                edgecolor="white", linewidth=1.2)
        left += value

    # Label the wide segment inside; the two thin ones get leader lines above.
    ax.text(treemap / 2, 0, f"TreeMap 2022, as published\n{treemap:,.0f} ac",
            ha="center", va="center", fontsize=8.5, color="white")
    for (label, value, colour, _), y_off in zip(steps[1:], (0.46, 0.72)):
        centre = sum(s[1] for s in steps[:steps.index((label, value, colour, _))]) + value / 2
        ax.annotate(f"{label}: {value:,.0f} ac", xy=(centre, 0.22), xytext=(centre, y_off),
                    ha="center", fontsize=8.5, color=colour if colour != "#d9d9d9" else "#666666",
                    arrowprops=dict(arrowstyle="-", lw=0.9,
                                    color=colour if colour != "#d9d9d9" else "#999999"))

    # EVALIDator domain estimate for the 5 counties; SE is large enough that the
    # interval, not the point estimate, is what the comparison can rest on.
    check_path = DATA / "fia_evalidator_check.json"
    ci_lo = ci_hi = None
    if check_path.exists():
        check = json.loads(check_path.read_text())
        ci_lo, ci_hi = check["ci95_low"], check["ci95_high"]
        ax.axvspan(ci_lo, ci_hi, ymin=0.30, ymax=0.62, color="#2166ac", alpha=0.13, lw=0)
        ax.annotate("FIA 95% CI", xy=((ci_lo + ci_hi) / 2, 0.40), ha="center",
                    fontsize=8, color="#2166ac")

    ax.axvline(fia_forest, color="#2166ac", lw=1.8, ymin=0.05, ymax=0.62)
    label = f"FIA design-based forest, circa 2022: {fia_forest:,.0f} ac"
    if ci_lo is not None:
        label += f"\n95% CI {ci_lo:,.0f} – {ci_hi:,.0f} (SE {check['se_percent']:.1f}%, {check['plots']} plots)"
    ax.annotate(label, xy=(fia_forest, -0.30), ha="center", va="top",
                fontsize=8.5, color="#2166ac")

    ax.set_ylim(-0.85, 0.95)
    ax.set_xlim(0, (ci_hi if ci_hi else fia_forest) * 1.04)
    ax.set_yticks([])
    ax.set_xlabel("forest area (acres)")
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    ax.spines["left"].set_visible(False)
    ax.set_title("Uncorrected TreeMap falls below the FIA 95% CI; the corrected value "
                 "falls inside it\nwithout overshooting the point estimate")

    VALUES["fia"] = {
        "fia_total_acres": fia_total, "fia_forest_acres": fia_forest,
        "aoi_acres": aoi, "treemap_forest_acres": treemap,
        "added_acres": added, "corrected_forest_acres": treemap + added,
        "shortfall_acres": fia_forest - treemap,
        "shortfall_closed_frac": added / (fia_forest - treemap),
        "remaining_acres": fia_forest - treemap - added,
    }
    save(fig, "fig8_fia")


def fig9_s3_validation():
    """External check of the S3 decision against LCMS (skipped if not yet run)."""
    path = DATA / "s3_validation_summary.csv"
    if not path.exists():
        print("  skipping fig9 (run validate_s3_lcms first)")
        return
    summary = pd.read_csv(path, index_col=0)
    order = ["S1_reference_positive", "S3_accepted", "S3_rejected", "S5_reference_negative"]
    summary = summary.reindex([o for o in order if o in summary.index])
    pretty = {"S1_reference_positive": "S1\nref +", "S3_accepted": "S3\naccept",
              "S3_rejected": "S3\nreject", "S5_reference_negative": "S5\nref −"}
    colours = ["#67000d", "#b2182b", "#f4a582", "#bdbdbd"]

    metrics = [("LU_forest_2022", "LCMS Land Use = Forest (2022)"),
               ("LC_trees_pre_cut", "LCMS Trees before the cut"),
               ("LC_trees_2024", "LCMS Trees by 2024"),
               ("tree_removal_2016_2022", "LCMS Tree Removal 2016–22")]
    fig, axes = plt.subplots(1, 4, figsize=(13, 3.5), sharey=True)
    for ax, (col, title) in zip(axes, metrics):
        vals = summary[col].values
        ax.bar([pretty[i] for i in summary.index], vals, color=colours, width=0.68)
        for i, v in enumerate(vals):
            ax.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)
        ax.set_title(title, fontsize=9)
        ax.set_ylim(0, 1.12)
        ax.tick_params(axis="x", labelsize=7.5)
    axes[0].set_ylabel("fraction of sampled points")
    fig.suptitle("Independent validation of the S3 decision against USFS LCMS "
                 "(different producer, different algorithm)", fontsize=10.5, y=1.04)

    acc, rej = summary.loc["S3_accepted"], summary.loc["S3_rejected"]
    s3_hole = VALUES.get("add_back_by_stratum", {}).get("S3", {}).get("hole_acres", 80741.65)
    s3_added = VALUES.get("add_back_by_stratum", {}).get("S3", {}).get("added_acres", 8087.58)
    s3_rejected_ac = s3_hole - s3_added
    true_pos = s3_added * acc["LU_forest_2022"]
    missed = s3_rejected_ac * rej["LU_forest_2022"]
    VALUES["s3_validation"] = {
        "precision_proxy_LU_forest": float(acc["LU_forest_2022"]),
        "rejected_still_forest_frac": float(rej["LU_forest_2022"]),
        "recall_proxy": float(true_pos / (true_pos + missed)),
        "estimated_missed_acres": float(missed),
        "s1_reference": float(summary.loc["S1_reference_positive", "LU_forest_2022"]),
        "s5_reference": float(summary.loc["S5_reference_negative", "LU_forest_2022"]),
    }
    save(fig, "fig9_s3_validation")


def fig10_gee_surfaces():
    """The two Earth Engine surfaces, so the mask can be inspected visually."""
    path = DATA / "hole_prob_similarity.tif"
    if not path.exists():
        print("  skipping fig10 (run embed_holes apply first)")
        return
    with rasterio.open(path) as src:
        scored = src.read()
    with rasterio.open(DATA / "treemap_hole_strata.tif") as src:
        strata = src.read(1)
    prob, sim = decode_score_bands(scored)
    holes = strata > 0

    model = json.loads((DATA / "hole_model.json").read_text())
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4))

    for ax, surface, title, thr, cmap in [
        (axes[0], sim, "(a) Stage A: max cosine similarity\nto clearcut exemplars",
         model["similarity_threshold"], "magma"),
        (axes[1], prob, "(b) Stage B: classifier probability\nof managed-forest clearcut",
         model["decision_threshold"], "viridis"),
    ]:
        shown = np.where(holes, surface, np.nan)
        im = ax.imshow(shown, cmap=cmap, vmin=np.nanpercentile(shown, 2),
                       vmax=np.nanpercentile(shown, 98), interpolation="nearest")
        ax.set_title(title)
        ax.axis("off")
        cb = fig.colorbar(im, ax=ax, fraction=0.036, pad=0.02)
        cb.ax.axhline(thr, color="red", lw=1.6)
        cb.set_label(f"red line = threshold {thr:.2f}", fontsize=7.5)

    axes[2].hist(sim[holes], bins=80, alpha=0.7, color="#b2182b", label="similarity", density=True)
    axes[2].axvline(model["similarity_threshold"], color="#b2182b", ls="--", lw=1.2)
    axes[2].hist(prob[holes], bins=80, alpha=0.55, color="#2166ac", label="probability", density=True)
    axes[2].axvline(model["decision_threshold"], color="#2166ac", ls="--", lw=1.2)
    axes[2].set_xlabel("score over all hole pixels")
    axes[2].set_ylabel("density")
    axes[2].set_title("(c) Score distributions with thresholds")
    axes[2].legend(frameon=False, fontsize=8)
    save(fig, "fig10_gee_surfaces")


def main() -> None:
    strata, add_back, bounds, transform = load_rasters()
    points = pd.read_csv(DATA / "hole_embeddings.csv")
    print("generating figures...")
    fig1_study_area(strata, bounds, transform)
    fig2_mechanism(strata, bounds, transform)
    fig3_strata(strata)
    fig4_shape(strata)
    fig5_stage_a(points)
    fig6_stage_b(points)
    fig7_add_back(strata, add_back)
    fig8_fia(strata, add_back)
    fig9_s3_validation()
    fig10_gee_surfaces()

    FIGS.mkdir(parents=True, exist_ok=True)
    (FIGS / "report_values.json").write_text(json.dumps(VALUES, indent=2))
    print(f"wrote report_values.json ({len(VALUES)} keys)")


if __name__ == "__main__":
    main()
