# serverAnalyze.R by JHCV

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

df <- read_csv("outputs/metrics/server_messages.csv")

#####

##### Channel Activity #####

ca <- df |>
  mutate(date = as.Date(datetime)) |>
  group_by(channel, date) |>
  summarize(n = n(), .groups = "drop_last") |>  # keep grouping by channel
  arrange(channel, date) |>
  mutate(tot = cumsum(n)) |>
  ungroup()

# Create label dataframe: last point per channel
label_df <- ca |>
  group_by(channel) |>
  filter(date == max(date)) |>
  ungroup()

cap <- ggplot(ca,
              aes(x = date,
                  y = tot,
                  color = channel)) +
  
  geom_line() +
  
  geom_text(data = label_df,
            mapping = aes(x = date, 
                          y = tot^1.005, 
                          label = paste(channel,
                                        ": ",
                                        tot,
                                        sep = ""), 
                          color = channel),
            hjust = 0.75,
            size = 3) +
  
  labs(x = "Date",
       y = "Total Messages Sent",
       title = "Room 40 Channel History",
       subtitle = paste(nrow(df),
                        "messages as of",
                        max(ca$date),
                        sep = " "),
       caption = "JHCV") +
  
  myTheme #+
  #myLegend

ggsave("/Users/jamescissel/discordBot/outputs/metrics/channelMessages.png",
       plot = cap,
       width = 10,
       height = 10,
       dpi = 300,
       bg = "transparent")

#####