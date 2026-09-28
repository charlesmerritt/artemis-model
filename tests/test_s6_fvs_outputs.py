"""Tests for the canonical FVS output stage (pipeline/s6_outputs/)."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.ids import IdPrecisionError
from pipeline.s6_outputs import age_class, fvs_out_db, owner_attribution, run_pipeline

pd = pytest.importorskip("pandas")
pytest.importorskip("matplotlib")

CONFIG = fvs_out_db.load_output_config()

# A 19-digit control number: past 2**53, so any trip through a float damages it.
WIDE_CN = "9876543210987654321"


# ---- fixtures: a miniature FVSOut.db and crosswalk ---------------------------------------

def _write_fvs_out(path: Path, rows, cases=None, *, summary_table="FVS_Summary2") -> Path:
    """Build an FVS Online-shaped output database from (StandID, Year, Age, ForTyp) rows."""
    cases = cases or {}
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE FVS_Cases (CaseID TEXT, Stand_CN TEXT, StandID TEXT, MgmtID TEXT, "
        "RunTitle TEXT, KeywordFile TEXT, SamplingWt REAL, Variant TEXT, Version TEXT, "
        "RV TEXT, Groups TEXT, RunDateTime TEXT)"
    )
    conn.execute(
        f"CREATE TABLE {summary_table} (CaseID TEXT, StandID TEXT, Year INT, RmvCode INT, "
        "Age INT, Tpa REAL, BA REAL, SDI INT, QMD REAL, TopHt INT, MCuFt REAL, "
        "ForTyp INT, SizeCls INT, StkCls INT)"
    )
    stands = sorted({r[0] for r in rows})
    for i, stand in enumerate(stands):
        weight, cn = cases.get(stand, (100.0, f"{4000000000000 + i}"))
        conn.execute(
            "INSERT INTO FVS_Cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"case-{stand}", cn, stand, "A001", "TestRun", "k", weight, "SN",
             "FS2026.1", "20260401", "All_FIA_Plots", "2026-01-01"),
        )
    for stand, year, age, fortyp, *rest in rows:
        rmv = rest[0] if rest else 0
        conn.execute(
            f"INSERT INTO {summary_table} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"case-{stand}", stand, year, rmv, age, 200.0, 90.0, 150, 8.0, 60, 1000.0,
             fortyp, 2, 2),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def fvs_out(tmp_path):
    """Two pine stands and one hardwood stand, aligned on 2026-2036."""
    rows = [
        ("120010100001", 2021, 20, 141),          # unbalanced: only this stand reports
        ("120010100001", 2026, 25, 141),
        ("120010100001", 2031, 30, 141),
        ("120010100001", 2036, 35, 141),
        ("120010300002", 2026, 75, 161),
        ("120010300002", 2031, 80, 161),
        ("120010300002", 2036, 85, 161),
        ("130010500003", 2026, 145, 602),         # hardwood, already in the open top class
        ("130010500003", 2031, 150, 602),
        ("130010500003", 2036, 155, 602),
    ]
    cases = {
        "120010100001": (1000.0, "1001"),
        "120010300002": (500.0, "1002"),
        "130010500003": (250.0, "1003"),
    }
    return _write_fvs_out(tmp_path / "FVSOut.db", rows, cases)


@pytest.fixture
def crosswalk(tmp_path):
    """Plot 1001 split across two owners; 1002 wholly private; 1003 absent."""
    path = tmp_path / "FVS_StandInit.csv"
    pd.DataFrame({
        "STAND_ID": ["MU_1", "MU_2", "MU_3"],
        "PLT_CN": ["1001", "1001", "1002"],
        "OWN_TYPE": ["Private", "Corporate", "Private"],
        "MGMT_TYPE": ["Upland", "Upland", "Riparian"],
        "Acres": [75.0, 25.0, 40.0],
    }).to_csv(path, index=False)
    return path


# ---- reading the output database ---------------------------------------------------------

def test_load_stand_years_joins_cases_and_decodes_the_stand_id(fvs_out):
    stand_years = fvs_out_db.load_stand_years(fvs_out)

    assert len(stand_years) == 10
    first = stand_years[stand_years["StandID"] == "120010100001"].iloc[0]
    assert first["state_fips"] == "12"
    assert first["county_fips"] == "12101"     # STATECD 12 + COUNTYCD 101
    assert first["plot_number"] == "00001"
    assert first["inventory_year"] == 2000     # the '00' in position 3-4
    assert first["SamplingWt"] == 1000.0


def test_a_missing_summary_table_names_the_table_rather_than_raising_sqlite_noise(tmp_path):
    path = _write_fvs_out(tmp_path / "bare.db", [("120010100001", 2026, 20, 141)],
                          summary_table="FVS_Summary")
    with pytest.raises(fvs_out_db.FvsOutputError, match="FVS_Summary2"):
        fvs_out_db.load_stand_years(path)


def test_pre_removal_rows_do_not_add_acres_to_the_final_state(tmp_path):
    path = _write_fvs_out(tmp_path / "thin.db", [
        ("120010100001", 2026, 20, 141, 0),
        ("120010100001", 2026, 20, 141, 1),      # pre-removal state
    ])
    stand_years = fvs_out_db.load_stand_years(path)
    assert len(stand_years) == 1
    assert (stand_years["RmvCode"] == 0).all()


def test_a_case_with_no_sampling_weight_is_dropped_rather_than_counted_as_an_acre(tmp_path):
    path = _write_fvs_out(tmp_path / "zero.db",
                          [("120010100001", 2026, 20, 141),
                           ("120010300002", 2026, 30, 161)],
                          cases={"120010300002": (0.0, "1002")})
    stand_years = fvs_out_db.load_stand_years(path)
    assert stand_years["StandID"].tolist() == ["120010100001"]


def test_a_wide_control_number_survives_the_read_intact(tmp_path):
    path = _write_fvs_out(tmp_path / "wide.db", [("120010100001", 2026, 20, 141)],
                          cases={"120010100001": (10.0, WIDE_CN)})
    stand_years = fvs_out_db.load_stand_years(path)
    assert stand_years["Stand_CN"].iloc[0] == WIDE_CN


def test_a_control_number_that_already_lost_digits_raises_instead_of_joining_wrongly(tmp_path):
    """A REAL-typed Stand_CN is the failure mode `pipeline/ids.py` exists to catch."""
    path = tmp_path / "float_cn.db"
    _write_fvs_out(path, [("120010100001", 2026, 20, 141)])
    conn = sqlite3.connect(path)
    conn.execute("UPDATE FVS_Cases SET Stand_CN = 9.876543210987654e+18")
    conn.commit()
    conn.close()
    with pytest.raises(IdPrecisionError):
        fvs_out_db.load_stand_years(path)


# ---- the reporting year grid --------------------------------------------------------------

def test_the_reporting_grid_excludes_years_not_every_case_reports(fvs_out):
    stand_years = fvs_out_db.load_stand_years(fvs_out)
    assert fvs_out_db.balanced_years(stand_years) == [2026, 2031, 2036]
    assert 2021 not in fvs_out_db.reporting_years(stand_years)


def test_a_run_whose_cycles_never_line_up_is_an_error_not_a_thin_report(tmp_path):
    path = _write_fvs_out(tmp_path / "ragged.db", [
        ("120010100001", 2026, 20, 141),
        ("120010300002", 2031, 30, 161),
    ])
    stand_years = fvs_out_db.load_stand_years(path)
    with pytest.raises(fvs_out_db.FvsOutputError, match="covered by all"):
        fvs_out_db.reporting_years(stand_years)


def test_year_grid_mode_all_keeps_the_unbalanced_years(fvs_out):
    stand_years = fvs_out_db.load_stand_years(fvs_out)
    config = {**CONFIG, "year_grid": {**CONFIG["year_grid"], "mode": "all"}}
    assert 2021 in fvs_out_db.reporting_years(stand_years, config=config)


# ---- age classes ---------------------------------------------------------------------------

@pytest.mark.parametrize("age,label", [
    (0, "unknown"),          # FVS writes 0 where FIA carried no condition age
    (1, "0-9"),
    (9, "0-9"),
    (10, "10-19"),
    (139, "130-139"),
    (140, "140+"),
    (238, "140+"),           # the open top class, not a class of its own
])
def test_ages_bin_into_the_configured_classes(age, label):
    binned = age_class.assign_age_class([age])
    assert binned["age_class_label"].iloc[0] == label


def test_age_zero_can_be_counted_as_the_youngest_class_when_the_config_says_so():
    config = {**CONFIG,
              "age_classes": {**CONFIG["age_classes"], "unknown_age_is_class_zero": True}}
    assert age_class.assign_age_class([0], config=config)["age_class_label"].iloc[0] == "0-9"


def test_a_negative_age_raises_rather_than_binning_into_a_nonsense_class():
    with pytest.raises(ValueError, match="negative stand age"):
        age_class.assign_age_class([-5])


def test_a_top_class_that_is_not_a_multiple_of_the_width_is_refused():
    config = {**CONFIG, "age_classes": {**CONFIG["age_classes"], "width": 10, "max": 145}}
    with pytest.raises(ValueError, match="multiple of"):
        age_class.age_class_bounds(config)


def test_unknown_ages_sort_after_every_real_class():
    binned = age_class.assign_age_class([0, 140, 5])
    real = binned[binned["age_class_label"] != "unknown"]["age_class_sort"]
    assert binned["age_class_sort"].iloc[0] > real.max()
    assert binned.sort_values("age_class_sort")["age_class_label"].tolist() == [
        "0-9", "140+", "unknown"]


# ---- the canonical tables -------------------------------------------------------------------

def test_forest_type_splits_pine_from_hardwood_and_carries_the_fia_detail(fvs_out):
    attributed = age_class.attribute(fvs_out_db.load_stand_years(fvs_out))
    labels = dict(zip(attributed["ForTyp"], attributed["forest_type_label"]))
    assert labels[141] == "Pine (softwood)"        # longleaf
    assert labels[161] == "Pine (softwood)"        # loblolly
    assert labels[602] == "Hardwood"               # sweetgum/Nuttall oak/willow oak
    detail = dict(zip(attributed["ForTyp"], attributed["fia_forest_type_label"]))
    assert detail[141] == "Longleaf/slash pine"
    assert detail[602] == "Oak/gum/cypress"


def test_every_table_is_acre_weighted_and_foots_to_the_run_total(fvs_out):
    stand_years = fvs_out_db.load_stand_years(fvs_out)
    years = fvs_out_db.reporting_years(stand_years)
    attributed = age_class.attribute(stand_years)
    total = 1000.0 + 500.0 + 250.0

    for table in (age_class.overall(attributed, years),
                  age_class.by_forest_type(attributed, years),
                  age_class.by_state(attributed, years),
                  age_class.by_county(attributed, years)):
        per_year = table.groupby("Year")["acres"].sum()
        assert per_year.round(6).eq(total).all()
        assert (table["weight_basis"] == age_class.SAMPLING_WEIGHT).all()


def test_shares_are_computed_within_a_cut_not_across_the_run(fvs_out):
    stand_years = fvs_out_db.load_stand_years(fvs_out)
    years = fvs_out_db.reporting_years(stand_years)
    table = age_class.by_forest_type(age_class.attribute(stand_years), years)
    within = table.groupby(["Year", "forest_type_label"])["share"].sum()
    assert within.round(6).eq(1.0).all()


def test_the_distribution_refuses_a_year_the_run_does_not_cover(fvs_out):
    attributed = age_class.attribute(fvs_out_db.load_stand_years(fvs_out))
    with pytest.raises(ValueError, match="reporting grid"):
        age_class.by_forest_type(attributed, [2099])


def test_mean_age_is_weighted_by_acres_not_by_stand_count(fvs_out):
    stand_years = fvs_out_db.load_stand_years(fvs_out)
    attributed = age_class.attribute(stand_years)
    mean = age_class.mean_age_trajectory(attributed, [2026])
    # 1000 ac at 25, 500 ac at 75, 250 ac at 145 -> 52.857...; the unweighted mean is 81.7
    assert mean["mean_age"].iloc[0] == pytest.approx((1000 * 25 + 500 * 75 + 250 * 145) / 1750)


# ---- owner attribution ------------------------------------------------------------------------

def test_owner_shares_sum_to_one_within_a_plot(crosswalk):
    shares = owner_attribution.load_owner_shares(crosswalk)
    assert shares.groupby("Stand_CN")["owner_share"].sum().round(6).eq(1.0).all()
    plot = shares[shares["Stand_CN"] == "1001"].set_index("owner_class")
    assert plot.loc["Private", "owner_share"] == pytest.approx(0.75)


def test_a_plot_spanning_two_owners_is_apportioned_not_assigned_to_the_largest(fvs_out,
                                                                               crosswalk):
    stand_years = fvs_out_db.load_stand_years(fvs_out)
    owned = owner_attribution.apportion(age_class.attribute(stand_years),
                                        owner_attribution.load_owner_shares(crosswalk))
    split = owned[(owned["Stand_CN"] == "1001") & (owned["Year"] == 2026)]
    assert set(split["owner_class"]) == {"Private", "Corporate"}
    assert split["owner_acres"].sum() == pytest.approx(100.0)


def test_plots_outside_the_crosswalk_are_reported_unattributed_not_dropped(fvs_out,
                                                                          crosswalk):
    stand_years = fvs_out_db.load_stand_years(fvs_out)
    owned = owner_attribution.apportion(age_class.attribute(stand_years),
                                        owner_attribution.load_owner_shares(crosswalk))
    missed = owned[owned["Stand_CN"] == "1003"]
    assert set(missed["owner_class"]) == {CONFIG["owner_classes"]["unattributed_label"]}
    assert owned["Stand_CN"].nunique() == stand_years["Stand_CN"].nunique()
    assert owner_attribution.coverage(owned)["cases_attributed"] == 2


def test_owner_tables_carry_the_owner_acre_basis_not_the_sampling_weight(fvs_out, crosswalk):
    stand_years = fvs_out_db.load_stand_years(fvs_out)
    years = fvs_out_db.reporting_years(stand_years)
    owned = owner_attribution.apportion(age_class.attribute(stand_years),
                                        owner_attribution.load_owner_shares(crosswalk))
    table = age_class.by_owner(owned, years)
    assert (table["weight_basis"] == age_class.OWNER_ACRES).all()
    # 100 crosswalked acres on plot 1001 plus 40 on 1002 — not the 1,750 sampling acres.
    assert table.groupby("Year")["acres"].sum().round(6).eq(140.0).all()


def test_a_crosswalk_without_the_owner_columns_says_so(tmp_path):
    path = tmp_path / "wrong.csv"
    pd.DataFrame({"PLT_CN": ["1"], "Acres": [1.0]}).to_csv(path, index=False)
    with pytest.raises((owner_attribution.OwnerCrosswalkError, ValueError)):
        owner_attribution.load_owner_shares(path)


# ---- the pipeline end to end ---------------------------------------------------------------------

def test_the_pipeline_runs_every_stage_and_the_last_one_only_draws(fvs_out, crosswalk,
                                                                   tmp_path):
    out_dir = tmp_path / "out"
    ctx = run_pipeline.run(fvs_out=str(fvs_out), crosswalk=str(crosswalk), out_dir=out_dir)

    tables = sorted(p.name for p in (out_dir / "tables").glob("*.csv"))
    assert "age_class_by_forest_type.csv" in tables
    assert "age_class_by_owner.csv" in tables
    figures = sorted(p.name for p in (out_dir / "figures").glob("*.png"))
    assert "age_class_by_forest_type.png" in figures
    assert "age_class_by_owner.png" in figures

    summary = json.loads((out_dir / "run_summary.json").read_text())
    assert summary["reporting_years"] == [2026, 2031, 2036]
    assert summary["owner_coverage"]["cases_attributed"] == 2
    assert ctx.years == [2026, 2031, 2036]


def test_the_visualize_stage_redraws_from_tables_alone(fvs_out, crosswalk, tmp_path):
    """Stage 5 holds no analysis: it reads stage 4's CSVs and nothing else."""
    out_dir = tmp_path / "out"
    run_pipeline.run(fvs_out=str(fvs_out), crosswalk=str(crosswalk), out_dir=out_dir)
    for figure in (out_dir / "figures").glob("*.png"):
        figure.unlink()

    run_pipeline.run(["visualize"], out_dir=out_dir)
    assert list((out_dir / "figures").glob("*.png"))


