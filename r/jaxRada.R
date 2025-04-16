# jaxRada.R by JHCV

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

plotjaxradar <- function() {
  
  kjaxUrl <- "https://radar.weather.gov/ridge/standard/KJAX_loop.gif"
  
  kjaxOut <- "outputs/weather/nwsJaxRadar.gif"
  
  download.file(kjaxUrl, kjaxOut, mode = "wb")
  
}

#####

plotjaxradar()