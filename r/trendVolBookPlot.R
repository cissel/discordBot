#!/usr/bin/env Rscript
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
d <- read_csv(file.path(base, "trend_vol_book_daily.csv"), show_col_types = FALSE) |> rename(date = 1)
res <- read_csv(file.path(base, "trend_vol_book_results.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg), strip.text = element_text(colour = txt))
keep <- c("always-long", "always-long x scalar", "200dma", "200dma x scalar + 10% band (LIVE)")
eq <- d |> select(date, all_of(keep)) |> arrange(date) |> mutate(across(-date, ~ cumprod(1 + .x) - 1)) |>
  pivot_longer(-date, names_to = "arm", values_to = "eq")
p1 <- ggplot(eq, aes(date, eq * 100, colour = arm)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = c(`always-long` = "#E0A02E", `always-long x scalar` = "#2E9E4E", `200dma` = "#7B4CE0",
                                 `200dma x scalar + 10% band (LIVE)` = "#2E7BE0")) +
  labs(title = "Trend/vol book arms, SPY 2017-2026, unit exposure, net of 0.25bp per exposure change",
       subtitle = "signals from the prior close; vol scalar = clip(0.11 / expected vol, 0.35, 1.25) from the GEX/term lookup",
       x = NULL, y = "cumulative return (%)", colour = NULL) + thm + theme(legend.position = "bottom")
t1 <- res |> filter(test == "T1") |> mutate(arm = factor(arm, levels = arm))
p2 <- ggplot(t1 |> select(arm, sharpe, max_dd) |> pivot_longer(-arm), aes(arm, value, fill = name)) +
  geom_col(position = "dodge") + facet_wrap(~name, scales = "free_y", ncol = 1) +
  scale_fill_manual(values = c(sharpe = "#2E7BE0", max_dd = "#E0392E"), guide = "none") +
  labs(title = "T1 arms: Sharpe and max drawdown (%)", x = NULL, y = NULL) + thm + theme(axis.text.x = element_text(angle = 25, hjust = 1))
t2 <- res |> filter(test == "T2") |> mutate(arm = factor(arm, levels = arm))
p3 <- ggplot(t2, aes(arm, sharpe, fill = grepl("200dma x", arm))) + geom_col() +
  geom_hline(yintercept = res$sharpe[res$arm == "always-long"][1], colour = "#E0A02E", linetype = "dashed") +
  geom_text(aes(label = sprintf("DD %.0f%%", max_dd)), vjust = -0.3, colour = txt, size = 3.2) +
  scale_fill_manual(values = c(`TRUE` = "#2E7BE0", `FALSE` = "#7B4CE0"), guide = "none") +
  labs(title = "T2 knife-edge: MA length and hysteresis", subtitle = "dashed = always-long Sharpe", x = NULL, y = "Sharpe") +
  thm + theme(axis.text.x = element_text(angle = 25, hjust = 1))
p <- p1 / (p2 | p3) & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "trend_vol_book.png"), p, width = 13, height = 11, dpi = 150, bg = bg)