def test_visualize_without_tables_says_to_run_summarize_first(tmp_path):
    with pytest.raises(run_pipeline.StageError, match="summarize"):
        run_pipeline.run(["visualize"], out_dir=tmp_path / "empty")


def test_an_unreachable_crosswalk_costs_the_owner_tables_and_nothing_else(fvs_out, tmp_path):
    out_dir = tmp_path / "out"
    ctx = run_pipeline.run(fvs_out=str(fvs_out), crosswalk="", out_dir=out_dir)

    assert ctx.owned is None
    assert "age_class_by_forest_type" in ctx.tables
    assert "age_class_by_owner" not in ctx.tables
    assert (out_dir / "figures" / "age_class_by_forest_type.png").exists()
    assert not (out_dir / "figures" / "age_class_by_owner.png").exists()


def test_an_unknown_stage_name_is_refused(fvs_out, tmp_path):
    with pytest.raises(run_pipeline.StageError, match="unknown stage"):
        run_pipeline.run(["vizualise"], out_dir=tmp_path)


# ---- against the real run ------------------------------------------------------------------------

def test_the_declared_five_county_run_reports_a_balanced_grid(data_access):
    """The real no-management FVS output, wherever the drive or the R2 mirror answers."""
    declared = run_pipeline._declared(run_pipeline.FVS_OUT_KEY)
    local = Path(declared) if Path(declared).exists() else data_access.ensure_local(declared)
    if local is None:
        pytest.skip(f"FVS output database unavailable: {declared}")

    stand_years = fvs_out_db.load_stand_years(local)
    years = fvs_out_db.reporting_years(stand_years)
    assert len(years) >= CONFIG["year_grid"]["min_years"]

    table = age_class.by_forest_type(age_class.attribute(stand_years), years)
    assert {"Pine (softwood)", "Hardwood"} <= set(table["forest_type_label"])
    # Every cut of the same run expands to the same acreage.
    identity = fvs_out_db.run_identity(stand_years)
    assert table[table["Year"] == years[0]]["acres"].sum() == pytest.approx(
        identity["sampling_weight_acres"])


