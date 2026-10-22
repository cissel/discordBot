#!/usr/bin/env Rscript
# Navy-theme chart for the volBook kill-rule calibration.
# Reads volbook_kill_curves.csv and volbook_kill_grid.csv written by
# python/research/structural/volbook_kill_calibration.py.
suppressPackageStartupMessages({library(ggplot2); library(dplyr); library(readr); library(patchwork)})
out <- "/home/jhcv/discordBot/outputs/research/structural"
curves <- read_csv(file.path(out, "volbook_kill_curves.csv"), show_col_types = FALSE)
grid <- read_csv(file.path(out, "volbook_kill_grid.csv"), show_col_types = FALSE)

navy <- "#02233F"; gridc <- "#274066"
cols <- c("#2E7BE0", "#E0A02E", "#2E9E4E", "#7B4CE0", "#E0392E")
theme_navy <- theme_minimal(base_size = 13) +
  theme(plot.background = element_rect(fill = navy, colour = navy),
        panel.background = element_rect(fill = navy, colour = navy),
        panel.grid.major = element_line(colour = gridc),
        panel.grid.minor = element_line(colour = gridc, linewidth = 0.25),
        text = element_text(colour = "white"),
        axis.text = element_text(colour = "white"),
        axis.title = element_text(colour = "white"),
        plot.title = element_text(colour = "white", face = "bold"),
        plot.subtitle = element_text(colour = "white"),
        plot.caption = element_text(colour = "white"),
        legend.text = element_text(colour = "white"),
        legend.title = element_text(colour = "white"),
        legend.background = element_rect(fill = navy, colour = navy),
        legend.key = element_rect(fill = navy, colour = navy),
        legend.position = "bottom")

lev <- c("empirical", "stylised p=0.85 (edge)", "stylised p=0.78 (thin)",
         "stylised p=0.70 (no edge)", "stylised p=0.62 (negative)")
curves$scenario <- factor(curves$scenario, levels = lev)

p1 <- ggplot(curves, aes(positions, p_killed, colour = scenario)) +
  geom_step(linewidth = 1.1) +
  geom_vline(xintercept = 25, colour = "white", linetype = "dashed", linewidth = 0.4) +
  scale_colour_manual(values = cols, name = NULL) +
  scale_y_continuous(labels = scales::percent, limits = c(0, 1)) +
  guides(colour = guide_legend(nrow = 2)) +
  labs(x = "Closed positions", y = "P(book killed by n)",
       title = "Panel 1: live kill rule, P(killed) vs closed positions",
       subtitle = "Equity -4% at any close, or at 25 closed: win rate < 60% and net < 0.\n1 contract, $100k equity. Dashed line = the 25-position review.") +
  theme_navy

grid <- grid %>% mutate(rule = ifelse(is_live, "live rule", "grid rule"),
                        Nlab = factor(paste0("review at N=", N), levels = paste0("review at N=", c(20, 25, 30, 40))))
front <- grid %>% arrange(false_kill_p85) %>% mutate(best = cummax(kill_p70_by_N15)) %>%
  group_by(best) %>% slice_min(false_kill_p85, n = 1, with_ties = FALSE) %>% ungroup()
p2 <- ggplot(grid, aes(false_kill_p85, kill_p70_by_N15)) +
  annotate("rect", xmin = 0, xmax = 0.05, ymin = 0.80, ymax = 1, fill = "#2E9E4E", alpha = 0.18) +
  annotate("text", x = 0.025, y = 0.96, label = "objective region", colour = "white", size = 3.5) +
  geom_point(aes(colour = Nlab), size = 2.3, alpha = 0.85) +
  geom_step(data = front, aes(false_kill_p85, best), colour = "white", linewidth = 0.6, direction = "vh") +
  geom_point(data = filter(grid, is_live), colour = "white", size = 6, shape = 21, stroke = 1.4, fill = NA) +
  geom_text(data = filter(grid, is_live), aes(label = "live rule"), colour = "white", nudge_x = 0.006, size = 3.8) +
  scale_colour_manual(values = cols[1:4], name = NULL) +
  scale_x_continuous(labels = scales::percent, limits = c(0, 0.06), breaks = seq(0, 0.06, 0.01)) +
  scale_y_continuous(labels = scales::percent, limits = c(0, 1)) +
  guides(colour = guide_legend(nrow = 1)) +
  labs(x = "False kill: P(kill by N+15) when p = 0.85 (edge real)",
       y = "Correct kill: P(kill by N+15) when p = 0.70 (no edge)",
       title = "Panel 2: rule grid (192 rules), false kill vs correct kill",
       subtitle = "Green box = objective (false kill <= 5%, correct kill >= 80%).\nWhite step = frontier. No rule reaches the box.") +
  theme_navy

p <- (p1 | p2) + plot_annotation(
  title = "volBook kill rule: is it calibrated? 20,000 simulated books per scenario, seed 42",
  caption = "Empirical = block bootstrap (blocks of 5, stride 2) of 2011-2026 synthetic 30d short-variance P&L mapped to condor outcomes; stylised = +0.55 w.p. p, -2.20 w.p. 0.6(1-p), uniform[-2.20, 0] otherwise.",
  theme = theme_navy)
ggsave(file.path(out, "volbook_kill.png"), p, bg = navy, width = 15, height = 7.5, dpi = 150)
cat("saved", file.path(out, "volbook_kill.png"), "\n")
