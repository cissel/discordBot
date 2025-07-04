# nextNFL.R by JHCV

##### Required Packages #####

library(tidyverse)
library(nflfastR)

#####

# pull schedule
sched <- fast_scraper_schedules(2025)

# filter upcoming games
fut <- sched |> subset(as.Date(gameday) >= today())

# select next game only
nextGame <- fut |> head(1)

# add countdown
nextGame$daysUntil <- as.Date(nextGame$gameday)-today()

# write csv to output folder
write_csv(nextGame, "/Users/jamescissel/discordBot/outputs/sports/nfl/nextGame.csv")
