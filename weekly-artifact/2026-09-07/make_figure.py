"""Stage 3 — render what adding a timing dimension did to the plan.

Reads only the committed CSVs and `solution_quality.json` in this directory and the
2026-08-31 artifact's, so the figure regenerates without re-running FVS or the annealer.
Every plotted number is also in a committed table.

Four panels, in the order the argument runs:

  (a) The plan against its target per cycle, with **both** attainable ceilings drawn —
      last week's from the untimed library, this week's from the timed one. The gap
      between the two lines is what the offset grid bought, and the far right of the
      panel is where it shows: cycle 10's ceiling was exactly zero.
  (b) Attainability, county x cycle, coloured by what *changed*. Recovered targets are
      the headline; the ones still out of reach say what timing alone could not fix.
  (c) The delays the scheduler actually chose, by acreage. A grid the search ignored
      would show as a single bar at offset 0.
  (d) The objective and the unreachable-target count, this week beside last week's, with
      the greedy baseline that should not have moved at all.

Palette: the Okabe-Ito-derived categorical set used by every figure in this series,
validated with the dataviz palette checker against the light surface #fcfcfb (lightness
band, chroma floor, normal-vision floor all pass; worst adjacent CVD pair is in the 6-8
floor band, which is legal with the secondary encoding used here — every low-contrast hue
carries a direct value label, and every plotted number is also in a committed CSV).

Usage:
    uv run python weekly-artifact/2026-09-07/make_figure.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
PREV_DIR = OUT_DIR.parent / "2026-08-31"

SURFACE = "#fcfcfb"
INK = "#1a1a19"
INK_2 = "#55554f"
MUTED = "#8a8a82"
GRID = "#e4e4de"

BLUE = "#0072B2"
ORANGE = "#D55E00"
GREEN = "#009E73"
PINK = "#CC79A7"
AMBER = "#E69F00"
SKY = "#56B4E9"
NEUTRAL = "#d9d9d3"

MCF = 1e6   # plot in million cubic feet

# The four states a target can be in between the two runs. Blue for the good direction,
# orange for the bad one, neutral for no change — the same reading as last week's
# attainability panel, so the two figures can be laid side by side.
CHANGE_COLOR = {
    "recovered": BLUE,
    "unchanged": NEUTRAL,
    "still unreachable": ORANGE,
    "lost": PINK,
}


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8, length=3, width=0.8)
    ax.yaxis.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def panel_plan(ax, cycles: pd.DataFrame, delta: pd.DataFrame):
    """Achieved volume per cycle, its target, and both weeks' attainable ceilings."""
    ceil = (delta[delta.dimension == "county"]
            .groupby("cycle", as_index=False)[["max_attainable_cuft_prev",
                                               "max_attainable_cuft_now"]].sum())
    x = cycles["cycle"]
    ax.bar(x, cycles["cuft"] / MCF, width=0.62, color=BLUE, zorder=3,
           label="Annealed plan (timed library)")
    ax.plot(ceil["cycle"], ceil["max_attainable_cuft_now"] / MCF, marker="o", ms=5,
            lw=2, color=ORANGE, zorder=5, label="Ceiling — with timing offsets")
    ax.plot(ceil["cycle"], ceil["max_attainable_cuft_prev"] / MCF, marker="o", ms=4,
            lw=1.6, ls=(0, (4, 2)), color=MUTED, zorder=4,
            label="Ceiling — 2026-08-31, no offsets")
    target = cycles["target_cuft"].iloc[0] / MCF
    ax.axhline(target, ls=(0, (5, 3)), lw=2, color=INK_2, zorder=2,
               label="TPO target (2013–2024)")

    for xi, yi in zip(x, cycles["cuft"] / MCF):
        if yi >= 100:
            ax.annotate(f"{yi:,.0f}", (xi, yi), textcoords="offset points", xytext=(0, -12),
                        ha="center", fontsize=7.5, color=SURFACE, zorder=6)
        elif yi > 0:
            ax.annotate(f"{yi:,.0f}", (xi, yi), textcoords="offset points", xytext=(13, -3),
                        ha="center", fontsize=7.5, color=INK_2, zorder=6)

    # The one cycle the offsets could not touch, and the reason, said on the figure.
    ax.annotate("2072 hosts no cycle:\nFVS never executes\nan entry scheduled here",
                xy=(x.iloc[-1], 0), xytext=(-14, 250), textcoords="offset points",
                ha="right", fontsize=7.5, color=INK_2, linespacing=1.35,
                arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.9,
                                shrinkA=0, shrinkB=4))

    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{y}" for y in cycles["calendar_year"]], rotation=45, ha="right")
    ax.set_ylabel("Removed merchantable volume  (million ft³ / 5-yr cycle)",
                  fontsize=8.5, color=INK_2)
    ax.set_title("(a)  Adding *when* raised the ceiling the plan was pinned under",
                 loc="left", fontsize=10.5, color=INK, pad=8)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK_2, loc="upper center",
              bbox_to_anchor=(0.5, -0.20), ncol=2, handletextpad=0.5, columnspacing=1.4)
    _style(ax)


