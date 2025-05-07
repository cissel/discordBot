# jaxSat.R by JHCV

##### Required Packages #####

library(tidyverse)
library(rvest)
library(magick)

#####

##### File path #####

setwd("/Users/jamescissel/discordBot")

######

##### Pull Satellite GIF #####

jaxSat <- function() {
  
  jsUrl <- "https://cdn.star.nesdis.noaa.gov/WFO/jax/GEOCOLOR/GOES19-JAX-GEOCOLOR-600x600.gif"
  
  jsOut <- "outputs/weather/nwsJaxSat.gif"
  
  download.file(jsUrl, jsOut, mode = "wb")
  
}

#####

##### Run Script #####

jaxSat()

#####