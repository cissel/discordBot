# ==============================================================================
# fetchHistoricalGameLogs.R
# Pulls 2024 + 2025 batter and pitcher game logs from Fangraphs via baseballr.
# Uses the same scoring and column schema as playerScore.R so the CSVs can
# be concatenated directly with the 2026 logs for multi-year model training.
#
# Output:
#   outputs/sports/mlb/fantasy/playerData/historical/batter_game_logs_YEAR.csv
#   outputs/sports/mlb/fantasy/playerData/historical/pitcher_game_logs_YEAR.csv
#
# Usage: Rscript fetchHistoricalGameLogs.R [2024] [2025]
#   Default: pulls both 2024 and 2025 if no args given.
# ==============================================================================

library(baseballr)
library(dplyr)
library(purrr)
library(readr)

RATE_LIMIT <- 1.0
OUTPUT_DIR <- "~/discordBot/outputs/sports/mlb/fantasy/playerData/historical"
dir.create(path.expand(OUTPUT_DIR), recursive = TRUE, showWarnings = FALSE)

# ── scoring (identical to playerScore.R) ──────────────────────────────────────
batting_pts <- function(df) {
  df %>% mutate(
    TB          = `1B` + 2*`2B` + 3*`3B` + 4*HR,
    fantasy_pts = R*1 + TB*1 + RBI*1 + BB*1 + K*(-1) + SB*1
  )
}
pitching_pts <- function(df) {
  df %>% mutate(
    fantasy_pts = IP*3 + H*(-1) + ER*(-2) + BB*(-1) +
      K*1 + W*2 + L*(-2) + SV*5 + HD*2
  )
}

# ── player pool per season ─────────────────────────────────────────────────────
pull_season_pool <- function(season) {
  message("\n>>> Pulling player pool for ", season, "...")

  batter_positions <- list(
    C    = "c",
    `1B` = "1b",
    `2B` = "2b",
    `3B` = "3b",
    SS   = "ss",
    OF   = "of"
  )

  top_batters <- imap_dfr(batter_positions, function(pos_code, fantasy_pos) {
    message("  Pulling batters: ", fantasy_pos)
    Sys.sleep(RATE_LIMIT)
    tryCatch({
      df <- fg_batter_leaders(
        startseason = as.character(season),
        endseason   = as.character(season),
        pos         = pos_code,
        lg          = "all",
        qual        = "0",
        ind         = "1"
      )
      if (is.null(df) || nrow(df) == 0) return(NULL)
      df %>%
        arrange(desc(WAR)) %>%
        slice_head(n = if (fantasy_pos == "OF") 90 else 30) %>%
        transmute(
          PlayerName       = PlayerName,
          playerid         = playerid,
          mlbam_id         = xMLBAMID,
          fantasy_position = fantasy_pos,
          WAR
        )
    }, error = function(e) { message("  Error: ", e$message); NULL })
  }) %>% distinct(playerid, .keep_all = TRUE)

  message("  Pulling pitchers...")
  Sys.sleep(RATE_LIMIT)
  fg_pitchers <- tryCatch(
    fg_pitcher_leaders(startseason = as.character(season), endseason = as.character(season),
                       lg = "all", qual = "0", ind = "1"),
    error = function(e) { message("  Pitcher leader error: ", e$message); NULL }
  )

  top_sp <- top_rp <- tibble()
  if (!is.null(fg_pitchers) && nrow(fg_pitchers) > 0) {
    top_sp <- fg_pitchers %>%
      filter(!is.na(GS) & GS >= 1) %>%
      arrange(desc(WAR)) %>%
      slice_head(n = 250) %>%
      transmute(PlayerName, playerid, mlbam_id = xMLBAMID, fantasy_position = "SP", WAR)
    top_rp <- fg_pitchers %>%
      filter(is.na(GS) | GS == 0) %>%
      arrange(desc(WAR)) %>%
      slice_head(n = 50) %>%
      transmute(PlayerName, playerid, mlbam_id = xMLBAMID, fantasy_position = "RP", WAR)
  }

  list(batters = top_batters, sp = top_sp, rp = top_rp)
}

