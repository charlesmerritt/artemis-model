"""
Data-driven silvicultural regime library.

Reads `config/regimes.yaml` — named prescriptions, each an ordered list of harvest
operations — and renders them into FVS keyfiles. This replaces the one-Python-builder-
per-regime pattern in `regime_templates.py`: adding a regime is a YAML edit, not a code
change, which is what keeps the owner-class regimes in `config/management_regimes.yaml`
reviewable by someone who does not read Python.

Every operation renders to the verified `ThinDBH` keyword (see `regime_templates`), so
regimes needing PLANT/NATREGEN, ThinBBA, or shelterwood cannot be expressed here yet —
that is issue #17, and approximating them with ThinDBH would be worse than their absence.

Year offsets in the config are relative to the inventory year; this module resolves them
to the absolute years FVS wants.

Usage:
    from pipeline.s4_fvs.regime_library import build_thins, render_keyfile

    key = render_keyfile("MU_123", "MU_123", "pine_plantation_industrial", inv_year=2022)
"""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from pipeline.s4_fvs.regime_templates import DEFAULT_INV_YEAR, ThinDBH
from pipeline.s4_fvs.regime_templates import render_keyfile as _render_keyfile

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "regimes.yaml"


@functools.lru_cache(maxsize=4)
def load_library(path: str | Path | None = None) -> dict:
    """Load and cache the regime library. Cached — `assign_regimes` calls this per row."""
    with open(Path(path) if path else CONFIG_PATH) as f:
        return yaml.safe_load(f)


def regime_names(library: dict | None = None) -> list[str]:
    return sorted((library or load_library())["regimes"])


def get_regime(name: str, library: dict | None = None) -> dict:
    regimes = (library or load_library())["regimes"]
    if name not in regimes:
        raise ValueError(f"unknown regime {name!r}; choices: {sorted(regimes)}")
    return regimes[name]


def build_thins(
    name: str,
    inv_year: int = DEFAULT_INV_YEAR,
    library: dict | None = None,
) -> list[ThinDBH]:
    """Resolve a regime's operations into absolute-year `ThinDBH` records."""
    regime = get_regime(name, library)
    return [
        ThinDBH(
            year=inv_year + int(op["year_offset"]),
            proportion=float(op["proportion"]),
            min_dbh=float(op["min_dbh"]),
            max_dbh=float(op["max_dbh"]),
            species=int(op.get("species", 0)),
        )
        for op in regime["operations"]
    ]


def render_keyfile(
    stand_id: str,
    stand_cn: str,
    regime: str,
    inv_year: int = DEFAULT_INV_YEAR,
    *,
    library: dict | None = None,
    **kwargs,
) -> str:
    """Render a single-stand FVS keyfile for a regime defined in `config/regimes.yaml`."""
    thins = build_thins(regime, inv_year=inv_year, library=library)
    return _render_keyfile(
        stand_id=stand_id, stand_cn=stand_cn, regime=regime,
        thins=thins, inv_year=inv_year, **kwargs,
    )


def cuts(name: str, library: dict | None = None) -> bool:
    """Does this regime harvest at all? `no_management` is the only one that does not."""
    return bool(get_regime(name, library)["cuts"])
