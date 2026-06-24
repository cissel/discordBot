# gexChart.R - Dealer GEX + DIX Chart
# Panel 1: GEX ($B) with zero line + regime shading (positive = suppression / negative = destabilizing)
# Panel 2: DIX (Dark Index) with 20-day MA
# Usage: Rscript gexChart.R [output_path] [days]

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(patchwork)
})

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BG     <- "#02233F"
GRID   <- "#274066"
ACCENT <- "#00bfff"
ORANGE <- "#ff8c00"
GREEN  <- "#00ff88"
RED    <- "#ff4444"

args   <- commandArgs(trailingOnly = TRUE)
OUTPUT <- if (length(args) >= 1) args[1] else
          path.expand("~/discordBot/outputs/markets/gexChart.png")
DAYS   <- if (length(args) >= 2) as.integer(args[2]) else 365

dir.create(dirname(OUTPUT), showWarnings = FALSE, recursive = TRUE)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
cache_path <- path.expand("~/discordBot/outputs/markets/cache/spy_gex_daily.csv")
if (!file.exists(cache_path)) {
  stop("GEX cache not found: ", cache_path)
}

df <- read_csv(cache_path, col_types = cols(date = col_date(), price = col_double(),
                                             dix = col_double(), gex = col_double()),
               show_col_types = FALSE) %>%
  arrange(date) %>%
  mutate(gex_b = gex / 1e9)

# Filter to requested window
cutoff <- Sys.Date() - DAYS
df <- df %>% filter(date >= cutoff)

if (nrow(df) == 0) stop("No data in requested window")

# Rolling stats
df <- df %>%
  mutate(
    dix_ma20 = zoo::rollmean(dix, k = 20, fill = NA, align = "right"),
    gex_ma20 = zoo::rollmean(gex_b, k = 20, fill = NA, align = "right"),
    regime   = ifelse(gex_b >= 0, "suppression", "destabilizing")
  )

# Current values for embed context
cur_date   <- max(df$date)
cur_gex    <- df$gex_b[df$date == cur_date]
cur_dix    <- df$dix[df$date == cur_date]

# Full history percentiles (load full data)
full <- read_csv(cache_path, col_types = cols(date = col_date(), price = col_double(),
                                               dix = col_double(), gex = col_double()),
                 show_col_types = FALSE) %>%
  mutate(gex_b = gex / 1e9)

gex_pct <- round(mean(full$gex_b < cur_gex, na.rm = TRUE) * 100)
dix_pct <- round(mean(full$dix  < cur_dix,  na.rm = TRUE) * 100)

# ---------------------------------------------------------------------------
# Shared theme
# ---------------------------------------------------------------------------
navy_theme <- theme_minimal(base_size = 11) +
  theme(
    plot.background   = element_rect(fill = BG,   color = NA),
    panel.background  = element_rect(fill = BG,   color = NA),
    panel.grid.major  = element_line(color = GRID, linewidth = 0.4),
    panel.grid.minor  = element_line(color = GRID, linewidth = 0.2),
    axis.ticks        = element_line(color = GRID),
    axis.text         = element_text(color = "white", size = 9),
    axis.title        = element_text(color = "white", size = 10),
    plot.title        = element_text(color = "white", hjust = 0.5, face = "bold", size = 13),
    plot.subtitle     = element_text(color = "white", hjust = 0.5, size = 9),
    plot.caption      = element_text(color = "grey70", size = 8, hjust = 1),
    legend.background = element_rect(fill = BG, color = NA),
    legend.text       = element_text(color = "white"),
    legend.title      = element_text(color = "white"),
    legend.position   = "none"
  )

# Dynamic x-axis breaks
if (DAYS <= 180) {
  x_breaks <- "1 month"; x_fmt <- "%b '%y"
} else if (DAYS <= 730) {
  x_breaks <- "3 months"; x_fmt <- "%b '%y"
} else {
  x_breaks <- "1 year"; x_fmt <- "%Y"
}

# ---------------------------------------------------------------------------
# Panel 1: GEX
# ---------------------------------------------------------------------------
gex_label <- sprintf("GEX: $%.1fB (%dth pct)", cur_gex, gex_pct)

# Regime shading - build ribbon data
ribbon_df <- df %>%
  mutate(
    gex_pos = pmax(gex_b, 0),
    gex_neg = pmin(gex_b, 0)
  )

