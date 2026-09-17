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


TERMINAL_KINDS = {"regeneration_harvest", "retention_harvest"}


@functools.lru_cache(maxsize=4)
def load_library(path: str | Path | None = None) -> dict:
    """Load, validate and cache the regime library. Cached — `assign_regimes` calls this per row."""
    with open(Path(path) if path else CONFIG_PATH) as f:
        return validate_library(yaml.safe_load(f))


def validate_library(library: dict) -> dict:
    """Enforce the file's own ``constraints`` block, so the config cannot state a rule it breaks.

    An off-cycle offset is silently shifted by FVS to the next cycle boundary; two cuts in one
    year render as duplicate ``ThinDBH`` lines; and without a regeneration keyword (issue #17)
    anything after a stand-replacing entry would cut whatever FVS grew back by default.

    >>> validate_library({"constraints": {"offsets_must_be_multiples_of": 5, "max_year_offset": 50},
    ...                   "regimes": {"bad": {"operations": [{"year_offset": 12, "kind": "selection"}]}}})
    Traceback (most recent call last):
    ...
    ValueError: regime 'bad': year_offset 12 is not a multiple of 5
    """
    rules = library["constraints"]
    cycle, horizon = rules["offsets_must_be_multiples_of"], rules["max_year_offset"]
    for name, regime in library["regimes"].items():
        ops = regime["operations"]
        offsets = [op["year_offset"] for op in ops]
        for off in offsets:
            if off % cycle:
                raise ValueError(f"regime {name!r}: year_offset {off} is not a multiple of {cycle}")
            if not 0 <= off <= horizon:
                raise ValueError(f"regime {name!r}: year_offset {off} is outside 0..{horizon}")
        if offsets != sorted(set(offsets)):
            raise ValueError(f"regime {name!r}: operations are not strictly ascending in time")
        for i, op in enumerate(ops[:-1]):
            if op.get("kind") in TERMINAL_KINDS:
                raise ValueError(f"regime {name!r}: operation scheduled after a {op['kind']}")
    return library


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
