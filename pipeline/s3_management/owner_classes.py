"""
Owner-class assignment (policy layer over `config/ownership_policy.yaml`).

Turns the two ownership sources into one ARTEMIS owner class per management unit:

    Harris et al. (2025) 30 m ownership raster  →  base class (public/private, gov level)
    Florida parcel DOR_UC + acreage             →  refinement WITHIN private only

The asymmetry is the point, and it is a finding rather than a design choice: the parcel
layer has no ownership-class column. Its only ownership signal is `DORUC`, the Florida
Department of Revenue *land use* code, and a use code is not an owner — DOR_UC 82
("forest, parks, recreational areas") is used by federal, state, county, and municipal
land alike. So the raster assigns the class, the parcel splits the raster's single
`corporate_forest` class into industrial vs. other corporate, and any disagreement is
flagged rather than silently resolved. See `config/ownership_policy.yaml` for the full
statement and `docs/config-policy.md` for the reasoning.

Everything here is a pure function of a mapping plus the config — no raster or vector I/O —
so it is testable without the data drive. The one exception is ``audit_parcels``, the
CLI helper that checks the DOR_UC transcription against a real parcel layer.

Usage:
    from pipeline.s3_management.owner_classes import classify_owner
    assignment = classify_owner({"OWN_CODE": 4, "DORUC": 54, "ACRES": 2400})
    assignment.owner_class   # 'private_industrial'
    assignment.tpo_group     # 'Private'

    uv run python -m pipeline.s3_management.owner_classes --audit-parcels \\
        --parcels data/raw/FL_5_Co_Parcels.gdb --layer FL_5_Co_Parcels
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "ownership_policy.yaml"

# Sentinel for pixels the pipeline must drop before FVS (Harris non_forest / water).
MASKED = "masked"

# Field aliases accepted on a unit mapping, in priority order.
_HARRIS_FIELDS = ("OWN_CODE", "owner_code", "harris_class", "ownership_class")
_DORUC_FIELDS = ("DORUC", "doruc", "DOR_UC", "dor_uc")
_ACRES_FIELDS = ("ACRES", "acres", "parcel_acres")


@dataclass(frozen=True)
class OwnerAssignment:
    """One unit's resolved ownership: the class, its TPO budget group, and QA flags."""

    owner_class: str
    tpo_group: str | None
    flags: tuple[str, ...] = field(default_factory=tuple)

    def has(self, flag: str) -> bool:
        return flag in self.flags


