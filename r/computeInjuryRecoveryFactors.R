#!/usr/bin/env Rscript
# computeInjuryRecoveryFactors.R
#
# Standalone, ADP/VORP-independent step: derive an age-informed injury-
# recovery adjustment factor for any 2025-injury-shortened player (lag1_games
# < 12) who has a real pre-injury HEALTHY 2024 season (10+ games) to anchor
# a baseline rate off of. Reusable every season - depends only on
# career_panel.csv + nflreadr::load_injuries(), NOT on vorp_2026.csv, so it
# can run before or after the VORP step with no circular dependency.
#
# Methodology (built 2026-08-18, see fantasy-sports-ml-pipeline skill /
# session history for the underlying study): using nflreadr::load_injuries()
# 2009-2025 (185 qualifying player-seasons: healthy baseline season with
# 10+ games -> injury season with 4+ weeks flagged Out/Doubtful -> return
# season), age at time of injury is the clearest predictor of recovery -
# return-to->=90%-of-baseline rate: <=25yo 49%, 26-28 37%, 29-31 25%, 32+
# 24%. Severity (weeks missed) and injury body-part/type were NOT reliably
# predictive at this sample size (correlations -0.08 and mixed-direction
# respectively) - only the age-bucket median recovery ratio is used here.
#
# adj_factor = (2024 healthy pts/game x age-bucket historical median
# recovery ratio) / (2025 actual pts/game). Clipped to [0.75, 1.30] - a
# directional nudge on top of the model's existing lag1_pts_per_game
# feature, not a standalone re-projection. Players without a usable 2024
# baseline (rookie-year injury, traded/holdout/benched rather than truly
# hurt, etc.) get NO factor (adj_factor = NA -> caller should default to 1.0)
# and should still be sanity-checked via the raw injury_shortened_2025 flag.
#
# Usage: Rscript r/computeInjuryRecoveryFactors.R
# Output: outputs/sports/nfl/fantasy/injury_recovery_factors_2026.csv
#         (norm_name, position keyed - independent of ADP/player_id source)
suppressMessages({
  library(nflreadr); library(dplyr); library(readr); library(stringr); library(stringi)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
INJURY_SEASON <- 2025
BASELINE_SEASON <- 2024
ADJ_FACTOR_CLIP <- c(0.75, 1.30)

norm_name <- function(s) {
  s <- stri_trans_general(s, "Latin-ASCII")
  s <- str_to_lower(s)
  s <- str_remove(s, "\\s+(jr|sr|ii|iii|iv|v)\\.?$")
  s <- str_replace_all(s, "[^a-z0-9 ]", "")
  str_squish(s)
}

career <- read_csv(file.path(OUT_DIR, "career_panel.csv"), show_col_types = FALSE) %>%
  filter(position %in% c("QB", "RB", "WR", "TE")) %>%
  mutate(norm_name = norm_name(player_display_name))

cur <- career %>% filter(season == INJURY_SEASON) %>%
  select(norm_name, position, age_injury_season = age, games_injury_season = games,
         ppg_injury_season = pts_per_game)
base <- career %>% filter(season == BASELINE_SEASON) %>%
  select(norm_name, position, games_baseline = games, ppg_baseline = pts_per_game)

flagged <- cur %>% filter(games_injury_season < 12) %>%
  inner_join(base, by = c("norm_name", "position")) %>%
  filter(games_baseline >= 10, ppg_injury_season > 0)

# 2025 weekly injury reports - only used for the diagnostic injury_class
# column here, not for the adjustment factor itself (severity/type weren't
# reliably predictive - see docstring)
inj <- load_injuries(INJURY_SEASON) %>% filter(report_status %in% c("Out", "Doubtful"))
inj$norm_name <- norm_name(inj$full_name)
inj_summary <- inj %>% group_by(norm_name) %>%
  summarise(weeks_out_or_doubtful = n_distinct(week),
            injury_types = paste(unique(na.omit(report_primary_injury)), collapse = "|"),
            .groups = "drop")

flagged <- flagged %>% left_join(inj_summary, by = "norm_name") %>%
  mutate(
    age_bucket = case_when(
      age_injury_season <= 25 ~ "<=25",
      age_injury_season <= 28 ~ "26-28",
      age_injury_season <= 31 ~ "29-31",
      TRUE ~ "32+"
    ),
    hist_median_recovery_ratio = case_when(
      age_bucket == "<=25" ~ 0.878,
      age_bucket == "26-28" ~ 0.733,
      age_bucket == "29-31" ~ 0.785,
      age_bucket == "32+" ~ 0.618
    ),
    hist_return_rate_90pct = case_when(
      age_bucket == "<=25" ~ 0.49,
      age_bucket == "26-28" ~ 0.37,
      age_bucket == "29-31" ~ 0.25,
      age_bucket == "32+" ~ 0.24
    ),
    injury_class = case_when(
      is.na(injury_types) | injury_types == "" ~ "no weekly report match (games<12 for other reason - trade/holdout/benching/rookie ramp)",
      grepl("Achilles|ACL|Knee|Foot|Ankle", injury_types, ignore.case = TRUE) ~ "structural/lower-body-major",
      grepl("Hamstring|Groin|Quad|Calf", injury_types, ignore.case = TRUE) ~ "soft-tissue",
      grepl("Concussion|Head", injury_types, ignore.case = TRUE) ~ "concussion",
      grepl("Shoulder|Hand|Wrist|Rib|Chest|Back", injury_types, ignore.case = TRUE) ~ "upper-body/back",
      TRUE ~ "other/unlisted"
    ),
    recovery_informed_ppg = ppg_baseline * hist_median_recovery_ratio,
    adj_factor_raw = recovery_informed_ppg / ppg_injury_season,
    adj_factor = pmin(pmax(adj_factor_raw, ADJ_FACTOR_CLIP[1]), ADJ_FACTOR_CLIP[2])
  )

out <- flagged %>%
  select(norm_name, position, age_injury_season, age_bucket, injury_class,
         weeks_out_or_doubtful, games_baseline, ppg_baseline, games_injury_season,
         ppg_injury_season, hist_median_recovery_ratio, hist_return_rate_90pct,
         recovery_informed_ppg, adj_factor_raw, adj_factor)

out_path <- file.path(OUT_DIR, "injury_recovery_factors_2026.csv")
write_csv(out, out_path)
cat("Wrote:", out_path, "(", nrow(out), "players with a usable adjustment factor )\n")
print(out %>% select(norm_name, position, age_bucket, injury_class, adj_factor) %>% arrange(adj_factor), n = 30)
