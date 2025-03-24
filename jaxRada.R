# jaxRada.R by JHCV

##### Required Packages #####

library(tidyverse)
library(rvest)
library(magick)

#####

##### Scrape Radar #####

plotjaxradar <- function() {
  
  kjaxUrl <- "https://radar.weather.gov/ridge/standard/KJAX_loop.gif"
  
  kjaxOut <- "nwsJaxRadar.gif"
  
  download.file(kjaxUrl, kjaxOut, mode = "wb")
  
}

#####

plotjaxradar()