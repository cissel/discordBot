#!/usr/bin/env Rscript
# spyVolRegimePlot.R - vol-regime scalar backtest (navy theme)
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot")
d <- read_csv(file.path(base, "outputs/markets/spy_vol_regime_backtest_daily.csv"), show_col_types = FALSE) |>
  mutate(date = as.Date(date))
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg), strip.text = element_text(colour = txt))

eq <- d |> select(date, `constant 10% exposure` = eq_const, `vol-regime scaled` = eq_scaled) |>
  pivot_longer(-date, names_to = "leg", values_to = "equity")
p1 <- ggplot(eq, aes(date, equity, colour = leg)) + geom_line(linewidth = 0.8) +
  scale_colour_manual(values = c("constant 10% exposure" = "#7B4CE0", "vol-regime scaled" = "#2E9E4E")) +
  labs(title = "SPY autopilot sizing: constant exposure vs GEX / term-structure vol scalar",
       subtitle = "2016-2026, 10% base exposure proxy, 1bp per side on exposure changes, all inputs lagged one day",
       x = NULL, y = "equity (start = 1)", colour = NULL) + thm + theme(legend.position = "top")

p2 <- ggplot(d, aes(date, scalar)) + geom_line(colour = "#E0A02E", linewidth = 0.4) +
  geom_hline(yintercept = 1, colour = txt, linewidth = 0.3, alpha = 0.6) +
  labs(x = NULL, y = "scalar", caption = "scalar = 0.11 / (VIX x RV/IV ratio by GEX quintile; 1.01 if VIX9D > VIX3M), clipped 0.35-1.25") + thm

v <- d |> filter(!is.na(gex_q)) |> group_by(gex_q = factor(gex_q)) |>
  summarise(raw = sd(SPY_ret) * sqrt(252) * 100, scaled = sd(SPY_ret * scalar) * sqrt(252) * 100 / mean(scalar) * 1, .groups = "drop") |>
  pivot_longer(-gex_q, names_to = "leg", values_to = "ann_vol")
p3 <- ggplot(v, aes(gex_q, ann_vol, fill = leg)) + geom_col(position = "dodge") +
  scale_fill_manual(values = c(raw = "#2E7BE0", scaled = "#2E9E4E")) +
  labs(x = "GEX trailing quintile (0 = lowest)", y = "realized vol of position, % ann", fill = NULL,
       title = "Does the scalar equalize risk across GEX regimes?") + thm

p <- (p1 / (p2 | p3)) + plot_layout(heights = c(2, 1.2)) & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "outputs/markets/spy_vol_regime_backtest.png"), p, width = 12, height = 8, dpi = 150, bg = bg)