# Review regressions

def test_managed_cycles_keep_post_removal_age_and_acreage(tmp_path):
    """A harvest year remains balanced and contributes its final state exactly once."""
    path = _write_fvs_out(tmp_path / "managed.db", [
        ("120010100001", 2026, 50, 141),
        ("120010100001", 2031, 55, 141, 1),
        ("120010100001", 2031, 1, 141, 2),
        ("120010100001", 2036, 6, 141),
        ("120010300002", 2026, 30, 161),
        ("120010300002", 2031, 35, 161),
        ("120010300002", 2036, 40, 161),
    ])
    rows = fvs_out_db.load_stand_years(path)
    years = fvs_out_db.reporting_years(rows)
    assert years == [2026, 2031, 2036]
    harvested = rows[(rows["Year"] == 2031) & (rows["StandID"] == "120010100001")]
    assert harvested["Age"].tolist() == [1]
    table = age_class.overall(age_class.attribute(rows), years)
    assert table.groupby("Year")["acres"].sum().eq(200).all()


@pytest.mark.parametrize("rows", [
    [("c", 2031, 1)],
    [("c", 2031, 0), ("c", 2031, 0)],
])
def test_incomplete_or_duplicate_cycle_states_are_rejected(rows):
    """Malformed cycles cannot silently remove stands or add acreage."""
    summary = pd.DataFrame(rows, columns=["CaseID", "Year", "RmvCode"])
    with pytest.raises(fvs_out_db.FvsOutputError):
        fvs_out_db.select_cycle_states(summary)


