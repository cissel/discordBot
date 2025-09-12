# scorekeepe.R by JHCV

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

##### URLS & Endpoints #####

baseUrl <- "https://api.sleeper.app/v1/league/1259616442014244864/"

#####

##### Pull current NFL state #####

cnsUrl <- "https://api.sleeper.app/v1/state/nfl"

sdf <- GET(url = cnsUrl) |>
  
  content(as = "text", 
          encoding = "UTF-8") |>
  
  fromJSON()

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

##### Pull matchup data from sleeper api #####

mUrl <- "https://api.sleeper.app/v1/league/1259616442014244864/matchups/"

mdf <- GET(url = paste(mUrl,
                       sdf$week,
                       sep = "")) |>
  
  content(as = "text", 
          encoding = "UTF-8") |>
  
  fromJSON()

#####

##### Import projected fantasy points #####

projections <- read_csv("outputs/sports/nfl/sleeper_proj_pts.csv")

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

##### Create Scoreboard #####

scoreboard <- mdf |> 
  
  select(matchup_id, 
         roster_id,
         points)

scoreboard$owner_id <- ""
scoreboard$owner_name <- ""
scoreboard$team_name <- ""

for (i in 1:nrow(scoreboard)) {
  
  for (j in 1:nrow(rdf)) {
    
    if (scoreboard$roster_id[i] == rdf$roster_id[j]) {
      
      scoreboard$owner_id[i] <- rdf$owner_id[j]
      
    }
    
  }
  
  for (j in 1:nrow(udf)) {
    
    if (scoreboard$owner_id[i] == udf$user_id[j]) {
      
      scoreboard$owner_name[i] <- udf$display_name[j]
      
      if (!is.na(udf$team_name[j])) {
        
        scoreboard$team_name[i] <- udf$team_name[j]
        
      } else {
        
        scoreboard$team_name[i] <- scoreboard$owner_name[i]
        
      }
      
    }
    
  }
  
}

# explode starters to one row per player
starters_long <- rdf %>%
  select(roster_id, starters) %>%
  unnest_longer(starters, values_to = "player_id", indices_include = FALSE) %>%
  mutate(player_id = as.character(player_id))

projections2 <- projections %>%
  transmute(player_id = as.character(player_id),
            proj_pts_ppr = as.numeric(pts_ppr))

scoreboard <- scoreboard %>%
  left_join(starters_long, by = "roster_id") %>%
  left_join(projections2, by = "player_id") %>%
  group_by(matchup_id, roster_id, points, owner_id, owner_name, team_name, .add = TRUE) %>%
  summarise(proj_pts = sum(proj_pts_ppr, na.rm = TRUE), .groups = "drop")

#####

##### Plot scoreboard #####

# interactive
sbpi <- ggplot(scoreboard,
              aes(x = matchup_id,
                  weight = points,
                  fill = factor(roster_id),
                  color = factor(owner_id))) +
  
  geom_bar(position = "dodge") +
  
  geom_bar(aes(weight = proj_pts),
           position = "dodge",
           alpha = .25) +
  
  geom_text(aes(label = team_name,
                y = points+5),
            color = "white",
            size = 1.75,
            position = position_dodge(width = .9,
                                      preserve = "total")) +
  
  geom_text(aes(label = points,
                y = points+2),
            color = "white",
            size = 3,
            position = position_dodge(width = .9,
                                      preserve = "total")) +
  
  labs(x = "Team",
       y = "Score",
       title = "Room 40") +
  
  myTheme

#ggplotly(sbpi)

# static
sbp <- ggplot(scoreboard,
              aes(x = matchup_id,
                  weight = points,
                  fill = factor(roster_id),
                  color = factor(owner_id))) +
  
  geom_bar(position = "dodge") +
  
  geom_bar(aes(weight = proj_pts),
           position = "dodge",
           alpha = .25) +
  
  geom_text(aes(label = team_name,
                y = points+4.8),
            color = "white",
            size = 3,
            position = position_dodge(width = .9,
                                      preserve = "total")) +
  
  geom_text(aes(label = points,
                y = points+2),
            color = "white",
            size = 5,
            position = position_dodge(width = .9,
                                      preserve = "total")) +
  
  labs(x = "Team",
       y = "Score",
       caption = "JHCV",
       subtitle = paste(sdf$season,
                        " | Week ",
                        sdf$week,
                        sep = ""), 
       title = "Room 40 Scoreboard") +
  
  theme(axis.text.x = element_blank()) +
  
  myTheme

ggsave("outputs/sports/nfl/fantasyScoreboard.png",
       plot = sbp,
       width = 10,
       height = 8,
       dpi = 300,
       bg = "transparent")

#####