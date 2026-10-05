# Add-back pixels carry a dated stand age and a real young plot, under new TreeMap IDs

**Status:** accepted, 2026-09-28. Supersedes [ADR 0002](0002-scaled-nearest-neighbour-establishment.md).

## Context

ADR 0002 started every scaled add-back stand at age 5 and kept the neighbour plot's
`TM_ID` in the improved raster, with provenance 4 warning consumers not to join it.
Two measurements showed that is not enough.

- **The ages are wrong.** Every any-two add-back pixel is dated by Obata or Hansen,
  because any two of the three hole methods must include a detector. Their median age
  since the cut in 2022 is 8, and only 12% are 5 or younger. TreeMap holes mostly record
  the 2012–2016 harvest.
- **LETO erases the patch.** LETO's segmentation builds STDAGE, BALIVE, QMD and TPA per
  pixel from `TM_ID` (`segmentation/cellular_automata.py`). An add-back pixel keeps its
  neighbour plot's ID, so it matches the stand around it exactly. The CA merges the two
  and the unit inherits the neighbour's mature age, which makes it harvest-eligible on
  day one.

## Decision

Every add-back pixel becomes an ordinary TreeMap pixel that carries its own true age.

- **Cut year.** The latest year Obata or Hansen detects. Where the two disagree by more
  than 2 years, a spectral tie-break decides (a separate ticket; until it lands, the
  latest year stands).
- **Stand age.** 2022 minus the establishment year, where the establishment year is the
  cut year plus 1 (planted the year after the harvest). A 2022 cut would be −1; it is set
  to 0.
- **Young plot.** A real FIA plot from TreeMap's own plot library in AL, FL and GA, with the
  neighbour plot's `FORTYPCD` and a stand age equal to the pixel's. If none matches, widen
  to ±1 year, then ±2, then the forest type group, then the age-indexed FIA profile. Among
  several matches, take the plot whose species mix is closest to the neighbour plot's,
  breaking ties on the `PLT_CN` string. At ages 0–2, FIA seedling records become small
  FVS tree records.
- **New TreeMap IDs.** The improved raster gets a new `VALUE` for each (neighbour plot,
  young plot, stand age) combination, outside TreeMap's own ID range. Its VAT row has all
  27 TreeMap columns, copied from the young plot, and its tree list is the young plot's.
  The row also records the 2022 stand age, cut year and its detector, and the neighbour
  plot. The row's age is authoritative. The young plot's FIA-measured age is not, so
  bringing published ages forward to 2022 (#82) must skip these IDs.
- **A dated cut wins over the LANDFIRE bookend stratum.** `donor_as_is` and the
  provenance-4 donor ID retire for dated pixels.

LETO ages management units from these pixels as it does for every other pixel, so no
separate age raster is produced.

## Considered options

- **Age from a single-date spectral classifier.** The detectors already date the cut from
  the full time series. A 2022 spectrum can tell bare ground from green cover but not age
  4 from age 9. Spectral evidence is kept for the tie-break only.
- **Earliest detected year instead of latest.** When the two disagree, Obata's year is the
  later one 99.5% of the time. For cuts after 2016, LANDFIRE 2022 mostly copied its 2016
  map, so a post-2016 event probably did not make the hole. The latest year was still
  chosen, because a real second harvest resets the age; the tie-break tests whether that
  later event was stand-replacing.
- **LETO overrides the features in place.** The raster would keep the neighbour plot's ID,
  and LETO would overwrite the features for add-back pixels in two places: the
  segmentation grids and the unit-age path. Rejected because the two overrides must stay
  in step, and every other consumer would still join the neighbour plot's mature trees.
- **Scaled profile stand (ADR 0002, extended by age).** It gives TPA, DBH and height only.
  The other VAT columns (stocking, volume, biomass, carbon, canopy) would need FIA's
  equations ported, and they would not agree with one another. It remains the fallback
  when no young plot matches.

## Consequences

- A consumer that joins a new ID to FIA's tree table gets the young plot's trees, which
  are correct. It no longer silently pulls mature biomass.
- The add-back rule (any-two or union) is still open and depends on the trade-off write-up.
  Under union, about 3,600 ac have no cut year and need another age source.
- Stale stands (published pixels harvested after their plot was measured) could reuse this
  representation. Whether they should is waiting on a NAIP review of the evidence.
