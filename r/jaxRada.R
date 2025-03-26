# jaxRada.R by JHCV

##### Required Packages #####

library(tidyverse)
library(rvest)
library(magick)

#####

setwd("/Users/jamescissel/discordBot")

##### Scrape Radar #####

plotjaxradar <- function() {
  
  kjaxUrl <- "https://radar.weather.gov/ridge/standard/KJAX_loop.gif"
  
  kjaxOut <- "outputs/nwsJaxRadar.gif"
  
  download.file(kjaxUrl, kjaxOut, mode = "wb")
  
}

#####

plotjaxradar()