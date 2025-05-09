# nbaOu.R by JHCV

##### Required Packages #####

library(tidyverse)
library(plotly)
library(hoopR)

#####

##### Pull NBA History #####

gp <- nba_schedule() |>
  
  filter(game_date < Sys.Date()) |>
  
  group_by(game_id, 
           game_date, 
           home_team_name, 
           home_team_tricode, 
           away_team_name,
           away_team_tricode) |>
  
  summarize("home_pts" = sum(home_team_score),
            "away_pts" = sum(away_team_score),
            "total_pts" = sum(home_team_score, away_team_score)) |>
  
  subset(total_pts > 77)

######

##### Total Pts O/U PDF #####

tpd <- density(gp$total_pts) 

pdf <- ggplot(gp,
              aes(x = total_pts)) +
  
  geom_density(color = "white",
               fill = "white",
               alpha = .25) +
  
  geom_vline(aes(xintercept = tpd$x[which.max(tpd$y)]),
             color = "white") +
  
  geom_text(aes(x = tpd$x[which.max(tpd$y)]*1.05,
                y = max(tpd$y)),
            color = "white",
            label = round(tpd$x[which.max(tpd$y)], 2)) +
  
  labs(x = "Total Points",
       y = "Probability",
       title = "NBA Total Score PDF 2025") +
  
  scale_y_continuous(labels = scales::percent) +
  
  myTheme

ggplotly(pdf)

#####

##### Total Points O/U CDF #####

ouEcdf <- ecdf(gp$total_pts)

cdf <- ggplot() +
  
  geom_line(aes(),
            color = "white") +
  
  myTheme

ggplotly(cdf)

#####
