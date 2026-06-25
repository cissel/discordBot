library(ggplot2)
library(dplyr)
library(tidyr)
library(scales)
library(patchwork)

eq <- read.csv("~/discordBot/outputs/research/hybrid_equity.csv",
               stringsAsFactors = FALSE)
eq$date <- as.Date(eq$date)

# ── Palette / theme ───────────────────────────────────────────────────────────
NAVY    <- "#0a0f1e"
PANEL   <- "#111827"
GRID    <- "#1e2d45"
TEXT    <- "#e2e8f0"
SUBTEXT <- "#64748b"

C_BNH    <- "#60a5fa"   # blue   - buy and hold
C_A      <- "#34d399"   # green  - hedged
C_B      <- "#f59e0b"   # amber  - high-dev filter
C_C      <- "#a78bfa"   # violet - regime gated
C_BASE   <- "#f87171"   # red    - bear exit baseline

navy_theme <- function(base = 10) {
  theme_minimal(base_size = base) +
    theme(
      plot.background  = element_rect(fill = NAVY,  color = NA),
      panel.background = element_rect(fill = PANEL, color = NA),
      panel.grid.major = element_line(color = GRID, linewidth = 0.3),
      panel.grid.minor = element_blank(),
      text             = element_text(color = TEXT),
      axis.text        = element_text(color = SUBTEXT),
      axis.title       = element_text(color = TEXT),
      plot.title       = element_text(color = TEXT,  face = "bold"),
      plot.subtitle    = element_text(color = SUBTEXT, size = 8),
      legend.background = element_rect(fill = NAVY, color = NA),
      legend.key        = element_rect(fill = NAVY, color = NA),
      legend.text       = element_text(color = TEXT),
      strip.background  = element_rect(fill = PANEL, color = NA),
      strip.text        = element_text(color = TEXT, face = "bold"),
    )
}

colors <- c(
  "Buy-and-Hold SPY"               = C_BNH,
  "Hedged Long (75/25 SPY/SH)"     = C_A,
  "High-Dev Filter (>=0.8%)"       = C_B,
  "Regime-Gated Exit"              = C_C,
  "Bear-Exit Baseline"             = C_BASE
)

# ── Reshape ───────────────────────────────────────────────────────────────────
long <- eq %>%
  select(date, bnh, equity_A, equity_B, equity_C, bear_exit) %>%
  pivot_longer(-date, names_to = "series", values_to = "value") %>%
  mutate(series = recode(series,
    "bnh"       = "Buy-and-Hold SPY",
    "equity_A"  = "Hedged Long (75/25 SPY/SH)",
    "equity_B"  = "High-Dev Filter (>=0.8%)",
    "equity_C"  = "Regime-Gated Exit",
    "bear_exit" = "Bear-Exit Baseline"
  )) %>%
  filter(!is.na(value))

# Final label positions
finals <- long %>%
  group_by(series) %>%
  slice_tail(n = 1) %>%
  mutate(label = paste0(ifelse(value >= 1, "+", ""),
                        round((value - 1) * 100, 1), "%"))

# ── Panel 1: Equity curves ────────────────────────────────────────────────────
p1 <- ggplot(long, aes(x = date, y = value, color = series)) +
  geom_line(linewidth = 0.85, alpha = 0.92) +
  geom_hline(yintercept = 1, color = GRID, linetype = "dashed", linewidth = 0.4) +
  geom_text(data = finals,
            aes(label = paste0(series, " ", label)),
            hjust = 0, nudge_x = 3, size = 2.6, fontface = "bold",
            show.legend = FALSE) +
  scale_color_manual(values = colors) +
  scale_y_continuous(labels = percent_format(accuracy = 1),
                     name = "Portfolio Value ($1 start)") +
  scale_x_date(name = NULL, date_breaks = "2 months", date_labels = "%b '%y",
               expand = expansion(mult = c(0.01, 0.28))) +
  labs(title    = "SPY Block Signal Hybrid Strategies",
       subtitle = "Jun 2025 - Jun 2026  |  0.01% TC per side  |  No shorting",
       color    = NULL) +
  navy_theme(11) +
  theme(legend.position = "none")

# ── Panel 2: Drawdown ─────────────────────────────────────────────────────────
dd <- long %>%
  group_by(series) %>%
  arrange(date) %>%
  mutate(peak = cummax(value),
         dd   = (value - peak) / peak * 100) %>%
  ungroup()

