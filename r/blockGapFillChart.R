library(ggplot2)
library(dplyr)
library(tidyr)
library(scales)
library(patchwork)

equity <- read.csv("~/discordBot/outputs/research/block_gap_fill_equity.csv",
                   stringsAsFactors = FALSE)
trades <- read.csv("~/discordBot/outputs/research/block_gap_fill_trades.csv",
                   stringsAsFactors = FALSE)

equity$date <- as.Date(equity$date)
trades$entry_date <- as.Date(trades$entry_date)
trades$exit_date  <- as.Date(trades$exit_date)

out_path <- "~/discordBot/outputs/research/block_gap_fill_chart.png"

# ── panel 1: equity curve ─────────────────────────────────────────────────────

eq_long <- equity %>%
  select(date, equity, bnh) %>%
  pivot_longer(cols = c(equity, bnh),
               names_to  = "series",
               values_to = "value") %>%
  mutate(series = recode(series,
                         "equity" = "Block Signal Strategy",
                         "bnh"    = "Buy-and-Hold SPY"))

# Final values for labels
final_vals <- eq_long %>%
  group_by(series) %>%
  slice_tail(n = 1) %>%
  mutate(label = paste0(series, "\n",
                        ifelse(value >= 1, "+", ""),
                        round((value - 1) * 100, 1), "%"))

strat_color <- "#00cc66"
bnh_color   <- "#4488ff"

p1 <- ggplot(eq_long, aes(x = date, y = value, color = series)) +
  geom_line(linewidth = 0.9, alpha = 0.9) +
  geom_hline(yintercept = 1, color = "#555555", linetype = "dashed", linewidth = 0.4) +
  geom_text(data = final_vals,
            aes(label = label, x = date + 5, y = value),
            hjust = 0, size = 3.0, fontface = "bold", show.legend = FALSE) +
  scale_color_manual(values = c("Block Signal Strategy" = strat_color,
                                "Buy-and-Hold SPY"      = bnh_color)) +
  scale_y_continuous(labels = percent_format(accuracy = 1),
                     name   = "Portfolio Value ($1 start)") +
  scale_x_date(name = NULL, date_breaks = "2 months", date_labels = "%b '%y",
               expand = expansion(mult = c(0.01, 0.12))) +
  labs(title    = "SPY Block Signal Strategy vs Buy-and-Hold",
       subtitle = paste0("Parallel equal-weight | ", nrow(trades),
                         " trades | Jun 2025 - Jun 2026"),
       color    = NULL) +
  theme_minimal(base_size = 11) +
  theme(
    plot.title      = element_text(face = "bold", size = 13),
    plot.subtitle   = element_text(color = "#888888", size = 9),
    legend.position = "none",
    panel.grid.minor = element_blank(),
    panel.grid.major = element_line(color = "#2a2a2a", linewidth = 0.3),
    plot.background = element_rect(fill = "#1a1a1a", color = NA),
    panel.background= element_rect(fill = "#1a1a1a", color = NA),
    text            = element_text(color = "#dddddd"),
    axis.text       = element_text(color = "#aaaaaa"),
    axis.title      = element_text(color = "#cccccc")
  )

# ── panel 2: trade return distribution by fill horizon ────────────────────────

trades <- trades %>%
  mutate(fill_horizon = factor(fill_horizon,
                               levels = c("1d","3d","1w","2w","1mo","1mo_stop"),
                               labels = c("1 Day","3 Days","1 Week","2 Weeks","1 Month","Stop (1mo)")))

horizon_colors <- c(
  "1 Day"      = "#aaddff",
  "3 Days"     = "#66bbff",
  "1 Week"     = "#3399ff",
  "2 Weeks"    = "#0066cc",
  "1 Month"    = "#004499",
  "Stop (1mo)" = "#cc3333"
)

