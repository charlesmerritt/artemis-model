"""
The canonical FVS output pipeline: a real FVS output database in, figures out.

Five stages, run in order, each one reading what the previous stage produced. The last
stage is nothing but visualization — by the time it runs every number in every figure has
already been written to a CSV beside it, so a figure can be redrawn, restyled, or replaced
without recomputing anything, and no chart holds a value that is not also on disk.

    1  resolve   locate the FVS output database (and the ownership crosswalk), fetching
                 from the R2 mirror when the workstation drive is not mounted
    2  extract   FVS_Cases + FVS_Summary2 -> one tidy stand-year frame, cycles checked
    3  attribute forest-type group, age class, area, and owner-class acre shares
    4  summarize the canonical age-class tables, written as CSV
    5  visualize the figures, drawn only from the stage-4 tables

Run every stage:

    uv run python -m pipeline.s6_outputs.run_pipeline

Check what it would read and write, without writing:

    uv run python -m pipeline.s6_outputs.run_pipeline --dry-run

Redraw the figures from tables that are already on disk:

    uv run python -m pipeline.s6_outputs.run_pipeline --stages visualize

Point it at a different run:

    uv run python -m pipeline.s6_outputs.run_pipeline \\
        --fvs-out /mnt/d/some_other_project/FVSOut.db --out-dir data/processed/that_run

Outputs land in `data/processed/fvs_outputs/<run>/`: `tables/*.csv`, `figures/*.png`, and
`run_summary.json` carrying the run's identity, the reporting grid, the crosswalk coverage,
and the acreage each table foots to — so a figure in a report can always be traced to the
FVS project that produced it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from pipeline import data_access
from pipeline.s6_outputs import age_class, figures, fvs_out_db, owner_attribution

logger = logging.getLogger("pipeline.s6_outputs")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "processed" / "fvs_outputs"

STAGES = ("resolve", "extract", "attribute", "summarize", "visualize")

# Config keys naming the default run: the five-county no-management FVS Online project and
# the ownership-segmented run whose stand-init table carries the owner classes.
FVS_OUT_KEY = ("raw", "Artemis_project_fvs_copy_no_management", "FVSOut_db")
OWNER_CROSSWALK_KEY = ("raw", "hard_ownership_boundaries", "stand_init_csv")

# The crosswalk is ~21 MB; the default 512 MB cap is plenty, but naming it keeps a
# mis-pointed key from pulling the 1.5 GB tree list that sits beside it.
CROSSWALK_MAX_FETCH_MB = 64


class StageError(RuntimeError):
    """A stage could not complete. Carries a message meant for an operator, not a trace."""


@dataclass
class Context:
    """What the stages hand each other."""

    config: dict
    out_dir: Path
    fvs_out_db_path: Path | None = None
    crosswalk_path: Path | None = None
    stand_years: pd.DataFrame | None = None
    attributed: pd.DataFrame | None = None
    owned: pd.DataFrame | None = None
    years: list[int] | None = None
    tables: dict[str, pd.DataFrame] | None = None
    summary: dict | None = None

    @property
    def table_dir(self) -> Path:
        return self.out_dir / "tables"

    @property
    def figure_dir(self) -> Path:
        return self.out_dir / "figures"


def _declared(key: tuple[str, ...]) -> str:
    node = data_access.data_paths()
    for part in key:
        node = node[part]
    return node


# ---- stage 1: resolve ------------------------------------------------------------------

def _resolve_one(path: str, what: str, *, max_fetch_mb: int | None = None) -> Path:
    local = Path(path)
    if local.exists():
        return local
    fetched = data_access.ensure_local(path, max_fetch_mb=max_fetch_mb)
    if fetched is None:
        raise StageError(
            f"{what} is not reachable: {path}\n"
            f"  {data_access.unavailable_reason(path)}\n"
            "  Mount the workstation drive, or set the RCLONE_CONFIG_R2_* variables so the "
            "R2 mirror can answer (config/data_paths.yaml documents both)."
        )
    return fetched


def stage_resolve(ctx: Context, *, fvs_out: str | None, crosswalk: str | None,
                  dry_run: bool) -> None:
    """Locate the inputs, pulling them from the R2 mirror if the drive is not mounted."""
    fvs_out = fvs_out or _declared(FVS_OUT_KEY)
    crosswalk = crosswalk if crosswalk is not None else _declared(OWNER_CROSSWALK_KEY)

    logger.info("FVS output database: %s", fvs_out)
    logger.info("owner crosswalk:     %s", crosswalk or "(none — owner tables skipped)")
    if dry_run:
        for label, path in (("FVS output database", fvs_out), ("owner crosswalk", crosswalk)):
            if path:
                logger.info("  %s: %s", label,
                            "present" if data_access.exists(path) else "NOT REACHABLE")
        return

    ctx.fvs_out_db_path = _resolve_one(fvs_out, "the FVS output database")
    if crosswalk:
        try:
            ctx.crosswalk_path = _resolve_one(crosswalk, "the owner-class crosswalk",
                                              max_fetch_mb=CROSSWALK_MAX_FETCH_MB)
        except StageError as exc:
            # An unreachable crosswalk costs the owner cut and nothing else, so it degrades
            # rather than fails — but it says so in the log and in run_summary.json.
            logger.warning("%s\n  owner-class tables and figures will be skipped", exc)
            ctx.crosswalk_path = None


# ---- stage 2: extract ------------------------------------------------------------------

def stage_extract(ctx: Context) -> None:
    """Read the run into one stand-year frame and settle the reporting year grid."""
    ctx.stand_years = fvs_out_db.load_stand_years(ctx.fvs_out_db_path, config=ctx.config)
    ctx.years = fvs_out_db.reporting_years(ctx.stand_years, config=ctx.config)

    identity = fvs_out_db.run_identity(ctx.stand_years)
    logger.info("run %s (%s variant): %d cases, %d–%d",
                ", ".join(identity["run_title"]), ", ".join(identity["variant"]),
                identity["cases"], identity["first_year"], identity["last_year"])
    logger.info("reporting on %d balanced year(s): %d–%d",
                len(ctx.years), ctx.years[0], ctx.years[-1])
    dropped = sorted(set(ctx.stand_years["Year"].unique()) - set(ctx.years))
    if dropped:
        logger.info("excluding %d unbalanced year(s) before %d: not every case reports in "
                    "them", len(dropped), ctx.years[0])


# ---- stage 3: attribute ----------------------------------------------------------------

def stage_attribute(ctx: Context) -> None:
    """Age class, forest-type group, area — then owner-class acre shares if available."""
    ctx.attributed = age_class.attribute(ctx.stand_years, config=ctx.config)
    if ctx.crosswalk_path is None:
        return
    shares = owner_attribution.load_owner_shares(ctx.crosswalk_path)
    ctx.owned = owner_attribution.apportion(ctx.attributed, shares, config=ctx.config)


# ---- stage 4: summarize ----------------------------------------------------------------

def stage_summarize(ctx: Context, *, write: bool = True) -> None:
    """Build the canonical tables and write them. Every figure is drawn from one of these."""
    years, attributed = ctx.years, ctx.attributed
    tables = {
        "age_class_overall": age_class.overall(attributed, years),
        "age_class_by_forest_type": age_class.by_forest_type(attributed, years),
        "age_class_by_forest_type_detail": age_class.by_forest_type_detail(attributed, years),
        "age_class_by_state": age_class.by_state(attributed, years),
        "age_class_by_county": age_class.by_county(attributed, years),
        "mean_age_by_forest_type": age_class.mean_age_trajectory(
            attributed, years, "forest_type_label"),
    }
    if ctx.owned is not None:
        tables |= {
            "age_class_by_owner": age_class.by_owner(ctx.owned, years),
            "age_class_by_owner_forest_type": age_class.by_owner_and_forest_type(
                ctx.owned, years),
            "age_class_by_management_type": age_class.by_management_type(ctx.owned, years),
            "mean_age_by_owner": age_class.mean_age_trajectory(
                ctx.owned, years, "owner_class", weight=age_class.OWNER_ACRES),
        }
    ctx.tables = tables
    ctx.summary = _build_summary(ctx)

    if not write:
        return
    ctx.table_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(ctx.table_dir / f"{name}.csv", index=False)
        logger.info("wrote tables/%s.csv (%d rows)", name, len(table))
    (ctx.out_dir / "run_summary.json").write_text(json.dumps(ctx.summary, indent=2) + "\n")
    logger.info("wrote run_summary.json")


def _build_summary(ctx: Context) -> dict:
    """Provenance and the totals each table foots to — what a reader checks a figure against."""
    identity = fvs_out_db.run_identity(ctx.stand_years)
    summary = {
        "fvs_output_db": str(ctx.fvs_out_db_path),
        "owner_crosswalk": str(ctx.crosswalk_path) if ctx.crosswalk_path else None,
        "run": identity,
        "reporting_years": ctx.years,
        "excluded_unbalanced_years": sorted(
            int(y) for y in set(ctx.stand_years["Year"].unique()) - set(ctx.years)
        ),
        "age_classes": ctx.config["age_classes"],
        "tables": {
            name: {
                "rows": len(table),
                "weight_basis": sorted(table["weight_basis"].unique().tolist())
                if "weight_basis" in table else None,
                "acres_first_year": float(
                    table[table["Year"] == ctx.years[0]]["acres"].sum()
                ) if "acres" in table else None,
            }
            for name, table in ctx.tables.items()
        },
    }
    if ctx.owned is not None:
        summary["owner_coverage"] = owner_attribution.coverage(ctx.owned, config=ctx.config)
    return summary


def _load_tables(ctx: Context) -> dict[str, pd.DataFrame]:
    """Read stage 4's output back, so `--stages visualize` can redraw without recomputing."""
    if not ctx.table_dir.exists():
        raise StageError(
            f"no tables in {ctx.table_dir}. Run the summarize stage before visualize."
        )
    tables = {p.stem: pd.read_csv(p) for p in sorted(ctx.table_dir.glob("*.csv"))}
    if not tables:
        raise StageError(f"{ctx.table_dir} holds no CSVs")
    summary_path = ctx.out_dir / "run_summary.json"
    if summary_path.exists():
        ctx.summary = json.loads(summary_path.read_text())
        ctx.years = ctx.summary["reporting_years"]
    else:
        ctx.years = sorted(tables["age_class_overall"]["Year"].unique())
    return tables


