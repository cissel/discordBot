#!/usr/bin/env Rscript
# fetchNflSnapShare.R
# Player-season snap share and target share (offense only) from nflverse.
# Purpose: distinguish "starter getting real volume" from "name-value backup"
# for the fantasy projection model - depth chart position matters more than
# raw talent for weekly fantasy floor/ceiling.
#
# Usage: Rscript fetchNflSnapShare.R [start_year] [end_year]
# Default: 2018 2024 (snap count data coverage starts ~2012 but PFR-sourced
# snap data is most reliable from ~2016 on; player_stats has receiving targets
# for the target-share half)
suppressMessages({
  library(nflreadr)
  library(dplyr)
})

args <- commandArgs(trailingOnly = TRUE)
start_yr <- if (length(args) >= 1) as.integer(args[1]) else 2018
end_yr   <- if (length(args) >= 2) as.integer(args[2]) else 2024

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)

cat(sprintf("Loading snap counts %d-%d ...\n", start_yr, end_yr))
snaps <- load_snap_counts(start_yr:end_yr)
cat("Snap rows:", nrow(snaps), "\n")

cat(sprintf("Loading player stats (weekly) %d-%d ...\n", start_yr, end_yr))
stats <- load_player_stats(seasons = start_yr:end_yr)
cat("Player-week rows:", nrow(stats), "\n")

# Season-level snap share: mean offense_pct across weeks played (only weeks
# with any offensive snaps - exclude bye weeks / inactive weeks which would
# drag the average down misleadingly toward 0)
snap_season <- snaps %>%
  filter(offense_snaps > 0) %>%
  group_by(player, pfr_player_id, team, season, position) %>%
  summarise(
    games_with_snaps = n(),
    snap_share_avg    = mean(offense_pct, na.rm = TRUE),
    snap_share_max    = max(offense_pct, na.rm = TRUE),
    .groups = "drop"
  )

# Target share: already computed per-week by nflreadr (target_share, air_yards_share
# columns) - aggregate to season level as a targets-weighted average so a player's
# season target share reflects games where they actually played more.
# NOTE: compute the weighted numerator/denominator as single expressions rather than
# referencing `targets` after it's already been re-bound to its own sum() in this same
# summarise() call - dplyr evaluates sequentially, so a later `targets` reference would
# silently resolve to the aggregated scalar, not the per-week vector (this bit us once).
player_targets <- stats %>%
  filter(!is.na(team)) %>%
  group_by(player_display_name, player_id, team, season, position) %>%
  summarise(
    .ts_num           = sum(target_share * targets, na.rm = TRUE),
    .ays_num          = sum(air_yards_share * targets, na.rm = TRUE),
    .w_denom          = sum(targets, na.rm = TRUE),
    targets           = sum(targets, na.rm = TRUE),
    receptions        = sum(receptions, na.rm = TRUE),
    rec_yards         = sum(receiving_yards, na.rm = TRUE),
    air_yards         = sum(receiving_air_yards, na.rm = TRUE),
    rush_attempts     = sum(carries, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  mutate(
    # 0 targets -> 0 target share (well-defined, not "unknown"). Using NA here
    # (as a naive 0/0 division would) wrongly caused dropna()-based training
    # pipelines to exclude QBs and other low-target-volume players entirely -
    # discovered when Phase 2 QB training set was suspiciously small (48 rows
    # over 5 seasons, ~9-10/year vs ~32 real starting QBs). Fixed Jul 29 2026.
    target_share    = ifelse(.w_denom > 0, .ts_num / .w_denom, 0),
    air_yards_share = ifelse(.w_denom > 0, .ays_num / .w_denom, 0)
  ) %>%
  select(-.ts_num, -.ays_num, -.w_denom)

out_snap <- file.path(OUT_DIR, "player_snap_share.csv")
out_target <- file.path(OUT_DIR, "player_target_share.csv")
write.csv(snap_season, out_snap, row.names = FALSE)
write.csv(player_targets, out_target, row.names = FALSE)

cat("Wrote:", out_snap, "(", nrow(snap_season), "rows )\n")
cat("Wrote:", out_target, "(", nrow(player_targets), "rows )\n\n")

cat("=== Sample: 2024 top target share, WR/TE, min 5 games ===\n")
sample <- player_targets %>%
  filter(season == 2024, position %in% c("WR", "TE")) %>%
  arrange(desc(target_share)) %>%
  select(player_display_name, team, position, targets, target_share) %>%
  head(8)
print(sample)
