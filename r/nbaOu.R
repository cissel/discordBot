# nbaOu.R by JHCV

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
  
  geom_line(aes(x = gp$total_pts,
                y = ouEcdf),
            color = "white") +
  
  myTheme

ggplotly(cdf)

#####

##### Player Level #####

pbp <- load_nba_pbp() |> 
  
  group_by(game_id, 
           home_team_abbrev, 
           away_team_abbrev) |>
  
  nest()

scoringPlays <- data.frame()

for (i in 1:nrow(pbp)) {
  
  pbp$data[[i]] <- pbp$data[[i]] |>
    
    filter(scoring_play == TRUE) |>
    
    group_by(team_id,
             home_team_id,
             home_team_name,
             away_team_id,
             away_team_name,
             athlete_id_1,
             athlete_name$PlayerIndex$PLAYER_SLUG) |>
    
    summarize("totPts" = sum(score_value))
  
  pbp$data[[i]]$athlete_name <- ""
  
  for (j in 1:nrow(pbp$data[[i]])) {
    
    pbp$data[[i]]$athlete_name[[j]] <- nba_playerindex(player_id = pbp$data[[i]]$athlete_id_1[[j]])
    
  }
  
}

#####

