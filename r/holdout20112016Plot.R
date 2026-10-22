#!/usr/bin/env Rscript
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
d <- read_csv(file.path(base, "holdout_2011_2016_daily.csv"), show_col_types = FALSE) |> rename(date = 1)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), strip.text = element_text(colour = txt, face = "bold"),
        legend.text = element_text(colour = txt), legend.title = element_text(colour = txt),
        legend.background = element_rect(fill = bg, colour = bg), legend.key = element_rect(fill = bg, colour = bg))
g <- d |> filter(!is.na(gex_q)) |> group_by(period, gex_q) |> summarise(pnl = mean(pnl_vol), rv_iv = mean(rv_iv), n = n(), .groups = "drop")
p1 <- ggplot(g, aes(factor(gex_q), pnl, fill = period)) + geom_col(position = "dodge") +
  geom_text(aes(label = sprintf("RV/IV\n%.2f", rv_iv), group = period), position = position_dodge(0.9), vjust = -0.2, colour = txt, size = 2.8) +
  scale_fill_manual(values = c(`holdout 2011-2016` = "#E0A02E", `in-sample 2016-2026` = "#2E7BE0")) +
  scale_y_continuous(expand = expansion(mult = c(0.05, 0.3))) +
  labs(title = "Short-variance P&L by GEX quintile: the holdout reverses the in-sample ordering",
       subtitle = "q1 = most negative dealer gamma. In-sample q1 is the worst cell (0.2 vol pts); on 2011-2016 it is the best (3.8)",
       x = "GEX trailing-252 quintile (lagged)", y = "vol points per entry", fill = NULL) + thm + theme(legend.position = "bottom")
sk <- d |> filter(!is.na(skew_z)) |> mutate(cell = ifelse(skew_z < -1, "SKEW z < -1", "rest")) |>
  group_by(period, cell) |> summarise(crash = mean(crash21), n = n(), .groups = "drop")
p2 <- ggplot(sk, aes(cell, crash, fill = period)) + geom_col(position = "dodge") +
  geom_text(aes(label = sprintf("%.0f%%\nn=%d", crash * 100, n), group = period), position = position_dodge(0.9), vjust = -0.2, colour = txt, size = 3) +
  scale_fill_manual(values = c(`holdout 2011-2016` = "#E0A02E", `in-sample 2016-2026` = "#2E7BE0"), guide = "none") +
  scale_y_continuous(labels = scales::percent, expand = expansion(mult = c(0.05, 0.3))) +
  labs(title = "Complacent-skew lead: P(21d drawdown < -5%)", subtitle = "in-sample 30% vs 12%; holdout 11% vs 9% (t 0.4)", x = NULL, y = NULL) + thm
cum <- d |> arrange(date) |> mutate(gated = ifelse(is.na(gex_q), NA, pnl_vol)) |>
  group_by(period) |> mutate(`every day` = cumsum(pnl_vol) / 21) |> ungroup() |>
  select(date, period, `every day`) |> pivot_longer(`every day`, names_to = "arm", values_to = "cum")
p3 <- ggplot(cum, aes(date, cum, colour = period)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = c(`holdout 2011-2016` = "#E0A02E", `in-sample 2016-2026` = "#2E7BE0"), guide = "none") +
  labs(title = "Unconditional short variance, cumulative vol points (per month-equivalent), reset at 2016-06",
       subtitle = "holdout monthly Sharpe 2.22, worst calendar month -16; in-sample 0.76 and -139 (Feb 2020)", x = NULL, y = "vol points") + thm
p <- p1 / (p2 | p3) & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "holdout_2011_2016.png"), p, width = 13, height = 10, dpi = 150, bg = bg)
