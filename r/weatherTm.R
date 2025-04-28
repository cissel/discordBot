# weatherTm.R by JHCV

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

weatherTm <- function() {
  
  wtmUrl <- "https://www.weather.gov/images/jax/graphicast/small3.png"
  
  wtmOut <- "outputs/weather/weatherTm.png"
  
  download.file(wtmUrl, wtmOut, mode = "wb")
  
}

#####

weatherTm()