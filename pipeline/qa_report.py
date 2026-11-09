"""A stage's QA report page: every table and figure in ``qa/<stage>/``, as one offline page.

Each runner stage writes its visual checkpoint to ``<run_dir>/qa/<stage>/``: CSV tables at the
top of the folder and PNG figures under ``figures/``. :func:`write_index` lays them all out in
``index.html``, which links them by relative path and fetches nothing, so the folder opens
from disk, a zip or a file share. A table or figure a later change adds shows up without
touching this module; ``notes`` only puts the known ones first and captions them. ``group``
sorts figures into collapsible sections (:class:`FigureGroup`), e.g. one per county.

    from pipeline.qa_report import write_index
    write_index(run_dir / "qa" / "outputs", "Outputs QA", {"harvest.csv": "Removals by group"})
"""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pandas as pd

INDEX = "index.html"
FIGURES = "figures"
MAX_HTML_ROWS = 200                           # a longer table shows its head on the page

STYLE = """
:root { --fg:#0b0b0b; --bg:#f9f9f7; --surface:#fcfcfb; --muted:#52514e; --rule:#d9d8d3; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#f0efec; --bg:#0d0d0d; --surface:#1a1a19; --muted:#c3c2b7; --rule:#383835; } }
body { font: 14px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg);
       margin: 0 auto; max-width: 1280px; padding: 16px; }
h1 { font-size: 22px; margin-bottom: 4px; } h2 { margin-top: 32px; border-bottom: 1px solid var(--rule); }
h3 { font-size: 15px; margin: 20px 0 4px; } p { color: var(--muted); margin: 4px 0 8px; }
.scroll { overflow-x: auto; max-height: 480px; } table { border-collapse: collapse; font-size: 12px;
  background: var(--surface); }
th, td { border: 1px solid var(--rule); padding: 3px 6px; text-align: right; white-space: nowrap; }
th { background: var(--bg); position: sticky; top: 0; }
.figures { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 16px; }
figure { margin: 0; } img { width: 100%; height: auto; border: 1px solid var(--rule); background: #fcfcfb; }
figcaption { font-size: 12px; color: var(--muted); }
details { margin: 8px 0; } summary { cursor: pointer; font-weight: 600; }
"""


class FigureGroup(NamedTuple):
    """A figure's section: sections are ordered by ``key`` and titled ``heading``."""

    key: str
    heading: str


def _ordered(paths: list[Path], notes: dict[str, str]) -> list[Path]:
    known = list(notes)
    return sorted(paths, key=lambda p: (known.index(p.name) if p.name in known else len(known), p.name))


def _table_html(path: Path) -> str:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    head = frame.head(MAX_HTML_ROWS)
    rows = "".join("<tr>" + "".join(f"<td>{html.escape(v)}</td>" for v in row) + "</tr>"
                   for row in head.itertuples(index=False))
    columns = "".join(f"<th>{html.escape(c)}</th>" for c in frame.columns)
    more = (f"<p>first {MAX_HTML_ROWS} of {len(frame)} rows: open the CSV for all</p>"
            if len(frame) > MAX_HTML_ROWS else "")
    return (f'<div class="scroll"><table><thead><tr>{columns}</tr></thead>'
            f"<tbody>{rows}</tbody></table></div>{more}")


def _figure_html(png: Path, notes: dict[str, str]) -> str:
    href = html.escape(f"{FIGURES}/{png.name}")
    caption = html.escape(notes.get(png.name, png.stem.replace("_", " ")))
    return (f'<figure><a href="{href}"><img src="{href}" alt="{caption}" loading="lazy"></a>'
            f"<figcaption>{caption}</figcaption></figure>")


def _figures_html(pngs: list[Path], notes: dict[str, str],
                  group: Callable[[Path], FigureGroup] | None) -> str:
    def grid(files):
        return f'<div class="figures">{"".join(_figure_html(p, notes) for p in files)}</div>'

    if group is None:
        return grid(pngs)
    sections: dict[FigureGroup, list[Path]] = {}
    for png in pngs:
        sections.setdefault(group(png), []).append(png)
    return "".join(f'<details{" open" if i == 0 else ""}><summary>{html.escape(g.heading)}'
                   f"</summary>{grid(files)}</details>"
                   for i, (g, files) in enumerate(sorted(sections.items())))


def write_index(qa: Path, title: str, notes: dict[str, str] | None = None,
                summary: str = "", group: Callable[[Path], FigureGroup] | None = None) -> Path:
    """``qa/index.html``: every CSV in ``qa`` and every PNG in ``qa/figures``, known ones first.

    ``group`` gives each figure its section; without it the figures are one grid.
    """
    notes = notes or {}
    tables = "".join(
        f'<section><h3><a href="{html.escape(p.name)}">{html.escape(p.name)}</a></h3>'
        f"<p>{html.escape(notes.get(p.name, ''))}</p>{_table_html(p)}</section>"
        for p in _ordered(list(qa.glob("*.csv")), notes))
    pngs = _ordered(list((qa / FIGURES).glob("*.png")), notes) if (qa / FIGURES).is_dir() else []
    figures = _figures_html(pngs, notes, group)
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>"
            f"<h1>{html.escape(title)}</h1><p>{html.escape(summary or str(qa))}</p>"
            f"<h2>Figures</h2>{figures}<h2>Tables</h2>{tables}</body></html>\n")
    path = qa / INDEX
    path.write_text(page)
    return path