def panel_change(ax, delta: pd.DataFrame):
    """County x cycle, coloured by how each target moved between the two runs."""
    c = delta[delta.dimension == "county"]
    keys = sorted(c["key"].unique())
    cycles = sorted(c["cycle"].unique())
    pct = c.pivot_table(index="key", columns="cycle",
                        values="max_as_pct_of_target_now").reindex(keys)
    change = c.pivot_table(index="key", columns="cycle", values="change",
                           aggfunc="first").reindex(keys)

    for i, k in enumerate(keys):
        for j, cy in enumerate(cycles):
            state = change.loc[k, cy]
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1,
                                       color=CHANGE_COLOR[state], zorder=1))
            # Every cell carries its own value, so the hue is a secondary encoding.
            ax.text(j, i, f"{pct.loc[k, cy]:.0f}", ha="center", va="center", fontsize=7,
                    color=SURFACE if state in ("recovered", "still unreachable") else INK,
                    zorder=2)
    ax.set_xlim(-0.5, len(cycles) - 0.5)
    ax.set_ylim(len(keys) - 0.5, -0.5)
    ax.set_xticks(range(len(cycles)))
    ax.set_xticklabels([str(c) for c in cycles], fontsize=8)
    ax.set_yticks(range(len(keys)))
    ax.set_yticklabels(keys, fontsize=8)
    ax.set_xlabel("cycle  (cell value: ceiling as % of county target, this week)",
                  fontsize=8.5, color=INK_2)
    ax.set_title("(b)  What moved: county targets, before and after the offset grid",
                 loc="left", fontsize=10.5, color=INK, pad=8)
    ax.set_facecolor(SURFACE)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=INK_2, length=0)
    order = ["recovered", "unchanged", "still unreachable", "lost"]
    labels = {"recovered": "no longer provably unreachable",
              "unchanged": "was reachable, still is",
              "still unreachable": "still unreachable at any selection",
              "lost": "newly unreachable"}
    present = [s for s in order if (change.to_numpy() == s).any()]
    ax.legend(handles=[Patch(color=CHANGE_COLOR[s], label=labels[s]) for s in present],
              frameon=False, fontsize=8, labelcolor=INK_2, loc="upper left",
              bbox_to_anchor=(0.0, -0.13), ncol=2, handletextpad=0.5, columnspacing=1.4)


def panel_offsets(ax, offset_mix: pd.DataFrame):
    """The delays the scheduler chose on cutting stands, by acreage."""
    d = offset_mix.sort_values("offset_years")
    labels = ["as resolved" if o == 0 else f"+{int(o)} yr" for o in d["offset_years"]]
    y = range(len(d))
    ax.barh(list(y), d["acres"] / 1000, color=BLUE, height=0.62, zorder=3)
    for i, (a, p) in enumerate(zip(d["acres"] / 1000, d["acres_pct"])):
        ax.annotate(f"{a:,.0f}k ac  ({p:.0f}%)", (a, i), textcoords="offset points",
                    xytext=(6, 0), va="center", fontsize=8, color=INK_2, zorder=4)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlim(0, float((d["acres"] / 1000).max()) * 1.42)
    ax.set_xlabel("thousand acres of cutting stands", fontsize=8.5, color=INK_2)
    ax.set_title("(c)  The scheduler uses the timing it was given", loc="left",
                 fontsize=10.5, color=INK, pad=8)
    _style(ax)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, lw=0.8)


