#!/usr/bin/env Rscript
# Navy-theme chart for the SPY horizon / dumb-baseline study.
# Panel 1: cumulative net P&L (additive, close-to-close) of five strategies, 2019-2026.
# Panel 2: per-year Sharpe grouped bars (5d strategies use the average over the 5 phase offsets).
suppressPackageStartupMessages({library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork)})
out <- "/home/jhcv/discordBot/outputs/research/structural"
navy <- "#02233F"; grid <- "#274066"
cols <- c("ML 1d logistic" = "#2E7BE0", "200dma rule" = "#E0A02E", "Always long" = "#2E9E4E",
          "5d logistic" = "#7B4CE0", "5d GBM" = "#E0392E")
lv <- names(cols)

daily <- read_csv(file.path(out, "horizon_baseline_daily.csv"), show_col_types = FALSE) %>%
  transmute(date,
            `ML 1d logistic` = pnl_ML_1d_logistic,
            `200dma rule` = pnl_dma200,
            `Always long` = pnl_always_long,
            `5d logistic` = pnl_5d_logistic_phaseavg,
            `5d GBM` = pnl_5d_gbm_phaseavg) %>%
  pivot_longer(-date, names_to = "strategy", values_to = "pnl") %>%
  group_by(strategy) %>% arrange(date) %>% mutate(cum = cumsum(pnl) * 100) %>% ungroup() %>%
  mutate(strategy = factor(strategy, levels = lv))

res <- read_csv(file.path(out, "horizon_baseline_results.csv"), show_col_types = FALSE) %>%
  filter(phase %in% c("all", "avg"), strategy != "dma200_AND_ML") %>%
  mutate(strategy = recode(strategy, ML_1d_logistic = "ML 1d logistic", dma200 = "200dma rule",
                           always_long = "Always long", `5d_logistic` = "5d logistic", `5d_gbm` = "5d GBM"),
         strategy = factor(strategy, levels = lv))

thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = navy, colour = navy),
        panel.background = element_rect(fill = navy, colour = navy),
        panel.grid.major = element_line(colour = grid), panel.grid.minor = element_line(colour = grid),
        text = element_text(colour = "white"), axis.text = element_text(colour = "white"),
        axis.title = element_text(colour = "white"), plot.title = element_text(colour = "white", face = "bold"),
        plot.subtitle = element_text(colour = "white", size = 9), plot.caption = element_text(colour = "white", size = 8),
        legend.text = element_text(colour = "white"), legend.title = element_text(colour = "white"),
        legend.background = element_rect(fill = navy, colour = navy), legend.key = element_rect(fill = navy),
        legend.position = "top")

p1 <- ggplot(daily, aes(date, cum, colour = strategy)) +
  geom_hline(yintercept = 0, colour = "white", linewidth = 0.3) +
  geom_line(linewidth = 0.8) +
  scale_colour_manual(values = cols) +
  labs(title = "SPY daily strategy: cumulative net P&L, walk-forward 2019-2026",
       subtitle = "Additive sum of daily close-to-close P&L, net of 0.25 bp per round trip. 5d strategies: weekly rebalance, averaged over 5 phase offsets.",
       x = NULL, y = "Cumulative P&L (% of notional)", colour = NULL) + thm

p2 <- ggplot(res, aes(factor(year), sharpe, fill = strategy)) +
  geom_hline(yintercept = 0, colour = "white", linewidth = 0.3) +
  geom_col(position = position_dodge(width = 0.85), width = 0.8) +
  scale_fill_manual(values = cols) +
  labs(title = "Per-year Sharpe (annualised)", x = NULL, y = "Sharpe", fill = NULL,
       caption = "200dma rule: long when yesterday's close is above its 200-day SMA. ML 1d: L2 logistic, long when p >= 0.51. 2026 through Oct 16.") +
  thm + theme(legend.position = "none")

p <- p1 / p2 + plot_layout(heights = c(1.3, 1)) & theme(plot.background = element_rect(fill = navy, colour = navy))
ggsave(file.path(out, "horizon_baseline.png"), p, bg = navy, width = 12, height = 9, dpi = 150)
cat("saved", file.path(out, "horizon_baseline.png"), "\n")