def test_post_removal_state_supersedes_an_unmanaged_row():
    """Even an extra code 0 does not double-count a managed cycle."""
    summary = pd.DataFrame({"CaseID": ["c"] * 3, "Year": [2031] * 3,
                            "RmvCode": [0, 1, 2], "Age": [50, 50, 1]})
    assert fvs_out_db.select_cycle_states(summary)["Age"].tolist() == [1]


def test_alternative_cases_are_rejected_at_extraction(fvs_out):
    """A second prescription for a stand cannot become additional landscape acres."""
    with sqlite3.connect(fvs_out) as conn:
        conn.execute("INSERT INTO FVS_Cases SELECT 'alternative', Stand_CN, StandID, "
                     "'B002', RunTitle, KeywordFile, SamplingWt, Variant, Version, RV, "
                     "Groups, RunDateTime FROM FVS_Cases LIMIT 1")
    with pytest.raises(fvs_out_db.FvsOutputError, match="one case per stand"):
        fvs_out_db.load_stand_years(fvs_out)


@pytest.mark.parametrize("summarize", [age_class.overall, age_class.mean_age_trajectory])
def test_direct_aggregation_rejects_alternatives_on_disjoint_grids(fvs_out, summarize):
    """Public table helpers enforce scenario semantics even without extraction."""
    attributed = age_class.attribute(fvs_out_db.load_stand_years(fvs_out))
    alternative = attributed.iloc[[0]].copy()
    alternative["CaseID"] = "alternative"
    alternative["Year"] = 2099
    with pytest.raises(fvs_out_db.FvsOutputError, match="multiple FVS cases"):
        summarize(pd.concat([attributed, alternative]), [2026])