p2 <- ggplot(dd, aes(x = date, y = dd, color = series)) +
  geom_line(linewidth = 0.7, alpha = 0.88) +
  geom_hline(yintercept = 0, color = GRID, linewidth = 0.3) +
  scale_color_manual(values = colors, guide = "none") +
  scale_y_continuous(labels = function(x) paste0(x, "%"),
                     name = "Drawdown (%)") +
  scale_x_date(name = NULL, date_breaks = "2 months", date_labels = "%b '%y") +
  labs(title    = "Drawdown from Peak",
       subtitle = "Hedged strategy (green) has shallowest drawdown outside BnH") +
  navy_theme(10)

# ── Panel 3: Rolling 21-day return ───────────────────────────────────────────
roll <- long %>%
  group_by(series) %>%
  arrange(date) %>%
  mutate(roll21 = (value / lag(value, 21) - 1) * 100) %>%
  filter(!is.na(roll21)) %>%
  ungroup()

p3 <- ggplot(roll, aes(x = date, y = roll21, color = series)) +
  geom_line(linewidth = 0.6, alpha = 0.80) +
  geom_hline(yintercept = 0, color = GRID, linetype = "dashed", linewidth = 0.4) +
  scale_color_manual(values = colors, guide = "none") +
  scale_y_continuous(labels = function(x) paste0(x, "%"),
                     name = "Rolling 21-day Return (%)") +
  scale_x_date(name = NULL, date_breaks = "2 months", date_labels = "%b '%y") +
  labs(title    = "Rolling 21-Day Return",
       subtitle = "Strategies diverge most sharply Nov 2025 - Apr 2026 (choppy regime)") +
  navy_theme(10)

# ── Panel 4: Summary metrics bar chart ───────────────────────────────────────
metrics_df <- long %>%
  group_by(series) %>%
  summarise(
    total_ret = (last(value) / first(value) - 1) * 100,
    sharpe    = {
      r  <- diff(value) / head(value, -1)
      mean(r) * 252 / (sd(r) * sqrt(252))
    },
    max_dd    = {
      pk <- cummax(value)
      min((value - pk) / pk) * 100
    },
    .groups = "drop"
  ) %>%
  pivot_longer(cols = c(total_ret, sharpe, max_dd),
               names_to = "metric", values_to = "val") %>%
  mutate(
    metric = recode(metric,
      "total_ret" = "Total Return (%)",
      "sharpe"    = "Sharpe Ratio",
      "max_dd"    = "Max Drawdown (%)"
    ),
    series = factor(series, levels = c(
      "Buy-and-Hold SPY",
      "Hedged Long (75/25 SPY/SH)",
      "High-Dev Filter (>=0.8%)",
      "Regime-Gated Exit",
      "Bear-Exit Baseline"
    ))
  )

p4 <- ggplot(metrics_df, aes(x = series, y = val, fill = series)) +
  geom_col(alpha = 0.85, width = 0.65) +
  geom_text(aes(label = round(val, 2),
                vjust = ifelse(val >= 0, -0.4, 1.2)),
            color = TEXT, size = 2.8, fontface = "bold") +
  geom_hline(yintercept = 0, color = GRID, linewidth = 0.3) +
  facet_wrap(~ metric, scales = "free_y", nrow = 1) +
  scale_fill_manual(values = colors, guide = "none") +
  scale_x_discrete(labels = function(x) {
    gsub(" \\(", "\n(", x)  # wrap at paren
  }) +
  labs(title    = "Strategy Metrics Summary",
       x = NULL, y = NULL) +
  navy_theme(9) +
  theme(axis.text.x = element_text(size = 7, angle = 10, hjust = 0.6))

# ── Combine ───────────────────────────────────────────────────────────────────
combined <- (p1 / p2 / p3 / p4) +
  plot_layout(heights = c(3, 1.5, 1.5, 2)) +
  plot_annotation(
    caption = paste0(
      "Strategies: A=75% SPY/25% SH on bear signal | B=bear exit, high-dev (>=0.8%) only",
      " | C=bear exit, bear+chop regime only (no-op this window)\n",
      "C identical to baseline because all bear signals fired in chop/bear regime on Jun2025-Jun2026 data."
    ),
    theme = theme(
      plot.background = element_rect(fill = NAVY, color = NA),
      plot.caption    = element_text(color = SUBTEXT, size = 7, hjust = 0)
    )
  )

out <- "~/discordBot/outputs/research/hybrid_strategy_chart.png"
ggsave(out, combined, width = 13, height = 17, dpi = 150, bg = NAVY)
cat("Saved:", out, "\n")
