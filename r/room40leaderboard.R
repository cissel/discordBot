# room40leaderboard.R by JHCV

##### Required packages #####

library(tidyverse)
library(lubridate)
library(plotly)
library(timetk)
library(fflr)
library(ggimage)
library(ggthemes)
library(nflfastR)
library(httr)
library(rvest)
library(wdman)
library(tidytext)
library(jsonlite)

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

#####

##### Legend Appearance Theme #####

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))#,
#legend.key.height = unit(100, "cm"))

#####

setwd("~/discordBot")

##### Base URL #####

baseUrl <- "https://api.sleeper.app/v1/league/1259616442014244864/"

#####

##### Pull current NFL state #####

cnsUrl <- "https://api.sleeper.app/v1/state/nfl"

sdf <- GET(url = cnsUrl) |>
  
  content(as = "text", 
          encoding = "UTF-8") |>
  
  fromJSON()

#####

##### Import player names & metadata #####

path <- "outputs/sports/nfl/players.json"

# read raw text
txt <- readChar(path, file.info(path)$size)

# 1) parse OUTER container (it's a one-element array)
outer <- jsonlite::fromJSON(txt, simplifyVector = FALSE)

# 2) get the INNER JSON string
if (is.list(outer) && length(outer) == 1 && is.character(outer[[1]])) {
  inner_txt <- outer[[1]]
} else if (is.character(outer)) {
  inner_txt <- outer[1]
} else {
  stop("Unexpected top-level shape.")
}

# 3) parse the INNER object (the real players dict keyed by player_id)
players_list <- jsonlite::fromJSON(inner_txt, simplifyVector = FALSE)

# 4) turn into a nested dataframe like before
players <- tibble::enframe(players_list, name = "player_id", value = "data")

#####

##### Pull league users #####

uUrl <- paste(baseUrl,
              "users",
              sep = "")

udf <- GET(url = uUrl) |>
  
  content(as = "text",
          encoding = "UTF-8") |>
  
  fromJSON() |>
  
  unnest()

#####

##### Pull rosters #####

rUrl <- paste(baseUrl,
              "rosters",
              sep = "")

rdf <- GET(url = rUrl) |>
  
  content(as = "text",
          encoding = "UTF-8") |>
  
  fromJSON()

#####

##### Merge dfs #####

leaderboard <- udf |>
  
  left_join(rdf,
            by = c("user_id" = "owner_id")) |>
  
  select(display_name,
         team_name,
         settings.y) |>
  
  unnest(settings.y) |>
  
  arrange(-fpts)

for (i in 1:nrow(leaderboard)) {
  
  if (is.na(leaderboard$team_name[i])) {
    
    leaderboard$team_name[i] <- leaderboard$display_name[i]
    
  }
  
}

leaderboard <- leaderboard |>
  
  group_by(wins) |>
  
  arrange(-fpts) |>
  
  ungroup() |>
  
  mutate("accuracy" = paste(round((fpts/ppts)*100, 
                                  2),
                            "%",
                            sep="")) |>
  
  select(display_name,
         team_name,
         wins,
         losses,
         fpts,
         fpts_against,
         ppts,
         accuracy,
         waiver_position,
         total_moves) |>
  
  arrange(-wins)

write_csv(leaderboard,
          "outputs/sports/nfl/room40leaderboard.csv")

#####
