# ==============================================================================
# Fantasy Baseball - Full Player Pool Game Log Puller
# Pulls: Top 30 per position (C, 1B, 2B, 3B, SS, OF)
#        Top 100 SP, Top 50 RP
# League scoring: World Sillies (ESPN)
# ==============================================================================

# install.packages(c("baseballr", "dplyr", "purrr", "readr"))
library(baseballr)
library(dplyr)
library(purrr)
library(readr)

# ------------------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------------------

SEASON     <- 2026
RATE_LIMIT <- 1.0
OUTPUT_DIR <- "~/discordBot/outputs/sports/mlb/fantasy/playerData"
dir.create(OUTPUT_DIR, recursive = TRUE, showWarnings = FALSE)

# ------------------------------------------------------------------------------
# SCORING
# fg_batter_game_logs columns: R, RBI, BB, SO, SB, 2B, 3B, HR, H
# fg_pitcher_game_logs columns: IP, H, ER, BB, SO, W, L, SV, HLD
# ------------------------------------------------------------------------------

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

# ------------------------------------------------------------------------------
# STEP 1: Pull leaderboard per position (pos= filters server-side)
# Keep both playerid (FG) and xMLBAMID for reference
# ------------------------------------------------------------------------------

pull_position_leaderboard <- function(pos_code, fantasy_pos, n) {
  message("  Pulling: ", fantasy_pos)
  Sys.sleep(RATE_LIMIT)
  tryCatch({
    df <- fg_batter_leaders(
      startseason = as.character(SEASON),
      endseason   = as.character(SEASON),
      pos         = pos_code,
      lg          = "all",
      qual        = "0",
      ind         = "1"
    )
    if (is.null(df) || nrow(df) == 0) {
      message("    No data for ", fantasy_pos); return(NULL)
    }
    df %>%
      arrange(desc(WAR)) %>%
      slice_head(n = n) %>%
      transmute(
        PlayerName       = PlayerName,
        playerid         = playerid,      # FG id — used by game log functions
        mlbam_id         = xMLBAMID,      # MLBAM id — for reference
        fantasy_position = fantasy_pos,
        PA, WAR
      )
  }, error = function(e) {
    message("    Error for ", fantasy_pos, ": ", e$message); NULL
  })
}

BATTER_POSITIONS <- list(
  C    = "c",
  `1B` = "1b",
  `2B` = "2b",
  `3B` = "3b",
  SS   = "ss",
  OF   = "of"   # 3 OF slots per team -> top 90
)

message("\n>>> Pulling batter leaderboards by position...")
top_batters <- imap_dfr(BATTER_POSITIONS, function(pos_code, fantasy_pos) {
  pull_position_leaderboard(pos_code, fantasy_pos, if (fantasy_pos == "OF") 90 else 30)
}) %>%
  distinct(playerid, .keep_all = TRUE)

message("  Total unique batters: ", nrow(top_batters))

# ------------------------------------------------------------------------------
# STEP 2: Pitchers
# ------------------------------------------------------------------------------

message("\n>>> Pulling pitcher leaderboard...")
fg_pitchers <- fg_pitcher_leaders(
  startseason = as.character(SEASON),
  endseason   = as.character(SEASON),
  lg          = "all",
  qual        = "0",
  ind         = "1"
)

top_sp <- fg_pitchers %>%
  filter(!is.na(GS) & GS >= 1) %>%
  arrange(desc(WAR)) %>%
  slice_head(n = 100) %>%
  transmute(PlayerName, playerid, mlbam_id = xMLBAMID,
            fantasy_position = "SP", GS, G, WAR)

top_rp <- fg_pitchers %>%
  filter(is.na(GS) | GS == 0) %>%
  arrange(desc(WAR)) %>%
  slice_head(n = 50) %>%
  transmute(PlayerName, playerid, mlbam_id = xMLBAMID,
            fantasy_position = "RP", GS, G, WAR)

message("  SP: ", nrow(top_sp), " | RP: ", nrow(top_rp))

# Save player pool
player_pool <- bind_rows(
  top_batters %>% select(PlayerName, playerid, mlbam_id, fantasy_position, WAR),
  top_sp      %>% select(PlayerName, playerid, mlbam_id, fantasy_position, WAR),
  top_rp      %>% select(PlayerName, playerid, mlbam_id, fantasy_position, WAR)
)
write_csv(player_pool, file.path(OUTPUT_DIR, "player_pool.csv"))
message("Saved player_pool.csv (", nrow(player_pool), " total players)")

# ------------------------------------------------------------------------------
# STEP 3: Game log fetchers
# Uses fg_batter_game_logs(playerid, year) and fg_pitcher_game_logs(playerid, year)
# Both take FG playerid, NOT mlbam_id
# ------------------------------------------------------------------------------

