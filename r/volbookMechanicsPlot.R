#!/usr/bin/env Rscript
# volbookMechanicsPlot.R: exit-policy and sizing-arm comparison for the SPY vol book (synthetic MTM variance swap)
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
ex <- read_csv(file.path(base, "volbook_mechanics_exits.csv"), show_col_types = FALSE)
sz <- read_csv(file.path(base, "volbook_mechanics_sizing.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
pal <- c(holdout = "#E0A02E", H1 = "#2E7BE0", H2 = "#2E9E4E")
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt), axis.title = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), strip.text = element_text(colour = txt, face = "bold"),
        legend.text = element_text(colour = txt), legend.title = element_text(colour = txt),
        legend.background = element_rect(fill = bg, colour = bg), legend.key = element_rect(fill = bg, colour = bg))
ex <- ex |> filter(!grepl("^5b", policy)) |>
  mutate(period = factor(period, levels = c("holdout", "H1", "H2")), policy = factor(policy, levels = unique(policy)))
sz <- sz |> mutate(period = factor(period, levels = c("holdout", "H1", "H2")), arm = factor(arm, levels = unique(arm)))

p1 <- ggplot(ex, aes(policy, monthly_sharpe, fill = period)) + geom_col(position = position_dodge(0.85), width = 0.8) +
  geom_text(aes(label = sprintf("%.2f", monthly_sharpe), group = period), position = position_dodge(0.85), vjust = -0.3, colour = txt, size = 2.8) +
  scale_fill_manual(values = pal) + scale_y_continuous(expand = expansion(mult = c(0.05, 0.2))) +
  labs(title = "Panel 1: monthly Sharpe by exit policy (short 30d variance swap, non-inverted entries, net of cost)",
       subtitle = "The live rule (5) has the LOWEST Sharpe in every period; the stop (5 vs 4) costs 0.3 to 0.5 Sharpe each time",
       x = NULL, y = "monthly Sharpe (stride 21, 3 offsets)", fill = NULL) + thm + theme(legend.position = "bottom")

p2 <- ggplot(ex, aes(policy, worst_month, fill = period)) + geom_col(position = position_dodge(0.85), width = 0.8) +
  geom_text(aes(label = sprintf("%.0f", worst_month), group = period), position = position_dodge(0.85), vjust = 1.3, colour = txt, size = 2.8) +
  scale_fill_manual(values = pal, guide = "none") + scale_y_continuous(expand = expansion(mult = c(0.2, 0.05))) +
  labs(title = "Panel 2: worst calendar-month mean P&L by exit policy (vol pts per unit vega)",
       subtitle = "Any early exit cuts the Feb 2020 tail (hold: -111); the live rule is the best tail in every period",
       x = NULL, y = "worst month (vol pts)") + thm

szl <- sz |> select(arm, period, monthly_sharpe, worst_month) |>
  pivot_longer(c(monthly_sharpe, worst_month), names_to = "metric", values_to = "value") |>
  mutate(metric = recode(metric, monthly_sharpe = "monthly Sharpe", worst_month = "worst month (vol pts)"))
p3 <- ggplot(szl, aes(arm, value, fill = period)) + geom_col(position = position_dodge(0.85), width = 0.8) +
  geom_text(aes(label = ifelse(metric == "monthly Sharpe", sprintf("%.2f", value), sprintf("%.0f", value)), group = period,
                vjust = ifelse(value >= 0, -0.3, 1.3)), position = position_dodge(0.85), colour = txt, size = 2.8) +
  facet_wrap(~metric, scales = "free_y") + scale_fill_manual(values = pal, guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0.2, 0.2))) +
  labs(title = "Panel 3: sizing arms, hold to expiry, mean size normalised to 1 per arm",
       subtitle = "20/VIX and the GEX regime lookup do not beat constant size on 2011-2016 or 2016-2021; the vol tail is not a VIX-level tail",
       x = NULL, y = NULL) + thm

p <- p1 / p2 / p3 + plot_annotation(
  caption = "Synthetic MTM: accrued realised variance + remaining VIX^2; credit proxied by trailing-252 median final P&L; no skew, no gamma path. Study: volbook_mechanics_study.py",
  theme = theme(plot.background = element_rect(fill = bg, colour = bg), plot.caption = element_text(colour = txt))) &
  theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "volbook_mechanics.png"), p, width = 13, height = 14, dpi = 150, bg = bg)
