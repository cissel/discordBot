library(ggplot2)
library(dplyr)
library(tidyr)
library(scales)
library(patchwork)

# Load all equity curves
gap_fill <- read.csv("~/discordBot/outputs/research/block_gap_fill_equity.csv",
                     stringsAsFactors = FALSE)
bear_exit <- read.csv("~/discordBot/outputs/research/bear_exit_equity.csv",
                      stringsAsFactors = FALSE)
cash_periods <- bear_exit  # has in_mkt col

# Also load original signal strategy from blockGapStrategy output
# Reconstruct BnH from either file (same underlying)
gap_fill$date  <- as.Date(gap_fill$date)
bear_exit$date <- as.Date(bear_exit$date)

# Combine into long format - align on shared date range
dates_common <- intersect(gap_fill$date, bear_exit$date)

combined <- gap_fill %>%
  filter(date %in% dates_common) %>%
  select(date, gap_fill = equity, bnh) %>%
  left_join(bear_exit %>% select(date, bear_exit = equity), by = "date") %>%
  pivot_longer(cols = c(gap_fill, bear_exit, bnh),
               names_to = "series", values_to = "value") %>%
  mutate(series = recode(series,
    "gap_fill"  = "Gap-Fill (parallel positions)",
    "bear_exit" = "Bear-Exit + Gap Re-entry",
    "bnh"       = "Buy-and-Hold SPY"
  ))

# Final labels
finals <- combined %>%
  group_by(series) %>%
  slice_tail(n = 1) %>%
  mutate(label = paste0(series, "  ", ifelse(value >= 1, "+", ""),
                        round((value - 1) * 100, 1), "%"))

colors <- c(
  "Gap-Fill (parallel positions)" = "#00cc66",
  "Bear-Exit + Gap Re-entry"      = "#ffaa00",
  "Buy-and-Hold SPY"              = "#4488ff"
)

# ── Panel 1: Equity curves ────────────────────────────────────────────────────
p1 <- ggplot(combined, aes(x = date, y = value, color = series)) +
  geom_line(linewidth = 0.9, alpha = 0.92) +
  geom_hline(yintercept = 1, color = "#555555", linetype = "dashed", linewidth = 0.4) +
  geom_text(data = finals,
            aes(label = label, x = date + 4, y = value),
            hjust = 0, size = 2.7, fontface = "bold", show.legend = FALSE) +
  scale_color_manual(values = colors) +
  scale_y_continuous(labels = percent_format(accuracy = 1),
                     name   = "Portfolio Value ($1 start)") +
  scale_x_date(name = NULL, date_breaks = "2 months", date_labels = "%b '%y",
               expand = expansion(mult = c(0.01, 0.20))) +
  labs(title    = "SPY Block Order Signal Strategies vs Buy-and-Hold",
       subtitle = "Jun 2025 - Jun 2026  |  0.01% transaction cost per side",
       color    = NULL) +
  theme_minimal(base_size = 11) +
  theme(
    plot.title       = element_text(face = "bold", size = 13, color = "#eeeeee"),
    plot.subtitle    = element_text(color = "#888888", size = 9),
    legend.position  = "top",
    legend.text      = element_text(color = "#cccccc", size = 8.5),
    panel.grid.minor = element_blank(),
    panel.grid.major = element_line(color = "#2a2a2a", linewidth = 0.3),
    plot.background  = element_rect(fill = "#1a1a1a", color = NA),
    panel.background = element_rect(fill = "#1a1a1a", color = NA),
    text             = element_text(color = "#dddddd"),
    axis.text        = element_text(color = "#aaaaaa"),
    axis.title       = element_text(color = "#cccccc"),
    legend.background = element_rect(fill = "#1a1a1a", color = NA),
    legend.key        = element_rect(fill = "#1a1a1a", color = NA)
  )

# ── Panel 2: Rolling 30-day return comparison ─────────────────────────────────
# Compute rolling 30d returns for each series
roll_df <- combined %>%
  group_by(series) %>%
  arrange(date) %>%
  mutate(roll30_ret = (value / lag(value, 21) - 1) * 100) %>%
  filter(!is.na(roll30_ret)) %>%
  ungroup()

