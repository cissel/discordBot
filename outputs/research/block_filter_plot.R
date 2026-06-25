library(tidyverse)
library(ggplot2)
library(scales)
library(patchwork)

# ── navy theme ─────────────────────────────────────────────────────────────
navy_theme <- function() {
  theme_minimal(base_size = 11) +
    theme(
      plot.background    = element_rect(fill = "#0d1b2a", color = NA),
      panel.background   = element_rect(fill = "#0d1b2a", color = NA),
      panel.grid.major   = element_line(color = "#1e3050", linewidth = 0.4),
      panel.grid.minor   = element_line(color = "#1a2840", linewidth = 0.2),
      text               = element_text(color = "#c8d8e8"),
      axis.text          = element_text(color = "#8aa0b8", size = 8),
      axis.title         = element_text(color = "#c8d8e8", size = 9),
      plot.title         = element_text(color = "#ffffff", face = "bold", size = 12),
      plot.subtitle      = element_text(color = "#8aa0b8", size = 9),
      legend.background  = element_rect(fill = "#0d1b2a", color = NA),
      legend.text        = element_text(color = "#c8d8e8", size = 8),
      legend.title       = element_text(color = "#c8d8e8", size = 8),
      strip.background   = element_rect(fill = "#1e3050", color = NA),
      strip.text         = element_text(color = "#ffffff", face = "bold", size = 9),
      plot.caption       = element_text(color = "#5a7090", size = 7),
      legend.key         = element_rect(fill = NA, color = NA)
    )
}

BASE <- "/home/jhcv/discordBot/outputs/research"

eq   <- read_csv(file.path(BASE, "filtered_equity.csv"), show_col_types = FALSE) %>%
  mutate(date = as.Date(date))
smry <- read_csv(file.path(BASE, "filtered_summary.csv"), show_col_types = FALSE)

# ── define the two active data windows ────────────────────────────────────
# Window 1: Jan 2022 - Mar 2022 (backfill got this far before stopping)
# Window 2: Jun 2025 - Jun 2026 (live data)
w1_start <- as.Date("2022-01-03")
w1_end   <- as.Date("2022-03-08")   # last backfilled day
w2_start <- as.Date("2025-06-02")   # first live day
w2_end   <- as.Date("2026-06-23")

# ── re-base equity curves within each window to 1.0 at window start ───────
rebase <- function(df, start_date) {
  df %>%
    filter(date >= start_date) %>%
    arrange(date) %>%
    mutate(across(-date, ~ . / first(.)))
}

eq_w1 <- rebase(eq %>% filter(date >= w1_start, date <= w1_end), w1_start)
eq_w2 <- rebase(eq %>% filter(date >= w2_start, date <= w2_end), w2_start)

# ── helper: pivot to long with friendly names ──────────────────────────────
make_long <- function(df) {
  df %>%
    select(date, BnH, Baseline, FA, FD, `C_A+D`, `C_A+B+D`, `C_A+C+D`) %>%
    rename(
      `BnH`              = BnH,
      `Baseline`         = Baseline,
      `A: DIX>=med`      = FA,
      `D: CVD>0`         = FD,
      `A+D (best)`       = `C_A+D`,
      `A+B+D`            = `C_A+B+D`,
      `A+C+D`            = `C_A+C+D`
    ) %>%
    pivot_longer(-date, names_to = "strategy", values_to = "equity")
}

long_w1 <- make_long(eq_w1)
long_w2 <- make_long(eq_w2)

line_pal <- c(
  "BnH"         = "#4fc3f7",
  "Baseline"    = "#546e7a",
  "A: DIX>=med" = "#26c6da",
  "D: CVD>0"    = "#66bb6a",
  "A+D (best)"  = "#ffca28",
  "A+B+D"       = "#ef5350",
  "A+C+D"       = "#ab47bc"
)
line_sz <- c(
  "BnH"         = 1.2,
  "Baseline"    = 0.8,
  "A: DIX>=med" = 0.9,
  "D: CVD>0"    = 0.9,
  "A+D (best)"  = 1.5,
  "A+B+D"       = 0.9,
  "A+C+D"       = 0.9
)

strat_order <- c("BnH","Baseline","A: DIX>=med","D: CVD>0","A+D (best)","A+B+D","A+C+D")
long_w1 <- long_w1 %>% mutate(strategy = factor(strategy, levels = strat_order))
long_w2 <- long_w2 %>% mutate(strategy = factor(strategy, levels = strat_order))