# ---- stage 5: visualize ----------------------------------------------------------------

def stage_visualize(ctx: Context) -> list[Path]:
    """
    Draw the figures. Reads the stage-4 tables and nothing else.

    This stage holds no analysis: if a number here looks wrong, it is wrong in
    `tables/*.csv` too, and that is the file to go and read.
    """
    tables = ctx.tables or _load_tables(ctx)
    years = ctx.years
    snapshots = [years[0] if s == "first" else years[-1] if s == "last" else int(s)
                 for s in ctx.config["figures"]["snapshot_years"]]
    last = years[-1]
    written: list[Path] = []

    written.append(figures.age_class_by_forest_type(
        tables["age_class_by_forest_type"], ctx.figure_dir, snapshots, config=ctx.config))
    written.append(figures.age_class_over_time(
        tables["age_class_by_forest_type"], ctx.figure_dir, config=ctx.config))
    written.append(figures.mean_age_trajectory(
        tables["mean_age_by_forest_type"], ctx.figure_dir, "forest_type_label",
        name="mean_age_by_forest_type",
        title="Acre-weighted mean stand age, pine against hardwood", config=ctx.config))
    written.append(figures.age_class_by_area(
        tables["age_class_by_state"], "state", ctx.figure_dir, last,
        name="age_class_by_state", title="Age-class distribution by state",
        config=ctx.config))
    written.append(figures.age_class_by_area(
        tables["age_class_by_county"], "county", ctx.figure_dir, last,
        name="age_class_by_county",
        title=f"Age-class distribution by county (largest {ctx.config['areas']['top_n_counties']})",
        config=ctx.config))

    if "age_class_by_owner_forest_type" in tables:
        written.append(figures.age_class_by_owner(
            tables["age_class_by_owner_forest_type"], ctx.figure_dir, last,
            config=ctx.config))
        written.append(figures.age_class_by_management_type(
            tables["age_class_by_management_type"], ctx.figure_dir, last, config=ctx.config))
        written.append(figures.mean_age_trajectory(
            tables["mean_age_by_owner"], ctx.figure_dir, "owner_class",
            name="mean_age_by_owner",
            title="Acre-weighted mean stand age by owner class", config=ctx.config))
    else:
        logger.warning("no owner tables: the owner-class figures were not drawn")

    return written


