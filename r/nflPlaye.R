# nflPlaye.R by JHCV

##### Required packages #####

library(tidyverse)
library(lubridate)
library(timetk)
library(httr)
library(rvest)
library(wdman)
library(tidytext)
library(jsonlite)

#####

setwd("~/discordBot")

##### Pull all NFL players #####

pUrl <- "https://api.sleeper.app/v1/players/nfl"

pdf <- GET(url = pUrl) |>
  
  content(as = "text", 
          encoding = "UTF-8") 

#####

##### Write JSON #####

write_json(pdf, "outputs/sports/nfl/players.json")

#####