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

##### Import Data #####

setwd("/Users/jamescissel/discordBot")

df <- read_csv("server_messages.csv")

#####

##### Daily Message Activity #####

# Clean data

dm <- df |>
  mutate(date = as.Date(datetime)) |>
  group_by(date) |>
  summarize(n = n())

# Moving average over last 7 days
dm$ma7 <- rollapply(
  dm$n,
  width = 7,
  FUN = mean,
  align = "right",
  fill = NA
)

# Moving average over last 30 days
dm$ma30 <- rollapply(
  dm$n,
  width = 30,
  FUN = mean,
  align = "right",
  fill = NA
)

# Plot data

dmp <- ggplot(dm,
              aes(x = date,
                  weight = n)) +
  
  geom_bar(fill = "white", 
           alpha = .5) +
  
  geom_line(aes(y = ma7),
            color = "cyan",
            alpha = .5) +
  
  geom_line(aes(y = ma30),
            color = "white") +
  
  labs(x = "Time",
       y = "Messages Sent",
       caption = "JHCV",
       subtitle = tail(dm$date, 1),
       title = "Room 40 Daily Activity") +
  
  #scale_y_log10() +
  
  myTheme

#ggplotly(dmp)
ggsave("/Users/jamescissel/discordBot/outputs/metrics/dailyMessages.png",
       plot = dmp,
       width = 10,
       height = 4,
       dpi = 300,
       bg = "transparent")

#####