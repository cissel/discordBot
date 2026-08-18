#!/usr/bin/env Rscript
# fetchNflDstVolatility.R
#
# 2025 weekly fantasy scoring mean/volatility for TEAM DEFENSE/SPECIAL TEAMS
# (Sleeper roster slot "DEF") - the DEF-position counterpart to
# buildNflWeeklyVolatility.R (QB/RB/WR/TE) and fetchNflKickerVolatility.R (K).
# Requested by James: the moneyball pipeline deliberately scoped out K/DEF
# from day one (see buildNflVorp.py docstring), but he wants at minimum a
# mean-vs-volatility view for both.
#
# Source: nflreadr::load_team_stats() week-level table - a play-by-play
# rollup nflverse already provides at the team-game level (sacks, INTs,
# fumble recoveries, defensive/return TDs, blocked kicks). No need to hand-rebuild
# this straight from load_pbp() - that table already IS the pbp aggregation for
# exactly the defensive categories Sleeper scores. Points allowed comes from
# load_schedules() (final score by team, not in the team_stats table).
#
# Room 40 DEF scoring (pulled live from Sleeper league API, league_id
# 1259616442014244864, scoring_settings endpoint, Aug 2026):
#   sack=1  int=2  fum_rec=2  safe=2  blk_kick=2
#   def_td=6  def_st_td=6 (return/non-offensive TDs - see note below)
#   pts_allow_0=10  pts_allow_1_6=7  pts_allow_7_13=4  pts_allow_14_20=1
#   pts_allow_21_27=0  pts_allow_28_34=-1  pts_allow_35p=-4
#
# TD CATEGORY NOTE: Sleeper's league scoring settings list four separate raw
# fields that can score a defensive/ST touchdown for the DEF roster slot:
#   def_td (turnover return, e.g. a pick-six or fumble-return TD)
#   def_st_td / st_td (kick/punt return TD)
#   fum_rec_td (recovered fumble advanced for a TD)
# All four pay the same 6.0 points in this league's settings, so rather than
# trying to exactly recreate Sleeper's internal event taxonomy from nflverse's
# team_stats columns (which don't cleanly separate all four), this script
# sums nflverse's own def_tds + special_teams_tds + fumble_recovery_tds and
# credits each at 6 pts. This can occasionally double count a single play if
# nflverse independently flags it under two of those three columns (e.g. a
# fumble return TD showing in both def_tds and fumble_recovery_tds) - a
# TODO-caveat surfaced explicitly below and in the printed diagnostics, not
# silently glossed over. In practice these overlaps are rare (single digits
# of plays per season across the whole league).
#
# BLOCKED KICK NOTE: Sleeper's blk_kick=2 rewards the DEFENSE that blocked a
# kick, which is the OPPONENT of the team whose fg_blocked/pat_blocked/
# pt_blocked count went up in team_stats (that stat lives on the KICKING
# team's row, credited against them, not the blocking team). This script
# self-joins team_stats to its own opponent_team row per game to move the
# blocked-kick credit to the correct (blocking) team - verified against the
# 2025 blocked-kick play log (34 blocked plays across the season) before
# writing final output.
#
# CAVEATS:
#   - Single season (2025) only, same small-sample caveat as the other two
#     volatility scripts - low games_played inflates/deflates Sharpe.
#   - Points-allowed bucket is by that DEFENSE's own score allowed to the
#     opponent OFFENSE - special teams/defensive TDs scored BY the opponent
#     against this defense still count in points allowed (matches how
#     fantasy platforms actually score DEF units, not a bug).
#   - Bye weeks simply don't produce a team_stats row - games_played
#     naturally reflects only games actually played.
#
# Usage: Rscript r/fetchNflDstVolatility.R
# Output: outputs/sports/nfl/fantasy/weekly_volatility_dst_2025.csv
suppressMessages({
  library(nflreadr)
  library(dplyr)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

MIN_GAMES_FOR_SHARPE <- 3  # same threshold as the other two volatility scripts

pts_allowed_bucket <- function(pts) {
  case_when(
    pts == 0            ~ 10,
    pts >= 1  & pts <= 6  ~ 7,
    pts >= 7  & pts <= 13 ~ 4,
    pts >= 14 & pts <= 20 ~ 1,
    pts >= 21 & pts <= 27 ~ 0,
    pts >= 28 & pts <= 34 ~ -1,
    pts >= 35             ~ -4,
    TRUE                  ~ NA_real_
  )
}

cat("Fetching 2025 weekly team stats...\n")
ts <- load_team_stats(seasons = 2025, summary_level = "week")
ts <- ts[ts$season_type == "REG", ]
cat("  ->", nrow(ts), "team-week rows\n")

cat("Fetching 2025 schedule for points-allowed...\n")
sched <- load_schedules(seasons = 2025)
sched <- sched[sched$game_type == "REG", ]

# Points allowed = opponent's score in that game (i.e. the score THIS team's
# defense gave up), independent of home/away.
pts_allow_long <- bind_rows(
  sched %>% transmute(week, team = home_team, opponent_team = away_team, pts_allowed = away_score),
  sched %>% transmute(week, team = away_team, opponent_team = home_team, pts_allowed = home_score)
)

ts <- ts %>%
  left_join(pts_allow_long, by = c("week", "team", "opponent_team"))

# Self-join to move blocked-kick credit to the BLOCKING team (see docstring
# note above - nflverse's fg_blocked/pat_blocked/pt_blocked live on the
# kicking team's row, but Sleeper's blk_kick scores the blocking defense).
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
      coalesce(pts_allow_bucket <- pts_allowed_bucket(pts_allowed), 0)
  )

vol <- ts %>%
  group_by(team) %>%
  summarise(
    games_played = n(),
    mean_pts     = mean(room40_pts, na.rm = TRUE),
    sd_pts       = sd(room40_pts, na.rm = TRUE),
    total_tds_credited = sum(td_credit, na.rm = TRUE),
    total_blocked_kicks_credited = sum(opp_blocked_kicks, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  mutate(
    sd_pts = coalesce(sd_pts, 0),
    sharpe_pts = ifelse(sd_pts > 1e-6 & games_played >= MIN_GAMES_FOR_SHARPE,
                         mean_pts / sd_pts, NA_real_),
    low_sample_flag = games_played < MIN_GAMES_FOR_SHARPE
  ) %>%
  arrange(desc(mean_pts))

cat("\nDEF/ST units with computed stats:", nrow(vol), "\n")
cat("Low-sample (<", MIN_GAMES_FOR_SHARPE, "games) flagged:", sum(vol$low_sample_flag), "\n")
cat("Total blocked-kick credits assigned league-wide:", sum(vol$total_blocked_kicks_credited), "\n")

out_path <- file.path(OUT_DIR, "weekly_volatility_dst_2025.csv")
write.csv(vol, out_path, row.names = FALSE)
cat("\nWrote:", out_path, "\n")

cat("\nTop 10 by mean_pts:\n")
print(head(vol[, c("team","games_played","mean_pts","sd_pts","sharpe_pts")], 10))
