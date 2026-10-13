#!/usr/bin/env Rscript
# nflFreeAgentRisk.R
#
# Companion to python/nflFreeAgents.py for the /nfl fantasyfreeagent Discord
# command. Takes a position-scoped free-agent CSV (sleeper_id/gsis_id/team
# list of players NOT currently rostered in Room 40) and plots a
# mean-vs-volatility "fantasy Sharpe" scatter, same visual language as
# James's MLB fantasyRisk.R / mlbERASharpe.R and SPY Sharpe work:
#   x = standard deviation of fantasy points per game (volatility)
#   y = mean fantasy points per game
#   color = Sharpe ratio (mean/sd) -> red (bad) / white (mid) / green (good)
#   size = total fantasy points scored this season
#
# Scoring formulas are Room 40's actual live Sleeper league scoring_settings
# (verified Oct 2026), reusing the same constants already established in
# this repo's buildNflWeeklyVolatility.R (skill positions), fetchNflKicker
# VolatilityR (K) and fetchNflDstVolatility.R (DEF) - kept in sync with
# those three scripts; if Room 40's scoring settings ever change, update all
# four together.
#
# CAVEATS (surfaced in the plot subtitle, not just here):
#   - Current season only, in-progress - early-season sample sizes are small
#     and sd/Sharpe on <3 games is mostly noise, not signal (flagged, not
#     hidden - low-sample players still plot, just without a Sharpe color,
#     i.e. gray/NA, so a 1-game flier doesn't masquerade as "great Sharpe").
#   - Only players with >=1 game played this season appear at all (undrafted
#     rookies who haven't played yet have no row to plot).
#   - Free agent status reflects the roster snapshot at the moment
#     nflFreeAgents.py was last run (immediately before this script in the
#     Discord command), not necessarily "this instant" if waiver claims
#     processed since.
#
# Usage: Rscript r/nflFreeAgentRisk.R <scope> <fa_csv> <out_png> [season] [top_n]
#   scope:   qb | rb | wr | te | flex | k | dst
#   fa_csv:  output of nflFreeAgents.py for that scope
#   out_png: where to save the plot
#   season:  default = current year
#   top_n:   cap plotted players by total fantasy points (default 40, keeps
#            the chart legible - flex alone can have 400+ free agents)
suppressPackageStartupMessages({
  library(nflreadr)
  library(dplyr)
  library(ggplot2)
  library(ggrepel)
  library(readr)
})

args    <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) stop("Usage: nflFreeAgentRisk.R <scope> <fa_csv> <out_png> [season] [top_n]")
scope   <- tolower(args[1])
fa_csv  <- args[2]
out_png <- path.expand(args[3])
season  <- if (length(args) >= 4 && nzchar(args[4])) as.integer(args[4]) else as.integer(format(Sys.Date(), "%Y"))
top_n   <- if (length(args) >= 5 && nzchar(args[5])) as.integer(args[5]) else 40L

MIN_GAMES_FOR_SHARPE <- 3L  # same threshold as the three per-position volatility scripts

SCOPE_LABELS <- c(qb = "QB", rb = "RB", wr = "WR", te = "TE",
                   flex = "FLEX (RB/WR/TE)", k = "K", dst = "DEF/ST")
if (!scope %in% names(SCOPE_LABELS)) stop(paste("Unknown scope:", scope))

fa <- read_csv(fa_csv, show_col_types = FALSE, col_types = cols(gsis_id = col_character()))
cat("Free agent candidates loaded:", nrow(fa), "\n")

# ── navy theme (James's standard - BG #02233F, all text white) ─────────────
myTheme <- theme(
  legend.position   = "right",
  plot.background   = element_rect(fill = "#02233F", color = NA),
  panel.background  = element_rect(fill = "#02233F", color = NA),
  panel.grid        = element_line(color = "#274066"),
  axis.ticks        = element_line(color = "#274066"),
  axis.text         = element_text(color = "white"),
  axis.title        = element_text(color = "white"),
  plot.title        = element_text(color = "white", hjust = .5, face = "bold", size = 15),
  plot.subtitle     = element_text(color = "white", hjust = .5, size = 10),
  plot.caption      = element_text(color = "white", size = 8),
  legend.background = element_rect(fill = "#02233F", color = NA),
  legend.text       = element_text(color = "white"),
  legend.title      = element_text(color = "white")
)

