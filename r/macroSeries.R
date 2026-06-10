suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(httr)
  library(jsonlite)
  library(scales)
})

# ── Args ──────────────────────────────────────────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("Usage: Rscript macroSeries.R <series_name> <output_path>")

series_name <- toupper(trimws(args[1]))
output_path <- args[2]

valid_series <- c("DXY", "M2", "REALYIELD", "CPI", "FEDFUNDS", "10Y")
if (!series_name %in% valid_series) {
  stop("series_name must be one of: ", paste(valid_series, collapse = ", "))
}

# ── Paths ─────────────────────────────────────────────────────────────────────
bot_dir    <- file.path(path.expand("~"), "discordBot")
cache_dir  <- file.path(bot_dir, "outputs", "markets", "cache")
dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
dir.create(cache_dir,            recursive = TRUE, showWarnings = FALSE)

# ── FRED API key ──────────────────────────────────────────────────────────────
env_file <- file.path(bot_dir, ".env")
if (file.exists(env_file)) readRenviron(env_file)
FRED_KEY <- Sys.getenv("FRED_API_KEY")
if (nchar(FRED_KEY) == 0) stop("FRED_API_KEY not found in .env")

# ── Theme constants ───────────────────────────────────────────────────────────
BG     <- "#02233F"
GRID   <- "#274066"
TXT    <- "white"
CYAN   <- "#00bfff"
GRAY   <- "white"
BORDER <- "#30363d"
RED    <- "#ff6b6b"

# ── FRED fetch + cache (24h TTL) ──────────────────────────────────────────────
# Returns list(df = data.frame, stale = logical)
fetch_fred <- function(series_id, start = "2010-01-01") {
  cache_file <- file.path(cache_dir, paste0("MACRO_", series_id, ".csv"))
  cache_fresh <- file.exists(cache_file) &&
    as.numeric(difftime(Sys.time(), file.info(cache_file)$mtime, units = "hours")) < 24

  if (cache_fresh) {
    message("  [cache] ", series_id)
    df <- read_csv(cache_file,
                   col_types = cols(date = col_date(), value = col_double()),
                   show_col_types = FALSE)
    return(list(df = df, stale = FALSE))
  }

  message("  [FRED]  ", series_id)
  url <- paste0(
    "https://api.stlouisfed.org/fred/series/observations",
    "?series_id=", series_id,
    "&api_key=",   FRED_KEY,
    "&file_type=json",
    "&observation_start=", start,
    "&sort_order=asc"
  )

  resp <- tryCatch(
    httr::GET(url, httr::timeout(30)),
    error = function(e) NULL
  )

  # Network/HTTP failure - fall back to stale cache if available
  if (is.null(resp) || httr::http_error(resp)) {
    if (file.exists(cache_file)) {
      message("  [stale] ", series_id, " (HTTP error / timeout)")
      df <- read_csv(cache_file,
                     col_types = cols(date = col_date(), value = col_double()),
                     show_col_types = FALSE)
      return(list(df = df, stale = TRUE))
    }
    stop("HTTP error for ", series_id, " and no cache available")
  }

  raw <- httr::content(resp, as = "text", encoding = "UTF-8")
  obs <- tryCatch(jsonlite::fromJSON(raw)$observations, error = function(e) NULL)

  if (is.null(obs) || nrow(obs) == 0) {
    if (file.exists(cache_file)) {
      message("  [stale] ", series_id, " (no observations)")
      df <- read_csv(cache_file,
                     col_types = cols(date = col_date(), value = col_double()),
                     show_col_types = FALSE)
      return(list(df = df, stale = TRUE))
    }
    stop("No observations returned for ", series_id)
  }

  df <- obs %>%
    transmute(
      date  = as.Date(date),
      value = suppressWarnings(as.numeric(value))
    ) %>%
    filter(!is.na(value))

  write_csv(df, cache_file)
  list(df = df, stale = FALSE)
}

# ── Recession bands (USREC) ───────────────────────────────────────────────────
fetch_recessions <- function() {
  result <- tryCatch(fetch_fred("USREC", start = "2010-01-01"), error = function(e) NULL)
  if (is.null(result)) return(NULL)

  df <- result$df %>% arrange(date)
  if (nrow(df) == 0) return(NULL)

  # Build contiguous recession intervals
  recs <- df %>%
    mutate(
      in_rec = value == 1,
      grp    = cumsum(c(TRUE, diff(in_rec) != 0))
    ) %>%
    filter(in_rec) %>%
    group_by(grp) %>%
    summarise(start = min(date), end = max(date), .groups = "drop")

  if (nrow(recs) == 0) return(NULL)
  recs
}

