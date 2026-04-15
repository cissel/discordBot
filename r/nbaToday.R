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

gt <- gt_raw |>
  filter(game_date == Sys.Date()) |>
  select(game_id, game_status_text, arena_name,
         home_team_city, home_team_name, home_team_tricode, home_team_wins, home_team_losses, home_team_score,
         away_team_city, away_team_name, away_team_tricode, away_team_wins, away_team_losses, away_team_score)

if (nrow(gt) == 0) {
  message("No games today.")
  write_csv(data.frame(time = character(), matchup = character()),
            "/Users/jamescissel/discordBot/outputs/sports/nba/gamesToday.csv")
  quit(save = "no", status = 0)
}

gtc <- data.frame(time = character(), matchup = character(), stringsAsFactors = FALSE)

for (i in 1:nrow(gt)) {
  game_time <- paste(gt$game_status_text[i], "@", gt$arena_name[i], sep = " ")
  
  home_team <- paste0(gt$home_team_city[i], " ", gt$home_team_name[i],
                      " (", gt$home_team_wins[i], "-", gt$home_team_losses[i], ")")
  away_team <- paste0(gt$away_team_city[i], " ", gt$away_team_name[i],
                      " (", gt$away_team_wins[i], "-", gt$away_team_losses[i], ")")
  
  matchup <- paste0(away_team, " @ ", home_team)
  
  # Fixed: was gt$game_status_text == 2 (missing [i])
  if (!is.na(gt$home_team_score[i]) && !is.na(gt$away_team_score[i]) &&
      gt$game_status_text[i] == 2) {
    score_text <- paste0(" — Score: ", gt$away_team_score[i], "-", gt$home_team_score[i])
    matchup <- paste0(matchup, score_text)
  }
  
  gtc <- rbind(gtc, data.frame(time = game_time, matchup = matchup))
}

print(gtc)
write_csv(gtc, "/Users/jamescissel/discordBot/outputs/sports/nba/gamesToday.csv")
print(".csv saved")