equity_panel <- function(data, title, subtitle, show_legend = FALSE) {
  p <- ggplot(data, aes(x = date, y = equity, color = strategy, linewidth = strategy)) +
    geom_line(alpha = 0.9) +
    geom_hline(yintercept = 1, color = "#ffffff40", linetype = "dashed", linewidth = 0.4) +
    scale_color_manual(values = line_pal, name = NULL, drop = FALSE) +
    scale_linewidth_manual(values = line_sz, name = NULL, drop = FALSE) +
    scale_y_continuous(labels = function(x) paste0(round((x - 1) * 100), "%"),
                       name = "Cumulative return") +
    scale_x_date(name = NULL, date_labels = "%b '%y") +
    labs(title = title, subtitle = subtitle) +
    navy_theme() +
    guides(linewidth = "none")
  if (!show_legend) p <- p + theme(legend.position = "none")
  p
}

p_w1 <- equity_panel(long_w1,
  "Window 1: Jan-Mar 2022 (backfill complete)",
  "Bear market entry - 2022 drawdown begins | 110 bull blocks detected",
  show_legend = FALSE)

p_w2 <- equity_panel(long_w2,
  "Window 2: Jun 2025 - Jun 2026 (live data)",
  "Bull run | 557 bull blocks detected",
  show_legend = TRUE)

# ── gap annotation panel ───────────────────────────────────────────────────
gap_df <- tibble(x = 0.5, y = 0.5, label = "")

p_gap <- ggplot(gap_df, aes(x = x, y = y)) +
  annotate("rect", xmin = 0, xmax = 1, ymin = 0, ymax = 1,
           fill = "#0a1520", color = "#1e3050", linewidth = 1) +
  annotate("text", x = 0.5, y = 0.78, label = "DATA GAP",
           color = "#ef5350", size = 5, fontface = "bold") +
  annotate("text", x = 0.5, y = 0.58, label = "Apr 2022 - May 2025",
           color = "#5a8fbf", size = 3.5, hjust = 0.5) +
  annotate("text", x = 0.5, y = 0.44, label = "~800 trading days\nof tick data",
           color = "#5a8fbf", size = 3.2, hjust = 0.5, lineheight = 1.4) +
  annotate("text", x = 0.5, y = 0.24, label = "Strategies = cash\nduring this window",
           color = "#3a6080", size = 2.9, hjust = 0.5, lineheight = 1.4) +
  xlim(0, 1) + ylim(0, 1) +
  theme_void() +
  theme(plot.background = element_rect(fill = "#0d1b2a", color = NA))

# ── Sharpe vs trade count scatter ─────────────────────────────────────────
smry2 <- smry %>%
  filter(label != "BnH SPY") %>%
  mutate(
    group = case_when(
      label == "Baseline (no filter)"    ~ "Baseline",
      str_detect(label, "^Filter")       ~ "Single filter",
      str_detect(label, "^A\\+D:")       ~ "Best combo",
      TRUE                               ~ "Multi-filter"
    ),
    short = str_extract(label, "^[^:]+"),
    short = str_replace(short, "Baseline \\(no filter\\)", "Baseline"),
    short = str_replace(short, "Filter ([A-D])", "\\1")
  )

group_pal2 <- c(
  "Baseline"      = "#546e7a",
  "Single filter" = "#26c6da",
  "Best combo"    = "#ffca28",
  "Multi-filter"  = "#ab47bc"
)

p_scatter <- ggplot(smry2, aes(x = n_trades, y = sharpe, color = group)) +
  geom_point(aes(size = group == "Best combo"), alpha = 0.85) +
  geom_text(
    data = filter(smry2, sharpe > 0.1 | group == "Best combo"),
    aes(label = short),
    nudge_y = 0.025, size = 2.7, fontface = "bold"
  ) +
  geom_hline(yintercept = 0, color = "#ffffff30", linetype = "dashed") +
  scale_color_manual(values = group_pal2, name = "Filter type") +
  scale_size_manual(values = c(`TRUE` = 5, `FALSE` = 2.3), guide = "none") +
  labs(title = "Sharpe vs Trade Count - All 17 Combinations",
       subtitle = "Filters: A=DIX>=med  B=GEX>0  C=VIX contango  D=CVD>0",
       x = "Trades taken", y = "Annualized Sharpe") +
  navy_theme()

# ── bar chart: key metrics ─────────────────────────────────────────────────
key_strats <- smry %>%
  filter(label %in% c(
    "BnH SPY",
    "Baseline (no filter)",
    "Filter A: DIX>=med",
    "Filter D: CVD>0",
    "A+D: DIX>=med & CVD>0",
    "A+B+D: DIX>=med & GEX>0 & CVD>0",
    "A+C+D: DIX>=med & VIX contango & CVD>0"
  )) %>%
  mutate(
    short_label = case_when(
      str_detect(label, "BnH")        ~ "BnH",
      str_detect(label, "Baseline")   ~ "Baseline",
      str_detect(label, "^Filter A")  ~ "A: DIX",
      str_detect(label, "^Filter D")  ~ "D: CVD",
      str_detect(label, "^A\\+D:")    ~ "A+D",
      str_detect(label, "^A\\+B\\+D") ~ "A+B+D",
      str_detect(label, "^A\\+C\\+D") ~ "A+C+D",
      TRUE ~ label
    ),
    short_label = factor(short_label,
      levels = c("BnH","Baseline","A: DIX","D: CVD","A+D","A+B+D","A+C+D"))
  )

