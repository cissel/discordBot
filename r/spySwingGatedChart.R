#!/usr/bin/env Rscript
# Cumulative P&L: vol-regime-gated vs ungated SPY intraday swing-reversal (4-fold WFCV, 0.12bp/side)
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr) })

inp <- "~/discordBot/outputs/markets/spy_swing_wfcv_gated_daily_pnl.csv"
out <- "~/discordBot/outputs/markets/spy_swing_gated_vs_ungated.png"

d <- read_csv(inp, show_col_types = FALSE) %>%
  mutate(trading_date = as.Date(trading_date)) %>%
  arrange(trading_date) %>%
  mutate(Gated = cumsum(gated_pnl) * 100, Ungated = cumsum(ungated_pnl) * 100)

long <- bind_rows(
  d %>% transmute(trading_date, series = "Ungated", cum = Ungated),
  d %>% transmute(trading_date, series = "Gated (VIX<25 & GEX not Q1)", cum = Gated))

sh <- function(x) if (sd(x) == 0) 0 else mean(x) / sd(x) * sqrt(252)
elig_pct <- mean(d$eligible) * 100
cap <- sprintf("4-fold expanding-window WFCV on 0.15%% zigzag labels, 0.12bp per side. Pooled Sharpe: ungated %+.2f, gated %+.2f. Eligible days %.0f%%.\nGate: prior-day VIX < 25 and prior-day GEX trailing-252 quintile != 1. P&L is 0 on gated-out days. Pre-registered pass rule: FAIL (pooled CI includes 0).",
               sh(d$ungated_pnl), sh(d$gated_pnl), elig_pct)

fold_starts <- d %>% group_by(fold) %>% summarise(start = min(trading_date), .groups = "drop")

bg <- "#02233F"; grid <- "#274066"
p <- ggplot(long, aes(trading_date, cum, colour = series)) +
  geom_vline(data = fold_starts, aes(xintercept = start), colour = grid, linetype = "dashed") +
  geom_hline(yintercept = 0, colour = "white", linewidth = 0.3, alpha = 0.6) +
  geom_line(linewidth = 1) +
  geom_text(data = fold_starts, aes(x = start, y = -Inf, label = paste0("fold ", fold)),
            inherit.aes = FALSE, colour = "white", vjust = -0.5, hjust = -0.1, size = 3.2) +
  scale_colour_manual(values = c("Ungated" = "#9FB7D4", "Gated (VIX<25 & GEX not Q1)" = "#F5B942")) +
  scale_x_date(date_breaks = "6 months", date_labels = "%b %Y") +
  labs(title = "SPY intraday swing-reversal: vol-regime gate vs ungated",
       subtitle = "Cumulative out-of-sample daily P&L (% of notional), walk-forward folds 1 to 4",
       x = NULL, y = "Cumulative P&L (%)", colour = NULL, caption = cap) +
  theme_minimal(base_size = 13) +
  theme(plot.background = element_rect(fill = bg, colour = bg),
        panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid), panel.grid.minor = element_line(colour = grid, linewidth = 0.25),
        text = element_text(colour = "white"), axis.text = element_text(colour = "white"),
        plot.title = element_text(colour = "white", face = "bold"),
        plot.subtitle = element_text(colour = "white"),
        plot.caption = element_text(colour = "white", hjust = 0, size = 9),
        legend.text = element_text(colour = "white"), legend.position = "top",
        legend.background = element_rect(fill = bg, colour = bg), legend.key = element_rect(fill = bg, colour = bg))

ggsave(path.expand(out), p, width = 11, height = 6.5, dpi = 150, bg = bg)
cat("saved", out, "\n")
