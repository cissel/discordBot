suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(httr)
  library(jsonlite)
  library(scales)
  library(patchwork)
})

# ── Args & paths ──────────────────────────────────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
output_path <- if (length(args) >= 1) args[1] else
  file.path(path.expand("~"), "discordBot", "outputs", "markets", "macroDashboard.png")

DAYS      <- if (length(args) >= 2) as.integer(args[2]) else 1825
OBS_START <- format(Sys.Date() - DAYS, '%Y-%m-%d')

cache_dir <- file.path(path.expand("~"), "discordBot", "outputs", "markets", "cache")
dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)

# ── Load FRED API key ─────────────────────────────────────────────────────────
env_file <- file.path(path.expand("~"), "discordBot", ".env")
if (file.exists(env_file)) readRenviron(env_file)
FRED_KEY <- Sys.getenv("FRED_API_KEY")
if (nchar(FRED_KEY) == 0) stop("FRED_API_KEY not found in ~/.env")

# ── Theme constants ───────────────────────────────────────────────────────────
BG      <- "#02233F"
GRID    <- "#274066"
TXT     <- "white"
CYAN    <- "#00bfff"
GRAY    <- "white"
BORDER  <- "#274066"

dark_theme <- theme_minimal(base_size = 9) +
  theme(
    plot.background    = element_rect(fill = BG, color = NA),
    panel.background   = element_rect(fill = BG, color = NA),
    panel.grid.major   = element_line(color = GRID, linewidth = 0.4),
    panel.grid.minor   = element_blank(),
    panel.border       = element_rect(color = BORDER, fill = NA, linewidth = 0.5),
    axis.text          = element_text(color = GRAY, size = 7),
    axis.title         = element_blank(),
    plot.title         = element_text(color = TXT,  size = 9, face = "bold",
                                      hjust = 0.5, margin = margin(b = 2)),
    plot.subtitle      = element_text(color = GRAY, size = 7.5,
                                      hjust = 0.5, margin = margin(b = 4)),
    plot.margin        = margin(8, 8, 8, 8),
    legend.position    = "none"
  )

# ── FRED fetch + cache ────────────────────────────────────────────────────────
fetch_fred <- function(series_id) {
  cache_file <- file.path(cache_dir, paste0("MACRO_", series_id, "_", DAYS, "d.csv"))
  use_cache  <- file.exists(cache_file) &&
    difftime(Sys.time(), file.info(cache_file)$mtime, units = "hours") < 24

  if (use_cache) {
    message("  [cache] ", series_id)
    df <- read_csv(cache_file, col_types = cols(date = col_date(), value = col_double()),
                   show_col_types = FALSE)
    return(df)
  }

  message("  [FRED]  ", series_id)
  url <- paste0(
    "https://api.stlouisfed.org/fred/series/observations",
    "?series_id=", series_id,
    "&api_key=",   FRED_KEY,
    "&file_type=json",
    "&observation_start=", OBS_START,
    "&sort_order=asc"
  )
  resp <- httr::GET(url, httr::timeout(30))
  if (httr::http_error(resp)) stop("HTTP error for ", series_id, ": ", httr::status_code(resp))

  raw  <- httr::content(resp, as = "text", encoding = "UTF-8")
  obs  <- jsonlite::fromJSON(raw)$observations
  if (is.null(obs) || nrow(obs) == 0) stop("No observations returned for ", series_id)

  df <- obs %>%
    transmute(
      date  = as.Date(date),
      value = suppressWarnings(as.numeric(value))
    ) %>%
    filter(!is.na(value))

  write_csv(df, cache_file)
  df
}

# ── Helper: "data unavailable" placeholder panel ──────────────────────────────
unavailable_panel <- function(title) {
  ggplot() +
    annotate("text", x = 0.5, y = 0.5, label = "data unavailable",
             color = GRAY, size = 3.5, fontface = "italic") +
    labs(title = title) +
    dark_theme +
    theme(
      axis.text = element_blank(),
      panel.grid = element_blank()
    ) +
    scale_x_continuous(limits = c(0, 1)) +
    scale_y_continuous(limits = c(0, 1))
}

