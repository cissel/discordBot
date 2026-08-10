#!/usr/bin/env Rscript
# wordleScores.R by JHCV
# Time series of each player's daily Wordle score (guesses to solve; X = fail = 7)
#
# Args:
#   [1] csv_path (default: ~/discordBot/outputs/games/wordle/wordle_scores.csv)
#   [2] out_path (default: ~/discordBot/outputs/games/wordle/wordleScores.png)

suppressPackageStartupMessages({
  library(tidyverse)
  library(lubridate)
})

args     <- commandArgs(trailingOnly = TRUE)
csv_path <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/games/wordle/wordle_scores.csv"
out_path <- if (length(args) >= 2) args[2] else "~/discordBot/outputs/games/wordle/wordleScores.png"
csv_path <- path.expand(csv_path)
out_path <- path.expand(out_path)

##### Plot Appearance Theme #####

myTheme <- theme(plot.background = element_rect(fill = "#02233F"),
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

myLegend <- theme(legend.position = "right",
                  legend.background = element_rect(fill = "#02233F"),
                  legend.text = element_text(color = "white"),
                  legend.title = element_text(color = "white"))

#####

if (!file.exists(csv_path)) {
  message("[wordleScores] CSV not found: ", csv_path)
  quit(status = 1)
}

df <- read_csv(csv_path, show_col_types = FALSE) %>%
  mutate(date = as.Date(date))

if (nrow(df) == 0) {
  message("[wordleScores] CSV is empty")
  quit(status = 1)
}

min_date <- min(df$date)
max_date <- max(df$date)
min_date_str <- format(min_date, "%Y-%m-%d")
max_date_str <- format(max_date, "%Y-%m-%d")

p <- ggplot(df, aes(x = date, y = score_value, color = player)) +
  geom_line(linewidth = 0.8, alpha = 0.85) +
  geom_point(size = 1.8) +
  scale_y_continuous(
    breaks = 1:7,
    labels = c("1","2","3","4","5","6","X (fail)"),
    limits = c(1, 7)
  ) +
  scale_x_date(date_breaks = "1 week", date_labels = "%b %d") +
  labs(
    x = NULL,
    y = "guesses to solve",
    color = "player",
    title = "Room 40 Wordle Scores",
    subtitle = paste0(nrow(df), " games logged  |  ", min_date_str, " to ", max_date_str),
    caption = "Source: Discord message log | JHCV"
  ) +
  myTheme +
  myLegend

ggsave(out_path,
       plot = p,
       width = 11,
       height = 6,
       dpi = 300,
       bg = "transparent")

cat(sprintf("[wordleScores] saved %s\n", out_path))
