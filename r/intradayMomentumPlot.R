#!/usr/bin/env Rscript
# intradayMomentumPlot.R - navy theme chart for the intraday momentum study
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
d <- read_csv(file.path(base, "intraday_momentum_daily_pnl.csv"), show_col_types = FALSE)
res <- read_csv(file.path(base, "intraday_momentum_results.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg), strip.text = element_text(colour = txt))

cum <- d |> arrange(date) |>
  transmute(date, `sign(r_first) net 0.2bp` = cumsum(strat_r_first) * 100,
            `sign(r_12) net 0.2bp` = cumsum(strat_r_12) * 100, `B&H last 30 min` = cumsum(bh_last30) * 100) |>
  pivot_longer(-date, names_to = "series", values_to = "cum_pct")
p1 <- ggplot(cum, aes(date, cum_pct, colour = series)) + geom_line(linewidth = 0.9) +
  geom_hline(yintercept = 0, colour = txt, linewidth = 0.3) +
  scale_colour_manual(values = c(`sign(r_first) net 0.2bp` = "#2E7BE0", `sign(r_12) net 0.2bp` = "#7B4CE0", `B&H last 30 min` = "#E0A02E")) +
  labs(title = "Intraday momentum: trade the last 30 min in the direction of the first 30 min",
       subtitle = "SPY 2021-2026, flat at close, measured round-trip cost 0.205bp", x = NULL, y = "cumulative return (%)", colour = NULL) + thm

yr <- res |> filter(test == "sign_year", pred == "r_first") |> mutate(year = as.integer(cell))
p2 <- ggplot(yr, aes(factor(year), sharpe, fill = sharpe > 0)) + geom_col() +
  scale_fill_manual(values = c(`TRUE` = "#2E9E4E", `FALSE` = "#E0392E"), guide = "none") +
  geom_text(aes(label = sprintf("%.0f%%", hit_rate * 100), vjust = ifelse(sharpe > 0, -0.4, 1.3)), colour = txt, size = 3.5) +
  labs(title = "Sign(r_first) strategy by year", subtitle = "bar = annualised Sharpe, label = hit rate", x = NULL, y = "Sharpe") + thm

cond <- res |> filter(test == "cond") |> mutate(cell = factor(cell, levels = cell))
p3 <- ggplot(cond, aes(cell, nw_t, fill = nw_t >= 2)) + geom_col() +
  geom_hline(yintercept = 2, colour = "#E0A02E", linetype = "dashed") +
  scale_fill_manual(values = c(`TRUE` = "#2E9E4E", `FALSE` = "#2E7BE0"), guide = "none") +
  geom_text(aes(label = sprintf("n=%d\n%.1fbp", n_days, mean_bps), vjust = ifelse(nw_t > 0, -0.2, 1.1)), colour = txt, size = 3) +
  labs(title = "Conditioning: Newey-West t of r_last ~ r_first", subtitle = "dashed = t 2.0 pass line; labels = n days, mean strategy bps/day net",
       x = NULL, y = "NW t-stat") + thm + theme(axis.text.x = element_text(angle = 30, hjust = 1))

p <- p1 / (p2 | p3) & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "intraday_momentum.png"), p, width = 13, height = 10, dpi = 150, bg = bg)
