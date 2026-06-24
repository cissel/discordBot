# userReport.R by JHCV
# Args: [1] username (user_name field from server_messages.csv)

##### Required Packages #####

library(tidyverse)

#####

##### Parse Args #####

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript userReport.R <username>")
target_user <- args[1]

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
                  legend.title = element_text(color = "white"))

#####

##### Import Data #####

setwd("~/discordBot")

df <- read_csv("outputs/metrics/server_messages.csv", show_col_types = FALSE)

# Filter to target user
user_df <- df |>
  filter(user_name == target_user)

if (nrow(user_df) == 0) {
  stop(paste("No messages found for user:", target_user))
}

# Use display name for title if available
display_name <- user_df |> pull(user_display_name) |> first()

#####

##### Channel Activity for User #####

ua <- user_df |>
  mutate(date = as.Date(datetime)) |>
  count(date, channel, name = "n") |>
  complete(date = seq.Date(min(date), max(date), by = "day"),
           channel,
           fill = list(n = 0)) |>
  arrange(channel, date) |>
  group_by(channel) |>
  mutate(tot = cumsum(n)) |>
  ungroup()

total_msgs <- sum(filter(ua, date == max(ua$date))$tot)

uarp <- ggplot(ua,
               aes(x = date,
                   y = tot,
                   fill = channel)) +

  geom_area(position = "stack", color = "white") +

  geom_text(
    data = ua |> filter(date == max(date)) |> arrange(desc(channel)) |>
      mutate(label_y = cumsum(tot) - tot / 2) |>
      filter(tot > 0),
    aes(x = max(ua$date), y = label_y, label = channel),
    color = "white", size = 2.5, hjust = -0.1,
    inherit.aes = FALSE
  ) +

  coord_cartesian(clip = "off") +
  theme(plot.margin = margin(5, 80, 5, 5)) +

  labs(x = "Date",
       y = "Total Messages Sent",
       title = paste("Room 40 Activity -", display_name),
       subtitle = paste(total_msgs,
                        "messages as of",
                        max(ua$date),
                        sep = " "),
       caption = "Source: Discord message log | JHCV") +

  myTheme

ggsave("~/discordBot/outputs/metrics/userReport.png",
       plot = uarp,
       width = 10,
       height = 10,
       dpi = 300,
       bg = "transparent")

#####