bar_pal <- c(
  "BnH"     = "#4fc3f7",
  "Baseline"= "#546e7a",
  "A: DIX"  = "#26c6da",
  "D: CVD"  = "#66bb6a",
  "A+D"     = "#ffca28",
  "A+B+D"   = "#ef5350",
  "A+C+D"   = "#ab47bc"
)

bar_df <- bind_rows(
  key_strats %>% select(short_label, value = total_ret)  %>% mutate(metric = "Total Return (%)"),
  key_strats %>% select(short_label, value = sharpe)     %>% mutate(metric = "Sharpe"),
  key_strats %>% select(short_label, value = max_dd)     %>% mutate(metric = "Max DD (%)")
) %>%
  mutate(metric = factor(metric, levels = c("Total Return (%)","Sharpe","Max DD (%)")))

p_bars <- ggplot(bar_df, aes(x = short_label, y = value, fill = short_label)) +
  geom_col(width = 0.7, alpha = 0.9) +
  geom_text(aes(label = round(value, 2),
                vjust = ifelse(value >= 0, -0.3, 1.2)),
            color = "#ffffff", size = 2.6) +
  geom_hline(yintercept = 0, color = "#ffffff50") +
  facet_wrap(~metric, scales = "free_y", ncol = 3) +
  scale_fill_manual(values = bar_pal, guide = "none") +
  labs(title = "Key Metrics - Full backtest window (2022 + 2025-2026)",
       subtitle = "NOTE: 2023-2024 gap means strategies are in cash = understated vs BnH which compounds through",
       x = NULL, y = NULL) +
  navy_theme() +
  theme(axis.text.x = element_text(angle = 35, hjust = 1, size = 8))

# ── heatmap: all combos ────────────────────────────────────────────────────
heat_df <- smry %>%
  filter(label != "BnH SPY") %>%
  mutate(
    combo_key = str_extract(label, "^[^:]+"),
    combo_key = str_replace(combo_key, "Baseline \\(no filter\\)", "Baseline"),
    combo_key = str_replace(combo_key, "Filter ([A-D])", "\\1"),
    combo_key = factor(combo_key, levels = rev(c(
      "Baseline","A","B","C","D",
      "A+B","A+C","A+D","B+C","B+D","C+D",
      "A+B+C","A+B+D","A+C+D","B+C+D","A+B+C+D"
    )))
  )

p_heat <- ggplot(heat_df, aes(x = n_trades, y = combo_key, fill = sharpe)) +
  geom_tile(color = "#0d1b2a", height = 0.85, width = 30) +
  geom_text(aes(label = sprintf("%.2f", sharpe)), color = "#ffffff", size = 2.8) +
  scale_fill_gradient2(low = "#c62828", mid = "#37474f", high = "#2e7d32",
                       midpoint = 0, name = "Sharpe", na.value = "#1e3050") +
  scale_x_continuous(name = "Trades taken", expand = c(0.15, 0)) +
  labs(title = "Filter Combo Sharpe Heatmap",
       subtitle = "All 17 combinations | Best: A+D (Sharpe 0.39, 117 trades)",
       y = NULL) +
  navy_theme()

# ── assemble ───────────────────────────────────────────────────────────────
top_row    <- (p_w1 | p_gap | p_w2) + plot_layout(widths = c(2, 1, 3))
middle_row <- (p_bars)
bottom_row <- (p_scatter | p_heat) + plot_layout(widths = c(1.2, 1))

final <- top_row / middle_row / bottom_row +
  plot_layout(heights = c(2, 1.6, 2)) +
  plot_annotation(
    title    = "Block Gap-Fill Strategy - Signal Filter Analysis",
    subtitle = "Full backfill complete: 3,087 block events | Jan 2022-Jun 2026 | Two active data windows shown (2023-2024 = cash gap)",
    caption  = "Filters: A=DIX>=median  B=GEX>0  C=VIX contango  D=CVD>0  |  Entry T+1 open | TC 0.01%/side | Cash when flat",
    theme = theme(
      plot.background = element_rect(fill = "#0d1b2a", color = NA),
      plot.title      = element_text(color = "#ffffff", face = "bold", size = 14),
      plot.subtitle   = element_text(color = "#8aa0b8", size = 10),
      plot.caption    = element_text(color = "#5a7090", size = 8)
    )
  )

ggsave(
  file.path(BASE, "block_filter_analysis.png"),
  final, width = 17, height = 15, dpi = 150, bg = "#0d1b2a"
)
cat("Saved block_filter_analysis.png\n")
