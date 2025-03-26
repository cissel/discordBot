# flRada.R by JHCV

##### Required Packages #####

library(tidyverse)
library(rvest)
library(magick)

#####

setwd("/Users/jamescissel/discordBot")

##### Scrape Radar #####

plotFLradar <- function() {
  
  flUrl <- "https://radar.weather.gov/ridge/standard/SOUTHEAST_loop.gif"
  
  flOut <- "outputs/flRadar.gif"
  
  download.file(flUrl, flOut, mode = "wb")
  
}

#####

plotFLradar()