# surf4castPlot.R by JHCV

##### Required Packages #####

library(tidyverse)
library(rvest)
library(httr)
library(magick)

#####

##### Plot Appearance Theme #####

myTheme <- theme(legend.position = "none",
                 plot.background = element_rect(fill = "#02233F"),
                 panel.background = element_rect(fill = "#02233F"),
                 panel.grid = element_line(color = "#274066"),
                 axis.ticks = element_line(color = "#274066"),
                 axis.text = element_text(color = "white"),
                 axis.title = element_text(color = "white"),
                 plot.title = element_text(color = "white",
                                           hjust = .5),
                 plot.subtitle = element_text(color = "white",
                                              hjust = .5),
                 plot.caption = element_text(color = "white"),
                 strip.background = element_rect(fill = "#02233F"),
                 strip.text = element_text(color = "white"))

#####

# Windows file path
#setwd("C:/Users/james/projects/discordBot")

# Mac file path
setwd("/Users/jamescissel/discordBot")

##### Surf Forecast Dataframe #####

surfFcstDf <- function() {
  
  url <- "https://www.surfguru.com/jacksonville-beach-pier-surf-report"
  
  htmlFcst <- read_html(url) |>
    
    html_nodes("div.surf-hgt") |>
    
    html_text()
  
  htmlTime <- read_html(url) |>
    
    html_nodes(".forecast-time") |>
    
    html_text()
  
  skips <- data.frame("skip" = seq(1, 49, 8))
  
  timeDF <- htmlTime |>
    
    unlist() |>
    
    as.data.frame()
  
  names(timeDF) <- "time"
  
  timeDF$keep <- TRUE
  
  for (i in 1:nrow(timeDF)) {
    
    if (i %in% skips$skip == TRUE) {
      
      timeDF$keep[i] <- FALSE
      
    }
    
  }
  
  timeDF <- timeDF |>
    
    subset(keep == TRUE) |>
    
    select(time)
  
  fcstDF <- data.frame("time" = timeDF$time,
                       "fcst" = htmlFcst)
  
  fcstDF$index <- rep((1:7), 7)
  
  fcstDF$date <- Sys.Date()
  
  for (i in 1:nrow(fcstDF)) {
    
    if (fcstDF$index[i] == 1) {
      
      fcstDF$date[i] <- fcstDF$date[i-1]+1
      
    } else {
      
      fcstDF$date[i] <- fcstDF$date[i-1]
      
    }
    
  }
  
  fcstDF <- fcstDF |>
    
    select(date,
           time,
           fcst)
  
  cnvTime <- function(time_str) {
    
    if (grepl("am",
              time_str,
              ignore.case = TRUE)) {
      
      time_str <- gsub("am",
                       "",
                       time_str,
                       ignore.case = TRUE)
      
      time_str <- sprintf("%02d:00:00",
                          as.numeric(time_str))
      
    } else if (grepl("pm",
                     time_str,
                     ignore.case = TRUE)) {
      
      time_str <- gsub("pm",
                       "",
                       time_str,
                       ignore.case = TRUE)
      
      time_str <- sprintf("%02d:00:00",
                          as.numeric(time_str)+12)
      
    }
    
    return(time_str)
    
  }
  
  fcstDF$timef <- sapply(fcstDF$time, cnvTime)
  
  fcstDF$datetime <- as.POSIXct(paste(fcstDF$date, fcstDF$timef),
                                format = "%Y-%m-%d %H:%M:%S")
  
  fcstDF$flo <- as.numeric(str_extract(fcstDF$fcst, "\\d+"))
  fcstDF$fhi <- as.numeric(str_extract(fcstDF$fcst, "(?<=-)\\d+"))
  
  htmlSwell <- read_html(url) |>
    
    html_nodes("div.forecast-swell-primary") |>
    
    html_text()
  
  swellDF <- htmlSwell |>
    
    unlist() |>
    
    as.data.frame()
  
  names(swellDF) <- "swellFcst"
  
  fcstDF$swellFcst <- swellDF$swellFcst
  
  for (i in 1:nrow(fcstDF)) {
    
    fcstDF$swellHt[i] <- gsub("ft", "", strsplit(fcstDF$swellFcst[i], " ")[[1]][1])
    fcstDF$swellPrd[i] <- gsub("s", "", strsplit(fcstDF$swellFcst[i], " ")[[1]][2])
    fcstDF$swellDir[i] <- strsplit(fcstDF$swellFcst[i], " ")[[1]][3]
    
  }
  
  fcstDF$swellHt <- as.numeric(fcstDF$swellHt)
  fcstDF$swellPrd <- as.numeric(fcstDF$swellPrd)
  
  htmlWindSpd <- read_html(url) |>
    
    html_nodes(".wnd-spd") |>
    
    html_text()
  
  windDF <- htmlWindSpd |>
    
    unlist() |>
    
    as.data.frame()
  
  names(windDF) <- "windSpd"
  
  htmlWindDir <- read_html(url) |>
    
    html_nodes(".wnd-dir") |>
    
    html_text()
  
  windDirDF <- htmlWindDir |>
    
    unlist() |>
    
    as.data.frame()
  
  names(windDirDF) <- "windDir"
  
  fcstDF$windSpd <- parse_number(windDF$windSpd)
  fcstDF$windDir <- windDirDF$windDir
  
  drows <- duplicated(fcstDF$datetime)
  
  fcstDF <- fcstDF[!drows, ]
  
  fcstDF <- fcstDF |>
    
    select(date,
           time,
           datetime,
           timef,
           fcst,
           swellFcst,
           swellHt,
           swellPrd,
           swellDir,
           windSpd,
           windDir)
  
  return(fcstDF)
  
}

#####

##### Surf Forecast Plot #####

plotSurfFcst <- function() {
  
  sfdf <- surfFcstDf()
  
  sfp <- ggplot(sfdf,
                aes(x = timef)) +
    
    geom_bar(aes(weight = swellHt),
             fill = "white") +
    
    geom_text(aes(y = swellHt+.1,
                  label = paste(swellHt,
                                "ft",
                                sep = "")),
              color = "white",
              size = 2) +
    
    geom_text(aes(y = swellHt-.1,
                  label = paste(swellPrd,
                                "s",
                                sep = "")),
              color = "#02233F",
              size = 2) +
    
    geom_text(aes(y = .25,
                  label = paste(windSpd,
                                "mph",
                                sep = "")),
              color = "#02233F",
              size = 2) +
    
    geom_text(aes(y = .1,
                  label = windDir),
              color = "#02233F",
              size = 2) +
    
    facet_wrap(sfdf$date, nrow = 1) +
    
    scale_x_discrete(labels = sfdf$time) +
    
    labs(x = "Time",
         y = "Swell Height (ft)",
         caption = "JHCV",
         subtitle = Sys.Date(),
         title = "Jax Beach Surf Forecast") +
    
    myTheme +
    theme(axis.text.x = element_text(size = 6,
                                     angle = -45),
          panel.grid.major.x = element_blank(),
          axis.ticks.x = element_blank())
  
  #p <- ggplotly(sfp)

  print("Generating surf forecast plot...")
  
  print("Creating outputs/ directory if missing...")
  
  if (!dir.exists("outputs/weather")) {
    
    dir.create("outputs/weather")
    
  }
  
print("Saving plot to: outputs/weather/surf_fcst.png")

ggsave("outputs/weather/surf_fcst.png", 
       plot = sfp, 
       width = 10, 
       height = 4, 
       dpi = 300, 
       bg = "transparent")

print("Plot successfully saved!")

}

#####

plotSurfFcst()