#!/usr/bin/env Rscript
# marketsSnapshot.R by JHCV
# Markets Snapshot - 3-row patchwork dashboard
#
# Row 1 - Equities:  SPY | QQQ | DIA | IWM
# Row 2 - Bonds:     SHY | IEF | TLT | HYG   (short to long + credit)
# Row 3 - FX + DXY:  EUR/USD | GBP/USD | USD/JPY | UUP (DXY proxy)
#
# Usage:
#   Rscript r/marketsSnapshot.R <timeframe> [output_path]
#
# Timeframes: intraday | 1w | 1mo | 3mo | 6mo | 1y
# Output: ~/discordBot/outputs/markets/snapshot/marketsSnapshot_<tf>.png

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(patchwork)
})

# ── Args ──────────────────────────────────────────────────────────────────────
args      <- commandArgs(trailingOnly = TRUE)
timeframe <- if (length(args) >= 1) args[1] else "1mo"
snap_dir  <- path.expand("~/discordBot/outputs/markets/snapshot")

out_path <- if (length(args) >= 2) {
  path.expand(args[2])
} else {
  file.path(snap_dir, paste0("marketsSnapshot_", timeframe, ".png"))
}
dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)

# ── Theme ─────────────────────────────────────────────────────────────────────
BG     <- "#02233F"
GRID   <- "#274066"
CYAN   <- "#00bfff"
GREEN  <- "#00e676"
RED    <- "#ff1744"
WHITE  <- "white"

snap_theme <- theme_minimal(base_size = 9) +
  theme(
    plot.background  = element_rect(fill = BG,   color = NA),
    panel.background = element_rect(fill = BG,   color = NA),
    panel.grid.major = element_line(color = GRID, linewidth = 0.3),
    panel.grid.minor = element_blank(),
    axis.ticks       = element_line(color = GRID),
    axis.text        = element_text(color = WHITE, size = 6.5),
    axis.title       = element_blank(),
    plot.title       = element_text(color = WHITE, hjust = 0.5, face = "bold", size = 9),
    plot.subtitle    = element_blank(),
    plot.margin      = margin(4, 6, 4, 6),
    legend.position  = "none"
  )

# ── Timeframe label ───────────────────────────────────────────────────────────
tf_label <- switch(timeframe,
  intraday = "Intraday",
  `1w`     = "1 Week",
  `1mo`    = "1 Month",
  `3mo`    = "3 Months",
  `6mo`    = "6 Months",
  `1y`     = "1 Year",
  timeframe
)

is_intraday <- timeframe %in% c("intraday", "1w")

# ── Helpers ───────────────────────────────────────────────────────────────────
# Format x-axis breaks based on timeframe
x_scale_date <- function() {
  breaks_val <- switch(timeframe,
    "1mo"  = "1 week",
    "3mo"  = "1 month",
    "6mo"  = "2 months",
    "1y"   = "3 months",
    "1 month"
  )
  labels_val <- switch(timeframe,
    "1y" = "%b '%y",
    "%b %d"
  )
  scale_x_date(
    date_breaks  = breaks_val,
    date_labels  = labels_val,
    expand       = expansion(mult = c(0.02, 0.04))
  )
}

x_scale_posix <- function() {
  scale_x_datetime(
    date_breaks  = if (timeframe == "intraday") "1 hour" else "1 day",
    date_labels  = if (timeframe == "intraday") "%H:%M" else "%b %d",
    expand       = expansion(mult = c(0.02, 0.04))
  )
}

