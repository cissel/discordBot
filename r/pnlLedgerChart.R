#!/usr/bin/env Rscript
# pnlLedgerChart.R - cumulative mark-to-market P&L by strategy (navy theme)
# Input : outputs/markets/pnl_ledger_daily.csv (written by python/pnlLedger.py)
# Output: outputs/markets/pnl_ledger.png
suppressPackageStartupMessages({
  library(ggplot2); library(dplyr); library(readr); library(scales)
})

base <- path.expand("~/discordBot")
d <- read_csv(file.path(base, "outputs/markets/pnl_ledger_daily.csv"),
              show_col_types = FALSE) |>
  mutate(date = as.Date(date))

if (nrow(d) == 0) quit(status = 0)

bg <- "#02233F"; txt <- "white"; grid <- "#274066"
pal <- c("SPY autopilot" = "#2E7BE0", "MM SOXL" = "#E0A02E", "MM TQQQ (RL)" = "#7B4CE0",
         "MM GME" = "#2E9E4E", "Grid BTC" = "#F7931A", "Grid ETH" = "#8C8CFF", "Vol book (IC)" = "#E0392E",
         "Grid SOL" = "#14F195")

last <- d |> group_by(strategy) |> slice_max(date, n = 1, with_ties = FALSE) |>
  mutate(lab = sprintf("%s  %+.0f", strategy, total))
d$strategy <- factor(d$strategy, levels = last$strategy[order(-last$total)])
last$strategy <- factor(last$strategy, levels = levels(d$strategy))

p <- ggplot(d, aes(date, total, colour = strategy)) +
  geom_hline(yintercept = 0, colour = txt, linewidth = 0.3, alpha = 0.6) +
  geom_line(linewidth = 0.9) +
  geom_point(data = last, size = 2.2) +
  scale_colour_manual(values = pal, labels = setNames(last$lab, last$strategy), drop = TRUE) +
  scale_y_continuous(labels = label_dollar()) +
  scale_x_date(date_labels = "%b %d") +
  labs(title = "Live strategy P&L (paper, mark-to-market)",
       subtitle = sprintf("realized + unrealized, reconciled from Alpaca fills - through %s",
                          format(max(d$date), "%b %d, %Y")),
       x = NULL, y = NULL, colour = NULL,
       caption = "Each line starts at its own launch date. Realized is net of broker fees. Marked at daily close.") +
  theme_minimal(base_size = 13) +
  theme(plot.background = element_rect(fill = bg, colour = bg),
        panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3),
        panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"),
        plot.subtitle = element_text(colour = txt), plot.caption = element_text(colour = txt),
        legend.text = element_text(colour = txt), legend.position = "right",
        legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg))

ggsave(file.path(base, "outputs/markets/pnl_ledger.png"), p, width = 11, height = 6, dpi = 150, bg = bg)
