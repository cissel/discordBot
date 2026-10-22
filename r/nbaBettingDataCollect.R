# nbaBettingDataCollect.R by JHCV
#
# Pulls NBA schedules + final scores (hoopR load_nba_schedule, ESPN-sourced
# bulk loader) for the requested season(s), then pulls ESPN odds per
# completed regular-season game (espn_nba_game_odds) and writes:
#   - a per-season snapshot:  outputs/sports/nba/betting/nba_odds_<season>.csv
#   - an append-only master log: outputs/sports/nba/betting/nba_odds_log.csv
#     (never overwritten; re-running is safe, de-duped on game_id+scrapedAt)
#
# Usage:
#   Rscript nbaBettingDataCollect.R 2026          # one season (2025-26)
#   Rscript nbaBettingDataCollect.R 2026 2027      # multiple seasons
#
# Season numbering follows hoopR convention: season = ending year, so
# 2026 == the 2025-26 season, 2027 == the 2026-27 (current) season.
#
# NOTE: ESPN's odds endpoint only has meaningful historical depth back to
# roughly the 2024-25 season (verified live Oct 2026) - older seasons will
# return mostly-empty odds tibbles. Scores/schedule go back decades
# regardless, so the schedule side is never the limiting factor.

suppressMessages({
  library(hoopR)
  library(dplyr)
  library(purrr)
})

args <- commandArgs(trailingOnly = TRUE)
seasons <- if (length(args) > 0) as.integer(args) else as.integer(format(Sys.Date(), "%Y"))

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nba/betting")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)
MASTER_LOG <- file.path(OUT_DIR, "nba_odds_log.csv")

scraped_at <- format(Sys.time(), tz = "UTC", usetz = TRUE)

# Incremental mode: skip game_ids already captured in the master log so a
# recurring (e.g. daily) cron run only pays the per-game odds-fetch cost for
# games that finished since the last run, not the whole season every time.
already_logged <- if (file.exists(MASTER_LOG)) {
  tryCatch(unique(read.csv(MASTER_LOG)$event_id), error = function(e) integer(0))
} else integer(0)

fetch_odds_safe <- function(game_id) {
  tryCatch({
    o <- espn_nba_game_odds(event_id = as.character(game_id))
    if (is.null(o) || nrow(o) == 0) return(NULL)
    o
  }, error = function(e) NULL)
}

for (szn in seasons) {

  cat(sprintf("\n=== NBA season %d: pulling schedule ===\n", szn))

  sched <- tryCatch(load_nba_schedule(seasons = szn), error = function(e) {
    cat("  schedule pull failed:", conditionMessage(e), "\n")
    NULL
  })

  reg <- NULL
  if (!is.null(sched) && nrow(sched) > 0 && any(sched$status_type_completed, na.rm = TRUE)) {
    reg <- sched %>%
      filter(season_type == 2, status_type_completed == TRUE) %>%
      select(game_id, game_date, season,
             home_abbreviation, away_abbreviation,
             home_score, away_score)
  } else {
    # hoopR's bulk load_nba_schedule() data repo lags the live season by
    # days-to-weeks and comes back with zero completed games for the
    # in-progress season - fall back to live per-team ESPN schedules
    # (same technique nhlBettingDataCollect.R uses) instead.
    cat("  bulk schedule loader has no completed games yet (current-season lag) - falling back to live per-team ESPN schedules\n")
    teams <- tryCatch(espn_nba_teams(), error = function(e) NULL)
    if (is.null(teams)) next
    all_games <- list()
    for (tid in unique(teams$team_id)) {
      ts <- tryCatch(espn_nba_team_schedule(team_id = tid, season = szn),
                      error = function(e) NULL)
      if (!is.null(ts) && nrow(ts) > 0) all_games[[length(all_games) + 1]] <- ts
      Sys.sleep(0.1)
    }
    if (length(all_games) == 0) { cat("  no live schedule data either\n"); next }
    # espn_nba_team_schedule() is team-centric (team_score/opponent_score/
    # home_away/result), unlike the game-centric bulk loader - reshape to
    # the same home/away/game_id shape used elsewhere in this script.
    raw <- bind_rows(all_games) %>%
      filter(!is.na(result), result != "", season_type == 2) %>%
      mutate(
        home_score = ifelse(home_away == "home", team_score, opponent_score),
        away_score = ifelse(home_away == "home", opponent_score, team_score)
      )
    reg <- raw %>%
      filter(home_away == "home") %>%
      distinct(event_id, .keep_all = TRUE) %>%
      mutate(season = szn,
             home_abbreviation = NA_character_,
             away_abbreviation = opponent_abbrev) %>%
      select(game_id = event_id, game_date = date, season,
             home_abbreviation, away_abbreviation,
             home_score, away_score)
  }

  reg <- reg %>% filter(!(game_id %in% already_logged))

  cat(sprintf("  %d completed regular-season games not yet logged\n", nrow(reg)))
  if (nrow(reg) == 0) next

  odds_rows <- list()
  n <- nrow(reg)
  for (i in seq_len(n)) {
    gid <- reg$game_id[i]
    o <- fetch_odds_safe(gid)
    if (!is.null(o)) {
      o$game_date <- as.character(reg$game_date[i])
      o$home_abbreviation <- reg$home_abbreviation[i]
      o$away_abbreviation <- reg$away_abbreviation[i]
      o$home_score <- reg$home_score[i]
      o$away_score <- reg$away_score[i]
      o$season <- reg$season[i]
      o$scrapedAt <- scraped_at
      odds_rows[[length(odds_rows) + 1]] <- o
    }
    if (i %% 50 == 0) cat(sprintf("  ...%d/%d games checked, %d with odds so far\n",
                                   i, n, length(odds_rows)))
    Sys.sleep(0.15)
  }

  if (length(odds_rows) == 0) {
    cat("  no odds rows retrieved for this season (too old for ESPN's odds history)\n")
    next
  }

  season_df <- bind_rows(odds_rows)
  write.table(season_df, MASTER_LOG, sep = ",", row.names = FALSE,
              col.names = !file.exists(MASTER_LOG), append = file.exists(MASTER_LOG))

  # Snapshot = full cumulative master log filtered to this season (not just
  # this run's new rows), so the per-season CSV always reflects everything
  # collected to date even on incremental runs.
  full_log <- read.csv(MASTER_LOG)
  snap_path <- file.path(OUT_DIR, sprintf("nba_odds_%d.csv", szn))
  write.csv(full_log %>% filter(season == szn), snap_path, row.names = FALSE)
  cat(sprintf("  +%d new odds rows (%d new games) this run -> master log; season snapshot -> %s\n",
              nrow(season_df), length(odds_rows), snap_path))
}

cat("\nDone.\n")
