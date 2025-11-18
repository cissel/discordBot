# wrFantasy.R by JHCV

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
nfl25 <- load_pbp(2025)

wrdf <- nfl25 |> 
  
  subset(season_type == "REG") |>
  
  subset(play_type == "pass" & complete_pass == 1) |>
  
  group_by(receiver_player_id,
           game_id) |>
  
  summarize(name = max(receiver_player_name),
            team = max(posteam),
            targets = sum(pass_attempt),
            catches = sum(complete_pass),
            drops = sum(incomplete_pass),
            total_yards = sum(yards_gained),
            TDs = sum(touchdown)) |>
  
  arrange(-catches)

wrdf$fantasy <- 0

for (i in 1:nrow(wrdf)) {
  
  wrdf$fantasy[i] <- (wrdf$catches[i]+(wrdf$total_yards[i]*.1)+(wrdf$TDs[i]*6))
  
}

wrdf <- wrdf |> arrange(-fantasy)

wrfdf <- wrdf |>
  
  group_by(receiver_player_id) |>
  
  summarize(name = max(name),
            team = max(team),
            pts = sum(fantasy),
            mean = mean(fantasy),
            sd = sd(fantasy),
            sharpe = mean(fantasy)/sd(fantasy)) |>
  
  arrange(-pts) |>
  
  head(100)

wrfp <- ggplot(wrfdf,
               aes(x = sd,
                   y = mean,
                   size = pts,
                   color = sharpe)) +
  
  geom_text(aes(label = name)) +
  
  labs(x="Standard Deviation",
       y="Mean",
       title = paste("NFL Receiving Fantasy Points 2025")) +
  
  scale_color_gradient("low" = "red",
                       "high" = "green") +
  
  myTheme

ggplotly(wrfp)
