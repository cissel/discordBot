# weathe.R by JHCV

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

weatherTd <- function() {
  
  wtdUrl <- "https://www.weather.gov/images/jax/graphicast/small2.png"
  
  wtdOut <- "outputs/weather/weatherTd.png"
  
  download.file(wtdUrl, wtdOut, mode = "wb")
  
}

#####

weatherTd()