# ── Series configuration ──────────────────────────────────────────────────────
series_config <- list(
  DXY = list(
    fred_id  = "DTWEXBGS",
    label    = "DXY - Broad Dollar Index",
    ylabel   = "Index",
    yformat  = function(x) formatC(x, format = "f", digits = 1),
    decimals = 1,
    hlines   = NULL,
    pct      = FALSE,
    m2_scale = FALSE,
    yoy      = FALSE
  ),
  M2 = list(
    fred_id  = "M2SL",
    label    = "M2 Money Supply",
    ylabel   = "Trillions (USD)",
    yformat  = function(x) paste0("$", formatC(x, format = "f", digits = 1), "T"),
    decimals = 1,
    hlines   = NULL,
    pct      = FALSE,
    m2_scale = TRUE,
    yoy      = FALSE
  ),
  REALYIELD = list(
    fred_id  = "DFII10",
    label    = "10Y Real Yield (TIPS)",
    ylabel   = "% p.a.",
    yformat  = function(x) paste0(formatC(x, format = "f", digits = 2), "%"),
    decimals = 2,
    hlines   = list(list(y = 0, color = GRAY, label = "0%")),
    pct      = TRUE,
    m2_scale = FALSE,
    yoy      = FALSE
  ),
  CPI = list(
    fred_id  = "CPIAUCSL",
    label    = "CPI - Year over Year %",
    ylabel   = "YoY %",
    yformat  = function(x) paste0(formatC(x, format = "f", digits = 1), "%"),
    decimals = 1,
    hlines   = list(list(y = 2, color = "#ffa500", label = "2% target")),
    pct      = TRUE,
    m2_scale = FALSE,
    yoy      = TRUE
  ),
  FEDFUNDS = list(
    fred_id  = c("DFEDTARU", "FEDFUNDS"),  # primary, fallback
    label    = "Fed Funds Rate",
    ylabel   = "% p.a.",
    yformat  = function(x) paste0(formatC(x, format = "f", digits = 2), "%"),
    decimals = 2,
    hlines   = NULL,
    pct      = TRUE,
    m2_scale = FALSE,
    yoy      = FALSE
  ),
  `10Y` = list(
    fred_id  = "DGS10",
    label    = "10Y Treasury Yield",
    ylabel   = "% p.a.",
    yformat  = function(x) paste0(formatC(x, format = "f", digits = 2), "%"),
    decimals = 2,
    hlines   = NULL,
    pct      = TRUE,
    m2_scale = FALSE,
    yoy      = FALSE
  )
)

cfg <- series_config[[series_name]]

# ── Fetch data ────────────────────────────────────────────────────────────────
stale_flag <- FALSE
fred_ids   <- cfg$fred_id  # may be a vector (FEDFUNDS has fallback)

result <- NULL
used_id <- NULL

for (fid in fred_ids) {
  result <- tryCatch(fetch_fred(fid), error = function(e) {
    message("  fetch_fred(", fid, ") failed: ", conditionMessage(e))
    NULL
  })
  if (!is.null(result)) {
    used_id    <- fid
    stale_flag <- result$stale
    break
  }
}

if (is.null(result)) stop("All FRED fetch attempts failed for series: ", series_name)

df_raw <- result$df

# ── Transform data ────────────────────────────────────────────────────────────
df <- df_raw %>% arrange(date)

if (cfg$m2_scale) {
  df <- df %>% mutate(value = value / 1000)
}

if (cfg$yoy) {
  df <- df %>%
    mutate(yoy = (value / lag(value, 12) - 1) * 100) %>%
    filter(!is.na(yoy)) %>%
    select(date, value = yoy)
}

df <- df %>% filter(!is.na(value))

if (nrow(df) == 0) stop("No data available after transformation for series: ", series_name)

# ── Metadata for annotations ──────────────────────────────────────────────────
latest      <- df %>% slice_tail(n = 1)
latest_date <- format(latest$date, "%b %d, %Y")
latest_val  <- cfg$yformat(latest$value)

subtitle_text <- if (stale_flag) {
  paste0("Latest: ", latest_val, "  (", latest_date, ")  [WARNING: using stale cache]")
} else {
  paste0("Latest: ", latest_val, "  (", latest_date, ")")
}

# ── Recession bands ───────────────────────────────────────────────────────────
recessions <- tryCatch(fetch_recessions(), error = function(e) {
  message("  Recession data unavailable: ", conditionMessage(e))
  NULL
})

# Clip recession bands to data x-range
x_min <- min(df$date)
x_max <- max(df$date)
if (!is.null(recessions) && nrow(recessions) > 0) {
  recessions <- recessions %>%
    mutate(
      start = pmax(start, x_min),
      end   = pmin(end,   x_max)
    ) %>%
    filter(start < end)
}

