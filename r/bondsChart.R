#!/usr/bin/env Rscript
# bondsChart.R by JHCV
# Bond Market Dashboard: 90-day normalized price chart for 6 bond ETFs
#
# Usage:
#   Rscript r/bondsChart.R [output_path]
#
# Defaults output to ~/discordBot/outputs/markets/bondsChart.png

suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(patchwork)
})

# ── Args & Paths ──────────────────────────────────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
out_path <- if (length(args) >= 1) args[1] else
  file.path(path.expand("~"), "discordBot", "outputs", "markets", "bondsChart.png")
out_path <- path.expand(out_path)

dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)

DAYS <- if (length(args) >= 2) as.integer(args[2]) else 90
cache_dir  <- path.expand("~/discordBot/outputs/markets/cache")
cache_file <- file.path(cache_dir, paste0("bonds_bars_", DAYS, "d.csv"))
dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)

# ── Load API Keys ─────────────────────────────────────────────────────────────
readRenviron("~/discordBot/.env")
alpaca_key    <- Sys.getenv("APCA_API_KEY_ID")
alpaca_secret <- Sys.getenv("APCA_API_SECRET_KEY")

if (alpaca_key == "" || alpaca_secret == "") {
  stop("Alpaca API keys not found in ~/discordBot/.env")
}

# ── ETF Definitions ───────────────────────────────────────────────────────────
etf_map <- c(
  TLT = "iShares 20+ Year Treasury",
  IEF = "iShares 7-10 Year Treasury",
  SHY = "iShares 1-3 Year Treasury",
  HYG = "iShares High Yield Corp Bond",
  LQD = "iShares Inv Grade Corp Bond",
  EMB = "iShares Emerging Market Bond"
)

etf_colors <- c(
  TLT = "#00bfff",
  IEF = "#00bfff",
  SHY = "#00bfff",
  HYG = "#ff8c00",
  LQD = "#ff8c00",
  EMB = "#ff8c00"
)

all_symbols    <- names(etf_map)
symbols_string <- paste(all_symbols, collapse = ",")

# ── Theme Constants ───────────────────────────────────────────────────────────
BG   <- "#02233F"
GRID <- "#274066"
CYAN <- "#00bfff"

bond_theme <- theme_minimal(base_size = 10) +
  theme(
    plot.background  = element_rect(fill = BG,   color = NA),
    panel.background = element_rect(fill = BG,   color = NA),
    panel.grid.major = element_line(color = GRID, linewidth = 0.4),
    panel.grid.minor = element_line(color = GRID, linewidth = 0.2),
    axis.ticks       = element_line(color = GRID),
    axis.text        = element_text(color = "white", size = 7.5),
    axis.title       = element_text(color = "white", size = 8),
    plot.title       = element_text(color = "white", hjust = 0.5, face = "bold", size = 9.5),
    plot.subtitle    = element_text(color = "white", hjust = 0.5, size = 8),
    plot.caption     = element_text(color = "white", size = 7, hjust = 1),
    strip.background = element_rect(fill = BG),
    strip.text       = element_text(color = "white"),
    legend.position  = "none",
    plot.margin      = margin(8, 10, 6, 8)
  )

