# nbaToday.R by JHCV
##### Required Packages #####
library(tidyverse)
library(hoopR)
#####

gt_raw <- tryCatch(
  nba_schedule(),
  error = function(e) { message("No NBA schedule data: ", e$message); NULL }
)

if (is.null(gt_raw)) {
  message("No games today.")
  write_csv(data.frame(time = character(), matchup = character()),
            "/Users/jamescissel/discordBot/outputs/sports/nba/gamesToday.csv")
  quit(save = "no", status = 0)
}

# Safely select only columns that actually exist
desired_cols <- c("game_id", "game_status_text", "arena_name",
                  "home_team_city", "home_team_name", "home_team_tricode",
                  "home_team_wins", "home_team_losses", "home_team_score",
                  "away_team_city", "away_team_name", "away_team_tricode",
                  "away_team_wins", "away_team_losses", "away_team_score")

available_cols <- intersect(desired_cols, colnames(gt_raw))
missing_cols   <- setdiff(desired_cols, colnames(gt_raw))
if (length(missing_cols) > 0) message("Missing columns: ", paste(missing_cols, collapse = ", "))

gt <- gt_raw |>
  filter(game_date == Sys.Date()) |>
  select(all_of(available_cols))

if (nrow(gt) == 0) {
  message("No games today.")
  write_csv(data.frame(time = character(), matchup = character()),
            "/Users/jamescissel/discordBot/outputs/sports/nba/gamesToday.csv")
  quit(save = "no", status = 0)
}

# Helper: detect if a game is live based on status text (handles both numeric codes and strings)
is_live <- function(status) {
  status_str <- as.character(status)
  status_str == "2" |
    grepl("^Q[1-4]", status_str) |        # "Q3 4:32"
    grepl("^[0-9]+:[0-9]+", status_str) & grepl("Half|OT", status_str) |
    grepl("Half", status_str, ignore.case = TRUE) |
    grepl("^OT", status_str, ignore.case = TRUE)
}

is_final <- function(status) {
  status_str <- as.character(status)
  status_str == "3" | grepl("^Final", status_str, ignore.case = TRUE)
}

gtc <- data.frame(time = character(), matchup = character(), stringsAsFactors = FALSE)

for (i in 1:nrow(gt)) {
  game_time <- paste(gt$game_status_text[i], "@", gt$arena_name[i])
  
  home_team <- paste0(gt$home_team_city[i], " ", gt$home_team_name[i],
                      " (", gt$home_team_wins[i], "-", gt$home_team_losses[i], ")")
  away_team <- paste0(gt$away_team_city[i], " ", gt$away_team_name[i],
                      " (", gt$away_team_wins[i], "-", gt$away_team_losses[i], ")")
  
  matchup <- paste0(away_team, " @ ", home_team)
  
  # Show score if game is live OR final
  has_scores <- !is.na(gt$home_team_score[i]) && !is.na(gt$away_team_score[i]) &&
    gt$home_team_score[i] != "" && gt$away_team_score[i] != ""
  
  if (has_scores && (is_live(gt$game_status_text[i]) || is_final(gt$game_status_text[i]))) {
    score_text <- paste0(" — Score: ", gt$away_team_score[i], "-", gt$home_team_score[i])
    matchup <- paste0(matchup, score_text)
  }
  
  gtc <- rbind(gtc, data.frame(time = game_time, matchup = matchup))
}

print(gtc)
write_csv(gtc, "/Users/jamescissel/discordBot/outputs/sports/nba/gamesToday.csv")
print(".csv saved")