"""LANDFIRE annual disturbance as a cut-year detector (#171, ADR 0003 amendment 2026-11-09)."""

import os
from pathlib import Path

import pandas as pd
import pytest
from rasterio.transform import Affine

from pipeline.s1_initial_state.county_improvement import Grid
from pipeline.s1_initial_state.landfire_disturbance import (
    DisturbanceProduct,
    find_products,
    harvest_years,
    landfire_cut_year,
)

GRID = Grid(Affine(30, 0, 0, 0, -30, 30), (1, 4))
WINDOW = (2001, 2022)


def _legend(rows, year_column="DIST_YEAR"):
    """Legend rows as (VALUE, year, DIST_TYPE)."""
    return pd.DataFrame(rows, columns=["VALUE", year_column, "DIST_TYPE"])


def _calendar_legend(rows):
    """An LF2024+ legend: (VALUE, FISCAL_YR, CALENDAR_YR, DIST_TYPE)."""
    return pd.DataFrame(rows, columns=["VALUE", "FISCAL_YR", "CALENDAR_YR", "DIST_TYPE"])


def test_products_are_found_by_folder_and_dist99_is_1999(landfire_dist):
    legend = _legend([(0, 1999, None)], "YEAR")
    landfire_dist("2001", "99", [[0]], legend)
    root = landfire_dist("2016", "15", [[0]], legend)
    products = find_products(root)
    assert [(p.name, p.year) for p in products] == [("LF2001_Dist99", 1999), ("LF2016_Dist15", 2015)]
    assert products[1].tif == root / "LF2016_Dist15_CONUS/LF2016_Dist15_CONUS/Tif/LF2016_Dist15_CONUS.tif"


def test_harvest_years_keep_clearcut_and_harvest_codes_dated_by_the_products_year(landfire_dist):
    # LF2008's legend labels most codes 2009, but Dist08 is the 2008 product: its year dates them.
    root = landfire_dist("2008", "08", [[0]], _legend([
        (-9999, 2008, "Fill-NoData"), (0, 2008, None), (421, 2009, "Clearcut"),
        (431, 2009, "Harvest"), (441, 2009, "Thinning"), (11, 2009, "Wildfire"),
        (920, 2008, "Clearcut")]))
    years = harvest_years(find_products(root)[0])
    assert years.to_dict() == {421: 2008, 431: 2008, 920: 2008}


def test_harvest_years_read_the_legacy_and_the_calendar_year_legends(landfire_dist):
    landfire_dist("2001", "05", [[0]], _legend([(421, 2005, "Clearcut"), (441, 2005, "Thinning")], "YEAR"))
    # LF2024+ has a BOM, and one product holds two calendar years: the legend row's wins.
    root = landfire_dist("2025", "25", [[0]], _calendar_legend([
        (2410, 2025, 2025, "Clearcut"), (2414, 2025, 2024, "Clearcut"),
        (2424, 2025, 2024, "Harvest"), (2510, 2025, 2025, "Mechanical Remove")]), encoding="utf-8-sig")
    old, new = find_products(root)
    assert harvest_years(old).to_dict() == {421: 2005}
    assert harvest_years(new).to_dict() == {2410: 2025, 2414: 2024, 2424: 2024}


def test_a_repeated_legend_row_counts_once_but_a_code_with_two_meanings_is_an_error(landfire_dist):
    # LF2001_Dist99 repeats some rows word for word.
    landfire_dist("2001", "99", [[0]], _legend([(421, 1999, "Clearcut"), (421, 1999, "Clearcut"),
                                                (1011, 1999, "Weather"), (1011, 1999, "Weather")], "YEAR"))
    root = landfire_dist("2001", "00", [[0]], _legend([(421, 2000, "Clearcut"), (421, 2000, "Thinning")], "YEAR"))
    repeated, ambiguous = find_products(root)
    assert harvest_years(repeated).to_dict() == {421: 1999}
    with pytest.raises(ValueError, match="LF2001_Dist00: code 421"):
        harvest_years(ambiguous)


def test_the_latest_harvest_year_in_the_window_dates_each_pixel_on_the_grid(landfire_dist):
    # 2012 clearcuts pixels 0 and 1; 2016 harvests pixel 1 again, thins pixel 2, and its
    # nodata covers pixel 3. A 2023 clearcut is past the window, so pixel 0 keeps 2012.
    landfire_dist("2012", "12", [[421, 421, 0, 0]], _legend([(421, 2012, "Clearcut")]))
    landfire_dist("2016", "16", [[0, 431, 441, 32767]],
                  _legend([(431, 2016, "Harvest"), (441, 2016, "Thinning")]))
    root = landfire_dist("2023", "23", [[421, 0, 0, 0]], _legend([(421, 2023, "Clearcut")]))
    assert landfire_cut_year(root, GRID, window=WINDOW).tolist() == [[2012, 2016, 0, 0]]


