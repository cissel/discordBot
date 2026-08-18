#!/usr/bin/env Rscript
# fetchNflKickerVolatility.R
#
# 2025 weekly fantasy scoring mean/volatility for KICKERS - the K-position
# counterpart to buildNflWeeklyVolatility.R (which only covers QB/RB/WR/TE).
# Requested by James: the moneyball pipeline deliberately scoped out K/DEF
# from day one (see buildNflVorp.py docstring - "skill positions" only), but
# he wants at minimum a mean-vs-volatility view for K and DEF/ST too.
#
# Source: nflreadr::load_player_stats() week-level table DOES carry kicker
# stat columns (fg_made_*, fg_missed, pat_made/missed/blocked) - no need to
# touch raw play-by-play for this one, unlike the DST script.
#
# Room 40 K scoring (pulled live from Sleeper league API, league_id
# 1259616442014244864, scoring_settings endpoint, Aug 2026):
#   fgm_0_19=3  fgm_20_29=3  fgm_30_39=3  fgm_40_49=4  fgm_50_59=5  fgm_60p=6
#   fgmiss=-1 (flat penalty regardless of distance - Sleeper has no
#              distance-tiered miss penalty for this league, only make tiers)
#   xpm=1  xpmiss=-1 (extra point miss; blocked PATs folded into this same
#                     bucket below - Sleeper's settings have no separate
#                     "pat_blocked" scoring category, so a blocked PAT is
#                     scored identically to a missed one from the kicker's
#                     side, same convention as most fantasy platforms)
#
# CAVEATS:
#   - Single season (2025), same small-sample caveat as the skill-position
#     volatility script - a kicker with 3 games has a near-meaningless SD.
#   - Kickers get traded/cut more than any other position - games_played
#     for a player who changed teams giage may span multiple teams; the
#     week-level rows are joined by player_id so this doesn't lose games,
#     but the mean will blend performance across different offenses.
#   - Long-snapper/holder injuries etc. that affect kicker performance are
#     not modeled here - this is pure box-score volatility, not a skill
#     signal (same limitation the skill-position script already has).
#
# Usage: Rscript r/fetchNflKickerVolatility.R
# Output: outputs/sports/nfl/fantasy/weekly_volatility_kickers_2025.csv
suppressMessages({
  library(nflreadr)
  library(dplyr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

MIN_GAMES_FOR_SHARPE <- 3  # same threshold as the skill-position script

cat("Fetching 2025 weekly player stats (kickers)...\n")
ps <- load_player_stats(seasons = 2025, summary_level = "week")
k <- ps %>% filter(season_type == "REG", position == "K")
cat("  ->", nrow(k), "kicker player-week rows\n")

k <- k %>%
  mutate(
    xpmiss_total = coalesce(pat_missed, 0) + coalesce(pat_blocked, 0),
    room40_pts =
      coalesce(fg_made_0_19, 0)  * 3 +
      coalesce(fg_made_20_29, 0) * 3 +
      coalesce(fg_made_30_39, 0) * 3 +
      coalesce(fg_made_40_49, 0) * 4 +
      coalesce(fg_made_50_59, 0) * 5 +
      coalesce(fg_made_60_, 0)   * 6 +
      coalesce(fg_missed, 0)     * -1 +
      coalesce(pat_made, 0)      * 1 +
      xpmiss_total               * -1
  )

vol <- k %>%
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

cat("\nKickers with computed stats:", nrow(vol), "\n")
cat("Low-sample (<", MIN_GAMES_FOR_SHARPE, "games) flagged:", sum(vol$low_sample_flag), "\n")

out_path <- file.path(OUT_DIR, "weekly_volatility_kickers_2025.csv")
write.csv(vol, out_path, row.names = FALSE)
cat("\nWrote:", out_path, "\n")

cat("\nTop 10 by mean_pts:\n")
print(head(vol[, c("player_display_name","games_played","mean_pts","sd_pts","sharpe_pts")], 10))