# ── game log fetchers ──────────────────────────────────────────────────────────
fetch_batter_log <- function(playerid, player_name, fantasy_pos, season) {
  Sys.sleep(RATE_LIMIT)
  tryCatch({
    log <- fg_batter_game_logs(playerid = playerid, year = season)
    if (is.null(log) || nrow(log) == 0) return(NULL)
    log %>%
      transmute(
        player_name      = player_name,
        playerid         = playerid,
        fantasy_position = fantasy_pos,
        season           = season,
        game_date        = as.Date(Date),
        team             = Team,
        opponent         = Opp,
        AB, H,
        `1B`, `2B`, `3B`, HR,
        R, RBI, BB,
        K  = SO,
        SB, PA,
        BatOrder = as.integer(BatOrder),
        wOBA       = as.numeric(wOBA),
        ISO        = as.numeric(ISO),
        BABIP      = as.numeric(BABIP),
        BB_pct     = as.numeric(`BB%`),
        K_pct      = as.numeric(`K%`),
        Hard_pct   = as.numeric(`Hard%`),
        Barrel_pct = as.numeric(`Barrel%`),
        GB_pct     = as.numeric(`GB%`),
        LD_pct     = as.numeric(`LD%`),
        FB_pct     = as.numeric(`FB%`),
        SwStr_pct  = as.numeric(`SwStr%`),
        xwOBA      = as.numeric(xwOBA),
        EV         = as.numeric(EV),
        maxEV      = as.numeric(maxEV),
        Barrels    = as.numeric(Barrels)
      ) %>%
      batting_pts()
  }, error = function(e) { message("  [SKIP] ", player_name, ": ", e$message); NULL })
}

fetch_pitcher_log <- function(playerid, player_name, fantasy_pos, season) {
  Sys.sleep(RATE_LIMIT)
  tryCatch({
    log <- fg_pitcher_game_logs(playerid = playerid, year = season)
    if (is.null(log) || nrow(log) == 0) return(NULL)
    log %>%
      transmute(
        player_name      = player_name,
        playerid         = playerid,
        fantasy_position = fantasy_pos,
        season           = season,
        game_date        = as.Date(Date),
        team             = Team,
        opponent         = Opp,
        home_away        = HomeAway,
        IP, H, ER, BB,
        K   = SO,
        W, L, SV,
        HD  = HLD,
        ERA, WHIP
      ) %>%
      pitching_pts()
  }, error = function(e) { message("  [SKIP] ", player_name, ": ", e$message); NULL })
}

# ── main loop ──────────────────────────────────────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
seasons <- if (length(args) > 0) as.integer(args) else c(2024L, 2025L)

for (season in seasons) {
  batter_out  <- path.expand(file.path(OUTPUT_DIR, paste0("batter_game_logs_",  season, ".csv")))
  pitcher_out <- path.expand(file.path(OUTPUT_DIR, paste0("pitcher_game_logs_", season, ".csv")))

  if (file.exists(batter_out) && file.exists(pitcher_out)) {
    bat_rows  <- nrow(read_csv(batter_out,  show_col_types = FALSE))
    ptch_rows <- nrow(read_csv(pitcher_out, show_col_types = FALSE))
    message("\n>>> Season ", season, " already fetched (",
            bat_rows, " batter rows, ", ptch_rows, " pitcher rows) - skipping.")
    message("    Delete the CSVs to re-fetch.")
    next
  }

  message("\n", strrep("=", 60))
  message(">>> Processing season: ", season)
  message(strrep("=", 60))

  pool <- pull_season_pool(season)

  # Batch fetch batters
  message("\n>>> Fetching batter game logs for ", nrow(pool$batters), " players...")
  batter_logs <- vector("list", nrow(pool$batters))
  for (i in seq_len(nrow(pool$batters))) {
    row <- pool$batters[i, ]
    if (i %% 20 == 0) message("  Progress: ", i, "/", nrow(pool$batters))
    batter_logs[[i]] <- fetch_batter_log(row$playerid, row$PlayerName, row$fantasy_position, season)
  }
  batter_logs <- bind_rows(batter_logs)

  # Batch fetch pitchers
  all_pitchers <- bind_rows(pool$sp, pool$rp)
  message("\n>>> Fetching pitcher game logs for ", nrow(all_pitchers), " players...")
  pitcher_logs <- vector("list", nrow(all_pitchers))
  for (i in seq_len(nrow(all_pitchers))) {
    row <- all_pitchers[i, ]
    if (i %% 20 == 0) message("  Progress: ", i, "/", nrow(all_pitchers))
    pitcher_logs[[i]] <- fetch_pitcher_log(row$playerid, row$PlayerName, row$fantasy_position, season)
  }
  pitcher_logs <- bind_rows(pitcher_logs)

  write_csv(batter_logs,  batter_out)
  write_csv(pitcher_logs, pitcher_out)
  message("\nSaved: ", batter_out,  " (", nrow(batter_logs),  " rows)")
  message("Saved: ", pitcher_out, " (", nrow(pitcher_logs), " rows)")
}

message("\n>>> All done. Historical logs saved to: ", OUTPUT_DIR)