# ── build per-player weekly room40_pts, scope-specific ──────────────────────
if (scope %in% c("qb", "rb", "wr", "te", "flex")) {

  pos_filter <- switch(scope,
    qb = "QB", rb = "RB", wr = "WR", te = "TE",
    flex = c("RB", "WR", "TE")
  )

  cat("Fetching", season, "weekly player stats...\n")
  ps <- load_player_stats(seasons = season, summary_level = "week") %>%
    filter(season_type == "REG", position %in% pos_filter) %>%
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

  stats <- ps %>%
    inner_join(fa %>% select(gsis_id, team_fa = team), by = c("player_id" = "gsis_id")) %>%
    group_by(player_id, player_display_name, position) %>%
    summarise(
      games_played = n(),
      total_pts    = sum(room40_pts, na.rm = TRUE),
      mean_pts     = mean(room40_pts, na.rm = TRUE),
      sd_pts       = sd(room40_pts, na.rm = TRUE),
      .groups = "drop"
    )

} else if (scope == "k") {

  cat("Fetching", season, "weekly player stats (kickers)...\n")
  ps <- load_player_stats(seasons = season, summary_level = "week") %>%
    filter(season_type == "REG", position == "K") %>%
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

  stats <- ps %>%
    inner_join(fa %>% select(gsis_id), by = c("player_id" = "gsis_id")) %>%
    group_by(player_id, player_display_name, position) %>%
    summarise(
      games_played = n(),
      total_pts    = sum(room40_pts, na.rm = TRUE),
      mean_pts     = mean(room40_pts, na.rm = TRUE),
      sd_pts       = sd(room40_pts, na.rm = TRUE),
      .groups = "drop"
    )

} else if (scope == "dst") {

  pts_allowed_bucket <- function(pts) {
    case_when(
      pts == 0               ~ 10,
      pts >= 1  & pts <= 6    ~ 7,
      pts >= 7  & pts <= 13   ~ 4,
      pts >= 14 & pts <= 20   ~ 1,
      pts >= 21 & pts <= 27   ~ 0,
      pts >= 28 & pts <= 34   ~ -1,
      pts >= 35               ~ -4,
      TRUE                     ~ NA_real_
    )
  }

  cat("Fetching", season, "weekly team stats...\n")
  ts <- load_team_stats(seasons = season, summary_level = "week")
  ts <- ts[ts$season_type == "REG", ]

  sched <- load_schedules(seasons = season)
  sched <- sched[sched$game_type == "REG", ]

  pts_allow_long <- bind_rows(
    sched %>% transmute(week, team = home_team, opponent_team = away_team, pts_allowed = away_score),
    sched %>% transmute(week, team = away_team, opponent_team = home_team, pts_allowed = home_score)
  )

  ts <- ts %>% left_join(pts_allow_long, by = c("week", "team", "opponent_team"))

  blocked_by_opp <- ts %>%
    transmute(week, team = opponent_team, opp_blocked_kicks =
                coalesce(fg_blocked, 0) + coalesce(pat_blocked, 0) + coalesce(pt_blocked, 0))

  ts <- ts %>%
    left_join(blocked_by_opp, by = c("week", "team")) %>%
    mutate(opp_blocked_kicks = coalesce(opp_blocked_kicks, 0))

  ts <- ts %>%
    mutate(
      td_credit = coalesce(def_tds, 0) + coalesce(special_teams_tds, 0) + coalesce(fumble_recovery_tds, 0),
      room40_pts =
        coalesce(def_sacks, 0)          * 1 +
        coalesce(def_interceptions, 0)  * 2 +
        (coalesce(fumble_recovery_own, 0) + coalesce(fumble_recovery_opp, 0)) * 2 +
        coalesce(def_safeties, 0)       * 2 +
        opp_blocked_kicks                * 2 +
        td_credit                        * 6 +
        coalesce(pts_allowed_bucket(pts_allowed), 0)
    )

  stats <- ts %>%
    inner_join(fa %>% select(team), by = "team") %>%
    group_by(team) %>%
    summarise(
      games_played = n(),
      total_pts    = sum(room40_pts, na.rm = TRUE),
      mean_pts     = mean(room40_pts, na.rm = TRUE),
      sd_pts       = sd(room40_pts, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    rename(player_display_name = team) %>%
    mutate(position = "DEF")
}

if (nrow(stats) == 0) {
  stop(paste0("No matching ", scope, " free agents found with any game data for season ", season,
              " - check that nflFreeAgents.py ran first and gsis_id crosswalk matched."))
}

stats <- stats %>%
  mutate(
    sd_pts     = coalesce(sd_pts, 0),
    sharpe_pts = ifelse(sd_pts > 1e-6 & games_played >= MIN_GAMES_FOR_SHARPE,
                         mean_pts / sd_pts, NA_real_)
  ) %>%
  arrange(desc(total_pts)) %>%
  slice_max(order_by = total_pts, n = top_n, with_ties = FALSE)

cat("Free agents plotted:", nrow(stats), "\n")
cat("Low-sample (<", MIN_GAMES_FOR_SHARPE, "games, no Sharpe color):",
    sum(is.na(stats$sharpe_pts)), "\n")

midpoint_val <- suppressWarnings(median(stats$sharpe_pts, na.rm = TRUE))
if (!is.finite(midpoint_val)) midpoint_val <- 0

p <- ggplot(stats, aes(x = sd_pts, y = mean_pts, size = total_pts, color = sharpe_pts)) +
  geom_point(alpha = 0.85) +
  geom_text_repel(aes(label = player_display_name), color = "white", size = 2.8,
                   max.overlaps = 30, box.padding = 0.4, force = 3,
                   segment.color = "#274066") +
  scale_color_gradient2(low = "red", mid = "white", high = "#2E9E4E",
                         midpoint = midpoint_val, na.value = "gray60",
                         name = "Sharpe\n(mean/sd)") +
  scale_size_continuous(name = "Total Pts\n(season)", range = c(2, 10)) +
  labs(
    x = "Std Dev of Fantasy Points per Game",
    y = "Mean Fantasy Points per Game",
    title = paste0("Room 40 Free Agents - ", SCOPE_LABELS[[scope]]),
    subtitle = paste0(season, " season, top ", nrow(stats), " by total points  |  ",
                       "min ", MIN_GAMES_FOR_SHARPE, " games for a Sharpe color (gray = too few games)  |  ",
                       "as of ", Sys.Date()),
    caption = "Source: Sleeper API (rosters) + nflverse/nflreadr (stats) | JHCV"
  ) +
  myTheme

ggsave(out_png, plot = p, width = 11, height = 7.5, dpi = 150)
cat("Saved:", out_png, "\n")