# ── Load ETF panel ────────────────────────────────────────────────────────────
make_etf_panel <- function(symbol, label, line_color = CYAN) {
  csv <- file.path(snap_dir, paste0(symbol, "_", timeframe, ".csv"))
  if (!file.exists(csv)) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5,
                 label = paste0(symbol, "\nNo data"),
                 color = "white", size = 4, hjust = 0.5) +
        theme_void() +
        theme(plot.background = element_rect(fill = BG, color = GRID, linewidth = 0.5))
    )
  }

  df <- read_csv(csv, show_col_types = FALSE, progress = FALSE)
  if (nrow(df) < 2) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5,
                 label = paste0(symbol, "\nInsufficient data"),
                 color = "white", size = 4, hjust = 0.5) +
        theme_void() +
        theme(plot.background = element_rect(fill = BG, color = GRID, linewidth = 0.5))
    )
  }

  if (is_intraday) {
    df <- df %>% mutate(t = as.POSIXct(date, tz = "America/New_York")) %>% arrange(t)
    # For intraday: show only regular session (9:30-16:00 ET)
    if (timeframe == "intraday") {
      df <- df %>% filter(
        format(t, "%H:%M") >= "09:30",
        format(t, "%H:%M") <= "16:00"
      )
    }
    if (nrow(df) < 2) return(plot_spacer())
    x_col  <- "t"
    open_p <- df$close[1]
  } else {
    df <- df %>% mutate(t = as.Date(date)) %>% arrange(t)
    x_col  <- "t"
    open_p <- df$close[1]
  }

  latest_p  <- tail(df$close, 1)
  pct_chg   <- (latest_p - open_p) / open_p * 100
  sign_str  <- if (pct_chg >= 0) "+" else ""
  pct_str   <- paste0(sign_str, round(pct_chg, 2), "%")
  lbl_color <- if (pct_chg >= 0) GREEN else RED

  # Normalize to 100 at start for multi-period
  df <- df %>% mutate(norm = close / close[1] * 100)

  y_range <- range(df$norm, na.rm = TRUE)
  y_pad   <- diff(y_range) * 0.08
  if (y_pad < 0.01) y_pad <- 0.5
  x_max_val <- max(df[[x_col]], na.rm = TRUE)
  y_top     <- y_range[2] + y_pad

  p <- ggplot(df, aes(x = .data[[x_col]], y = norm)) +
    geom_hline(yintercept = 100, color = GRID, linewidth = 0.4, linetype = "dashed") +
    geom_line(color = line_color, linewidth = 0.9) +
    annotate("text",
             x = x_max_val, y = y_range[1] + diff(y_range) * 0.06,
             label = pct_str,
             color = lbl_color, fontface = "bold", size = 3.0,
             hjust = 1, vjust = 0) +
    scale_y_continuous(
      labels = function(x) paste0(round(x - 100, 0), "%"),
      expand = expansion(mult = c(0.05, 0.10))
    ) +
    labs(title = paste0(symbol, " - ", label)) +
    snap_theme

  if (is_intraday) {
    p <- p + x_scale_posix()
  } else {
    p <- p + x_scale_date()
  }
  p
}

# ── Load FX panel ─────────────────────────────────────────────────────────────
make_fx_panel <- function(pair_safe, pair_display, line_color = CYAN) {
  csv <- file.path(snap_dir, paste0("FX_", pair_safe, "_", timeframe, ".csv"))
  if (!file.exists(csv)) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5,
                 label = paste0(pair_display, "\nNo data"),
                 color = "white", size = 4, hjust = 0.5) +
        theme_void() +
        theme(plot.background = element_rect(fill = BG, color = GRID, linewidth = 0.5))
    )
  }

  df <- read_csv(csv, show_col_types = FALSE, progress = FALSE)
  if (nrow(df) < 2) return(plot_spacer())

  df <- df %>% mutate(t = as.Date(date)) %>% arrange(t)

  # For intraday/1w: show last 30d of daily data but label as "today's close"
  if (timeframe == "intraday") {
    df <- tail(df, 5)  # last 5 days to keep it tight
  } else if (timeframe == "1w") {
    df <- tail(df, 10)
  }
  if (nrow(df) < 2) return(plot_spacer())

  latest_r  <- tail(df$rate, 1)
  open_r    <- df$rate[1]
  pct_chg   <- (latest_r - open_r) / open_r * 100
  sign_str  <- if (pct_chg >= 0) "+" else ""
  pct_str   <- paste0(sign_str, round(pct_chg, 2), "%")
  lbl_color <- if (pct_chg >= 0) GREEN else RED

  # Decimal places based on magnitude
  dec <- if (latest_r >= 100) 2 else if (latest_r >= 1) 4 else 5

  y_range <- range(df$rate, na.rm = TRUE)
  y_pad   <- diff(y_range) * 0.08
  if (y_pad < 0.0001) y_pad <- latest_r * 0.005
  x_max_val <- max(df$t, na.rm = TRUE)

  ggplot(df, aes(x = t, y = rate)) +
    geom_line(color = line_color, linewidth = 0.9) +
    annotate("text",
             x = x_max_val, y = y_range[1] + diff(y_range) * 0.06,
             label = pct_str,
             color = lbl_color, fontface = "bold", size = 3.0,
             hjust = 1, vjust = 0) +
    scale_x_date(
      date_breaks  = if (nrow(df) <= 10) "2 days" else "1 week",
      date_labels  = "%b %d",
      expand       = expansion(mult = c(0.02, 0.04))
    ) +
    scale_y_continuous(
      labels = function(x) formatC(x, digits = dec, format = "f"),
      expand = expansion(mult = c(0.05, 0.10))
    ) +
    labs(title = pair_display) +
    snap_theme
}

