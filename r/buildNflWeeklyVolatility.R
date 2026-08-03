#!/usr/bin/env Rscript
# buildNflWeeklyVolatility.R
#
# Phase 4 extension: per-player consistency metrics from 2025 weekly fantasy
# points, requested by James for the live draft tool alongside ADP dispersion
# - "how many points did this guy score on average, how much did that vary
# week to week, and what's the risk-adjusted return" (same instinct as his
# SPY Sharpe work, applied to weekly fantasy scoring).
#
# Reuses the exact Room 40 scoring formula from fetchNflCareerData.R (same
# constants - keep both in sync if league scoring settings ever change):
#   pass_yd=0.04  pass_td=4   pass_int=-1  pass_2pt=2
#   rush_yd=0.1   rush_td=6   rush_2pt=2
#   rec=1 (PPR)   rec_yd=0.1  rec_td=6     rec_2pt=2
#   fum_lost=-2
#
# METRICS (2025 regular season, weekly):
#   games_played   - weeks with any recorded stat line
#   mean_pts       - average room40_pts per game played
#   sd_pts         - population stdev of room40_pts across games played
#   sharpe_pts     - mean_pts / sd_pts (higher = more weekly-consistent
#                    scorer relative to their own volatility - NOT the same
#                    as "good", a low-mean/low-sd role player can out-Sharpe
#                    a high-mean/high-variance boom/bust player; read
#                    alongside mean_pts, not instead of it)
#
# CAVEATS (be upfront about these in any display of this data):
#   - Single season (2025) only, not multi-year - a small-sample estimate,
#     same statistical-power caveat as any 17-week-max stdev.
#   - Injury-shortened seasons produce artificially low games_played and can
#     inflate/deflate sharpe_pts on very few data points - games_played is
#     included specifically so callers can flag/filter low-sample players
#     rather than trusting a Sharpe computed on 3 games the same as one on 17.
#   - sd_pts of 0 or near-0 (e.g. a player with 1 game played) makes
#     sharpe_pts explode/undefined - guarded to NA below rather than Inf.
#
# Usage: Rscript r/buildNflWeeklyVolatility.R
# Output: outputs/sports/nfl/fantasy/weekly_volatility_2025.csv
suppressMessages({
  library(nflreadr)
  library(dplyr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

MIN_GAMES_FOR_SHARPE <- 3  # fewer than this and sd_pts is mostly noise, not signal

cat("Fetching 2025 weekly player stats...\n")
ps <- load_player_stats(seasons = 2025, summary_level = "week")
cat("  ->", nrow(ps), "player-week rows\n")

ps <- ps %>%
  filter(season_type == "REG", position %in% c("QB", "RB", "WR", "TE")) %>%
  mutate(
    room40_pts =
      coalesce(passing_yards, 0) * 0.04 +
      coalesce(passing_tds, 0) * 4 +
      coalesce(passing_interceptions, 0) * -1 +
      coalesce(passing_2pt_conversions, 0) * 2 +
      coalesce(rushing_yards, 0) * 0.1 +
      coalesce(rushing_tds, 0) * 6 +
      coalesce(rushing_2pt_conversions, 0) * 2 +
      coalesce(receptions, 0) * 1 +
      coalesce(receiving_yards, 0) * 0.1 +
      coalesce(receiving_tds, 0) * 6 +
      coalesce(receiving_2pt_conversions, 0) * 2 +
      coalesce(fumbles_lost_total, 0) * -2
  )

vol <- ps %>%
  group_by(player_id, player_display_name, position) %>%
  summarise(
    games_played = n(),
    mean_pts     = mean(room40_pts, na.rm = TRUE),
    sd_pts       = sd(room40_pts, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  mutate(
    sd_pts = coalesce(sd_pts, 0),
    sharpe_pts = ifelse(sd_pts > 1e-6 & games_played >= MIN_GAMES_FOR_SHARPE,
                         mean_pts / sd_pts, NA_real_),
    low_sample_flag = games_played < MIN_GAMES_FOR_SHARPE
  ) %>%
  arrange(desc(mean_pts))

cat("\nPlayers with computed stats:", nrow(vol), "\n")
cat("Low-sample (<", MIN_GAMES_FOR_SHARPE, "games) flagged:", sum(vol$low_sample_flag), "\n")

out_path <- file.path(OUT_DIR, "weekly_volatility_2025.csv")
write.csv(vol, out_path, row.names = FALSE)
cat("\nWrote:", out_path, "\n")

cat("\nTop 10 by mean_pts:\n")
print(head(vol[, c("player_display_name","position","games_played","mean_pts","sd_pts","sharpe_pts")], 10))

cat("\nTop 10 by sharpe_pts (min", MIN_GAMES_FOR_SHARPE, "games):\n")
print(head(vol %>% filter(!low_sample_flag) %>% arrange(desc(sharpe_pts)) %>%
             select(player_display_name, position, games_played, mean_pts, sd_pts, sharpe_pts), 10))