@pytest.mark.parametrize("stages", [["visualize"], ["extract"], ["summarize"],
                                   ["attribute", "visualize"], list(run_pipeline.STAGES)])
def test_dry_run_never_executes_stages_or_changes_files(tmp_path, monkeypatch, stages):
    """Every stage subset honors the CLI promise to write nothing."""
    marker = tmp_path / "figures" / "age_class_by_owner.png"
    marker.parent.mkdir()
    marker.write_bytes(b"previous figure")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def unexpected(*args, **kwargs):
        """Fail if a dry run attempts to execute a processing stage or fetch data."""
        pytest.fail("dry run executed a processing stage")

    for name in ["stage_extract", "stage_attribute", "stage_summarize", "stage_visualize"]:
        monkeypatch.setattr(run_pipeline, name, unexpected)
    monkeypatch.setattr(run_pipeline.data_access, "ensure_local", unexpected)
    monkeypatch.setattr(run_pipeline.data_access, "exists", lambda path: False)
    run_pipeline.run(stages, out_dir=tmp_path, dry_run=True)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_oversized_optional_crosswalk_degrades_to_ownerless_reporting(fvs_out, tmp_path,
                                                                    monkeypatch, caplog):
    """The crosswalk fetch cap costs only owner reporting, with a visible warning."""
    def oversized(path, *, max_fetch_mb=None):
        """Simulate the optional crosswalk exceeding the configured fetch cap."""
        assert max_fetch_mb == run_pipeline.CROSSWALK_MAX_FETCH_MB
        raise run_pipeline.data_access.RemoteFetchTooLarge("70 MB exceeds 64 MB cap")

    monkeypatch.setattr(run_pipeline.data_access, "ensure_local", oversized)
    ctx = run_pipeline.run(["resolve", "extract", "attribute", "summarize"],
                           fvs_out=str(fvs_out), crosswalk=str(tmp_path / "remote.csv"),
                           out_dir=tmp_path / "out")
    assert ctx.summary["owner_crosswalk"] is None
    assert "age_class_by_owner" not in ctx.tables
    assert "70 MB exceeds" in caplog.text