fetch_batter_log <- function(playerid, player_name, fantasy_pos) {
  Sys.sleep(RATE_LIMIT)
  tryCatch({
    log <- fg_batter_game_logs(playerid = playerid, year = SEASON)
    if (is.null(log) || nrow(log) == 0) return(NULL)
    
    log %>%
      transmute(
        player_name      = player_name,
        playerid         = playerid,
        fantasy_position = fantasy_pos,
        game_date        = as.Date(Date),
        team             = Team,
        opponent         = Opp,
        AB, H,
        `1B`, `2B`, `3B`, HR,
        R, RBI, BB,
        K  = SO,
        SB
      ) %>%
      batting_pts()
  }, error = function(e) {
    message("  [SKIP] ", player_name, ": ", e$message); NULL
  })
}

fetch_pitcher_log <- function(playerid, player_name, fantasy_pos) {
  Sys.sleep(RATE_LIMIT)
  tryCatch({
    log <- fg_pitcher_game_logs(playerid = playerid, year = SEASON)
    if (is.null(log) || nrow(log) == 0) return(NULL)
    
    log %>%
      transmute(
        player_name      = player_name,
        playerid         = playerid,
        fantasy_position = fantasy_pos,
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
  }, error = function(e) {
    message("  [SKIP] ", player_name, ": ", e$message); NULL
  })
}

# ------------------------------------------------------------------------------
# STEP 4: Batch fetch with progress
# ------------------------------------------------------------------------------

batch_fetch <- function(player_df, fetch_fn, label) {
  n <- nrow(player_df)
  message("\n>>> Fetching ", label, " game logs for ", n, " players...")
  results <- vector("list", n)
  for (i in seq_len(n)) {
    row <- player_df[i, ]
    if (i %% 10 == 0) message("  Progress: ", i, "/", n)
    results[[i]] <- fetch_fn(row$playerid, row$PlayerName, row$fantasy_position)
  }
  bind_rows(results)
}

batter_logs  <- batch_fetch(top_batters, fetch_batter_log,  "Batters")
sp_logs      <- batch_fetch(top_sp,      fetch_pitcher_log, "SP")
rp_logs      <- batch_fetch(top_rp,      fetch_pitcher_log, "RP")
pitcher_logs <- bind_rows(sp_logs, rp_logs)

# ------------------------------------------------------------------------------
# STEP 5: Save game logs
# ------------------------------------------------------------------------------

write_csv(batter_logs,  file.path(OUTPUT_DIR, "batter_game_logs.csv"))
write_csv(pitcher_logs, file.path(OUTPUT_DIR, "pitcher_game_logs.csv"))
message("\nSaved batter_game_logs.csv  (", nrow(batter_logs),  " rows)")
message("Saved pitcher_game_logs.csv (", nrow(pitcher_logs), " rows)")

# ------------------------------------------------------------------------------
# STEP 6: Season summaries
# ------------------------------------------------------------------------------

batter_summary <- batter_logs %>%
  group_by(player_name, playerid, fantasy_position) %>%
  summarise(
    games = n(),
    across(c(AB, H, `2B`, `3B`, HR, R, RBI, BB, K, SB, TB, fantasy_pts),
           \(x) sum(x, na.rm = TRUE)),
    avg_pts_per_game = round(fantasy_pts / games, 2),
    .groups = "drop"
  ) %>%
  arrange(desc(fantasy_pts))

pitcher_summary <- if (nrow(pitcher_logs) > 0) {
  pitcher_logs %>%
    group_by(player_name, playerid, fantasy_position) %>%
    summarise(
      games = n(),
      across(c(IP, H, ER, BB, K, W, L, SV, HD, fantasy_pts),
             \(x) sum(x, na.rm = TRUE)),
      avg_pts_per_game = round(fantasy_pts / games, 2),
      ERA  = round((ER * 9) / IP, 2),
      WHIP = round((H + BB) / IP, 3),
      .groups = "drop"
    ) %>%
    arrange(desc(fantasy_pts))
} else {
  message("WARNING: pitcher_logs is empty — check pitcher fetch errors above")
  tibble()
}

write_csv(batter_summary,  file.path(OUTPUT_DIR, "batter_season_summary.csv"))
write_csv(pitcher_summary, file.path(OUTPUT_DIR, "pitcher_season_summary.csv"))

# ------------------------------------------------------------------------------
# QUICK PEEK
# ------------------------------------------------------------------------------

cat("\n===== TOP 10 BATTERS BY FANTASY PTS =====\n")
print(batter_summary %>%
        select(player_name, fantasy_position, games, fantasy_pts, avg_pts_per_game) %>%
        head(10))

cat("\n===== TOP 10 PITCHERS BY FANTASY PTS =====\n")
print(pitcher_summary %>%
        select(player_name, fantasy_position, games, fantasy_pts, avg_pts_per_game) %>%
        head(10))

cat("\n===== OUTPUT FILES =====\n")
for (f in c("player_pool.csv", "batter_game_logs.csv", "pitcher_game_logs.csv",
            "batter_season_summary.csv", "pitcher_season_summary.csv")) {
  cat("  ", file.path(OUTPUT_DIR, f), "\n")
}