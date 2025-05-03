# noaaBuoy.R by JHCV

##### Required Packages #####

library(tidyverse)
library(rvest)
library(magick)
library(lubridate)

#####

##### Scrape Data #####

url <- "https://www.ndbc.noaa.gov/data/realtime2/41117.txt"

df <- read_html(url) |>
  
  html_nodes("body") |>
  
  html_text()

#####

##### Clean Data #####

raw_text <- strsplit(df, "\n")[[1]] 

data_lines <- raw_text[!grepl("^#", 
                              raw_text)]

df <- read_table2(paste(data_lines, 
                        collapse = "\n"),
  col_names = c("YY", 
                "MM", 
                "DD", 
                "hh", 
                "mm", 
                "WDIR", 
                "WSPD",
                "GST", 
                "WVHT", 
                "DPD", 
                "APD", 
                "MWD", 
                "PRES", 
                "ATMP", 
                "WTMP", 
                "DEWP", 
                "VIS", 
                "PTDY", 
                "TIDE"),
  na = "MM"
) 

df <- df[, colSums(!is.na(df)) > 0]

df$date <- as.Date(NA)

for (i in 1:nrow(df)) {
  
  df$date[i] <- as.Date(paste(df$YY[i],
                              df$MM[i],
                              df$DD[i],
                              sep = "-"),
                        format = "%Y-%m-%d")
  
}

df <- df |>
  
  select(date,
         hh, 
         mm,
         WVHT,
         DPD,
         APD,
         MWD,
         WTMP)

df$time <- ""

for (i in 1:nrow(df)) {
  
  df$time[i] <- (paste(df$hh[i],
                       df$mm[i],
                       sep = ":"))
  
}

df$dt <- as.POSIXct(
  paste(as.character(df$date), df$time),
  format = "%Y-%m-%d %H:%M",
  tz = "UTC")

df <- df |>
  
  select(dt,
         WVHT,
         DPD,
         APD,
         MWD,
         WTMP)

#####

##### Write .csv #####

write_csv(df, "/Users/jamescissel/discordBot/outputs/weather/buoy41117.csv")
print(".csv saved")

#####

# View the result
#print(df)