# ── Helper: build a standard line panel ──────────────────────────────────────
make_panel <- function(df, title, y_suffix = "", y_prefix = "",
                       y_scale_fn = NULL, decimals = 2) {
  if (is.null(df) || nrow(df) < 2) stop("insufficient data for panel: ", title)
  if (is.null(y_scale_fn)) {
    fmt_fn <- function(x) paste0(y_prefix, formatC(x, format = "f", digits = decimals), y_suffix)
  } else {
    fmt_fn <- y_scale_fn
  }

  latest <- df %>% filter(!is.na(value)) %>% slice_tail(n = 1)
  latest_label <- paste0(y_prefix,
                         formatC(latest$value, format = "f", digits = decimals),
                         y_suffix)

  y_range <- range(df$value, na.rm = TRUE)
  y_span  <- diff(y_range)
  ann_y   <- y_range[1] + 0.05 * y_span   # near bottom
  ann_x   <- max(df$date)

  ggplot(df, aes(x = date, y = value)) +
    geom_line(color = CYAN, linewidth = 0.75) +
    annotate("text",
             x = ann_x, y = ann_y,
             label = latest_label,
             color = CYAN, size = 3.2, hjust = 1, fontface = "bold") +
    scale_x_date(
      date_breaks       = if (DAYS <= 120) "1 month" else if (DAYS <= 400) "3 months" else if (DAYS <= 800) "6 months" else "1 year",
      date_labels       = if (DAYS <= 400) "%b '%y" else "'%y",
      minor_breaks = waiver(),
      expand            = expansion(mult = c(0.02, 0.04))) +
    scale_y_continuous(labels = fmt_fn,
                       expand = expansion(mult = c(0.05, 0.10))) +
    labs(title = title) +
    dark_theme
}

# ── Build each panel ──────────────────────────────────────────────────────────

# 1. Fed Funds Rate
p_fed <- tryCatch({
  df <- tryCatch(fetch_fred("DFEDTARU"), error = function(e) fetch_fred("FEDFUNDS"))
  make_panel(df, "Fed Funds Rate", y_suffix = "%", decimals = 2)
}, error = function(e) {
  message("Fed Funds panel error: ", conditionMessage(e))
  unavailable_panel("Fed Funds Rate")
})

# 2. 10Y Treasury
p_dgs10 <- tryCatch({
  df <- fetch_fred("DGS10")
  make_panel(df, "10Y Treasury", y_suffix = "%", decimals = 2)
}, error = function(e) {
  message("DGS10 panel error: ", conditionMessage(e))
  unavailable_panel("10Y Treasury")
})

# 3. 10Y Real Yield (TIPS)
p_tips <- tryCatch({
  df <- fetch_fred("DFII10")
  make_panel(df, "10Y Real Yield (TIPS)", y_suffix = "%", decimals = 2)
}, error = function(e) {
  message("DFII10 panel error: ", conditionMessage(e))
  unavailable_panel("10Y Real Yield (TIPS)")
})

# 4. CPI YoY %
p_cpi <- tryCatch({
  df_raw <- fetch_fred("CPIAUCSL")
  df <- df_raw %>%
    arrange(date) %>%
    mutate(yoy = (value / lag(value, 12) - 1) * 100) %>%
    filter(!is.na(yoy)) %>%
    select(date, value = yoy)
  make_panel(df, "CPI YoY %", y_suffix = "%", decimals = 1)
}, error = function(e) {
  message("CPI panel error: ", conditionMessage(e))
  unavailable_panel("CPI YoY %")
})

# 5. M2 Money Supply ($T)
p_m2 <- tryCatch({
  df_raw <- fetch_fred("WM2NS")
  df <- df_raw %>%
    mutate(value = value / 1000) %>%
    filter(!is.na(value))
  make_panel(df, "M2 Money Supply ($T)", y_prefix = "$", y_suffix = "T", decimals = 1)
}, error = function(e) {
  message("M2 panel error: ", conditionMessage(e))
  unavailable_panel("M2 Money Supply ($T)")
})

# 6. DXY (Broad Dollar)
p_dxy <- tryCatch({
  df <- fetch_fred("DTWEXBGS")
  make_panel(df, "DXY (Broad Dollar)", decimals = 1)
}, error = function(e) {
  message("DTWEXBGS panel error: ", conditionMessage(e))
  unavailable_panel("DXY (Broad Dollar)")
})

# ── Assemble 2x3 grid ─────────────────────────────────────────────────────────
today_str <- format(Sys.Date(), "%B %d, %Y")

combined <- (p_fed | p_dgs10 | p_tips) /
            (p_cpi | p_m2   | p_dxy ) +
  plot_annotation(
    title    = "Macro Dashboard",
    subtitle = paste0("As of ", today_str, " (", round(DAYS/365, 1), "Y)"),
    caption  = "Source: FRED | JHCV",
    theme = theme(
      plot.background = element_rect(fill = BG, color = NA),
      plot.title      = element_text(color = TXT,  size = 16, face = "bold",
                                     hjust = 0.5, margin = margin(b = 2)),
      plot.subtitle   = element_text(color = GRAY, size = 10,
                                     hjust = 0.5, margin = margin(b = 8)),
      plot.caption    = element_text(color = GRAY, size = 8,
                                     margin = margin(t = 6)),
      plot.margin     = margin(14, 14, 10, 14)
    )
  ) &
  theme(plot.background = element_rect(fill = BG, color = NA),
        panel.background = element_rect(fill = BG, color = NA))

# ── Save ──────────────────────────────────────────────────────────────────────
ggsave(
  output_path,
  plot   = combined,
  width  = 1400,
  height = 900,
  units  = "px",
  dpi    = 150,
  bg     = BG
)

message("Saved: ", output_path)