# ── Y-axis limits with padding ────────────────────────────────────────────────
y_range <- range(df$value, na.rm = TRUE)
y_span  <- diff(y_range)
if (y_span == 0) y_span <- abs(y_range[1]) * 0.1 + 0.1
y_lo  <- y_range[1] - 0.05 * y_span
y_hi  <- y_range[2] + 0.12 * y_span

# Expand to include any hline references
if (!is.null(cfg$hlines)) {
  for (hl in cfg$hlines) {
    y_lo <- min(y_lo, hl$y - 0.05 * y_span)
    y_hi <- max(y_hi, hl$y + 0.05 * y_span)
  }
}

# Position for current-value annotation (near top-right)
ann_y <- y_hi - 0.06 * (y_hi - y_lo)
ann_x <- latest$date

# ── Build plot ────────────────────────────────────────────────────────────────
p <- ggplot(df, aes(x = date, y = value))

# Recession shading
if (!is.null(recessions) && nrow(recessions) > 0) {
  p <- p + geom_rect(
    data        = recessions,
    aes(xmin = start, xmax = end, ymin = -Inf, ymax = Inf),
    inherit.aes = FALSE,
    fill        = "#ffffff",
    alpha       = 0.04
  )
}

# Horizontal reference lines
if (!is.null(cfg$hlines)) {
  for (hl in cfg$hlines) {
    p <- p +
      geom_hline(
        yintercept = hl$y,
        color      = hl$color,
        linewidth  = 0.55,
        linetype   = "dashed",
        alpha      = 0.75
      ) +
      annotate(
        "text",
        x      = x_min + (x_max - x_min) * 0.01,
        y      = hl$y + (y_hi - y_lo) * 0.025,
        label  = hl$label,
        color  = hl$color,
        size   = 3.0,
        hjust  = 0,
        fontface = "italic"
      )
  }
}

# Main line
p <- p + geom_line(color = CYAN, linewidth = 1.0)

# Vertical dotted line at latest date
p <- p + geom_vline(
  xintercept = latest$date,
  color      = CYAN,
  linewidth  = 0.5,
  linetype   = "dotted",
  alpha      = 0.7
)

# Annotate current value (offset leftward by ~1% of date range)
date_span_days <- as.numeric(x_max - x_min)
p <- p + annotate(
  "text",
  x        = ann_x - round(date_span_days * 0.01),
  y        = ann_y,
  label    = latest_val,
  color    = CYAN,
  size     = 4.2,
  hjust    = 1,
  fontface = "bold"
)

# Scales and labels
p <- p +
  scale_x_date(
    date_breaks  = "1 year",
    date_labels  = "'%y",
    expand       = expansion(mult = c(0.01, 0.03))
  ) +
  scale_y_continuous(
    labels = cfg$yformat,
    limits = c(y_lo, y_hi),
    expand = expansion(mult = c(0, 0))
  ) +
  labs(
    title    = cfg$label,
    subtitle = subtitle_text,
    x        = NULL,
    y        = cfg$ylabel,
    caption  = "Source: FRED | JHCV"
  )

# ── Dark theme ────────────────────────────────────────────────────────────────
p <- p + theme_minimal(base_size = 11) +
  theme(
    plot.background    = element_rect(fill = BG,     color = NA),
    panel.background   = element_rect(fill = BG,     color = NA),
    panel.grid.major   = element_line(color = GRID,  linewidth = 0.4),
    panel.grid.minor   = element_blank(),
    panel.border       = element_rect(color = BORDER, fill = NA, linewidth = 0.6),
    axis.text          = element_text(color = GRAY,  size = 9),
    axis.title.y       = element_text(color = GRAY,  size = 9,  margin = margin(r = 6)),
    axis.title.x       = element_blank(),
    plot.title         = element_text(color = TXT,   size = 15, face = "bold",
                                      hjust = 0.5,   margin = margin(b = 3)),
    plot.subtitle      = element_text(color = GRAY,  size = 10,
                                      hjust = 0.5,   margin = margin(b = 8)),
    plot.caption       = element_text(color = GRAY,  size = 8,
                                      hjust = 1,     margin = margin(t = 6)),
    plot.margin        = margin(14, 16, 10, 14),
    legend.position    = "none"
  )

# ── Save ──────────────────────────────────────────────────────────────────────
ggsave(
  filename = output_path,
  plot     = p,
  width    = 1200,
  height   = 500,
  units    = "px",
  dpi      = 150,
  bg       = BG
)

message("Saved: ", output_path)
