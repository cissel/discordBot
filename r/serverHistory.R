# serverHistory.R by JHCV

##### Required Packages #####

library(tidyverse)

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

##### Import Data #####

setwd("/Users/jamescissel/discordBot")

df <- read_csv("server_messages.csv")

#####

##### All Activity #####

aa <- df |>
  
  mutate(date = as.Date(datetime)) |>
  
  group_by(date) |>
  
  summarize("n" = n()) |>
  
  arrange(date) |>
  
  mutate(tot = cumsum(n))

aap <- ggplot(aa,
              aes(x = date,
                  y = tot)) +
  
  geom_line(color = "white") +
  
  labs(x = "Date",
       y = "Total Messages Sent",
       title = "Room 40 Activity History",
       subtitle = paste(tail(aa$tot, 1),
                        "messages as of",
                        max(aa$date),
                        sep = " "),
       caption = "JHCV") +
  
  myTheme

ggsave("/Users/jamescissel/discordBot/outputs/metrics/allMessages.png",
       plot = aap,
       width = 10,
       height = 10,
       dpi = 300,
       bg = "transparent")

#####