# ── Build all panels ──────────────────────────────────────────────────────────
message("[snapshot] Building equity panels...")
p_spy <- make_etf_panel("SPY", "S&P 500",         CYAN)
p_qqq <- make_etf_panel("QQQ", "Nasdaq 100",       "#c084fc")
p_dia <- make_etf_panel("DIA", "Dow Jones",        "#fbbf24")
p_iwm <- make_etf_panel("IWM", "Russell 2000",     "#34d399")

message("[snapshot] Building bond panels...")
p_shy <- make_etf_panel("SHY", "1-3Y Treasury",    "#60a5fa")
p_ief <- make_etf_panel("IEF", "7-10Y Treasury",   "#3b82f6")
p_tlt <- make_etf_panel("TLT", "20Y+ Treasury",    "#1d4ed8")
p_hyg <- make_etf_panel("HYG", "High Yield Corp",  "#f97316")

message("[snapshot] Building FX panels...")
p_eurusd <- make_fx_panel("EUR_USD", "EUR/USD", CYAN)
p_gbpusd <- make_fx_panel("GBP_USD", "GBP/USD", "#c084fc")
p_usdjpy <- make_fx_panel("USD_JPY", "USD/JPY", "#fbbf24")
p_uup    <- make_etf_panel("UUP",    "DXY (UUP)", "#34d399")

# ── Assemble 3x4 grid ─────────────────────────────────────────────────────────
row1 <- (p_spy | p_qqq | p_dia | p_iwm)
row2 <- (p_shy | p_ief | p_tlt | p_hyg)
row3 <- (p_eurusd | p_gbpusd | p_usdjpy | p_uup)

dashboard <- row1 / row2 / row3 +
  plot_annotation(
    title    = paste0("Markets Snapshot  -  ", tf_label),
    subtitle = format(Sys.Date(), "%B %d, %Y"),
    caption  = "Equities/Bonds: Alpaca Markets  |  FX: ECB/Frankfurter  |  JHCV",
    theme = theme(
      plot.background = element_rect(fill = BG, color = NA),
      plot.title    = element_text(color = WHITE, hjust = 0.5, face = "bold",
                                   size = 17, margin = margin(t = 10, b = 3)),
      plot.subtitle = element_text(color = CYAN,  hjust = 0.5, size = 10,
                                   margin = margin(b = 6)),
      plot.caption  = element_text(color = WHITE, hjust = 1, size = 7,
                                   margin = margin(t = 4, b = 6)),
      plot.margin   = margin(10, 12, 6, 12)
    )
  )

# ── Save ──────────────────────────────────────────────────────────────────────
ggsave(
  filename = out_path,
  plot     = dashboard,
  width    = 1600,
  height   = 1200,
  units    = "px",
  dpi      = 150,
  bg       = BG
)

message(sprintf("[snapshot] Saved -> %s", out_path))
cat(sprintf("OUTPUT_PATH:%s\n", out_path))