p2 <- ggplot(roll_df, aes(x = date, y = roll30_ret, color = series)) +
  geom_line(linewidth = 0.7, alpha = 0.85) +
  geom_hline(yintercept = 0, color = "#555555", linetype = "dashed", linewidth = 0.4) +
  scale_color_manual(values = colors, guide = "none") +
  scale_y_continuous(labels = function(x) paste0(x, "%"), name = "Rolling 21-day Return") +
  scale_x_date(name = NULL, date_breaks = "2 months", date_labels = "%b '%y") +
  labs(title    = "Rolling 21-Day Return",
       subtitle = "Momentum comparison across strategies") +
  theme_minimal(base_size = 10) +
  theme(
    plot.title       = element_text(face = "bold", size = 11, color = "#eeeeee"),
    plot.subtitle    = element_text(color = "#888888", size = 8),
    panel.grid.minor = element_blank(),
    panel.grid.major = element_line(color = "#2a2a2a", linewidth = 0.3),
    plot.background  = element_rect(fill = "#1a1a1a", color = NA),
    panel.background = element_rect(fill = "#1a1a1a", color = NA),
    text             = element_text(color = "#dddddd"),
    axis.text        = element_text(color = "#aaaaaa", size = 8),
    axis.title       = element_text(color = "#cccccc")
  )

# ── Panel 3: Drawdown ─────────────────────────────────────────────────────────
dd_df <- combined %>%
  group_by(series) %>%
  arrange(date) %>%
  mutate(running_max = cummax(value),
         drawdown    = (value - running_max) / running_max * 100) %>%
  ungroup()

p3 <- ggplot(dd_df, aes(x = date, y = drawdown, color = series, fill = series)) +
  geom_line(linewidth = 0.7, alpha = 0.9) +
  geom_hline(yintercept = 0, color = "#555555", linewidth = 0.3) +
  scale_color_manual(values = colors, guide = "none") +
  scale_fill_manual(values  = colors, guide = "none") +
  scale_y_continuous(labels = function(x) paste0(x, "%"), name = "Drawdown (%)") +
  scale_x_date(name = NULL, date_breaks = "2 months", date_labels = "%b '%y") +
  labs(title    = "Drawdown from Peak",
       subtitle = "Bear-Exit strategy drawdown vs BnH and gap-fill") +
  theme_minimal(base_size = 10) +
  theme(
    plot.title       = element_text(face = "bold", size = 11, color = "#eeeeee"),
    plot.subtitle    = element_text(color = "#888888", size = 8),
    panel.grid.minor = element_blank(),
    panel.grid.major = element_line(color = "#2a2a2a", linewidth = 0.3),
    plot.background  = element_rect(fill = "#1a1a1a", color = NA),
    panel.background = element_rect(fill = "#1a1a1a", color = NA),
    text             = element_text(color = "#dddddd"),
    axis.text        = element_text(color = "#aaaaaa", size = 8),
    axis.title       = element_text(color = "#cccccc")
  )

# ── Panel 4: Cash/in-market for bear-exit ────────────────────────────────────
bear_exit_clean <- bear_exit %>%
  mutate(state = ifelse(in_mkt == 1, "Long SPY", "Cash"))

p4 <- ggplot(bear_exit_clean, aes(x = date, y = 1, fill = state)) +
  geom_tile(height = 1, alpha = 0.85) +
  scale_fill_manual(values = c("Long SPY" = "#ffaa00", "Cash" = "#333333"),
                    name = NULL) +
  scale_x_date(name = NULL, date_breaks = "2 months", date_labels = "%b '%y") +
  scale_y_continuous(breaks = NULL, name = NULL) +
  labs(title    = "Bear-Exit: Market Exposure",
       subtitle = paste0("Orange = long SPY  |  Dark = cash  |  ",
                         round(mean(bear_exit$in_mkt) * 100, 1), "% time in market")) +
  theme_minimal(base_size = 10) +
  theme(
    plot.title       = element_text(face = "bold", size = 11, color = "#eeeeee"),
    plot.subtitle    = element_text(color = "#888888", size = 8),
    panel.grid       = element_blank(),
    plot.background  = element_rect(fill = "#1a1a1a", color = NA),
    panel.background = element_rect(fill = "#1a1a1a", color = NA),
    text             = element_text(color = "#dddddd"),
    axis.text.x      = element_text(color = "#aaaaaa", size = 8),
    legend.position  = "right",
    legend.text      = element_text(color = "#cccccc", size = 8),
    legend.background = element_rect(fill = "#1a1a1a", color = NA),
    legend.key        = element_rect(fill = "#1a1a1a", color = NA)
  )

# ── Combine ───────────────────────────────────────────────────────────────────
combined_plot <- (p1 / p2 / p3 / p4) +
  plot_layout(heights = c(3, 1.5, 1.5, 0.6)) +
  plot_annotation(
    theme = theme(plot.background = element_rect(fill = "#1a1a1a", color = NA))
  )

out_path <- "~/discordBot/outputs/research/block_strategy_comparison.png"
ggsave(out_path, combined_plot, width = 12, height = 16, dpi = 150, bg = "#1a1a1a")
cat("Saved:", out_path, "\n")
