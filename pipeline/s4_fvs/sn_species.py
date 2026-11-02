"""FVS Southern variant species codes: FIA SPCD -> SN alpha code.

The positionally aligned `JSP` / `FIAJSP` tables of the compiled variant's `sn/blkdat.f`, as
`experiments/2026-08-24_leto-ca-forest-viz/04_fvs_run.py` first typed them. The trajectory
library apportions natural regeneration by the stand's own species (Diaz et al. 2015) with it.
"""

from __future__ import annotations

SN_JSP = ("FR JU PI PU SP SA SR LL TM PP PD WP LP VP BY PC HM FM BE RM SV SM BU BB SB AH "
           "HI CA HB RD DW PS AB AS WA BA GA HL LB HA HY BN WN SU YP MG CT MS MV ML AP MB "
           "WT BG TS HH SD RA SY CW BT BC WO SO SK CB TO LK OV BJ SN CK WK CO RO QS PO BO "
           "LO BK WI SS BD EL WE AE RL OS OH OT").split()
SN_FIAJSP = ("010 057 090 107 110 111 115 121 123 126 128 129 131 132 221 222 260 311 313 "
              "316 317 318 330 370 372 391 400 450 460 471 491 521 531 540 541 543 544 552 "
              "555 580 591 601 602 611 621 650 651 652 653 654 660 680 691 693 694 701 711 "
              "721 731 740 743 762 802 806 812 813 819 820 822 824 825 826 827 832 833 834 "
              "835 837 838 901 920 931 950 970 971 972 975 299 998 999").split()

if len(SN_JSP) != len(SN_FIAJSP):
    raise AssertionError("SN/FIA species tables are not positionally aligned")

FIA_TO_SN: dict[str, str] = dict(zip(SN_FIAJSP, SN_JSP))
