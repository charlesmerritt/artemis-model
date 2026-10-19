"""The harvest-scheduler comparison deck's claims, held to the code they describe."""

import doctest
import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "research/harvest_scheduler_comparison/probe.py"
DECK = ROOT / "docs/harvest-scheduler-comparison/presentation.html"
ECOTRUST = Path(os.environ.get("ECOTRUST_HARVEST_SCHEDULER",
                               Path.home() / "projects/harvest-scheduler"))


def _load_probe():
    spec = importlib.util.spec_from_file_location("harvest_scheduler_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def _has_ecotrust_history() -> bool:
    return subprocess.run(["git", "-C", str(ECOTRUST), "cat-file", "-e",
                           f"{probe.ECOTRUST_HEAD}^{{commit}}"],
                          capture_output=True).returncode == 0


def test_print_rewrite_doctest():
    assert doctest.testmod(probe).failed == 0


@pytest.mark.parametrize("name,run", probe.artemis_probes(), ids=lambda p: str(p)[:3])
def test_artemis_claim_holds(name, run):
    run()


@pytest.mark.skipif(not _has_ecotrust_history(),
                    reason="needs a clone of Ecotrust/harvest-scheduler with its history")
def test_ecotrust_claims_hold():
    for _, run in probe.ecotrust_probes(ECOTRUST):
        run()


def test_deck_cites_every_probe():
    deck = DECK.read_text()
    names = [n for n, _ in probe.artemis_probes()]
    if _has_ecotrust_history():
        names += [n for n, _ in probe.ecotrust_probes(ECOTRUST)]
    missing = sorted(i for i in {n.split()[0] for n in names}
                     if not re.search(rf"\b{i}\b", deck))
    assert not missing, f"probes the deck never cites: {missing}"
