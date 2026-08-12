suppressMessages(library(dplyr))
options(width = 200)
ua  <- read.csv("output/FL_uncertainty_after_scaling.csv")
new <- read.csv("output/FL_uncertainty_before_scaling.csv")
old <- read.csv("baseline_2026-02/FL_uncertainty_before_scaling.csv")

cat("rows: new before =", nrow(new), " baseline before =", nrow(old), " after =", nrow(ua), "\n")
cat("types with FIA match (non-NA FIA_ACRES):", sum(!is.na(ua$FIA_ACRES)), "\n")
cat("types with no FIA area match:", sum(is.na(ua$FIA_ACRES)), "->",
    paste(ua$ForTypName[is.na(ua$FIA_ACRES)], collapse=" | "), "\n\n")

cat("=== Reproducibility check: new vs Feb baseline, shared columns ===\n")
j <- inner_join(new %>% select(FORTYPCD, nb_BAA=TM_BAA_mean, nb_FIA_BAA=FIA_BAA, nb_pix=N_PIXELS, nb_plots=N_PLOTS),
                old %>% select(FORTYPCD, ob_BAA=TM_BAA_mean, ob_FIA_BAA=FIA_BAA, ob_pix=N_PIXELS, ob_plots=N_PLOTS),
                by="FORTYPCD")
cat("matched types:", nrow(j), "\n")
cat("max |TM_BAA_mean diff|      :", max(abs(j$nb_BAA - j$ob_BAA), na.rm=TRUE), "\n")
cat("max |FIA_BAA diff|          :", max(abs(j$nb_FIA_BAA - j$ob_FIA_BAA), na.rm=TRUE), "\n")
cat("max |N_PIXELS diff|         :", max(abs(j$nb_pix - j$ob_pix), na.rm=TRUE), "\n")
cat("total pixel diff            :", sum(j$nb_pix) - sum(j$ob_pix), "\n")
cat("types where N_PLOTS differs :", sum(j$nb_plots != j$ob_plots), "\n\n")

cat("=== Area-weighted state-level bias decomposition ===\n")
d <- ua %>% filter(!is.na(FIA_ACRES))
mk <- function(bef, aft, tmw, fiaw) c(before = sum(bef, na.rm=TRUE)/sum(tmw, na.rm=TRUE),
                                      after  = sum(aft, na.rm=TRUE)/sum(fiaw, na.rm=TRUE))
tab <- rbind(
  BAA  = mk(ua$BAA_contrib_before,  ua$BAA_contrib_after,  ua$TM_ACRES, ua$FIA_ACRES),
  TPA  = mk(ua$TPA_contrib_before,  ua$TPA_contrib_after,  ua$TM_ACRES, ua$FIA_ACRES),
  CARB = mk(ua$CARB_contrib_before, ua$CARB_contrib_after, ua$TM_ACRES, ua$FIA_ACRES))
tab <- as.data.frame(tab) %>% mutate(change = after - before,
                                     improved = ifelse(abs(after) < abs(before), "yes", "NO"))
print(round(tab[,1:3], 4)); print(tab[,4,drop=FALSE])

cat("\n=== Top 12 types by area, per-acre bias (TreeMap - FIA) ===\n")
d %>% arrange(desc(FIA_ACRES)) %>% head(12) %>%
  transmute(FORTYPCD, Type = substr(ForTypName,1,30), N_PLOTS, ESS = round(ESS,1),
            TM_ACRES = round(TM_ACRES), FIA_ACRES = round(FIA_ACRES),
            AREA_PCT_DIFF = round(AREA_PCT_DIFF,1),
            BAA_bias = round(BAA_bias_abs,1), BAA_pct = round(BAA_bias_pct,1), BAA_z = round(BAA_z,2),
            TPA_pct = round(TPA_bias_pct,1), CARB_pct = round(CARB_bias_pct,1)) %>% print(row.names=FALSE)

cat("\n=== Types contributing most to residual (post-scaling) BAA bias ===\n")
d %>% mutate(share = 100*BAA_contrib_after/sum(BAA_contrib_after, na.rm=TRUE)) %>%
  arrange(desc(abs(BAA_contrib_after))) %>% head(8) %>%
  transmute(FORTYPCD, Type = substr(ForTypName,1,30), FIA_ACRES = round(FIA_ACRES),
            BAA_bias = round(BAA_bias_abs,1), contrib_after = round(BAA_contrib_after),
            pct_of_total = round(share,1)) %>% print(row.names=FALSE)

cat("\n=== ESS / plot-concentration ===\n")
cat("types with ESS < 5 :", sum(d$ESS < 5), "of", nrow(d), "\n")
cat("acres in types with ESS < 5:", round(sum(d$FIA_ACRES[d$ESS < 5])), "(",
    round(100*sum(d$FIA_ACRES[d$ESS<5])/sum(d$FIA_ACRES),1), "% of FIA forest area )\n")
cat("median ESS:", round(median(d$ESS),1), " median N_PLOTS:", median(d$N_PLOTS), "\n")
cat("|z|>2 counts -- BAA:", sum(abs(d$BAA_z)>2, na.rm=TRUE),
    " TPA:", sum(abs(d$TPA_z)>2, na.rm=TRUE),
    " CARB:", sum(abs(d$CARB_z)>2, na.rm=TRUE), "of", nrow(d), "\n")