def test_ownerless_rerun_removes_stale_owned_outputs(fvs_out, crosswalk, tmp_path):
    """Summarize invalidates old figures and removes owner CSVs before a redraw."""
    out = tmp_path / "out"
    run_pipeline.run(fvs_out=str(fvs_out), crosswalk=str(crosswalk), out_dir=out)
    custom = out / "tables" / "user_notes.csv"
    custom.write_text("note\nkeep me\n")
    run_pipeline.run(["resolve", "extract", "attribute", "summarize"],
                     fvs_out=str(fvs_out), crosswalk="", out_dir=out)
    assert not (out / "tables" / "age_class_by_owner.csv").exists()
    assert not list((out / "figures").glob("*.png"))
    run_pipeline.run(["visualize"], out_dir=out)
    assert not (out / "figures" / "age_class_by_owner.png").exists()
    assert (out / "figures" / "age_class_by_state.png").exists()
    assert custom.read_text() == "note\nkeep me\n"


@pytest.fixture
def capture_figure(monkeypatch):
    """Capture the rendered Matplotlib figure so tests inspect its actual encodings."""
    from pipeline.s6_outputs import figures
    captured = []

    def capture(fig, out_dir, name, config):
        """Retain the figure for assertions without writing a raster image."""
        captured.append(fig)
        return out_dir / f"{name}.png"

    monkeypatch.setattr(figures, "_save", capture)
    yield captured
    for fig in captured:
        figures.plt.close(fig)


