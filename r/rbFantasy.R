# rbFantasy.R by JHCV

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

##### Legend appearance theme #####

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))#,
#legend.key.height = unit(100, "cm"))

#####

# Load play by play for entire NFL szn
nfl24 <- load_pbp(2024)

rbdf <- nfl24 |>
  
  subset(season_type == "REG") |>
  
  subset(play_type == "run") |>
  
  group_by(rusher_player_id,
           game_id) |>
  
  summarize(name = max(rusher_player_name),
            team = max(posteam),
            carries = sum(rush_attempt),
            yards = sum(yards_gained),
            TDs = sum(touchdown)) |>
  
  arrange(-yards) 

rbdf$fantasy <- 0

for (i in 1:nrow(rbdf)) {
  
  rbdf$fantasy[i] <- ((rbdf$yards[i]*.1)+(rbdf$TDs[i]*6))
  
}

wrdf <- nfl24 |> 
  
  subset(season_type == "REG") |>
  
  subset(play_type == "pass") |>
  
  group_by(receiver_player_id,
           game_id) |>
  
  summarize(name = max(receiver_player_name),
            team = max(posteam),
            catches = sum(complete_pass),
            total_yards = sum(yards_gained),
            TDs = sum(touchdown)) |>
  
  arrange(-catches)

wrdf$fantasy <- 0

for (i in 1:nrow(wrdf)) {
  
  wrdf$fantasy[i] <- (wrdf$catches[i]+(wrdf$total_yards[i]*.1)+(wrdf$TDs[i]*6))
  
}

wrdf <- wrdf |> arrange(-fantasy)

# keep rushing fantasy separate so you can see what gets added
rbdf$rush_fantasy <- rbdf$fantasy
rbdf$rec_fantasy  <- 0  # will fill from wrdf

# For each RB row, scan wrdf for same player+game and add receiving fantasy
for (i in seq_len(nrow(rbdf))) {
  pid_i  <- rbdf$rusher_player_id[i]
  game_i <- rbdf$game_id[i]
  
  # Search wrdf for a row with the same player and game
  for (j in seq_len(nrow(wrdf))) {
    if (!is.na(wrdf$receiver_player_id[j]) &&
        wrdf$receiver_player_id[j] == pid_i &&
        wrdf$game_id[j] == game_i) {
      
      # add receiving fantasy to this RB
      rbdf$rec_fantasy[i] <- wrdf$fantasy[j]
      rbdf$fantasy[i]     <- rbdf$fantasy[i] + wrdf$fantasy[j]
      
      # there should only be one wrdf row per player+game after your group_by()
      break
    }
  }
}

# (Optional) peek at the result
#rbdf[order(-rbdf$fantasy), c("rusher_player_id","game_id","name","team",
#                             "carries","yards","TDs","rush_fantasy","rec_fantasy","fantasy")][1:10, ]

rbfdf <- rbdf |>
  
  group_by(rusher_player_id) |>
  
  summarize(name = max(name),
            team = max(team),
            pts = sum(fantasy),
            mean = mean(fantasy),
            sd = sd(fantasy),
            sharpe = mean(fantasy)/sd(fantasy)) |>
  
  arrange(-pts) |>
  
  head(100)

rbfp <- ggplot(rbfdf,
               aes(x = sd,
                   y = mean,
                   size = pts,
                   color = sharpe)) +
  
  geom_text(aes(label = name)) +
  
  #scale_x_log10()+
  #scale_y_log10()+
  
  scale_color_gradient("low" = "red",
                       "high" = "green") +
  
  myTheme

ggplotly(rbfp)
