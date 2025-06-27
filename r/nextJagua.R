# nextJagua.R by JHCV

##### Required Packages #####

library(tidyverse)
library(nflfastR)

#####

# pull schedule
sched <- fast_scraper_schedules(2025)

# filter for jags
jax <- sched |> subset(home_team == "JAX" | away_team == "JAX")

# filter for games that haven't happened yet
jaxFut <- jax |> subset(as.Date(gameday) >= today())

# select the next game
jaxNext <- jaxFut |> head(1)

# add countdown
jaxNext$daysUntil <- as.Date(jaxNext$gameday)-today()

# write csv to output folder
write_csv(jaxNext, "/Users/jamescissel/discordBot/outputs/sports/nfl/nextJags.csv")