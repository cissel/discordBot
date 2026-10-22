#!/usr/bin/env Rscript
# spyExecutionCostPlot.R - SPY execution cost model (navy theme)
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot")
tab <- read_csv(file.path(base, "outputs/markets/spy_cost_model.csv"), show_col_types = FALSE)
fills <- read_csv(file.path(base, "outputs/markets/spy_cost_fills.csv"), show_col_types = FALSE) |>
  filter(!is.na(shortfall_vs_signal_close_bps))
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg))

tab$bucket <- factor(tab$bucket, levels = tab$bucket)
long <- tab |> select(bucket, median = half_spread_median_bps, p75 = half_spread_p75_bps, p95 = half_spread_p95_bps) |>
  pivot_longer(-bucket, names_to = "stat", values_to = "bps")
p1 <- ggplot(long, aes(bucket, bps, fill = stat)) + geom_col(position = "dodge") +
  geom_hline(yintercept = 0.5, colour = "#E0A02E", linetype = "dashed") +
  annotate("text", x = 1, y = 0.53, label = "swing backtests assumed 0.5 bp", colour = "#E0A02E", hjust = 0, size = 3.5) +
  geom_hline(yintercept = 1.0, colour = "#E0392E", linetype = "dashed") +
  annotate("text", x = 1, y = 1.03, label = "structural studies assumed 1.0 bp", colour = "#E0392E", hjust = 0, size = 3.5) +
  scale_fill_manual(values = c(median = "#2E7BE0", p75 = "#7B4CE0", p95 = "#2E9E4E")) +
  labs(title = "SPY half-spread by time of day (NBBO, Jun-Oct 2026, 31,516 minutes)",
       subtitle = "cost to cross for a small market order, per side, bps of mid", x = NULL, y = "bps", fill = NULL) + thm

p2 <- ggplot(fills, aes(shortfall_vs_signal_close_bps, fill = side)) +
  geom_histogram(binwidth = 10, position = "identity", alpha = 0.7, colour = bg) +
  geom_vline(xintercept = 0, colour = txt) +
  scale_fill_manual(values = c(buy = "#2E9E4E", sell = "#E0392E")) +
  labs(title = "Autopilot implementation shortfall: next-open fill vs the signal's close",
       subtitle = sprintf("%d fills, %d days; mean %+.1f bp, adverse %.0f%% of the time, $%.0f total vs +$131 strategy P&L",
                          nrow(fills), n_distinct(fills$day), weighted.mean(fills$shortfall_vs_signal_close_bps, fills$dollar),
                          100 * mean(fills$shortfall_vs_signal_close_bps > 0),
                          sum(ifelse(fills$side == "buy", 1, -1) * (fills$price - fills$signal_close) * fills$qty)),
       x = "bps (positive = paid more than the signal price)", y = "fills", fill = NULL,
       caption = "The spread is not the cost. The overnight gap between the 16:15 signal and the 09:30 fill is.") + thm

p <- p1 / p2 & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "outputs/markets/spy_cost_model.png"), p, width = 12, height = 9, dpi = 150, bg = bg)
