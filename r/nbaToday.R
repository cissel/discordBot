# nbaToday.R by JHCV

##### Required Packages #####

library(tidyverse)
library(hoopR)

#####

# Pull today's NBA games
gt <- nba_schedule() |>  
  filter(game_date == Sys.Date()) |>  # Filter only today's games
  select(game_id,
         game_status_text,
         game_time_est,  # Get the game time
         home_team_city, home_team_name, home_team_tricode, home_team_wins, home_team_losses,
         away_team_city, away_team_name, away_team_tricode, away_team_wins, away_team_losses)

# Initialize dataframe for matchups
gtc <- data.frame(time = character(),
                  matchup = character(),
                  stringsAsFactors = FALSE)

# Loop through each game and extract matchup details
for (i in 1:nrow(gt)) {
  game_time <- gt$game_status_text[i]  # Extract game time
  
  # Format each team's name with record (W-L)
  home_team <- paste0(gt$home_team_city[i], " ", gt$home_team_name[i], 
                      " (", gt$home_team_wins[i], "-", gt$home_team_losses[i], ")")
  away_team <- paste0(gt$away_team_city[i], " ", gt$away_team_name[i], 
                      " (", gt$away_team_wins[i], "-", gt$away_team_losses[i], ")")
  
  # Format the matchup string
  matchup <- paste0(away_team, " @ ", home_team)  
  
  # Append to the dataframe
  gtc <- rbind(gtc, data.frame(time = game_time, matchup = matchup))
}

# Print the formatted matchups
print(gtc)

write_csv(gtc, "/Users/jamescissel/discordBot/outputs/sports/nba/gamesToday.csv")

print(".csv saved")
