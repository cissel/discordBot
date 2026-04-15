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
                 plot.title = element_text(color = "white", hjust = .5),
                 plot.subtitle = element_text(color = "white", hjust = .5),
                 strip.background = element_rect(fill = "#02233F"),
                 strip.text = element_text(color = "white"))
#####
##### Legend appearance theme #####
myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))
#####
setwd("/Users/jamescissel/discordBot")

today <- Sys.Date()
yesterday <- today - 1

safe_scoreboardv3 <- function(date) {
  tryCatch(
    nba_scoreboardv3(game_date = date),
    error = function(e) { message("No v3 data for ", date, ": ", e$message); NULL }
  )
}

safe_scoreboardv2 <- function(date) {
  tryCatch(
    nba_scoreboardv2(game_date = date)$LineScore,
    error = function(e) { message("No v2 data for ", date, ": ", e$message); NULL }
  )
}

gt_list <- list(safe_scoreboardv3(yesterday), safe_scoreboardv3(today))
gt_list <- Filter(Negate(is.null), gt_list)

sb_list <- list(safe_scoreboardv2(yesterday), safe_scoreboardv2(today))
sb_list <- Filter(Negate(is.null), sb_list)

if (length(gt_list) == 0 || length(sb_list) == 0) {
  message("No NBA scoreboard data available. Writing empty CSV.")
  write_csv(data.frame(), "outputs/sports/nba/liveScoreboard.csv")
  quit(save = "no", status = 0)
}

gt <- bind_rows(gt_list) |>
  select(game_id, game_code, game_status, game_status_text)

sb <- bind_rows(sb_list) |>
  select(GAME_DATE_EST, GAME_ID, TEAM_ID, TEAM_ABBREVIATION,
         TEAM_CITY_NAME, TEAM_NAME, PTS) |>
  drop_na() |>
  rename(game_id = GAME_ID) |>
  mutate(game_id = as.character(game_id),
         team_name = paste(TEAM_CITY_NAME, TEAM_NAME, sep = " "))

df <- sb %>% left_join(gt, by = "game_id")
write_csv(df, "outputs/sports/nba/liveScoreboard.csv")