@pytest.mark.parametrize("peaks,unit,tick", [
    ([50, 5000], "Acres", "5,000"),
    ([500000, 50], "Thousand acres", "5"),
    ([50, 500000], "Thousand acres", "5"),
])
def test_snapshot_axes_share_one_formatter_and_correct_unit(tmp_path, capture_figure,
                                                           peaks, unit, tick):
    """Snapshots crossing the unit threshold still label the same tick consistently."""
    from pipeline.s6_outputs import figures
    table = pd.DataFrame({"Year": [2026, 2076], "forest_type_label": ["Hardwood"] * 2,
                          "age_class_label": ["10-19"] * 2, "age_class_sort": [10] * 2,
                          "acres": peaks, "weight_basis": ["sampling_weight"] * 2})
    figures.age_class_by_forest_type(table, tmp_path, [2026, 2076])
    axes = capture_figure[0].axes
    assert axes[0].get_ylabel() == unit
    assert all(ax.yaxis.get_major_formatter()(5000, None) == tick for ax in axes)


def test_time_axes_use_the_combined_stacked_peak(tmp_path, capture_figure):
    """A small last forest-type panel cannot change the shared axis to raw acres."""
    from pipeline.s6_outputs import figures
    table = pd.DataFrame({"Year": [2026] * 3, "forest_type_label":
                          ["Pine (softwood)", "Pine (softwood)", "Hardwood"],
                          "age_class_label": ["10-19", "20-29", "10-19"],
                          "age_class_sort": [10, 20, 10], "acres": [6000, 6000, 50],
                          "weight_basis": ["sampling_weight"] * 3})
    figures.age_class_over_time(table, tmp_path)
    axes = capture_figure[0].axes
    assert axes[0].get_ylabel() == "Thousand acres"
    assert all(ax.yaxis.get_major_formatter()(12000, None) == "12" for ax in axes)


@pytest.mark.parametrize("unknown_only", [False, True])
def test_unknown_types_and_custom_labels_appear_in_all_charts(fvs_out, tmp_path,
                                                            capture_figure, unknown_only):
    """Figures conserve unknown acreage and use configured labels for palette lookup."""
    from matplotlib.colors import to_rgba
    from pipeline.s6_outputs import figures
    config = {**CONFIG, "forest_type_labels": {**CONFIG["forest_type_labels"],
                                              "pine": "Conifers"}}
    rows = fvs_out_db.load_stand_years(fvs_out)
    rows.loc[rows["StandID"] == "130010500003", "ForTyp"] = None
    if unknown_only:
        rows["ForTyp"] = None
    table = age_class.by_forest_type(age_class.attribute(rows, config=config), [2026])
    figures.age_class_by_forest_type(table, tmp_path, [2026], config=config)
    snapshot = capture_figure[-1]
    assert sum(p.get_height() for p in snapshot.axes[0].patches) == pytest.approx(1750)
    legend = snapshot.axes[0].get_legend()
    assert "Unknown" in [t.get_text() for t in legend.get_texts()]
    if not unknown_only:
        assert snapshot.axes[0].patches[0].get_facecolor() == to_rgba(
            CONFIG["figures"]["palette"]["pine"])
    figures.age_class_over_time(table, tmp_path, config=config)
    assert "Unknown" in [ax.get_title() for ax in capture_figure[-1].axes]
    attributed = age_class.attribute(rows, config=config)
    figures.age_class_by_area(age_class.by_state(attributed, [2026]), "state", tmp_path,
                              2026, name="state", title="State", config=config)
    assert sum(p.get_height() for ax in capture_figure[-1].axes for p in ax.patches) == 1750


