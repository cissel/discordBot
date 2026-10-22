#!/usr/bin/env Rscript
# SPY + gold trend study chart: cumulative P&L across holdout and confirm windows, drawdowns, rolling leg correlation
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(tidyr); library(patchwork) })
base <- path.expand("~/discordBot/outputs/research/structural")
d <- read_csv(file.path(base, "spy_gold_trend_daily.csv"), show_col_types = FALSE) |> arrange(date)
res <- read_csv(file.path(base, "spy_gold_trend_results.csv"), show_col_types = FALSE)
bg <- "#02233F"; txt <- "white"; grid <- "#274066"
thm <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = bg, colour = bg), panel.background = element_rect(fill = bg, colour = bg),
        panel.grid.major = element_line(colour = grid, linewidth = 0.3), panel.grid.minor = element_blank(),
        text = element_text(colour = txt), axis.text = element_text(colour = txt), axis.title = element_text(colour = txt),
        plot.title = element_text(colour = txt, face = "bold"), plot.subtitle = element_text(colour = txt),
        plot.caption = element_text(colour = txt), legend.text = element_text(colour = txt),
        legend.title = element_text(colour = txt), legend.background = element_rect(fill = bg, colour = bg),
        legend.key = element_rect(fill = bg, colour = bg), strip.text = element_text(colour = txt))
a1 <- "1 SPY 200dma x inv vol"; a3 <- "3 SPY+gold trend N=2 (park)"; a4 <- "4 SPY+gold always-long vol-sized"
cols <- c("#2E7BE0", "#2E9E4E", "#E0A02E"); names(cols) <- c(a1, a3, a4)
split_date <- min(d$date[d$window == "CONFIRM"])

sh <- res |> filter(period == "full", arm %in% c(a1, a3)) |>
  mutate(lab = sprintf("%s %s: Sharpe %.2f, DD %.0f%%", window, ifelse(arm == a1, "SPY", "SPY+gold"), sharpe, max_dd))
sub <- paste(paste(sh$lab[sh$window == "HOLDOUT"], collapse = "   |   "),
             paste(sh$lab[sh$window == "CONFIRM"], collapse = "   |   "), sep = "\n")

eq <- d |> select(date, all_of(c(a1, a3, a4))) |>
  mutate(across(-date, ~ cumprod(1 + .x) - 1)) |> pivot_longer(-date, names_to = "arm", values_to = "eq") |>
  mutate(arm = factor(arm, levels = c(a1, a3, a4)))
ymax <- max(eq$eq) * 100
p1 <- ggplot(eq, aes(date, eq * 100, colour = arm)) + geom_line(linewidth = 0.9) +
  geom_vline(xintercept = split_date, colour = txt, linetype = "dashed", linewidth = 0.5) +
  annotate("text", x = split_date, y = ymax, label = "HOLDOUT 2012-2016  |  CONFIRM 2017-2026", colour = txt, hjust = 0.5, vjust = 0, size = 3.5) +
  scale_colour_manual(values = cols) +
  labs(title = "SPY + gold trend book vs SPY alone: cumulative P&L, net of 0.25bp per unit exposure change per asset",
       subtitle = sub, x = NULL, y = "cumulative return (%)", colour = NULL) +
  thm + theme(legend.position = "bottom")

dd <- d |> select(date, all_of(c(a1, a3))) |>
  mutate(across(-date, ~ { e <- cumprod(1 + .x); (e / cummax(e) - 1) * 100 })) |>
  pivot_longer(-date, names_to = "arm", values_to = "dd") |> mutate(arm = factor(arm, levels = c(a1, a3)))
p2 <- ggplot(dd, aes(date, dd, colour = arm, fill = arm)) + geom_area(alpha = 0.25, position = "identity") +
  geom_line(linewidth = 0.7) + geom_vline(xintercept = split_date, colour = txt, linetype = "dashed", linewidth = 0.5) +
  scale_colour_manual(values = cols[c(a1, a3)]) + scale_fill_manual(values = cols[c(a1, a3)]) +
  labs(title = "Drawdown: SPY trend vs SPY+gold trend (park)", x = NULL, y = "drawdown (%)", colour = NULL, fill = NULL) +
  thm + theme(legend.position = "bottom")

rc <- d |> select(date, roll252_leg_corr) |> filter(!is.na(roll252_leg_corr))
p3 <- ggplot(rc, aes(date, roll252_leg_corr)) +
  geom_hline(yintercept = 0, colour = txt, linewidth = 0.3) +
  geom_line(colour = "#7B4CE0", linewidth = 0.8) +
  geom_vline(xintercept = split_date, colour = txt, linetype = "dashed", linewidth = 0.5) +
  labs(title = "Rolling 1-year correlation of the two trend legs' daily P&L (SPY leg vs gold leg)",
       x = NULL, y = "correlation (252d)") + thm

p <- p1 / p2 / p3 + plot_layout(heights = c(1.4, 1, 0.8)) & theme(plot.background = element_rect(fill = bg, colour = bg))
ggsave(file.path(base, "spy_gold_trend.png"), p, width = 13, height = 12, dpi = 150, bg = bg)