# Summary stats per horizon for annotation
hz_summary <- trades %>%
  group_by(fill_horizon) %>%
  summarise(n       = n(),
            avg_ret = mean(return_pct),
            win_pct = mean(return_pct > 0) * 100,
            .groups = "drop") %>%
  mutate(label = paste0("n=", n, "\navg=", ifelse(avg_ret >= 0, "+", ""),
                        round(avg_ret, 2), "%\nwin=", round(win_pct, 0), "%"))

p2 <- ggplot(trades, aes(x = return_pct, fill = fill_horizon)) +
  geom_histogram(bins = 35, alpha = 0.85, color = NA) +
  geom_vline(xintercept = 0, color = "#ffffff", linetype = "dashed",
             linewidth = 0.5, alpha = 0.6) +
  facet_wrap(~ fill_horizon, nrow = 2, scales = "free_y") +
  geom_text(data = hz_summary,
            aes(x = Inf, y = Inf, label = label),
            hjust = 1.1, vjust = 1.2, size = 2.7,
            color = "#dddddd", inherit.aes = FALSE) +
  scale_fill_manual(values = horizon_colors, guide = "none") +
  scale_x_continuous(labels = function(x) paste0(x, "%")) +
  labs(title    = "Trade Return Distribution by Fill Horizon",
       subtitle = "Each bar = one block event trade | red = stop-out (never filled within 1mo)",
       x        = "Trade Return (%)",
       y        = "Count") +
  theme_minimal(base_size = 10) +
  theme(
    plot.title      = element_text(face = "bold", size = 11, color = "#dddddd"),
    plot.subtitle   = element_text(color = "#888888", size = 8),
    strip.text      = element_text(color = "#dddddd", face = "bold", size = 9),
    panel.grid.minor = element_blank(),
    panel.grid.major = element_line(color = "#2a2a2a", linewidth = 0.3),
    plot.background = element_rect(fill = "#1a1a1a", color = NA),
    panel.background= element_rect(fill = "#1a1a1a", color = NA),
    strip.background= element_rect(fill = "#222222", color = NA),
    text            = element_text(color = "#dddddd"),
    axis.text       = element_text(color = "#aaaaaa", size = 8),
    axis.title      = element_text(color = "#cccccc")
  )

# ── panel 3: positions over time (n concurrent positions per day) ─────────────

equity$n_positions[is.na(equity$n_positions)] <- 0

p3 <- ggplot(equity, aes(x = date, y = n_positions)) +
  geom_area(fill = strat_color, alpha = 0.35) +
  geom_line(color = strat_color, linewidth = 0.5) +
  scale_x_date(name = NULL, date_breaks = "2 months", date_labels = "%b '%y") +
  scale_y_continuous(name = "Concurrent Positions") +
  labs(title    = "Concurrent Open Positions Over Time",
       subtitle = paste0("Avg ", round(mean(equity$n_positions[equity$n_positions > 0]), 1),
                         " positions on active days")) +
  theme_minimal(base_size = 10) +
  theme(
    plot.title      = element_text(face = "bold", size = 11, color = "#dddddd"),
    plot.subtitle   = element_text(color = "#888888", size = 8),
    panel.grid.minor = element_blank(),
    panel.grid.major = element_line(color = "#2a2a2a", linewidth = 0.3),
    plot.background = element_rect(fill = "#1a1a1a", color = NA),
    panel.background= element_rect(fill = "#1a1a1a", color = NA),
    text            = element_text(color = "#dddddd"),
    axis.text       = element_text(color = "#aaaaaa"),
    axis.title      = element_text(color = "#cccccc")
  )

# ── combine and save ──────────────────────────────────────────────────────────

combined <- (p1 / p3 / p2) +
  plot_layout(heights = c(2.5, 1, 2.5)) +
  plot_annotation(
    theme = theme(plot.background = element_rect(fill = "#1a1a1a", color = NA))
  )

ggsave(out_path, combined, width = 12, height = 15, dpi = 150, bg = "#1a1a1a")
cat("Saved:", out_path, "\n")