def test_county_remainder_is_pooled_using_only_the_selected_year(tmp_path, capture_figure):
    """The pooled facet preserves all acreage and aggregates overlapping age/type rows."""
    from pipeline.s6_outputs import figures
    table = pd.DataFrame({"Year": [2026] * 3 + [2076] * 3,
                          "county": ["A", "B", "C"] * 2,
                          "forest_type_label": ["Hardwood"] * 6,
                          "age_class_label": ["10-19"] * 6, "age_class_sort": [10] * 6,
                          "acres": [1000, 20, 10, 10, 20, 100],
                          "weight_basis": ["sampling_weight"] * 6})
    config = {**CONFIG, "areas": {**CONFIG["areas"], "top_n_counties": 1}}
    figures.age_class_by_area(table, "county", tmp_path, 2076, name="county",
                              title="County", config=config)
    axes = capture_figure[-1].axes
    assert [ax.get_title() for ax in axes] == ["C\n100 acres", "Other counties\n30 acres"]
    assert sum(p.get_height() for ax in axes for p in ax.patches) == 130
    assert table["county"].tolist() == ["A", "B", "C"] * 2


def test_unknown_only_ages_keep_tables_and_skip_mean_age_figures(fvs_out, crosswalk,
                                                               tmp_path, caplog):
    """Unknown ages remain reportable without inventing a mean or aborting figures."""
    with sqlite3.connect(fvs_out) as conn:
        conn.execute("UPDATE FVS_Summary2 SET Age = 0")
    ctx = run_pipeline.run(fvs_out=str(fvs_out), crosswalk=str(crosswalk), out_dir=tmp_path)
    mean = ctx.tables["mean_age_by_forest_type"]
    assert sorted(mean["Year"].unique()) == ctx.years
    assert mean["mean_age"].isna().all()
    assert mean["acres"].eq(0).all()
    assert not (tmp_path / "figures" / "mean_age_by_forest_type.png").exists()
    assert (tmp_path / "figures" / "age_class_by_owner.png").exists()
    assert "no known stand ages" in caplog.text
    run_pipeline.run(["visualize"], out_dir=tmp_path)


def test_crosswalk_default_matches_the_declared_ownership_run():
    """The S6 crosswalk is the ownership-segmented run's declared stand-init table."""
    assert str(run_pipeline._declared(run_pipeline.OWNER_CROSSWALK_KEY)).endswith(
        "20260804_095846_Hard_Ownership_Boundaries/Inputs/FVS_StandInit.csv")


@pytest.mark.parametrize("local,remote", [
    (run_pipeline._declared(run_pipeline.OWNER_CROSSWALK_KEY),
     "20260804_095846_Hard_Ownership_Boundaries/Inputs/FVS_StandInit.csv"),
    ("/mnt/d/Artemis_project_fvs_copy/FVSOut.db",
     "Artemis_project_fvs_copy_no_management/FVSOut.db"),
    ("/mnt/d/TreeMap-2022/Data/TreeMap2022_CONUS.tif",
     "TreeMap-2022/Data/TreeMap2022_CONUS.tif"),
    ("/mnt/d/forest_condition_2026/FVS/FVS_Database_Runs/another_run/Inputs.csv",
     "forest_condition_2026/FVS/FVS_Database_Runs/another_run/Inputs.csv"),
])
def test_drive_paths_keep_their_documented_r2_locations(local, remote):
    """The nested crosswalk alias preserves existing aliases and unrelated paths."""
    assert run_pipeline.data_access.remote_url(local) == f"r2:artemis-r2/data/{remote}"
