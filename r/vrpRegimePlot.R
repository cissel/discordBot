#!/usr/bin/env Rscript
# vrpRegimePlot.R - conditional variance risk premium study (navy theme)
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
d <- read_csv(file.path(base, "vrp_regime_daily.csv"), show_col_types = FALSE)
cells <- read_csv(file.path(base, "vrp_regime_cells.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg))

a <- d |> arrange(date) |> mutate(gated = ifelse(gate_both, varswap_pnl_vol, 0)) |>
  transmute(date, `every day` = cumsum(varswap_pnl_vol) / 21, `gated (no GEX q1, no inversion)` = cumsum(gated) / 21) |>
  pivot_longer(-date, names_to = "series", values_to = "cum")
p1 <- ggplot(a, aes(date, cum, colour = series)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = c(`every day` = "#E0A02E", `gated (no GEX q1, no inversion)` = "#2E7BE0")) +
  labs(title = "Track A: short 30-day SPY variance swap, cumulative vol points (1 vega per month equivalent)",
       subtitle = "strike = VIX^2, settle on next-21-day realised variance; gates lagged one day", x = NULL, y = "vol points", colour = NULL) + thm

cells$cell <- factor(cells$cell, levels = cells$cell)
p2 <- ggplot(cells, aes(cell, mean_volpts, fill = mean_volpts > 0)) + geom_col() +
  scale_fill_manual(values = c(`TRUE` = "#2E9E4E", `FALSE` = "#E0392E"), guide = "none") +
  geom_text(aes(label = sprintf("RV/IV %.2f\nn=%d", rv_over_iv, n), vjust = ifelse(mean_volpts > 0, -0.2, 1.2)), colour = txt, size = 3.2) +
  labs(title = "Mean P&L per entry by regime cell", x = NULL, y = "vol points") + thm + coord_cartesian(clip = "off")

b <- d |> arrange(date) |> filter(!is.na(VXX_gate_none)) |>
  transmute(date, `short VXX every day` = cumprod(1 + VXX_gate_none) - 1, `short VXX gated` = cumprod(1 + VXX_gate_both) - 1) |>
  pivot_longer(-date, names_to = "series", values_to = "eq")
p3 <- ggplot(b, aes(date, eq * 100, colour = series)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = c(`short VXX every day` = "#E0A02E", `short VXX gated` = "#2E7BE0")) +
  labs(title = "Track B: short VXX (data from Jul 2020, post-crash)", subtitle = "the same gates HURT here: VXX shorts earn most in post-inversion collapses",
       x = NULL, y = "cumulative return (%)", colour = NULL) + thm

p <- p1 / (p2 | p3) & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "vrp_regime.png"), p, width = 13, height = 10, dpi = 150, bg = bg)
