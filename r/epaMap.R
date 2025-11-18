# nflEpa.R by JHCV

##### Required packages #####

library(tidyverse)
library(nflfastR)
library(ggimage)
library(ggthemes)

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

##### Load pbp & summarize data #####

nfl25 <- load_pbp()

o25 <- nfl25 |>
  
  group_by(posteam) |>
  
  summarize(off = mean(epa, na.rm = TRUE))

d25 <- nfl25 |>
  
  group_by(defteam) |>
  
  summarize(def = mean(epa, na.rm = TRUE))

epa25 <- o25 |> 
  
  left_join(d25, by = c("posteam" = "defteam")) 

epa25 <- epa25 |> dplyr::rename(team = posteam)

epa25 <- epa25 |> left_join(teams_colors_logos,
                            by = c("team" = "team_abbr"))

#####

##### Plot data #####

ep <- ggplot(epa25,
             aes(x = def,
                 y = off)) +
  
  geom_vline(aes(xintercept = mean(def)),
             color = "white",
             alpha = .5) +
  
  geom_hline(aes(yintercept = mean(off)),
             color = "white",
             alpha = .5) +
  
  geom_abline(aes(slope = -1,
                  intercept = 0),
              color = "white",
              alpha = .5) +
  
  geom_image(aes(image = team_logo_wikipedia),
             stat = "identity") +
  
  #geom_text(aes(label = team),
  #          color = "white") +
  
  labs(x = "Defense",
       y = "Offense",
       caption = "JHCV",
       subtitle = today(),
       title = "NFL Mean EPA per Play") +
  
  scale_x_reverse() +
  
  myTheme

ggsave("outputs/sports/nfl/epaMap.png",
       plot = ep,
       width = 10,
       height = 10,
       dpi = 300,
       bg = "transparent")

#####