@lru_cache(maxsize=None)
def load_ownership_policy(path: str | None = None) -> dict:
    """Load and cache `config/ownership_policy.yaml`."""
    with open(Path(path) if path else CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _first_present(unit: Mapping, names: tuple[str, ...]):
    for name in names:
        if name in unit and unit[name] is not None:
            return unit[name]
    return None


def _as_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def harris_value_to_class(value: int | None, policy: dict | None = None) -> str:
    """
    Map a Harris raster pixel value to an ARTEMIS owner class.

    Value 4 (`corporate_forest`) maps to `corporate_default_class` — industrial by default,
    demotable by parcel evidence in :func:`refine_private_class`. Masked values (non_forest,
    water) return :data:`MASKED`; anything unrecognised returns ``"unknown"``.
    """
    policy = policy or load_ownership_policy()
    if value is None:
        return "unknown"
    if value in policy["masked_harris_values"]:
        return MASKED

    corporate_default = policy["corporate_default_class"]
    for name, spec in policy["classes"].items():
        if value not in spec["harris_values"]:
            continue
        # Value 4 is claimed by both corporate classes; the default decides which.
        if value in policy["classes"][corporate_default]["harris_values"]:
            return corporate_default
        return name
    return "unknown"


def doruc_signal(doruc, policy: dict | None = None) -> str | None:
    """
    Classify a DOR_UC code into an ownership *signal*.

    Returns ``"government"``, ``"timberland"``, ``"agricultural"``, ``"vacant_acreage"``,
    or ``None`` when the code is missing or not in the transcribed table. ``"government"``
    deliberately does not say *which* government — the code table cannot.
    """
    policy = policy or load_ownership_policy()
    code = _as_int(doruc)
    if code is None:
        return None
    for signal, codes in policy["doruc_signals"].items():
        if code in codes:
            return signal
    return None


def _owner_name_is_industrial(name, policy: dict | None = None) -> bool:
    policy = policy or load_ownership_policy()
    cfg = policy["private_refinement"]["owner_name"]
    if not cfg.get("enabled") or not isinstance(name, str):
        return False
    upper = name.upper()
    return any(pattern.upper() in upper for pattern in cfg["industrial_patterns"])


def refine_private_class(
    base_class: str,
    *,
    doruc=None,
    acres=None,
    owner_name=None,
    policy: dict | None = None,
) -> tuple[str, tuple[str, ...]]:
    """
    Apply the parcel refinement to a corporate unit. Returns ``(owner_class, flags)``.

    Only `private_industrial` (the raster's `corporate_forest`) is refinable. The unit
    keeps that class if any industrial signal fires — timberland DOR_UC, parcel acreage at
    or above the threshold, or a matching owner name — and is demoted to
    `private_corporate_other` only when no industrial signal fires and a non-industrial
    DOR_UC does. With no parcel evidence at all the unit keeps the class and is flagged
    ``unrefined``, so that area can be reported separately.
    """
    policy = policy or load_ownership_policy()
    if base_class != policy["corporate_default_class"]:
        return base_class, ()

    rules = policy["private_refinement"]
    code = _as_int(doruc)
    acreage = _as_float(acres)
    has_evidence = code is not None or acreage is not None or _owner_name_is_industrial(owner_name, policy)
    if not has_evidence:
        return base_class, ("unrefined",)

    industrial = (
        (code is not None and code in rules["timberland_doruc"])
        or (acreage is not None and acreage >= rules["industrial_min_acres"])
        or _owner_name_is_industrial(owner_name, policy)
    )
    if industrial:
        return base_class, ()

    if code is not None and code in rules["non_industrial_doruc"]:
        return "private_corporate_other", ("demoted_to_other_corporate",)

    # Evidence exists but is neutral (e.g. a small agricultural parcel): keep the class,
    # record that nothing decided it.
    return base_class, ("unrefined",)


def tpo_group_for(owner_class: str, policy: dict | None = None) -> str | None:
    """The `config/tpo_targets.yaml` owner group a class's volume is charged against."""
    policy = policy or load_ownership_policy()
    if owner_class == MASKED:
        return None
    spec = policy["classes"].get(owner_class)
    return spec["tpo_group"] if spec else None


def classify_owner(unit: Mapping, policy: dict | None = None) -> OwnerAssignment:
    """
    Resolve one unit's owner class, following `ownership_policy.yaml` precedence.

    ``unit`` is any mapping. Recognised keys: a Harris class (``OWN_CODE`` /
    ``owner_code`` / ``harris_class`` / ``ownership_class``), ``DORUC``, ``ACRES``, and the
    owner-name field named in the config when that refinement is enabled. Missing keys
    degrade to a flagged assignment rather than an error — an unclassifiable unit still
    has to appear in the area accounting.

    Flags: ``unknown_ownership``, ``unrefined``, ``demoted_to_other_corporate``,
    ``owner_conflict``.
    """
    policy = policy or load_ownership_policy()

    harris = _as_int(_first_present(unit, _HARRIS_FIELDS))
    base = harris_value_to_class(harris, policy)
    if base == MASKED:
        return OwnerAssignment(MASKED, None, ("masked",))

    doruc = _first_present(unit, _DORUC_FIELDS)
    acres = _first_present(unit, _ACRES_FIELDS)
    name_field = policy["private_refinement"]["owner_name"].get("field")
    owner_name = unit.get(name_field) if name_field else None

    owner_class, flags = refine_private_class(
        base, doruc=doruc, acres=acres, owner_name=owner_name, policy=policy
    )

    flags = list(flags)
    if owner_class == "unknown":
        flags.append("unknown_ownership")

    # Disagreement flag: the parcel says government, the raster says private (or vice
    # versa). The raster class stands — this is a QA signal about vintage and
    # registration, not a correction.
    signal = doruc_signal(doruc, policy)
    if signal == "government":
        is_private = owner_class.startswith("private_") or owner_class == "unknown"
        if is_private:
            flags.append("owner_conflict")
    elif signal in {"timberland", "agricultural"} and owner_class in {"federal", "state", "local"}:
        flags.append("owner_conflict")

    return OwnerAssignment(owner_class, tpo_group_for(owner_class, policy), tuple(flags))


def classify_owners(units, policy: dict | None = None):
    """
    Row-wise assignment over a DataFrame/GeoDataFrame.

    Returns a copy with ``owner_class``, ``owner_group`` (the TPO group name the harvest
    scheduler budgets against), and ``owner_flags`` (semicolon-joined) added.
    """
    policy = policy or load_ownership_policy()
    df = units.copy()
    assignments = [classify_owner(row, policy) for _, row in df.iterrows()]
    df["owner_class"] = [a.owner_class for a in assignments]
    df["owner_group"] = [a.tpo_group for a in assignments]
    df["owner_flags"] = [";".join(a.flags) for a in assignments]
    return df


# ---- parcel audit ---------------------------------------------------------------------

def audit_parcels(parcels_path: Path, layer: str | None = None, policy: dict | None = None):
    """
    Check the DOR_UC transcription in the config against a real parcel layer.

    Prints every observed ``DORUC`` value with its ``PARUSEDESC`` text, parcel count, and
    acreage, marking which config signal (if any) claims it. Codes with no signal are the
    ones to look at: they either belong in `doruc_signals` or confirm the code is
    irrelevant to ownership. This is what has to pass before `ownership_policy.yaml`
    can be marked ``verified: true``.
    """
    import geopandas as gpd
    import pandas as pd

    policy = policy or load_ownership_policy()
    gdf = gpd.read_file(parcels_path, layer=layer) if layer else gpd.read_file(parcels_path)

    doruc_col = next((c for c in _DORUC_FIELDS if c in gdf.columns), None)
    if doruc_col is None:
        raise ValueError(
            f"No DOR use-code column in {parcels_path} (looked for {list(_DORUC_FIELDS)}); "
            f"columns are {list(gdf.columns)}"
        )
    acres_col = next((c for c in _ACRES_FIELDS if c in gdf.columns), None)
    desc_col = next((c for c in ("PARUSEDESC", "paruseddesc", "DOR_UC_DESC") if c in gdf.columns), None)

    agg = {"parcels": (doruc_col, "size")}
    if acres_col:
        agg["acres"] = (acres_col, "sum")
    if desc_col:
        agg["description"] = (desc_col, "first")
    summary = gdf.groupby(doruc_col, dropna=False).agg(**agg).reset_index()

    summary["signal"] = summary[doruc_col].map(lambda c: doruc_signal(c, policy) or "-")
    summary["config_label"] = summary[doruc_col].map(
        lambda c: policy["doruc_labels"].get(_as_int(c), "")
    )
    summary = summary.sort_values("parcels", ascending=False)

    unmatched = summary[summary["signal"] == "-"]
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(summary.to_string(index=False))
    print(f"\n{len(summary)} distinct DOR use codes; {len(unmatched)} carry no config signal.")

    # Owner-name refinement is disabled until a name column is confirmed present.
    name_candidates = [c for c in gdf.columns if "OWN" in c.upper() and "NAME" in c.upper()]
    print(f"Candidate owner-name columns: {name_candidates or 'none found'}")
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="ARTEMIS owner-class policy utilities")
    parser.add_argument("--audit-parcels", action="store_true",
                        help="Check the DOR_UC transcription against a real parcel layer")
    parser.add_argument("--parcels", type=Path, help="Path to the parcel GDB/GeoPackage")
    parser.add_argument("--layer", type=str, default=None)
    args = parser.parse_args()

    if args.audit_parcels:
        if not args.parcels:
            parser.error("--audit-parcels requires --parcels")
        audit_parcels(args.parcels, args.layer)
        return

    policy = load_ownership_policy()
    print(f"ownership_policy.yaml v{policy['version']} (verified={policy['verified']})")
    for name, spec in policy["classes"].items():
        print(f"  {name:<26} harris={spec['harris_values']}  tpo_group={spec['tpo_group']!r}")


if __name__ == "__main__":
    main()
