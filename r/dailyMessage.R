# dailyMessage.R by JHCV

##### Required Packages #####

library(tidyverse)
library(zoo)

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

##### Legend appearance theme #####

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))#,
#legend.key.height = unit(100, "cm"))

#####

setwd("~/discordBot")

df <- read_csv("outputs/metrics/server_messages.csv") |>
  mutate(date = as.Date(datetime))

# counts per date x channel (for stacked bars)
cdm <- df |>
  count(date, channel, name = "n")

# total per day + moving avgs (for lines)
dm <- cdm |>
  group_by(date) |>
  summarise(n = sum(n), 
            .groups = "drop") |>
  arrange(date) |>
  mutate(
    ma7  = rollapply(n, 
                     7,  
                     mean, 
                     align = "right", 
                     fill = NA),   # or partial=TRUE if you prefer
    ma30 = rollapply(n, 
                     30, 
                     mean, 
                     align = "right", 
                     fill = NA)
  )

dmp <- ggplot(cdm, 
              aes(x = date, 
                  y = n, 
                  fill = channel)) +
  
  geom_col(alpha = 0.75, 
           position = "stack") +
  
  # draw lines from the *dm* data, and don't inherit bar aesthetics
  geom_line(
    data = dm, 
    aes(x = date, 
        y = ma7),
    color = "white", 
    alpha = 0.65, 
    linewidth = .5, 
    inherit.aes = FALSE) +
  
  geom_line(
    data = dm, 
    aes(x = date, 
        y = ma30),
    color = "white", 
    linewidth = 0.75, 
    inherit.aes = FALSE) +
  
  labs(
    x = "Time", 
    y = "Messages Sent",
    #legend = "Channel",
    #caption = "JHCV", 
    #subtitle = max(dm$date, na.rm = TRUE),
    title = "Room 40 Daily Activity") +
  
  #scale_y_log10() +
  
  myTheme +
  myLegend

ggsave("/Users/jamescissel/discordBot/outputs/metrics/dailyMessages.png",
       plot = dmp, 
       width = 10, 
       height = 4, 
       dpi = 300, 
       bg = "transparent")

#####