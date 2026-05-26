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

setwd("~/discordBot")

df <- read_csv("outputs/metrics/server_messages.csv")

#####

##### All Activity #####

aa <- df |>
  mutate(date = as.Date(datetime)) |>
  count(date, channel, name = "n") |>
  complete(date = seq.Date(min(date), max(date), by = "day"),
           channel,
           fill = list(n = 0)) |>
  arrange(channel, date) |>
  group_by(channel) |>
  mutate(tot = cumsum(n)) |>
  ungroup()

aap <- ggplot(aa,
              aes(x = date,
                  y = tot,
                  fill = channel)) +
  
  geom_area(alpha = 0.75,
            position = "stack") +
  
  geom_text(
    data = aa |> filter(date == max(date)) |> arrange(desc(channel)) |>
      mutate(label_y = cumsum(tot) - tot / 2),
    aes(x = max(aa$date), y = label_y, label = channel),
    color = "white", size = 2.5, hjust = -0.1,
    inherit.aes = FALSE
  ) +
  
  coord_cartesian(clip = "off") +
  theme(plot.margin = margin(5, 80, 5, 5)) +
  
  labs(x = "Date",
       y = "Total Messages Sent",
       title = "Room 40 Activity History",
       subtitle = paste(sum(filter(aa, date == max(aa$date))$tot),
                        "messages as of",
                        max(aa$date),
                        sep = " "),
       caption = "JHCV") +
  
  myTheme #+
  #myLegend

ggsave("~/discordBot/outputs/metrics/allMessages.png",
       plot = aap,
       width = 10,
       height = 10,
       dpi = 300,
       bg = "transparent")

#####
