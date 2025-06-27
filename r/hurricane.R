# hurricane.R by JHCV

##### required packages #####

library(tidyverse)
library(rvest)

#####

# mac file path
setwd("/Users/jamescissel/discordBot")

##### scrape noaa 7 day tropical weather outlook #####

two7 <- function() {
  
  tUrl <- "https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png"
  tOut <- "outputs/weather/two7d.png"
  download.file(tUrl, tOut, mode = "wb")
  
}

#####

two7()