# ---- driver ----------------------------------------------------------------------------

def run(stages=STAGES, *, fvs_out: str | None = None, crosswalk: str | None = None,
        out_dir: Path | None = None, config_path: str | None = None,
        dry_run: bool = False) -> Context:
    """Run the requested stages in pipeline order and return the context they filled."""
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        raise StageError(f"unknown stage(s) {', '.join(unknown)}; choose from {', '.join(STAGES)}")
    stages = [s for s in STAGES if s in set(stages)]

    config = fvs_out_db.load_output_config(config_path)
    ctx = Context(config=config, out_dir=Path(out_dir or DEFAULT_OUT_DIR))

    if "resolve" in stages:
        stage_resolve(ctx, fvs_out=fvs_out, crosswalk=crosswalk, dry_run=dry_run)
        if dry_run:
            return ctx
    if "extract" in stages:
        stage_extract(ctx)
    if "attribute" in stages:
        stage_attribute(ctx)
    if "summarize" in stages:
        stage_summarize(ctx)
    if "visualize" in stages:
        written = stage_visualize(ctx)
        logger.info("wrote %d figure(s) to %s", len(written), ctx.figure_dir)
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fvs-out", help="FVS output database (default: the config key)")
    parser.add_argument("--crosswalk", help="ownership stand-init CSV; '' to skip the "
                                            "owner-class tables")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--config", help="alternative config/fvs_outputs.yaml")
    parser.add_argument("--stages", nargs="+", default=list(STAGES), choices=STAGES,
                        metavar="STAGE", help=f"stages to run ({', '.join(STAGES)})")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be read and written, write nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        ctx = run(args.stages, fvs_out=args.fvs_out, crosswalk=args.crosswalk,
                  out_dir=args.out_dir, config_path=args.config, dry_run=args.dry_run)
    except (StageError, fvs_out_db.FvsOutputError,
            owner_attribution.OwnerCrosswalkError) as exc:
        logger.error("%s", exc)
        return 1
    if not args.dry_run and ctx.summary:
        print(yaml.safe_dump({"outputs": str(ctx.out_dir), **{
            k: ctx.summary[k] for k in ("reporting_years", "owner_coverage")
            if k in ctx.summary}}, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
