#!/usr/bin/env Rscript
# nflTeamRosterRisk.R
#
# Companion to python/nflTeamRosterRisk.py for the /nfl teamrisk Discord
# command. Takes one Room 40 team's full roster CSV (every rostered player,
# any position) and plots a mean-vs-volatility "fantasy Sharpe" scatter,
# same visual language as James's nflFreeAgentRisk.R / MLB fantasyRisk.R /
# SPY Sharpe work:
#   x = standard deviation of fantasy points per game (volatility)
#   y = mean fantasy points per game
#   color = position (QB/RB/WR/TE/K/DEF - fixed palette, James's standard)
#   size = total fantasy points scored this season
#
# Scoring formulas are Room 40's actual live Sleeper league scoring_settings
# (verified Oct 2026), reusing the same constants already established in
# nflFreeAgentRisk.R / buildNflWeeklyVolatility.R / fetchNflKickerVolatility.R
# / fetchNflDstVolatility.R - kept in sync with those scripts; if Room 40's
# scoring settings ever change, update all five together.
#
# CAVEATS (surfaced in the plot subtitle, not just here):
#   - Current season only, in-progress - early-season sample sizes are small
#     and sd/Sharpe on <3 games is mostly noise, not signal (flagged via a
#     low-opacity point, not hidden).
#   - Only rostered players with >=1 game played this season appear
#     (rookies/newly-added players who haven't played yet have no row).
#   - Roster snapshot reflects the moment nflTeamRosterRisk.py was last run
#     (immediately before this script in the Discord command), not
#     necessarily "this instant" if a trade/waiver claim processed since.
#
# Usage: Rscript r/nflTeamRosterRisk.R <roster_csv> <out_png> <team_name> [season]
suppressPackageStartupMessages({
  library(nflreadr)
  library(dplyr)
  library(ggplot2)
  library(ggrepel)
  library(readr)
})

args      <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) stop("Usage: nflTeamRosterRisk.R <roster_csv> <out_png> <team_name> [season]")
roster_csv <- args[1]
out_png    <- path.expand(args[2])
team_name  <- args[3]
season     <- if (length(args) >= 4 && nzchar(args[4])) as.integer(args[4]) else as.integer(format(Sys.Date(), "%Y"))

MIN_GAMES_FOR_FULL_OPACITY <- 3L  # same threshold as the free-agent/volatility scripts

# James's standard position color palette (navy-theme family)
POS_COLORS <- c(QB = "#7B4CE0", RB = "#2E9E4E", WR = "#2E7BE0", TE = "#E0A02E",
                 K = "#C9C9C9", DEF = "#E05C5C")

roster <- read_csv(roster_csv, show_col_types = FALSE,
                    col_types = cols(gsis_id = col_character(), sleeper_id = col_character()))
cat("Rostered players loaded:", nrow(roster), "for team:", team_name, "\n")
if (nrow(roster) == 0) stop("Roster CSV is empty - check nflTeamRosterRisk.py ran first.")

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

skill_ids <- roster %>% filter(position %in% c("QB", "RB", "WR", "TE"), !is.na(gsis_id), gsis_id != "")
k_ids     <- roster %>% filter(position == "K")
dst_teams <- roster %>% filter(position == "DEF")

stats_list <- list()

