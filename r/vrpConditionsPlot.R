#!/usr/bin/env Rscript
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr) })
base <- path.expand("~/discordBot/outputs/research/structural")
res <- read_csv(file.path(base, "vrp_conditions_results.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg), strip.text = element_text(colour = txt, face = "bold"))
res <- res |> mutate(cell = factor(cell, levels = rev(cell)),
                     status = case_when(pass ~ "pass", pred_sign == 0 ~ "exploratory", TRUE ~ "fail"))
p <- ggplot(res, aes(diff, cell, fill = status)) + geom_col() +
  geom_vline(xintercept = 0, colour = txt, linewidth = 0.3) +
  geom_text(aes(label = sprintf("t %.1f  n %d  H1 %+.1f H2 %+.1f", nw_t, n, diff_H1, diff_H2),
                x = ifelse(diff > 0, diff + 0.15, diff - 0.15), hjust = ifelse(diff > 0, 0, 1)), colour = txt, size = 3) +
  scale_fill_manual(values = c(pass = "#2E9E4E", fail = "#E0392E", exploratory = "#2E7BE0")) +
  facet_grid(hyp ~ ., scales = "free_y", space = "free_y") +
  scale_x_continuous(expand = expansion(mult = c(0.35, 0.45))) +
  labs(title = "VRP timing conditions: cell mean minus complement, vol points per entry",
       subtitle = "Short 30d SPY variance 2016-2026. Pass = predicted sign in both halves AND |NW t| >= 2",
       x = "difference in mean P&L per entry (vol points)", y = NULL, fill = NULL,
       caption = "Only 29 of 2,573 windows contain neither FOMC nor CPI: V1 is untestable at 21-day horizon.") + thm
ggsave(file.path(base, "vrp_conditions.png"), p, width = 13, height = 10, dpi = 150, bg = bg)
