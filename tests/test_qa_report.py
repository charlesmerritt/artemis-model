"""A stage's offline QA page (``pipeline.qa_report``): ``qa/<stage>/index.html``."""

import re

import pandas as pd

from pipeline import qa_report


def _png(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG")


def test_every_table_and_figure_is_on_the_page_known_ones_first_and_nothing_is_fetched(tmp_path):
    pd.DataFrame({"a": [1]}).to_csv(tmp_path / "zz_later.csv", index=False)
    pd.DataFrame({"a": [1]}).to_csv(tmp_path / "known.csv", index=False)
    _png(tmp_path / "figures" / "one.png")

    html = qa_report.write_index(tmp_path, "QA", {"known.csv": "the known table"}).read_text()

    assert html.index('href="known.csv"') < html.index('href="zz_later.csv"')
    assert "the known table" in html and 'src="figures/one.png"' in html
    assert not re.search(r"""(src|href)=["'](https?:)?//""", html)


def test_table_text_is_escaped_and_identifiers_stay_as_written(tmp_path):
    pd.DataFrame({"county_fips": ["01001"], "PLT_CN": ["1234567890123456789"],
                  "note": ["<b>&"]}).to_csv(tmp_path / "t.csv", index=False)

    html = qa_report.write_index(tmp_path, "QA").read_text()

    assert "&lt;b&gt;&amp;" in html and "<b>&" not in html
    assert "<td>01001</td>" in html and "<td>1234567890123456789</td>" in html


def test_a_long_table_shows_its_head_and_says_how_many_rows_it_has(tmp_path):
    pd.DataFrame({"n": range(qa_report.MAX_HTML_ROWS + 5)}).to_csv(tmp_path / "long.csv", index=False)

    html = qa_report.write_index(tmp_path, "QA").read_text()

    assert f"first {qa_report.MAX_HTML_ROWS} of {qa_report.MAX_HTML_ROWS + 5} rows" in html
    assert f"<td>{qa_report.MAX_HTML_ROWS + 4}</td>" not in html


def test_grouped_figures_fall_into_sections_in_key_order_the_first_one_open(tmp_path):
    for name in ("b_x.png", "a_x.png", "a_y.png"):
        _png(tmp_path / "figures" / name)

    def group(png):
        scope = png.stem.split("_")[0]
        return qa_report.FigureGroup(key=scope, heading=f"Scope {scope}")

    html = qa_report.write_index(tmp_path, "QA", group=group).read_text()

    sections = re.findall(r"<details( open)?><summary>(.*?)</summary>", html)
    assert sections == [(" open", "Scope a"), ("", "Scope b")]
    assert html.index('src="figures/a_y.png"') < html.index("Scope b")


def test_the_summary_replaces_the_folder_path(tmp_path):
    html = qa_report.write_index(tmp_path, "QA", summary="Union County").read_text()

    assert "Union County" in html and str(tmp_path) not in html
