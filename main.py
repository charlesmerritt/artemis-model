"""Design sketch for the ARTEMIS iterative-coupling entry point.

`Artemis` is NOT implemented yet — this file records the intended top-level
flow (partition → project 5 yr → check thresholds → apply prescriptions →
repeat). Running it raises NotImplementedError rather than a NameError, so the
gap is explicit.

This flow is the RETIRED iterative-coupling design (git show e207953:docs/superpowers/specs/2026-07-16-parallel-fvs-runs-design.md).
The design of record precomputes trajectory libraries and selects among them by simulated
annealing: docs/architecture/presentation.html.
"""


def main():
    raise NotImplementedError(
        "Artemis is not implemented. This sketch is the retired iterative-coupling "
        "flow; the current design is docs/architecture/presentation.html. Retired flow:\n"
        "  1. Initialize state from the enriched TreeMap.\n"
        "  2. Partition the AOI and run FVS per partition in parallel.\n"
        "  3. Project each partition 5 years.\n"
        "  4. Check management thresholds per owner type per area.\n"
        "  5. Generate and apply prescriptions where thresholds are met.\n"
        "  6. Repeat to the end of the time horizon."
    )


if __name__ == "__main__":
    main()