# ── Fetch Bars from Alpaca ────────────────────────────────────────────────────
fetch_bonds_bars <- function() {
  # 10-day window for latest quotes (for caching)
  start_date_api <- format(Sys.Date() - 10, "%Y-%m-%d")
  # 90-day history start
  start_date_90  <- format(Sys.Date() - (DAYS + 5), "%Y-%m-%d")

  # Fetch the full 90-day history in one call
  url <- paste0(
    "https://data.alpaca.markets/v2/stocks/bars",
    "?symbols=", symbols_string,
    "&timeframe=1Day",
    "&start=",   start_date_90,
    "&feed=sip",
    "&sort=asc"
  )

  message("[bondsChart] Fetching from Alpaca API ...")
  resp <- GET(
    url,
    add_headers(
      "APCA-API-KEY-ID"     = alpaca_key,
      "APCA-API-SECRET-KEY" = alpaca_secret
    ),
    timeout(45)
  )

  if (http_error(resp)) {
    stop(paste("Alpaca API error:", status_code(resp),
               content(resp, "text", encoding = "UTF-8")))
  }

  raw    <- content(resp, "text", encoding = "UTF-8")
  parsed <- fromJSON(raw, simplifyVector = FALSE)
  bars_list <- parsed$bars

  if (is.null(bars_list) || length(bars_list) == 0) {
    stop("No bar data returned from Alpaca API")
  }

  rows <- list()
  for (sym in names(bars_list)) {
    bars <- bars_list[[sym]]
    if (length(bars) == 0) next
    for (b in bars) {
      rows[[length(rows) + 1]] <- data.frame(
        symbol = sym,
        date   = substr(b$t, 1, 10),
        close  = as.numeric(b$c),
        stringsAsFactors = FALSE
      )
    }
  }

  if (length(rows) == 0) {
    stop("No valid bar rows parsed from API response")
  }

  df <- bind_rows(rows)
  write_csv(df, cache_file)
  message(sprintf("[bondsChart] Cached %d rows to %s", nrow(df), cache_file))
  return(df)
}

# ── Cache Check (1h TTL) ──────────────────────────────────────────────────────
use_cache <- FALSE
if (file.exists(cache_file)) {
  cache_age_min <- as.numeric(difftime(Sys.time(), file.mtime(cache_file), units = "mins"))
  if (cache_age_min <= 60) {
    use_cache <- TRUE
    message(sprintf("[bondsChart] Using cached data (age: %.1f min)", cache_age_min))
  }
}

df_raw <- tryCatch(
  if (use_cache) read_csv(cache_file, show_col_types = FALSE) else fetch_bonds_bars(),
  error = function(e) {
    message("[bondsChart] API fetch failed: ", e$message)
    if (file.exists(cache_file)) {
      message("[bondsChart] Falling back to stale cache")
      read_csv(cache_file, show_col_types = FALSE)
    } else {
      stop(e)
    }
  }
)

# ── Prepare Data ──────────────────────────────────────────────────────────────
df_raw <- df_raw %>%
  mutate(date = as.Date(date)) %>%
  filter(!is.na(close), close > 0)

# Keep last 90 trading days
cutoff <- Sys.Date() - (DAYS + 5)
df_raw <- df_raw %>% filter(date >= cutoff)

# Determine which ETFs have enough data
available_syms <- df_raw %>%
  group_by(symbol) %>%
  summarise(n = n(), .groups = "drop") %>%
  filter(n >= 5) %>%
  pull(symbol)

missing_syms <- setdiff(all_symbols, available_syms)
if (length(missing_syms) > 0) {
  message("[bondsChart] Skipping ETFs with insufficient data: ",
          paste(missing_syms, collapse = ", "))
}

if (length(available_syms) == 0) {
  stop("[bondsChart] No ETF data available to plot")
}

# Normalize each ETF to 100 at period start
normalize_etf <- function(sym) {
  d <- df_raw %>%
    filter(symbol == sym) %>%
    arrange(date)
  base <- d$close[1]
  if (is.na(base) || base == 0) return(NULL)
  d <- d %>%
    mutate(
      norm  = close / base * 100,
      pct   = (close / base - 1) * 100
    )
  d
}

norm_list <- lapply(available_syms, normalize_etf)
names(norm_list) <- available_syms
norm_list <- Filter(Negate(is.null), norm_list)

# ── Credit Spread Proxy (HYG return - IEF return) ────────────────────────────
spread_label <- "N/A"
if ("HYG" %in% names(norm_list) && "IEF" %in% names(norm_list)) {
  hyg_ret <- tail(norm_list[["HYG"]]$pct, 1)
  ief_ret <- tail(norm_list[["IEF"]]$pct, 1)
  spread  <- hyg_ret - ief_ret
  sign_ch <- if (spread >= 0) "+" else ""
  spread_label <- paste0(sign_ch, round(spread, 2), "%")
}

