# mlbTomorrow.R

##### Required Packages #####

library(tidyverse)
library(dplyr)
library(lubridate)
library(ggplot2)
library(ggthemes)
library(scales)
library(baseballr)

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
                 plot.caption = element_text(color = "white"),
                 strip.background = element_rect(fill = "#02233F"),
                 strip.text = element_text(color = "white"))

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))

#####

##### Pull Data #####

df <- mlb_schedule(2026) |>
  
  subset(series_description == "Regular Season") |>
  
  subset(date == today()+1) |>
  
  mutate(game_time = format(ymd_hms(game_date), "%H:%M:%S")) |>
  
  select(date,
         game_time,
         teams_away_team_name,
         teams_away_league_record_pct,
         teams_away_score,
         teams_home_team_name,
         teams_home_league_record_pct,
         teams_home_score,
         venue_name,
         status_abstract_game_state)

#####

##### Write .csv #####

dir.create("~/discordBot/outputs/sports/mlb/", recursive = TRUE, showWarnings = FALSE)
write_csv(df, "~/discordBot/outputs/sports/mlb/gamesTomorrow.csv")
print(".csv saved")

#####