p1 <- ggplot(df, aes(x = date)) +
  # Positive GEX fill (green = suppression)
  geom_ribbon(data = ribbon_df, aes(ymin = 0, ymax = gex_pos),
              fill = GREEN, alpha = 0.15) +
  # Negative GEX fill (red = destabilizing)
  geom_ribbon(data = ribbon_df, aes(ymin = gex_neg, ymax = 0),
              fill = RED, alpha = 0.25) +
  # GEX line
  geom_line(aes(y = gex_b), color = ACCENT, linewidth = 0.7) +
  # 20-day MA
  geom_line(aes(y = gex_ma20), color = ORANGE, linewidth = 0.8, linetype = "dashed", na.rm = TRUE) +
  # Zero line
  geom_hline(yintercept = 0, color = "white", linewidth = 0.6, linetype = "solid") +
  # Current value label
  annotate("text", x = max(df$date), y = cur_gex,
           label = sprintf("$%.1fB", cur_gex),
           color = "white", hjust = 1.1, vjust = -0.5, size = 3.2, fontface = "bold") +
  scale_x_date(date_breaks = x_breaks, date_labels = x_fmt,
               expand = expansion(mult = c(0.01, 0.03))) +
  scale_y_continuous(labels = function(x) paste0("$", x, "B")) +
  labs(
    title = paste("Dealer GEX + DIX -", format(cur_date, "%B %d, %Y")),
    subtitle = sprintf("GEX: $%.1fB (%dth pct) | Regime: %s",
                       cur_gex, gex_pct,
                       ifelse(cur_gex >= 0, "Suppression (vol dampened)", "Destabilizing (vol amplified)")),
    x = NULL,
    y = "GEX ($B)",
    caption = NULL
  ) +
  navy_theme +
  theme(axis.text.x = element_blank(), axis.ticks.x = element_blank(),
        plot.margin = margin(10, 10, 2, 10))

# ---------------------------------------------------------------------------
# Panel 2: DIX
# ---------------------------------------------------------------------------
dix_label <- sprintf("DIX: %.4f (%dth pct)", cur_dix, dix_pct)

# DIX midpoint for coloring (above/below historical median)
dix_med <- median(full$dix, na.rm = TRUE)

p2 <- ggplot(df, aes(x = date)) +
  # Fill above/below median
  geom_ribbon(aes(ymin = dix_med, ymax = pmax(dix, dix_med)),
              fill = GREEN, alpha = 0.15) +
  geom_ribbon(aes(ymin = pmin(dix, dix_med), ymax = dix_med),
              fill = RED, alpha = 0.20) +
  # DIX line
  geom_line(aes(y = dix), color = ACCENT, linewidth = 0.7) +
  # 20-day MA
  geom_line(aes(y = dix_ma20), color = ORANGE, linewidth = 0.8, linetype = "dashed", na.rm = TRUE) +
  # Median reference
  geom_hline(yintercept = dix_med, color = "grey60", linewidth = 0.5, linetype = "dotted") +
  # Current value label
  annotate("text", x = max(df$date), y = cur_dix,
           label = sprintf("%.4f", cur_dix),
           color = "white", hjust = 1.1, vjust = -0.5, size = 3.2, fontface = "bold") +
  scale_x_date(date_breaks = x_breaks, date_labels = x_fmt,
               expand = expansion(mult = c(0.01, 0.03))) +
  scale_y_continuous(labels = scales::percent_format(accuracy = 1)) +
  labs(
    subtitle = sprintf("DIX: %.4f (%dth pct) | %s of dark pool volume buying",
                       cur_dix, dix_pct,
                       ifelse(cur_dix >= dix_med, "Above-median", "Below-median")),
    x = NULL,
    y = "DIX",
    caption = "Source: SqueezeMetrics | JHCV | Dashed = 20-day MA | Green = bullish regime / Red = bearish regime"
  ) +
  navy_theme +
  theme(plot.margin = margin(2, 10, 10, 10))

# ---------------------------------------------------------------------------
# Combine + save
# ---------------------------------------------------------------------------
final <- p1 / p2 + plot_layout(heights = c(1.4, 1))

ggsave(OUTPUT, final, width = 10, height = 7, dpi = 150, bg = BG)
cat(sprintf("[gexChart] saved -> %s\n", OUTPUT))
cat(sprintf("[gexChart] GEX=%.2fB (%dth pct) DIX=%.4f (%dth pct)\n",
            cur_gex, gex_pct, cur_dix, dix_pct))
