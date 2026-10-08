#!/usr/bin/env Rscript
# maptapSharpe.R by JHCV
# Risk/reward scatter of each player's MapTap performance:
#   x = std dev of final_score (consistency, lower = steadier)
#   y = mean final_score (higher = better, since a bigger score wins)
# Top-left is the "good" corner: high average AND low variance.
#
# Args:
#   [1] csv_path (default: ~/discordBot/outputs/games/maptap/maptap_scores.csv)
#   [2] out_path (default: ~/discordBot/outputs/games/maptap/maptapSharpe.png)

suppressPackageStartupMessages({
  library(tidyverse)
  library(RColorBrewer)
  library(ggrepel)
})

args     <- commandArgs(trailingOnly = TRUE)
csv_path <- if (length(args) >= 1) args[1] else "~/discordBot/outputs/games/maptap/maptap_scores.csv"
out_path <- if (length(args) >= 2) args[2] else "~/discordBot/outputs/games/maptap/maptapSharpe.png"
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
  message("[maptapSharpe] CSV not found: ", csv_path)
  quit(status = 1)
}

df <- read_csv(csv_path, show_col_types = FALSE) %>%
  mutate(date = as.Date(date))

if (nrow(df) == 0) {
  message("[maptapSharpe] CSV is empty")
  quit(status = 1)
}

# need at least 2 games per player for a stddev to exist, but require more
# for the stat to actually mean something
MIN_GAMES <- 3
stats <- df %>%
  group_by(player) %>%
  summarise(n_games = n(),
            mean_score = mean(final_score, na.rm = TRUE),
            sd_score   = sd(final_score, na.rm = TRUE),
            .groups = "drop") %>%
  filter(n_games >= MIN_GAMES, !is.na(sd_score))

dropped <- df %>% count(player, name = "n_games") %>% filter(n_games < MIN_GAMES) %>% pull(player)
if (length(dropped) > 0) {
  cat(sprintf("[maptapSharpe] excluding %d player(s) with <%d games: %s\n",
              length(dropped), MIN_GAMES, paste(dropped, collapse = ", ")))
}

if (nrow(stats) == 0) {
  message(sprintf("[maptapSharpe] no players with >=%d logged games", MIN_GAMES))
  quit(status = 1)
}

min_date_str <- format(min(df$date), "%Y-%m-%d")
max_date_str <- format(max(df$date), "%Y-%m-%d")

n_players <- nrow(stats)
pal <- if (n_players <= 8) {
  brewer.pal(max(3, n_players), "Dark2")[1:n_players]
} else {
  colorRampPalette(brewer.pal(8, "Dark2"))(n_players)
}

p <- ggplot(stats, aes(x = sd_score, y = mean_score, color = player, size = n_games)) +
  geom_point(alpha = 0.9) +
  geom_text_repel(aes(label = player), color = "white", size = 3.5,
                   show.legend = FALSE, seed = 42) +
  scale_color_manual(values = pal, name = "player") +
  scale_size_continuous(name = "games played", range = c(3, 8)) +
  labs(
    x = "std dev of final score (consistency)",
    y = "mean final score (higher = better)",
    title = "Room 40 MapTap Risk/Reward",
    subtitle = paste0(nrow(df), " games logged  |  ", min_date_str, " to ", max_date_str,
                       "  |  top-left = best (high avg, low variance)"),
    caption = "Source: Discord message log | JHCV"
  ) +
  myTheme +
  myLegend

ggsave(out_path,
       plot = p,
       width = 11,
       height = 6.5,
       dpi = 300,
       bg = "transparent")

cat(sprintf("[maptapSharpe] saved %s\n", out_path))
