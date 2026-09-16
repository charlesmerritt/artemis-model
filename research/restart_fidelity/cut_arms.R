# Management-injection (gate) spike driver. Windows host, via Rscript.exe.
#
# Proves fvsCutNow at a barrier is faithful:
#   g1  scheduled ThinDBH in the keyfile          (authoritative in-FVS baseline)
#   g2  fvsCutNow(p) injected in-process           (should match g1)
#   g3seg1 / g3seg2  fvsCutNow(p) after a restart  (should match g2)
#
# fvsCutNow can only be called at stop point 2. A negative restart code is the
# "stand restored, call again to resume" signal (cmdline.f:299), and after that
# signal the stand sits at the stored stop point -- so g3 injects the cut there.
#
# Usage: cut_arms.R <arm> [prop] [year]
#   arms: g1 | g2 | g3seg1 | g3seg2

library(rFVS)

FVSBIN <- "C:\\FVS\\FVSSoftware\\FVSbin"
SPIKE  <- "C:\\FVS\\artemis_spike"
setwd(SPIKE)

args   <- commandArgs(trailingOnly = TRUE)
arm    <- if (length(args) > 0) args[1] else "g1"
prop   <- if (length(args) > 1) as.numeric(args[2]) else 0.30
year   <- if (length(args) > 2) as.integer(args[3]) else 2004
target <- if (length(args) > 3) args[4] else ""

# g1: scheduled ThinDBH already in the keyfile; just run it.
run_g1 <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=g1.key")
  repeat { rc <- fvsRun(); if (rc != 0) break }
  cat("g1 done rc=", rc, "\n")
}

# g2: no scheduled thin; pause at stop point 2 in `year`, inject the cut, resume.
run_g2 <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=g2.key")
  rc <- fvsRun(2, year)                 # stop point 2, at `year`
  cat("g2 paused at", year, "rc=", rc, "restartcode=", fvsGetRestartcode(), "\n")
  cc <- fvsCutNow(prop)                 # inject proportional cut on current stand
  cat("g2 fvsCutNow(", prop, ") rc=", cc, "\n")
  repeat { rc <- fvsRun(); if (rc != 0) break }
  cat("g2 done rc=", rc, "\n")
}

# g3seg1: run to `year`, store all state (no cut yet -- the orchestrator stores
# at the barrier, decides externally, then injects on restart).
run_g3seg1 <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine(paste0("--keywordfile=g3.key --stoppoint=2,", year, ",g3_", year, ".rst"))
  repeat { rc <- fvsRun(); if (rc != 0) break }
  cat("g3seg1 stored at", year, "rc=", rc, "\n")
}

# g3seg2: restart, SCHEDULE the cut for the barrier year, resume to end.
# fvsCutNow can't be used here -- after a restore the stand is not in the
# stop-point-2 context it requires. fvsAddActivity has no such restriction and
# is the documented runtime-scheduling path (its own example schedules a
# base_thindbh). parms match the ThinDBH keyword: minDBH, maxDBH, proportion.
run_g3seg2 <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine(paste0("--restart=g3_", year, ".rst"))
  rc <- fvsRun()                        # -N signal: stand restored
  cat("g3seg2 restart signal rc=", rc, "restartcode=", fvsGetRestartcode(), "\n")
  aa <- fvsAddActivity(year, "base_thindbh", c(0.0, 999.0, prop, 0.0, 0.0))
  cat("g3seg2 fvsAddActivity base_thindbh(prop=", prop, ") rc=", aa, "\n")
  repeat { rc <- fvsRun(); if (rc != 0) break }
  cat("g3seg2 done rc=", rc, "\n")
}

# t0: multi-stand bundle, NO cuts -- the targeting baseline.
run_t0 <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=t.key")
  repeat { rc <- fvsRun(); if (rc != 0) break }
  cat("t0 done rc=", rc, "\n")
}

# t1: multi-stand bundle, cut ONLY the stand whose ID contains `target`.
# Pause each stand at stop point 2 of `year`, check fvsGetStandIDs, cut if it
# is the target -- proving per-stand selective harvest within one bundle.
run_t1 <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=t.key")
  cut_done <- character(0)             # cut each stand at most once
  repeat {
    rc <- fvsRun(2, year)
    if (rc == 2) break
    if (rc != 0) { cat("t1 unexpected rc=", rc, "\n"); break }
    sid <- paste(unlist(fvsGetStandIDs()), collapse = " ")
    if (nzchar(target) && grepl(target, sid, fixed = TRUE) && !(sid %in% cut_done)) {
      cc <- fvsCutNow(prop)
      cut_done <- c(cut_done, sid)
      cat("t1 CUT   stand [", sid, "] rc=", cc, "\n")
    } else {
      cat("t1 pass  stand [", sid, "]\n")
    }
  }
  cat("t1 done rc=", rc, "\n")
}

switch(arm,
  g1     = run_g1(),
  g2     = run_g2(),
  g3seg1 = run_g3seg1(),
  g3seg2 = run_g3seg2(),
  t0     = run_t0(),
  t1     = run_t1(),
  stop(paste("unknown arm:", arm))
)
