#!/usr/bin/env Rscript
# Navy-theme chart for the crypto grid study (python/research/structural/crypto_grid_study.py).
# Panel 1: cumulative grid equity vs vol-matched buy-and-hold, per symbol (12-month 1-min window, live policy).
# Panel 2: P&L decomposition bars per symbol (round-trip income, fees, inventory, halt losses, total).
# Panel 3: halt count and max inventory as % of budget per symbol.
suppressPackageStartupMessages({library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork)})
out <- "/home/jhcv/discordBot/outputs/research/structural"
navy <- "#02233F"; grid <- "#274066"
pal <- c("#2E7BE0", "#E0A02E", "#2E9E4E", "#7B4CE0", "#E0392E")

th <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = navy, colour = navy),
        panel.background = element_rect(fill = navy, colour = navy),
        panel.grid.major = element_line(colour = grid), panel.grid.minor = element_blank(),
        text = element_text(colour = "white"), axis.text = element_text(colour = "white"),
        axis.title = element_text(colour = "white"), plot.title = element_text(colour = "white", face = "bold"),
        plot.subtitle = element_text(colour = "white", size = 9), plot.caption = element_text(colour = "white", size = 8),
        strip.text = element_text(colour = "white", face = "bold"),
        legend.text = element_text(colour = "white"), legend.title = element_text(colour = "white"),
        legend.background = element_rect(fill = navy, colour = navy), legend.key = element_rect(fill = navy, colour = navy),
        legend.position = "top")

daily <- read_csv(file.path(out, "crypto_grid_daily.csv"), show_col_types = FALSE) %>%
  filter(window == "12m_1min", policy == "live", symbol != "BOOK")
res <- read_csv(file.path(out, "crypto_grid_results.csv"), show_col_types = FALSE)
dec <- read_csv(file.path(out, "crypto_grid_decomposition.csv"), show_col_types = FALSE) %>%
  filter(window == "12m_1min", policy == "live")

budgets <- res %>% filter(window == "12m_1min", policy == "live", symbol != "BOOK") %>% select(symbol, budget, verdict, halts, max_inv_pct, total_pnl_net)

eq <- daily %>% left_join(budgets, by = "symbol") %>%
  transmute(date, symbol, Grid = grid_equity - budget, `Buy-and-hold, vol-matched` = bh_volmatched_equity - budget) %>%
  pivot_longer(c(Grid, `Buy-and-hold, vol-matched`), names_to = "series", values_to = "pnl") %>%
  mutate(symbol = factor(symbol, levels = c("BTC/USD", "ETH/USD", "SOL/USD")))
lab <- budgets %>% mutate(txt = sprintf("%s  net %+.0f USD", verdict, total_pnl_net),
                          symbol = factor(symbol, levels = c("BTC/USD", "ETH/USD", "SOL/USD")))

p1 <- ggplot(eq, aes(date, pnl, colour = series)) +
  geom_hline(yintercept = 0, colour = "white", linewidth = 0.3) +
  geom_line(linewidth = 0.7) +
  geom_text(data = lab, aes(x = min(eq$date), y = Inf, label = txt), inherit.aes = FALSE,
            hjust = 0, vjust = 1.5, colour = "white", size = 3.2) +
  facet_wrap(~symbol, ncol = 3, scales = "free_y") +
  scale_colour_manual(values = c("Grid" = pal[1], "Buy-and-hold, vol-matched" = pal[2])) +
  labs(title = "Live crypto grid replayed on 1-minute bars, last 12 months, live halt policy (reset next UTC day)",
       subtitle = "Cumulative P&L in USD net of 0.15% maker fees on both legs and 0.25% taker on breakout flattens. Benchmark holds the grid's own average USD inventory.",
       x = NULL, y = "P&L (USD)", colour = NULL) + th

dl <- dec %>% select(symbol, round_trip_income, fees, inventory, halt_losses, total) %>%
  pivot_longer(-symbol, names_to = "term", values_to = "usd") %>%
  mutate(term = factor(term, levels = c("round_trip_income", "fees", "inventory", "halt_losses", "total"),
                       labels = c("Round-trip income", "Fees", "Inventory (unrealized)", "Halt losses", "Total net")),
         symbol = factor(symbol, levels = c("BTC/USD", "ETH/USD", "SOL/USD")))
p2 <- ggplot(dl, aes(term, usd, fill = term)) +
  geom_hline(yintercept = 0, colour = "white", linewidth = 0.3) +
  geom_col(width = 0.7) +
  geom_text(aes(label = sprintf("%+.0f", usd), vjust = ifelse(usd >= 0, -0.3, 1.2)), colour = "white", size = 2.8) +
  facet_wrap(~symbol, ncol = 3, scales = "free_y") +
  scale_fill_manual(values = c(pal[3], pal[5], pal[4], pal[2], pal[1])) +
  labs(title = "Where the money goes: total = round-trip income + fees + inventory + halt losses",
       x = NULL, y = "USD", fill = NULL) + th +
  theme(axis.text.x = element_text(angle = 20, hjust = 1), legend.position = "none")

hb <- budgets %>% select(symbol, halts, max_inv_pct) %>%
  pivot_longer(-symbol, names_to = "metric", values_to = "value") %>%
  mutate(metric = factor(metric, levels = c("halts", "max_inv_pct"), labels = c("Breakout halts (count)", "Max inventory (% of budget)")),
         symbol = factor(symbol, levels = c("BTC/USD", "ETH/USD", "SOL/USD")))
p3 <- ggplot(hb, aes(symbol, value, fill = symbol)) +
  geom_col(width = 0.6) +
  geom_text(aes(label = sprintf("%.0f", value)), vjust = -0.3, colour = "white", size = 3) +
  facet_wrap(~metric, scales = "free_y") +
  scale_fill_manual(values = pal[1:3]) +
  scale_y_continuous(expand = expansion(mult = c(0, 0.15))) +
  labs(title = "Risk taken to earn it", x = NULL, y = NULL,
       caption = "Fill model: limit fills when bar low/high crosses the level, at the level price, one fill per level per bar, re-arms live from the next bar. ATR from completed UTC daily bars. Data: Alpaca crypto bars.") +
  th + theme(legend.position = "none")

p <- p1 / p2 / p3 + plot_layout(heights = c(1.3, 1, 0.8))
ggsave(file.path(out, "crypto_grid.png"), p, bg = navy, width = 13, height = 13, dpi = 150)
cat("saved", file.path(out, "crypto_grid.png"), "\n")
