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

player_df <- nba_playerindex()$PlayerIndex |>
  select(PERSON_ID,
         PLAYER_SLUG,
         PLAYER_FIRST_NAME,
         PLAYER_LAST_NAME,
         JERSEY_NUMBER,
         TEAM_ID,
         TEAM_SLUG)

pbp <- load_nba_pbp() |> 
  group_by(game_id, 
           home_team_abbrev, 
           away_team_abbrev) |>
  nest()

for (i in 1:nrow(pbp)) {
  
  ldf <- pbp$data[[i]]
  
  ldf$player_name <- ""
  
  for (j in 1:nrow(ldf)) {
    
    for (k in 1:nrow(player_df)) {
      
      if (ldf$athlete_id_1[j] == player_df$PERSON_ID[k]) {
        
        ldf$player_name[j] <- paste(player_df$PLAYER_FIRST_NAME[k],
                                    player_df$PLAYER_LAST_NAME[k],
                                    sep = " ")
        
      }
      
    }
    
  }
  
}

#####
