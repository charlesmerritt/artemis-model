"""WSL <-> Windows path translation for the restart-fidelity spike.

FVS runs on the Windows host and cannot see WSL paths, so every run directory
must live under /mnt/c (Windows-visible). This module is the only place that
translation happens.
"""

from __future__ import annotations

from pathlib import Path

SPIKE_DIR_WSL = Path("/mnt/c/FVS/artemis_spike")
SPIKE_DIR_WIN = r"C:\FVS\artemis_spike"

FVS_DATA_DB = "FVS_Data.db"
FVS_DATA_DB_SRC = Path("/mnt/c/FVS/Artemis_project/FVS_Data.db")

RSCRIPT_EXE = Path("/mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe")
FVS_BIN_WIN = r"C:\FVS\FVSSoftware\FVSbin"


def to_windows(p: Path) -> str:
    """Convert a /mnt/<drive>/... WSL path to a Windows path."""
    parts = p.parts
    if len(parts) < 3 or parts[0] != "/" or parts[1] != "mnt":
        raise ValueError(f"not a /mnt/<drive> path: {p}")
    drive = parts[2].upper()
    rest = "\\".join(parts[3:])
    return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
