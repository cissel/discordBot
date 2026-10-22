#!/usr/bin/env Rscript
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr) })
base <- path.expand("~/discordBot/outputs/research/structural")
res <- read_csv(file.path(base, "skew_premium_results.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), strip.text = element_text(colour = txt, face = "bold"),
        legend.position = "none")
lab <- c(pnl_vol = "short-variance P&L (vol pts / entry)", down_share = "realised downside share of variance",
         rv_iv = "realised / implied vol", crash21 = "P(21d drawdown < -5%)")
res <- res |> mutate(outcome = factor(lab[outcome], levels = lab),
                     cell = factor(cell, levels = c("skew low", "skew mid", "skew high", "skew z > +1", "skew z < -1")),
                     kind = ifelse(grepl("z", cell), "z-score", "tercile"))
p <- ggplot(res, aes(cell, mean, fill = kind)) + geom_col() +
  geom_text(aes(label = sprintf("%.3f\nn=%d", mean, n)), vjust = -0.2, colour = txt, size = 3) +
  facet_wrap(~outcome, scales = "free_y", ncol = 2) +
  scale_fill_manual(values = c(tercile = "#2E7BE0", `z-score` = "#7B4CE0")) +
  scale_y_continuous(expand = expansion(mult = c(0.05, 0.3))) +
  labs(title = "C2 skew premium: forward 21-day outcomes by CBOE SKEW state (lagged), SPY 2016-2026",
       subtitle = "high minus low tercile: P&L +0.98 vol pts (NW t 0.84), downside share +0.02 (t 0.21), RV/IV -0.08, crash prob -0.10 (t -1.6). FAIL.",
       x = NULL, y = NULL,
       caption = "Exploratory, not pre-registered: SKEW z < -1 (complacent skew) precedes a 21d drawdown worse than -5% 31% of the time vs 12% otherwise.") + thm
ggsave(file.path(base, "skew_premium.png"), p, width = 13, height = 9, dpi = 150, bg = bg)
