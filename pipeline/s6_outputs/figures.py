"""
Draw the canonical FVS output figures.

Every figure here is drawn from one of the tables `age_class.py` emits, and every one is
written beside the CSV it came from. That pairing is load-bearing rather than tidy: the
forest-type hues are validated as a categorical set, and the pine hue sits at 2.74:1
against a white surface — under the 3:1 bar — so the table is the accessibility relief the
palette requires, not an optional extra.

Encoding decisions, once, so the figures stay consistent:

* **Acres, never stand counts.** Bar height is always acreage on the basis named in the
  subtitle; see `age_class.py` for why the two bases never share an axis.
* **Age class is ordered, so it is the x axis** — a distribution reads left to right as
  young to old, with the open top class last. Where age class has to be a *color* (the
  stacked view over time) it takes one hue light-to-dark, never a categorical cycle.
* **Forest type is an identity, so it is the color** — three validated hues, assigned in a
  fixed order that does not change when a cut drops a type. Nonstocked is a neutral gray
  because it is the absence of a type rather than a fourth one.
* **Owner classes and counties are faceted, not stacked into one axis.** There are nine of
  the former and up to sixty of the latter, well past the eight a categorical palette can
  separate; small multiples on a shared axis compare them without asking color to do work
  it cannot.
* **No dual axes anywhere.** Where acres and mean age both matter they are two panels.

Usage:
    from pipeline.s6_outputs import figures

    figures.age_class_by_forest_type(by_type_table, out_dir, years=(2026, 2076))
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D

from pipeline.s6_outputs.fvs_out_db import load_output_config

logger = logging.getLogger(__name__)

# Fixed assignment, by the broad forest-type label the tables carry. Keyed on the label so
# a cut that has no oak/pine acres still draws hardwood in the hardwood hue.
_GROUP_KEYS = {
    "Pine (softwood)": "pine",
    "Oak/pine (mixed)": "mixed",
    "Hardwood": "hardwood",
    "Nonstocked": "nonstocked",
}

BAR_GAP = 0.18          # surface gap between adjacent bars, as a share of the slot
MIN_FACET_ACRES = 1.0   # a facet below this has nothing to draw and is dropped
REFERENCE_MAX_SERIES = 4  # past this the 1:1 guide is hidden under the data


def _style(config: dict) -> dict:
    fig = config["figures"]
    plt.rcParams.update({
        "font.size": 8.5,
        "axes.titlesize": 9.5,
        "axes.labelsize": 8.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": fig["text_secondary"],
        "axes.labelcolor": fig["text_secondary"],
        "text.color": fig["text_primary"],
        "xtick.color": fig["text_secondary"],
        "ytick.color": fig["text_secondary"],
        "grid.color": fig["grid"],
        "grid.linewidth": 0.6,
        "figure.dpi": fig["dpi"],
        "savefig.bbox": "tight",
        "savefig.facecolor": "white",
        "legend.frameon": False,
    })
    return fig


def _color(label: str, palette: dict) -> str:
    return palette.get(_GROUP_KEYS.get(label, "unknown"), palette["unknown"])


def _age_order(table: pd.DataFrame) -> list[str]:
    """Age-class labels in age order, `unknown` last — the x axis of every distribution."""
    ordered = table[["age_class_label", "age_class_sort"]].drop_duplicates()
    return ordered.sort_values("age_class_sort")["age_class_label"].tolist()


def _acre_axis(ax, peak: float) -> str:
    """
    Label the y axis in the unit the panel's own numbers are readable in.

    A facet grid runs from 276,000 acres down to 2,500, and printing both in thousands
    gives the small panel a column of rounded zeros. The unit goes in the axis label, so a
    panel is never read against the wrong scale.
    """
    if peak >= 10_000:
        ax.yaxis.set_major_formatter(lambda v, _: f"{v / 1000:,.0f}")
        return "Thousand acres"
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    return "Acres"


def _save(fig, out_dir: Path, name: str, config: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.{config['figures']['format']}"
    fig.savefig(path)
    plt.close(fig)
    logger.info("wrote %s", path.name)
    return path


def _basis_note(table: pd.DataFrame) -> str:
    bases = sorted(table["weight_basis"].unique())
    words = {
        "sampling_weight": "FVS sampling-weight acres (whole run)",
        "owner_acres": "management-unit acres from the ownership run (crosswalked plots only)",
    }
    return " / ".join(words.get(b, b) for b in bases)


def _grouped_bars(ax, table: pd.DataFrame, series_col: str, palette: dict,
                  ages: list[str]) -> list[str]:
    """Age class on x, one colored bar per series in each class."""
    series = [s for s in _GROUP_KEYS if s in set(table[series_col])]
    series += [s for s in sorted(set(table[series_col])) if s not in series]
    x = np.arange(len(ages))
    width = (1 - BAR_GAP) / max(len(series), 1)
    for i, name in enumerate(series):
        rows = table[table[series_col] == name].set_index("age_class_label")
        acres = [rows["acres"].get(a, 0.0) for a in ages]
        ax.bar(x - 0.5 + BAR_GAP / 2 + width * (i + 0.5), acres, width * 0.92,
               color=_color(name, palette), label=name, linewidth=0)
    ax.set_xticks(x, ages, rotation=90)
    ax.grid(axis="y", zorder=0)
    ax.set_axisbelow(True)
    _acre_axis(ax, table["acres"].max() if len(table) else 0)
    return series


# ---- the canonical figures -------------------------------------------------------------

def age_class_by_forest_type(table: pd.DataFrame, out_dir: Path, years,
                             *, config: dict | None = None) -> Path:
    """
    The headline: acres by age class, pine against hardwood, at each snapshot year.

    One panel per year on a shared y axis, so the fifty-year shift is a change in shape
    rather than a change in scale the eye has to correct for.
    """
    config = config or load_output_config()
    style = _style(config)
    years = list(years)
    ages = _age_order(table)

    fig, axes = plt.subplots(1, len(years), figsize=(5.4 * len(years), 3.6), sharey=True)
    axes = np.atleast_1d(axes)
    series: list[str] = []
    for ax, year in zip(axes, years):
        series = _grouped_bars(ax, table[table["Year"] == year], "forest_type_label",
                               style["palette"], ages)
        ax.set_title(f"{year}")
        ax.set_xlabel("Stand age class (years)")
    axes[0].set_ylabel("Thousand acres")

    handles = [Line2D([], [], color=_color(s, style["palette"]), lw=6) for s in series]
    axes[-1].legend(handles, series, loc="upper right", title=None)
    fig.suptitle("Age-class distribution by forest type", y=1.02, fontsize=11)
    fig.text(0, -0.13, f"Weight: {_basis_note(table)}.", fontsize=7.5,
             color=style["text_secondary"])
    return _save(fig, out_dir, "age_class_by_forest_type", config)


def age_class_over_time(table: pd.DataFrame, out_dir: Path,
                        *, config: dict | None = None) -> Path:
    """
    Acres in each age class across the whole projection, one panel per forest type.

    A stacked area with age class on a light-to-dark ramp: under no management the bands
    migrate upward together, and anything that interrupts that migration — mortality, a
    forest-type change — shows as a band that stops.
    """
    config = config or load_output_config()
    style = _style(config)
    ages = _age_order(table)
    ramp = LinearSegmentedColormap.from_list("age", style["age_ramp"])
    colors = [ramp(i / max(len(ages) - 1, 1)) for i in range(len(ages))]

    types = [t for t in _GROUP_KEYS if t in set(table["forest_type_label"])]
    fig, axes = plt.subplots(1, len(types), figsize=(4.0 * len(types), 3.4), sharey=True)
    axes = np.atleast_1d(axes)
    for ax, forest_type in zip(axes, types):
        rows = table[table["forest_type_label"] == forest_type]
        years = sorted(rows["Year"].unique())
        wide = (rows.pivot_table(index="Year", columns="age_class_label", values="acres",
                                 aggfunc="sum")
                .reindex(index=years, columns=ages).fillna(0.0))
        ax.stackplot(years, [wide[a].to_numpy() for a in ages], colors=colors, linewidth=0)
        ax.set_title(forest_type)
        ax.set_xlabel("Year")
        ax.margins(x=0)
        unit = _acre_axis(ax, wide.to_numpy().sum(axis=1).max())
    axes[0].set_ylabel(unit)

    # Direct-label the ramp rather than printing fifteen legend entries. Outside the axes:
    # the oldest band fills the top right of the last panel, where a legend would sit.
    handles = [Line2D([], [], color=colors[i], lw=6) for i in (0, len(ages) // 2, -1)]
    fig.legend(handles, [ages[0], ages[len(ages) // 2], ages[-1]],
               loc="center left", bbox_to_anchor=(1.0, 0.55), title="Age class",
               title_fontsize=7.5, fontsize=7.5)
    fig.suptitle("Acres by age class through the projection", y=1.02, fontsize=11)
    fig.text(0, -0.1, f"Weight: {_basis_note(table)}. Age class runs light (young) to "
             "dark (old).", fontsize=7.5, color=style["text_secondary"])
    return _save(fig, out_dir, "age_class_over_time", config)


def _facet_grid(table: pd.DataFrame, facet_col: str, out_dir: Path, name: str, title: str,
                year: int, *, order=None, config: dict | None = None,
                max_facets: int | None = None) -> Path:
    """Small multiples: one age-class panel per facet, stacked by forest type."""
    config = config or load_output_config()
    style = _style(config)
    ages = _age_order(table)
    rows = table[table["Year"] == year]

    totals = rows.groupby(facet_col)["acres"].sum().sort_values(ascending=False)
    # A facet with no acres is not a finding, it is an empty panel. The commonest one is
    # `Unattributed`, which by construction carries no owner acreage at all.
    totals = totals[totals >= MIN_FACET_ACRES]
    if totals.empty:
        raise ValueError(f"no {facet_col} facet in {year} has acres to draw")
    facets = [f for f in (order or []) if f in totals.index]
    facets += [f for f in totals.index if f not in facets]
    if max_facets:
        facets = facets[:max_facets]

    cols = min(4, max(len(facets), 1))
    grid_rows = math.ceil(len(facets) / cols)
    # Each panel labels its own x axis: the grid's last row is ragged, so a shared axis
    # would leave the bottom panel of a short column unlabelled.
    fig, axes = plt.subplots(grid_rows, cols, figsize=(3.3 * cols, 2.7 * grid_rows),
                             squeeze=False)
    flat = axes.ravel()
    stacked = "forest_type_label" in rows.columns
    # Fixed across every panel, from the whole grid rather than one facet: a stack order
    # that changed per panel would repaint the same forest type in two colors, and a
    # legend built from the last panel would omit whatever that panel happens to lack.
    series = [s for s in _GROUP_KEYS if s in set(rows["forest_type_label"])] if stacked \
        else []

    for ax, facet in zip(flat, facets):
        panel = rows[rows[facet_col] == facet]
        x = np.arange(len(ages))
        if stacked:
            bottom = np.zeros(len(ages))
            for name_ in series:
                part = panel[panel["forest_type_label"] == name_].set_index("age_class_label")
                acres = np.array([part["acres"].get(a, 0.0) for a in ages])
                ax.bar(x, acres, 1 - BAR_GAP, bottom=bottom,
                       color=_color(name_, style["palette"]), linewidth=0)
                bottom += acres
        else:
            part = panel.set_index("age_class_label")
            ax.bar(x, [part["acres"].get(a, 0.0) for a in ages], 1 - BAR_GAP,
                   color=style["palette"]["mixed"], linewidth=0)
        ax.set_title(f"{facet}\n{totals.get(facet, 0):,.0f} acres", fontsize=8.5)
        ax.set_xticks(x[::2], [ages[i] for i in range(0, len(ages), 2)], rotation=90,
                      fontsize=7)
        ax.grid(axis="y")
        ax.set_axisbelow(True)
        ax.set_ylabel(_acre_axis(ax, panel.groupby("age_class_label")["acres"].sum().max()))

    for ax in flat[len(facets):]:
        ax.set_visible(False)

    if stacked and series:
        handles = [Line2D([], [], color=_color(s, style["palette"]), lw=6) for s in series]
        fig.legend(handles, series, loc="lower center", ncols=len(series),
                   bbox_to_anchor=(0.5, -0.04))
    fig.suptitle(f"{title} — {year}", y=1.0, fontsize=11)
    fig.text(0, -0.08,
             f"Weight: {_basis_note(table)}. Each panel has its own y axis — the panel "
             "total is in its title; compare shapes, not bar heights.",
             fontsize=7.5, color=style["text_secondary"])
    fig.tight_layout()
    return _save(fig, out_dir, name, config)


def age_class_by_owner(table: pd.DataFrame, out_dir: Path, year: int,
                       *, config: dict | None = None) -> Path:
    """One age-class panel per owner class, each stacked by forest type."""
    config = config or load_output_config()
    order = [*config["owner_classes"]["order"],
             config["owner_classes"]["unattributed_label"]]
    return _facet_grid(table, "owner_class", out_dir, "age_class_by_owner",
                       "Age-class distribution by owner class", year,
                       order=order, config=config)


def age_class_by_management_type(table: pd.DataFrame, out_dir: Path, year: int,
                                 *, config: dict | None = None) -> Path:
    """Upland management units against streamside zones, which are never harvested."""
    config = config or load_output_config()
    return _facet_grid(table, "management_type", out_dir, "age_class_by_management_type",
                       "Age-class distribution by management type", year, config=config)


def age_class_by_area(table: pd.DataFrame, area_col: str, out_dir: Path, year: int,
                      *, name: str, title: str, config: dict | None = None) -> Path:
    config = config or load_output_config()
    top_n = config["areas"]["top_n_counties"] if area_col == "county" else None
    return _facet_grid(table, area_col, out_dir, name, title, year,
                       config=config, max_facets=top_n)


def mean_age_trajectory(table: pd.DataFrame, out_dir: Path, series_col: str,
                        *, name: str, title: str, config: dict | None = None) -> Path:
    """
    Acre-weighted mean age per year, one line per series, end-labelled.

    Under no management a line that tracks the 1:1 reference is a forest that is only
    getting older; a line that falls away from it is one losing old acres to mortality or
    to a forest-type change. That the lines here come out parallel is the finding, not a
    plotting artifact — the reference is drawn only where few enough series leave room to
    see it.
    """
    config = config or load_output_config()
    style = _style(config)
    fig, ax = plt.subplots(figsize=(6.6, 3.8))

    table = table.dropna(subset=["mean_age"])
    years = sorted(table["Year"].unique())
    series = list(table.groupby(series_col, observed=True))

    if len(series) <= REFERENCE_MAX_SERIES:
        # The run's own starting mean age, aged one year per year: what a projection with
        # no mortality and no type change would draw. Past a handful of series the lines
        # sit on top of it and it stops being readable.
        first = table[table["Year"] == years[0]]
        start_age = np.average(first["mean_age"], weights=first["acres"])
        reference = start_age + (np.array(years) - years[0])
        ax.plot(years, reference, color=style["palette"]["nonstocked"], lw=1.2, ls=(0, (5, 4)),
                zorder=1)
        ax.annotate("one year older per year", (years[0], reference[0]),
                    textcoords="offset points", xytext=(2, -13), ha="left", fontsize=7.5,
                    color=style["text_secondary"])

    ends: list[tuple[float, str]] = []
    for label, rows in series:
        rows = rows.sort_values("Year")
        color = _color(label, style["palette"]) if series_col == "forest_type_label" \
            else style["palette"]["mixed"]
        ax.plot(rows["Year"], rows["mean_age"], lw=2, color=color, marker="o", ms=4,
                zorder=2)
        ends.append((float(rows["mean_age"].iloc[-1]), str(label)))

    # End labels carry the identity (the owner cut draws one hue for eight series), so they
    # have to be legible: push any that would overlap apart, keeping their vertical order.
    span = max(e[0] for e in ends) - min(e[0] for e in ends)
    gap = max(span * 0.055, 1.0)
    placed: list[tuple[float, float, str]] = []
    for value, label in sorted(ends):
        y = value if not placed else max(value, placed[-1][1] + gap)
        placed.append((value, y, label))
    for value, y, label in placed:
        ax.annotate(f" {label}", (years[-1], y), fontsize=8,
                    color=style["text_secondary"], va="center")
        if abs(y - value) > 0.1:
            ax.plot([years[-1], years[-1]], [value, y], lw=0.6, color=style["grid"],
                    zorder=1)

    ax.set_xlabel("Year")
    ax.set_ylabel("Acre-weighted mean stand age (years)")
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    ax.margins(x=0.22)
    fig.suptitle(title, y=1.0, fontsize=11)
    fig.text(0, -0.1, f"Weight: {_basis_note(table)}. Stands with no FIA age are excluded.",
             fontsize=7.5, color=style["text_secondary"])
    return _save(fig, out_dir, name, config)