if (nrow(skill_ids) > 0) {
  cat("Fetching", season, "weekly player stats (skill positions)...\n")
  ps <- load_player_stats(seasons = season, summary_level = "week") %>%
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

  skill_stats <- ps %>%
    inner_join(skill_ids %>% select(gsis_id), by = c("player_id" = "gsis_id")) %>%
    group_by(player_id, player_display_name, position) %>%
    summarise(
      games_played = n(),
      total_pts    = sum(room40_pts, na.rm = TRUE),
      mean_pts     = mean(room40_pts, na.rm = TRUE),
      sd_pts       = sd(room40_pts, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    rename(label = player_display_name)
  stats_list[["skill"]] <- skill_stats
}

if (nrow(k_ids) > 0) {
  cat("Fetching", season, "weekly player stats (kickers)...\n")
  ps_k <- load_player_stats(seasons = season, summary_level = "week") %>%
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

  k_stats <- ps_k %>%
    inner_join(k_ids %>% select(gsis_id), by = c("player_id" = "gsis_id")) %>%
    group_by(player_id, player_display_name, position) %>%
    summarise(
      games_played = n(),
      total_pts    = sum(room40_pts, na.rm = TRUE),
      mean_pts     = mean(room40_pts, na.rm = TRUE),
      sd_pts       = sd(room40_pts, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    rename(label = player_display_name)
  stats_list[["k"]] <- k_stats
}

if (nrow(dst_teams) > 0) {
  pts_allowed_bucket <- function(pts) {
    case_when(
      pts == 0                ~ 10,
      pts >= 1  & pts <= 6     ~ 7,
      pts >= 7  & pts <= 13    ~ 4,
      pts >= 14 & pts <= 20    ~ 1,
      pts >= 21 & pts <= 27    ~ 0,
      pts >= 28 & pts <= 34    ~ -1,
      pts >= 35                ~ -4,
      TRUE                     ~ NA_real_
    )
  }

  cat("Fetching", season, "weekly team stats (D/ST)...\n")
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

  dst_stats <- ts %>%
    inner_join(dst_teams %>% select(team), by = "team") %>%
    group_by(team) %>%
    summarise(
      games_played = n(),
      total_pts    = sum(room40_pts, na.rm = TRUE),
      mean_pts     = mean(room40_pts, na.rm = TRUE),
      sd_pts       = sd(room40_pts, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    rename(player_id = team, label = team) %>%
    mutate(position = "DEF")
  stats_list[["dst"]] <- dst_stats
}

stats <- bind_rows(stats_list)

if (nrow(stats) == 0) {
  stop(paste0("No matching roster players found with any ", season,
              " game data - check that nflTeamRosterRisk.py ran first and gsis_id crosswalk matched."))
}

stats <- stats %>%
  mutate(
    sd_pts         = coalesce(sd_pts, 0),
    low_sample     = games_played < MIN_GAMES_FOR_FULL_OPACITY,
    point_alpha    = ifelse(low_sample, 0.45, 0.9),
    position       = factor(position, levels = names(POS_COLORS))
  ) %>%
  arrange(desc(total_pts))

cat("Roster players plotted:", nrow(stats), "\n")
cat("Low-sample (<", MIN_GAMES_FOR_FULL_OPACITY, "games, faded point):",
    sum(stats$low_sample), "\n")

p <- ggplot(stats, aes(x = sd_pts, y = mean_pts, size = total_pts, color = position, alpha = point_alpha)) +
  geom_point() +
  geom_text_repel(aes(label = label), color = "white", size = 2.8,
                   max.overlaps = 30, box.padding = 0.4, force = 3,
                   segment.color = "#274066") +
  scale_color_manual(values = POS_COLORS, name = "Position", drop = TRUE) +
  scale_size_continuous(name = "Total Pts\n(season)", range = c(2, 10)) +
  scale_alpha_identity() +
  labs(
    x = "Std Dev of Fantasy Points per Game",
    y = "Mean Fantasy Points per Game",
    title = paste0("Room 40 Roster Risk - ", team_name),
    subtitle = paste0(season, " season, ", nrow(stats), " rostered players with game data  |  ",
                       "faded = <", MIN_GAMES_FOR_FULL_OPACITY, " games played (noisy sd)  |  ",
                       "as of ", Sys.Date()),
    caption = "Source: Sleeper API (roster) + nflverse/nflreadr (stats) | JHCV"
  ) +
  myTheme

ggsave(out_png, plot = p, width = 11, height = 7.5, dpi = 150)
cat("Saved:", out_png, "\n")
