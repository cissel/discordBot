#!/usr/bin/env Rscript
# impliedCorrWFCVPlot.R - implied correlation WFCV strategy backtest (navy theme)
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
d <- read_csv(file.path(base, "implied_correlation_dispersion_WFCV_daily.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg))

a <- d |> arrange(date) |>
  transmute(date, `BASELINE (live GEX/term gate)` = cumsum(pnl_baseline) / 21,
            `PROPOSED (+ COR3M walk-forward gate)` = cumsum(pnl_proposed) / 21,
            `unconditional (every day)` = cumsum(pnl_unconditional) / 21) |>
  pivot_longer(-date, names_to = "series", values_to = "cum")
p1 <- ggplot(a, aes(date, cum, colour = series)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = c(`BASELINE (live GEX/term gate)` = "#2E7BE0",
                                 `PROPOSED (+ COR3M walk-forward gate)` = "#E0A02E",
                                 `unconditional (every day)` = "#7B4CE0")) +
  labs(title = "Short 30d SPY variance swap: cumulative vol points, walk-forward OOS",
       subtitle = "PROPOSED adds a COR3M bottom-quintile filter (trailing 756d, no lookahead) on top of the live GEX/term gate",
       x = NULL, y = "cumulative vol points", colour = NULL) + thm

m <- d |> mutate(month = as.Date(cut(date, "month"))) |>
  group_by(month) |>
  summarise(baseline = mean(pnl_baseline), proposed = mean(pnl_proposed), .groups = "drop") |>
  pivot_longer(-month, names_to = "series", values_to = "pnl")
p2 <- ggplot(m, aes(month, pnl, fill = series)) + geom_col(position = "dodge") +
  scale_fill_manual(values = c(baseline = "#2E7BE0", proposed = "#E0A02E")) +
  labs(title = "Mean daily P&L by calendar month (vol points)", subtitle = "PROPOSED's worst month is far shallower, but it also trades far less often",
       x = NULL, y = "vol points", fill = NULL) + thm

p <- p1 / p2 & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "implied_correlation_dispersion_WFCV.png"), p, width = 13, height = 9, dpi = 150, bg = bg)