# ── Latest Date ───────────────────────────────────────────────────────────────
latest_date  <- max(df_raw$date, na.rm = TRUE)
date_str     <- format(latest_date, "%B %d, %Y")
subtitle_str <- paste0(date_str, "  |  HYG-IEF spread: ", spread_label)

# ── Build Individual Panels ───────────────────────────────────────────────────
make_panel <- function(sym) {
  d <- norm_list[[sym]]
  if (is.null(d) || nrow(d) == 0) return(NULL)

  col       <- etf_colors[sym]
  full_name <- etf_map[sym]
  panel_title <- paste0(sym, " - ", full_name)

  latest_pct <- tail(d$pct, 1)
  sign_ch    <- if (latest_pct >= 0) "+" else ""
  pct_label  <- paste0(sign_ch, round(latest_pct, 2), "%")

  # x/y range for annotation placement
  x_max <- max(d$date)
  y_min <- min(d$norm, na.rm = TRUE)
  y_max <- max(d$norm, na.rm = TRUE)
  y_annot <- y_min + (y_max - y_min) * 0.04

  ggplot(d, aes(x = date, y = norm)) +
    geom_hline(yintercept = 100, color = GRID, linewidth = 0.5, linetype = "dashed") +
    geom_line(color = col, linewidth = 1.0) +
    annotate(
      "text",
      x         = x_max,
      y         = y_annot,
      label     = pct_label,
      color     = col,
      fontface  = "bold",
      size      = 3.8,
      hjust     = 1,
      vjust     = 0
    ) +
    scale_x_date(
      date_breaks  = if (DAYS <= 120) "1 month" else if (DAYS <= 400) "3 months" else if (DAYS <= 800) "6 months" else "1 year",
      date_labels  = if (DAYS <= 400) "%b '%y" else "'%y",
      minor_breaks = waiver(),
      expand       = expansion(mult = c(0.02, 0.02))
    ) +
    scale_y_continuous(
      labels = function(x) paste0(round(x - 100, 0), "%"),
      expand = expansion(mult = c(0.05, 0.08))
    ) +
    labs(
      title   = panel_title,
      x       = NULL,
      y       = "Return"
    ) +
    bond_theme
}

panels <- lapply(available_syms, make_panel)
names(panels) <- available_syms
panels <- Filter(Negate(is.null), panels)

if (length(panels) == 0) {
  stop("[bondsChart] No panels could be built")
}

# ── Arrange in 2x3 Grid ───────────────────────────────────────────────────────
# Fixed order matching etf_map
ordered_syms <- intersect(names(etf_map), names(panels))
p_list       <- panels[ordered_syms]

# Pad to 6 panels if fewer available
if (length(p_list) < 6) {
  n_missing <- 6 - length(p_list)
  for (i in seq_len(n_missing)) {
    p_list[[paste0("empty_", i)]] <- plot_spacer()
  }
}

combined <- wrap_plots(p_list, ncol = 3, nrow = 2) +
  plot_annotation(
    title    = paste0("Bond Market Dashboard (", DAYS, "D normalized)"),
    subtitle = subtitle_str,
    caption  = "Source: Alpaca Markets | JHCV",
    theme    = theme(
      plot.background = element_rect(fill = BG, color = NA),
      plot.title      = element_text(
        color    = "white",
        face     = "bold",
        size     = 16,
        hjust    = 0.5,
        margin   = margin(t = 12, b = 4)
      ),
      plot.subtitle   = element_text(
        color    = CYAN,
        size     = 10,
        hjust    = 0.5,
        margin   = margin(b = 8)
      ),
      plot.caption    = element_text(
        color    = "white",
        size     = 8,
        hjust    = 1,
        margin   = margin(t = 6, b = 6)
      ),
      plot.margin = margin(12, 14, 8, 14)
    )
  )

# ── Save ──────────────────────────────────────────────────────────────────────
ggsave(
  filename = out_path,
  plot     = combined,
  width    = 1400,
  height   = 900,
  units    = "px",
  dpi      = 150,
  bg       = BG
)

message(sprintf("[bondsChart] Saved -> %s", out_path))
cat(sprintf("OUTPUT_PATH:%s\n", out_path))
