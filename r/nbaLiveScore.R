# nbaLiveScore.R by JHCV

##### Required Packages #####

library(tidyverse)
library(plotly)
library(hoopR)

#####

##### Plot Appearance Theme #####

myTheme <- theme(legend.position = "none",
                 plot.background = element_rect(fill = "#02233F"),
                 panel.background = element_rect(fill = "#02233F"),
                 panel.grid = element_line(color = "#274066"),
                 axis.ticks = element_line(color = "#274066"),
                 axis.text = element_text(color = "white"),
                 axis.title = element_text(color = "white"),
                 plot.title = element_text(color = "white",
                                           hjust = .5),
                 plot.subtitle = element_text(color = "white",
                                              hjust = .5),
                 strip.background = element_rect(fill = "#02233F"),
                 strip.text = element_text(color = "white"))

#####

##### Legend appearance theme #####

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))#,
#legend.key.height = unit(100, "cm"))

#####

setwd("/Users/jamescissel/discordBot")

# Get today's and yesterday's dates
today <- Sys.Date()
yesterday <- today - 1

# Combine results from both days
gt <- bind_rows(nba_scoreboardv3(game_date = yesterday),
                nba_scoreboardv3(game_date = today)) |>
  
  select(game_id, 
         game_code, 
         game_status, 
         game_status_text)

sb <- bind_rows(nba_scoreboardv2(game_date = yesterday)$LineScore,
                nba_scoreboardv2(game_date = today)$LineScore) |>
  
  select(GAME_DATE_EST, 
         GAME_ID, 
         TEAM_ID, 
         TEAM_ABBREVIATION, 
         TEAM_CITY_NAME,
         TEAM_NAME,
         PTS) |>
  
  drop_na() |>
  
  rename(game_id = GAME_ID) |>
  
  mutate(game_id = as.character(game_id),
         team_name = paste(TEAM_CITY_NAME, 
                           TEAM_NAME,
                           sep = " "))

# Join to form the final scoreboard
df <- sb %>%
  left_join(gt, by = "game_id")

# Save to CSV
write_csv(df, "outputs/sports/nba/liveScoreboard.csv")