suppressMessages(library(dplyr))
options(width = 190)
cmp <- read.csv("output/FL_FIA_TreeMap_comparison.csv")
adj <- read.csv("output/FL_TreeMap_scaled_summary.csv")
fia <- cmp %>% filter(SOURCE == "FIA"); tm <- cmp %>% filter(SOURCE == "TreeMap2022")

out <- data.frame(
  metric      = c("Forest acres", "BAA (sq ft/ac)", "TPA (trees/ac)", "Carbon AG live (tons/ac)"),
  FIA         = c(fia$FOREST_ACRES, fia$BAA_sqft_ac, fia$TPA_live_ac, fia$Carbon_tons_ac),
  TreeMap_raw = c(tm$FOREST_ACRES, tm$BAA_sqft_ac, tm$TPA_live_ac, tm$Carbon_tons_ac),
  TreeMap_scaled = c(adj$FIA_TOTAL_ACRES, adj$BAA_adj_ac, adj$TPA_adj_ac, adj$CARB_adj_ac))
out <- out %>% mutate(
  raw_pct_diff    = round(100*(TreeMap_raw    - FIA)/FIA, 2),
  scaled_pct_diff = round(100*(TreeMap_scaled - FIA)/FIA, 2),
  abs_improved    = ifelse(abs(scaled_pct_diff) < abs(raw_pct_diff), "yes", "NO"))
cat("=== State level: FIA vs TreeMap raw vs TreeMap area-scaled (mean-balanced) ===\n")
print(out %>% mutate(across(where(is.numeric), ~round(.x,2))), row.names = FALSE)
write.csv(out, "output/FL_state_level_scaling_comparison.csv", row.names = FALSE)

cat("\n=== Area allocation: worst per-type area misallocation (>=100k FIA acres) ===\n")
ua <- read.csv("output/FL_uncertainty_after_scaling.csv") %>% filter(!is.na(FIA_ACRES))
ua %>% filter(FIA_ACRES >= 1e5) %>% arrange(AREA_PCT_DIFF) %>%
  transmute(FORTYPCD, Type = substr(ForTypName,1,32), N_PLOTS, ESS = round(ESS,1),
            TM_ACRES = round(TM_ACRES), FIA_ACRES = round(FIA_ACRES),
            AREA_PCT_DIFF = round(AREA_PCT_DIFF,1), AREA_SCALE = round(AREA_SCALE,2)) %>%
  head(10) %>% print(row.names = FALSE)
cat("\nTotal TM acres in matched types:", round(sum(ua$TM_ACRES)),
    "| Total FIA acres in matched types:", round(sum(ua$FIA_ACRES)), "\n")
cat("Sum |area misallocation| across types:", round(sum(abs(ua$TM_ACRES - ua$FIA_ACRES))),
    "acres =", round(100*sum(abs(ua$TM_ACRES-ua$FIA_ACRES))/sum(ua$FIA_ACRES),1), "% of FIA forest area\n")