def panel_quality(ax, q: dict, prev: dict):
    """Objective this week beside last week's, with the unreachable count alongside."""
    o = q["objective_vs_2026_08_31"]
    rows = [
        ("Random (mean of 5)", prev["objective_random_baseline_mean"],
         q["objective_random_baseline_mean"], MUTED),
        ("Greedy baseline", o["greedy_prev"], o["greedy_now"], AMBER),
        ("Annealed plan", o["best_prev"], o["best_now"], BLUE),
        ("Relaxation bound", o["bound_prev"], o["bound_now"], GREEN),
    ]
    y = range(len(rows))
    h = 0.34
    for i, (_, before, after, color) in enumerate(rows):
        ax.barh(i - h / 2 - 0.02, before, height=h, color=NEUTRAL, zorder=3)
        ax.barh(i + h / 2 + 0.02, after, height=h, color=color, zorder=3)
        ax.annotate(f"{before:,.1f}", (before, i - h / 2 - 0.02), xytext=(5, 0),
                    textcoords="offset points", va="center", fontsize=7.5, color=MUTED)
        ax.annotate(f"{after:,.1f}", (after, i + h / 2 + 0.02), xytext=(5, 0),
                    textcoords="offset points", va="center", fontsize=7.5, color=INK_2)
    ax.set_yticks(list(y))
    ax.set_yticklabels([r[0] for r in rows], fontsize=8.5)
    ax.invert_yaxis()
    widest = max(max(r[1], r[2]) for r in rows)
    ax.set_xlim(0, widest * 1.22)
    ax.set_xlabel("objective  (lower is better)", fontsize=8.5, color=INK_2)
    ax.set_title("(d)  Solution quality, 2026-08-31 → 2026-09-07", loc="left",
                 fontsize=10.5, color=INK, pad=8)
    ax.legend(handles=[Line2D([], [], marker="s", ls="", ms=8, color=NEUTRAL,
                              label="2026-08-31 (no offsets)"),
                       Line2D([], [], marker="s", ls="", ms=8, color=BLUE,
                              label="2026-09-07 (timed library)")],
              frameon=False, fontsize=8, labelcolor=INK_2, loc="lower right",
              handletextpad=0.4)
    _style(ax)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, lw=0.8)


def main() -> None:
    cycles = pd.read_csv(OUT_DIR / "harvest_by_cycle.csv")
    delta = pd.read_csv(OUT_DIR / "envelope_delta.csv")
    offset_mix = pd.read_csv(OUT_DIR / "offset_mix.csv")
    q = json.loads((OUT_DIR / "solution_quality.json").read_text())
    prev = json.loads((PREV_DIR / "solution_quality.json").read_text())

    fig = plt.figure(figsize=(16.5, 11.4), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 0.86], hspace=0.46, wspace=0.20,
                          left=0.055, right=0.975, top=0.880, bottom=0.095)

    panel_plan(fig.add_subplot(gs[0, 0]), cycles, delta)
    panel_change(fig.add_subplot(gs[0, 1]), delta)
    panel_offsets(fig.add_subplot(gs[1, 0]), offset_mix)
    panel_quality(fig.add_subplot(gs[1, 1]), q, prev)

    att = q["attainability_vs_2026_08_31"]
    fig.suptitle("ARTEMIS — what a timing dimension buys: the offset grid, five-county "
                 "Florida pilot", x=0.055, ha="left", fontsize=15.5, color=INK, y=0.968)
    fig.text(0.055, 0.936,
             f"{q['library']['options_total']:,} trajectories over {q['stands']:,} stands, "
             f"median {q['library']['options_per_upland_stand_median']} per upland stand "
             f"(§4 asks for 6–12; last week's median was 3) · "
             f"targets proven unreachable: {att['unreachable_prev']} → "
             f"{att['unreachable_now']} of {q['targets_total']} "
             f"({att['recovered']} recovered, {att['newly_unreachable']} newly unreachable)",
             ha="left", fontsize=9.5, color=INK_2)
    fig.text(0.055, 0.912,
             "Offset 0 reproduces 2026-08-31 exactly — same config, seeds, targets and "
             "greedy seed · spatial penalties still unavailable: stands are pixel "
             "classes, so there is no polygon adjacency",
             ha="left", fontsize=9.5, color=MUTED)

    # The honest caveat under the headline: a decision space four times larger is harder
    # to search on an unchanged iteration budget, and the seed spread says so.
    fig.text(0.055, 0.022,
             f"Seed spread across the same five restarts: "
             f"{prev['seed_spread']['range']:.3f} → {q['seed_spread']['range']:.2f} "
             f"({100 * q['seed_spread']['range'] / q['objective_best']:.1f}% of the "
             f"objective). A four-times larger decision space, searched on an unchanged "
             f"iteration budget, is searched less tightly.",
             ha="left", fontsize=8, color=MUTED)

    out = OUT_DIR / "timing_offsets.png"
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
