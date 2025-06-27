# usRada.R by JHCV

##### required packages #####

library(tidyverse)
library(rvest)
library(magick)

#####

# mac file path
setwd("/Users/jamescissel/discordBot")

# scrape radar

usrad <- function() {
  
  uUrl <- "https://radar.weather.gov/ridge/standard/CONUS_loop.gif"
  uOut <- "outputs/weather/usRadar.gif"
  download.file(uUrl, uOut, mode = "wb")
  
}

usrad()