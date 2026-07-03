#!/usr/bin/env bash
# Copy WSL data outputs (interim + processed) to the Windows host D: drive
# so they can be consumed by ArcGIS on Windows.
#
# WSL source : /home/chazm/projects/artemis-model/data/{interim,processed}
# Win target : D:\Artemis_data\{interim,processed}

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_ROOT="/mnt/d/Artemis_data"

mkdir -p "$DEST_ROOT"

for SUB in interim processed; do
    SRC="${SRC_DIR}/${SUB}"
    DEST="${DEST_ROOT}/${SUB}"

    if [[ ! -d "$SRC" ]]; then
        echo "WARN: source missing, skipping: ${SRC}"
        continue
    fi

    echo "Copying ${SRC} -> ${DEST}"
    mkdir -p "$DEST"
    cp -ruv --preserve=timestamps "${SRC}/." "$DEST"
done

echo "Done."
