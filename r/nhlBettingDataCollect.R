# nhlBettingDataCollect.R by JHCV
#
# Pulls NHL schedules + final scores + ESPN odds for the requested season(s).
#
# Unlike hoopR's load_nba_schedule(), fastRhockey's load_nhl_schedules()
# returns NHL.com's own game_id format (e.g. 2025030416), which does NOT
# match the ESPN event id espn_nhl_game_odds() needs. So instead this script
# loops espn_nhl_team_schedule(team_id, season) over all 32 teams (which
# already returns ESPN event ids + final scores + dates in one call) and
# dedupes on game_id, since each game appears once per participating team.
#
# Writes:
#   - per-season snapshot:  outputs/sports/nhl/betting/nhl_odds_<season>.csv
#   - append-only master log: outputs/sports/nhl/betting/nhl_odds_log.csv
#
# Usage:
#   Rscript nhlBettingDataCollect.R 2026          # one season (2025-26)
#   Rscript nhlBettingDataCollect.R 2026 2027      # multiple seasons
#
# Season numbering: season = ending year (2026 == 2025-26 season).
#
# NOTE: ESPN's odds endpoint only has meaningful historical depth back to
# roughly the 2024-25 season (verified live Oct 2026, same as the NBA
# collector) - older seasons will mostly come back empty.

suppressMessages({
  library(fastRhockey)
  library(dplyr)
})

args <- commandArgs(trailingOnly = TRUE)
seasons <- if (length(args) > 0) as.integer(args) else as.integer(format(Sys.Date(), "%Y"))

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nhl/betting")
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)
MASTER_LOG <- file.path(OUT_DIR, "nhl_odds_log.csv")

scraped_at <- format(Sys.time(), tz = "UTC", usetz = TRUE)

# Incremental mode: skip game_ids already captured in the master log so a
# recurring (e.g. daily) cron run only pays the per-game odds-fetch cost for
# games that finished since the last run, not the whole season every time.
already_logged <- if (file.exists(MASTER_LOG)) {
  tryCatch(unique(read.csv(MASTER_LOG)$event_id), error = function(e) integer(0))
} else integer(0)

fetch_odds_safe <- function(game_id) {
  tryCatch({
    o <- espn_nhl_game_odds(event_id = as.character(game_id))
    if (is.null(o) || nrow(o) == 0) return(NULL)
    o
  }, error = function(e) NULL)
}

for (szn in seasons) {

  cat(sprintf("\n=== NHL season %d: pulling team schedules ===\n", szn))

  standings <- tryCatch(espn_nhl_standings(season = szn), error = function(e) {
    cat("  standings pull failed:", conditionMessage(e), "\n")
    NULL
  })
  if (is.null(standings)) next

  team_ids <- unique(standings$team_id)
  cat(sprintf("  %d teams\n", length(team_ids)))

  all_games <- list()
  for (tid in team_ids) {
    ts <- tryCatch(espn_nhl_team_schedule(team_id = tid, season = szn),
                    error = function(e) NULL)
    if (!is.null(ts) && nrow(ts) > 0) all_games[[length(all_games) + 1]] <- ts
    Sys.sleep(0.1)
  }

  if (length(all_games) == 0) {
    # espn_nhl_team_schedule() has a known fastRhockey bug for in-progress
    # seasons (fails internally combining a double home_score for played
    # games with a character placeholder for unplayed ones, for every
    # team) - fall back to espn_nhl_scoreboard(dates=...) day-by-day from
    # the season's known start date, which doesn't hit that bug.
    cat("  per-team schedule pull failed for every team (current-season fastRhockey bug) - falling back to day-by-day scoreboard\n")
    season_start <- as.Date(sprintf("%d-10-01", szn - 1))
    days <- seq(season_start, Sys.Date(), by = "day")
    day_games <- list()
    for (dt in as.list(days)) {
      dstr <- format(dt, "%Y%m%d")
      sb <- tryCatch(espn_nhl_scoreboard(dates = dstr), error = function(e) NULL)
      if (!is.null(sb) && nrow(sb) > 0) day_games[[length(day_games) + 1]] <- sb
      Sys.sleep(0.1)
    }
    if (length(day_games) == 0) { cat("  no schedule data\n"); next }
    sched <- bind_rows(day_games) %>%
      mutate(home_score = as.integer(home_score), away_score = as.integer(away_score)) %>%
      filter(status_type_completed == TRUE, season_type == 2) %>%
      distinct(game_id, .keep_all = TRUE) %>%
      mutate(season_year = szn) %>%
      select(game_id, date, home_abbreviation, away_abbreviation,
             home_score, away_score, season_year)
  } else {
    sched <- bind_rows(all_games) %>%
      filter(status_type_completed == TRUE, season_type == 2) %>%
      distinct(game_id, .keep_all = TRUE) %>%
      select(game_id, date, home_abbreviation, away_abbreviation,
             home_score, away_score, season_year)
  }

  sched <- sched %>% filter(!(game_id %in% already_logged))

  cat(sprintf("  %d unique completed regular-season games not yet logged\n", nrow(sched)))
  if (nrow(sched) == 0) next

  odds_rows <- list()
  n <- nrow(sched)
  for (i in seq_len(n)) {
    gid <- sched$game_id[i]
    o <- fetch_odds_safe(gid)
    if (!is.null(o)) {
      o$game_date <- sched$date[i]
      o$home_abbreviation <- sched$home_abbreviation[i]
      o$away_abbreviation <- sched$away_abbreviation[i]
      o$home_score <- sched$home_score[i]
      o$away_score <- sched$away_score[i]
      o$season <- sched$season_year[i]
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
  snap_path <- file.path(OUT_DIR, sprintf("nhl_odds_%d.csv", szn))
  write.csv(full_log %>% filter(season == szn), snap_path, row.names = FALSE)
  cat(sprintf("  +%d new odds rows (%d new games) this run -> master log; season snapshot -> %s\n",
              nrow(season_df), length(odds_rows), snap_path))
}

cat("\nDone.\n")
