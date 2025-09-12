# myRoste.R by JHCV

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

##### My team #####

myTeam <- udf |>
  
  subset(display_name == "jhcv")

myRoster <- rdf |>
  
  subset(owner_id == myTeam$user_id) |>
  
  unnest_longer(players,
                values_to = "player_id",
                indices_include = FALSE) |>
  
  mutate(player_id = as.character(player_id))

myRoster$isStarter <- 0

for (i in 1:nrow(myRoster)) {
  
  if (myRoster$player_id[i] %in% myRoster$starters[[i]]) {
    
    myRoster$isStarter[i] <- 1
    
  }
  
}

myRoster <- myRoster |>
  
  select(player_id,
         isStarter) |>
  
  left_join(players, by = "player_id") #|>
  
  #unnest_longer(data,
  #              values_to = .,
  #              indices_include = FALSE)

#####

##### Import projections #####

proj <- read.csv(paste("outputs/sports/nfl/fantasy_proj_week",
                       sdf$week,
                       ".csv",
                       sep = ""))

#####

##### Add projections #####

myRoster <- myRoster |>
  
  left_join(proj, by = "player_id")

#####

##### Plot team #####

tp <- ggplot(myRoster,
             aes(x = player_id,
                 y = proj_pts_ppr,
                 fill = ))

#####
