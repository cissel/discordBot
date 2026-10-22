#!/usr/bin/env Rscript
# T3 multi-asset trend study chart: cumulative P&L, drawdowns, per-asset Sharpe
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
d <- read_csv(file.path(base, "multi_asset_trend_daily.csv"), show_col_types = FALSE)
res <- read_csv(file.path(base, "multi_asset_trend_results.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt), axis.title = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg), strip.text = element_text(colour = txt))
a1 <- "1 SPY 200dma x GEX scalar (live)"; a2 <- "2 SPY 200dma x inv vol"
a3 <- "3 basket TSMOM (SPY QQQ GLD TLT)"; a4 <- "4 basket always-long equal-risk"
cols <- c("#2E7BE0", "#E0A02E", "#2E9E4E", "#7B4CE0"); names(cols) <- c(a1, a2, a3, a4)

eq <- d |> select(date, all_of(c(a1, a2, a3, a4))) |> arrange(date) |>
  mutate(across(-date, ~ cumprod(1 + .x) - 1)) |> pivot_longer(-date, names_to = "arm", values_to = "eq") |>
  mutate(arm = factor(arm, levels = c(a1, a2, a3, a4)))
p1 <- ggplot(eq, aes(date, eq * 100, colour = arm)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = cols) +
  labs(title = "T3 multi-asset trend: cumulative P&L, 2017-2026, net of 0.25bp per unit exposure change",
       subtitle = "signals from the prior close; basket weight = (1/4) x min(1.5, 0.12 / 20d realised vol) when above own 200dma",
       x = NULL, y = "cumulative return (%)", colour = NULL) + thm + theme(legend.position = "bottom") +
  guides(colour = guide_legend(nrow = 2))

dd <- d |> select(date, all_of(c(a1, a3))) |> arrange(date) |>
  mutate(across(-date, ~ { e <- cumprod(1 + .x); (e / cummax(e) - 1) * 100 })) |>
  pivot_longer(-date, names_to = "arm", values_to = "dd") |> mutate(arm = factor(arm, levels = c(a1, a3)))
p2 <- ggplot(dd, aes(date, dd, colour = arm, fill = arm)) + geom_area(alpha = 0.25, position = "identity") +
  geom_line(linewidth = 0.7) + scale_colour_manual(values = cols[c(a1, a3)]) + scale_fill_manual(values = cols[c(a1, a3)]) +
  labs(title = "Drawdown: live SPY vs basket TSMOM", x = NULL, y = "drawdown (%)", colour = NULL, fill = NULL) +
  thm + theme(legend.position = "bottom")

sh <- res |> filter(period == "pooled") |>
  filter(grepl("^standalone", arm) | arm %in% c(a1, a3)) |>
  mutate(label = case_when(arm == a1 ~ "SPY live (GEX scalar)", arm == a3 ~ "basket TSMOM",
                           TRUE ~ paste0(sub("standalone (\\w+) .*", "\\1", arm), " 200dma x inv vol")),
         kind = case_when(arm == a3 ~ "basket", arm == a1 ~ "live", TRUE ~ "asset")) |>
  mutate(label = factor(label, levels = c("SPY live (GEX scalar)", "SPY 200dma x inv vol", "QQQ 200dma x inv vol",
                                          "GLD 200dma x inv vol", "TLT 200dma x inv vol", "IEF 200dma x inv vol", "basket TSMOM")))
p3 <- ggplot(sh, aes(label, sharpe, fill = kind)) + geom_col() +
  geom_hline(yintercept = 0, colour = txt, linewidth = 0.3) +
  geom_text(aes(label = sprintf("%.2f\nDD %.0f%%", sharpe, max_dd), vjust = ifelse(sharpe >= 0, -0.2, 1.2)), colour = txt, size = 3) +
  scale_fill_manual(values = c(asset = "#7B4CE0", live = "#2E7BE0", basket = "#2E9E4E"), guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0.35, 0.35))) +
  labs(title = "Pooled Sharpe: assets standalone vs basket", x = NULL, y = "Sharpe") +
  thm + theme(axis.text.x = element_text(angle = 25, hjust = 1))

p <- p1 / (p2 | p3) + plot_layout(heights = c(1.2, 1)) & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "multi_asset_trend.png"), p, width = 13, height = 11, dpi = 150, bg = bg)
