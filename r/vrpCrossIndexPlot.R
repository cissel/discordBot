#!/usr/bin/env Rscript
# vrpCrossIndexPlot.R - short variance swap premium across SPY/QQQ/IWM, with the SPX term gate (navy theme)
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
d <- read_csv(file.path(base, "vrp_cross_index_daily.csv"), show_col_types = FALSE)
res <- read_csv(file.path(base, "vrp_cross_index_results.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
cols <- c(SPY = "#2E7BE0", QQQ = "#E0A02E", IWM = "#2E9E4E")
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg), strip.text = element_text(colour = txt, face = "bold"))

a <- d |> arrange(date) |>
  select(date, ends_with("_pnl_ungated"), ends_with("_pnl_gated")) |>
  pivot_longer(-date, names_to = c("index", "gate"), names_pattern = "(\\w+)_pnl_(\\w+)", values_to = "pnl") |>
  group_by(index, gate) |> mutate(cum = cumsum(pnl) / 21) |> ungroup() |>
  mutate(index = factor(index, levels = names(cols)),
         gate = factor(ifelse(gate == "gated", "gated (not inverted, lagged)", "every day"),
                       levels = c("every day", "gated (not inverted, lagged)")))
p1 <- ggplot(a, aes(date, cum, colour = index, linetype = gate)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = cols) + scale_linetype_manual(values = c("dotted", "solid")) +
  labs(title = "Short 30-day variance swap, cumulative vol points (1 vega per month equivalent)",
       subtitle = "strike = IV^2 (VIX, VXN, RVX); settle on next-21-day realised variance; gate = VIX9D <= VIX3M lagged one day, same gate for all",
       x = NULL, y = "vol points", colour = NULL, linetype = NULL) + thm

b <- res |> filter(period == "full") |>
  mutate(index = factor(index, levels = names(cols)),
         gate = factor(ifelse(gate == "gate_term", "gated", "every day"), levels = c("every day", "gated")))
p2 <- ggplot(b, aes(index, mean_volpts, fill = gate)) + geom_col(position = position_dodge(width = 0.7), width = 0.65) +
  geom_text(aes(label = sprintf("%.2f\nhit %.0f%%\nSh %.2f", mean_volpts, 100 * hit_rate, monthly_sharpe)),
            position = position_dodge(width = 0.7), vjust = -0.15, colour = txt, size = 3.1, lineheight = 0.9) +
  scale_fill_manual(values = c(`every day` = "#7B4CE0", gated = "#E0392E")) +
  scale_y_continuous(expand = expansion(mult = c(0.05, 0.35))) +
  labs(title = "Mean P&L per entry by index and gate state (full window)", x = NULL, y = "vol points per unit vega", fill = NULL) + thm

p <- p1 / p2 + plot_layout(heights = c(1.4, 1)) & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "vrp_cross_index.png"), p, width = 13, height = 10, dpi = 150, bg = bg)
