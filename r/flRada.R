# flRada.R by JHCV

##### Required Packages #####

library(tidyverse)
library(rvest)
library(magick)

#####

# Windows file path
#setwd("C:/Users/james/projects/discordBot")

# Mac file path
setwd("/Users/jamescissel/discordBot")

##### Scrape Radar #####

plotFLradar <- function() {
  
  flUrl <- "https://radar.weather.gov/ridge/standard/SOUTHEAST_loop.gif"
  
  flOut <- "outputs/weather/flRadar.gif"
  
  download.file(flUrl, flOut, mode = "wb")
  
}

#####

plotFLradar()