def test_each_product_is_read_onto_the_grid_whatever_its_own_extent(landfire_dist):
    # LF2022/LF2023 sit one pixel east and one narrower; LF2016 is smaller. Each aligns.
    landfire_dist("2022", "21", [[421, 0, 421]], _legend([(421, 2021, "Clearcut")]),
                  transform=Affine(30, 0, 30, 0, -30, 30), nodata=-32768)
    root = landfire_dist("2016", "15", [[431]], _legend([(431, 2015, "Harvest")]))
    assert landfire_cut_year(root, GRID, window=WINDOW).tolist() == [[2015, 2021, 0, 2021]]


def test_a_root_with_no_products_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="no LANDFIRE disturbance products"):
        landfire_cut_year(tmp_path, GRID, window=WINDOW)


# The stand-replacing codes of each product, from its own legend (staged 2026-11-09). A
# change here means LANDFIRE re-issued a legend: re-read it before re-pinning.
_LEGACY = [421, 422, 423, 431, 432, 433, 721, 722, 723, 731, 732, 733]
_SHORT = _LEGACY + [921, 931]
_MID = _LEGACY + [920, 921, 922, 923, 930, 931, 932, 933]
_LONG = _LEGACY + list(range(920, 928)) + list(range(930, 938))
_LONG8 = _LEGACY + list(range(920, 929)) + list(range(930, 939))
_CALENDAR = list(range(2410, 2418)) + list(range(2420, 2428))
PINNED_CODES = {
    **{f"LF2001_Dist{yy}": _SHORT for yy in ("99", "00", "01", "02", "03", "04", "05", "06", "07")},
    "LF2008_Dist08": _MID, "LF2010_Dist09": _MID, "LF2010_Dist10": _MID,
    "LF2012_Dist11": _SHORT, "LF2012_Dist12": sorted(_SHORT + [927]),
    "LF2014_Dist13": _LONG, "LF2014_Dist14": _LONG, "LF2016_Dist15": _LONG8,
    "LF2016_Dist16": _LONG,
    **{f"LF{v}_Dist{yy}": _LONG8 for v, yy in (("2020", "17"), ("2020", "18"), ("2020", "19"),
                                               ("2020", "20"), ("2022", "21"), ("2022", "22"),
                                               ("2023", "23"))},
    "LF2024_Dist24": _CALENDAR, "LF2025_Dist25": _CALENDAR,
}
# The legend's own YEAR / DIST_YEAR, where it disagrees with the product's year: LF2008's
# legend labels these codes 2009. The product year dates them (Dist09 is its own product).
LEGEND_YEAR_DISAGREES = {"LF2008_Dist08": set(_LEGACY)}


def _staged_product(data_access, name: str) -> DisturbanceProduct:
    """The product's legend, local or fetched from R2 (it alone: a few kB), as a product."""
    root = os.environ.get("ARTEMIS_LANDFIRE_DIST") or data_access.data_paths()["raw"]["landfire"]["dist_dir"]
    product = DisturbanceProduct.at(Path(root) / f"{name}_CONUS" / f"{name}_CONUS")
    assert product is not None
    local = (product.legend_csv if product.legend_csv.exists()
             else data_access.ensure_local(product.legend_csv))
    if local is None:
        pytest.skip(data_access.unavailable_reason(product.legend_csv)
                    + " (or set ARTEMIS_LANDFIRE_DIST to a local copy)")
    return DisturbanceProduct.at(Path(local).parent.parent)


@pytest.mark.production_data
@pytest.mark.parametrize("name", sorted(PINNED_CODES))
def test_the_staged_legends_give_the_pinned_stand_replacing_codes(data_access, name):
    product = _staged_product(data_access, name)
    dated = harvest_years(product)
    assert sorted(dated.index) == PINNED_CODES[name]
    if product.version >= 2024:                  # a fiscal year: two calendar years
        assert set(dated) <= {product.year - 1, product.year}
        return
    assert set(dated) == {product.year}
    legend = product.legend.set_index("VALUE")
    own_year = legend.loc[dated.index, "YEAR" if "YEAR" in legend.columns else "DIST_YEAR"]
    assert set(own_year.index[own_year != product.year]) == LEGEND_YEAR_DISAGREES.get(name, set())
