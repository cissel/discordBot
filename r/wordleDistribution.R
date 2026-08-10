#!/usr/bin/env Rscript
# wordleDistribution.R by JHCV
# Overlaid (non-dodged) histogram of each player's Wordle score distribution
# (guesses to solve), semi-transparent bars plus a line+point curve per player
# tracing their own counts so individual distributions stay legible when bars overlap.
#
# Args:
#   [1] csv_path (default: ~/discordBot/outputs/games/wordle/wordle_scores.csv)
#   [2] out_path (default: ~/discordBot/outputs/games/wordle/wordleDistribution.png)

suppressPackageStartupMessages({
  library(tidyverse)
  library(RColorBrewer)
  library(scales)
})

args     <- commandArgs(trailingOnly = TRUE)
csv_path <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/games/wordle/wordle_scores.csv"
out_path <- if (length(args) >= 2) args[2] else "~/discordBot/outputs/games/wordle/wordleDistribution.png"
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
  message("[wordleDistribution] CSV not found: ", csv_path)
  quit(status = 1)
}

df <- read_csv(csv_path, show_col_types = FALSE)

if (nrow(df) == 0) {
  message("[wordleDistribution] CSV is empty")
  quit(status = 1)
}

# strip leading/trailing emoji/symbol decoration from player names (defense in
# depth in case an older CSV was generated before the Python-side name cleanup)
df <- df %>%
  mutate(player = str_replace(player, "^[^[:alnum:]]+", ""),
         player = str_replace(player, "[^[:alnum:]]+$", ""))

df <- df %>%
  mutate(score = factor(score, levels = c("1", "2", "3", "4", "5", "6", "X")))

# drop players with fewer than 3 logged games - not enough data for a
# meaningful distribution (and geom_density() needs >=2 points per group anyway)
MIN_GAMES <- 3
games_per_player <- df %>% count(player, name = "n_games")
dropped <- games_per_player %>% filter(n_games < MIN_GAMES) %>% pull(player)
if (length(dropped) > 0) {
  cat(sprintf("[wordleDistribution] excluding %d player(s) with <%d games: %s\n",
              length(dropped), MIN_GAMES, paste(dropped, collapse = ", ")))
}
df <- df %>% semi_join(games_per_player %>% filter(n_games >= MIN_GAMES), by = "player")

if (nrow(df) == 0) {
  message(sprintf("[wordleDistribution] no players with >=%d logged games", MIN_GAMES))
  quit(status = 1)
}

min_date_str <- format(min(as.Date(df$date)), "%Y-%m-%d")
max_date_str <- format(max(as.Date(df$date)), "%Y-%m-%d")

n_players <- n_distinct(df$player)
pal <- if (n_players <= 8) {
  brewer.pal(max(3, n_players), "Dark2")[1:n_players]
} else {
  colorRampPalette(brewer.pal(8, "Dark2"))(n_players)
}

score_labels <- c("1","2","3","4","5","6","X (fail)")

p <- ggplot(df, aes(x = score_value, fill = player, color = player)) +
  geom_histogram(
    aes(y = after_stat(density)),
    binwidth = 1, boundary = 0.5,
    position = "identity", alpha = 0.35, color = "white", linewidth = 0.25
  ) +
  geom_density(
    linewidth = 0.9, alpha = 0, bw = "nrd0", adjust = 1.2, trim = FALSE
  ) +
  scale_x_continuous(breaks = 1:7, labels = score_labels, limits = c(0.5, 7.5)) +
  scale_y_continuous(labels = percent_format(accuracy = 1)) +
  scale_fill_manual(values = pal, name = "player") +
  scale_color_manual(values = pal, name = "player") +
  guides(color = "none") +
  labs(
    x = "guesses to solve",
    y = "share of that player's games",
    fill = "player",
    title = "Room 40 Wordle Score Distribution",
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

cat(sprintf("[wordleDistribution] saved %s\n", out_path))
