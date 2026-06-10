# vixTerm.R - VIX Term Structure / Fear Curve
# Nodes: VVIX | VIX9D | VIX | VIX3M | VIX6M | VIX1Y
# Usage: Rscript vixTerm.R [output_path]

suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(scales)
  library(patchwork)
})

##### Config #####
BG        <- "#02233F"
GRID      <- "#274066"
ACCENT    <- "#00bfff"
ORANGE    <- "#ff8c00"
CACHE     <- path.expand("~/discordBot/outputs/markets/cache/vix_term.csv")
CACHE_TTL <- 3600

args   <- commandArgs(trailingOnly = TRUE)
OUTPUT <- if (length(args) >= 1) args[1] else
          path.expand("~/discordBot/outputs/markets/vixTerm.png")

dir.create(dirname(CACHE),  showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(OUTPUT), showWarnings = FALSE, recursive = TRUE)

##### Theme #####
navy_theme <- theme_minimal(base_size = 12) +
  theme(
    plot.background    = element_rect(fill = BG,   color = NA),
    panel.background   = element_rect(fill = BG,   color = NA),
    panel.grid.major   = element_line(color = GRID, linewidth = 0.4),
    panel.grid.minor   = element_line(color = GRID, linewidth = 0.2),
    axis.ticks         = element_line(color = GRID),
    axis.text          = element_text(color = "white"),
    axis.title         = element_text(color = "white"),
    plot.title         = element_text(color = "white", hjust = 0.5, face = "bold", size = 16),
    plot.subtitle      = element_text(color = "white", hjust = 0.5, size = 10),
    plot.caption       = element_text(color = "white", size = 8),
    legend.background  = element_rect(fill = BG,   color = NA),
    legend.text        = element_text(color = "white"),
    legend.title       = element_text(color = "white"),
    strip.background   = element_rect(fill = BG,   color = NA),
    strip.text         = element_text(color = "white")
  )

##### Fetch single VIX value #####
fetch_vix <- function(symbol) {
  encoded <- URLencode(symbol, reserved = TRUE)
  url <- paste0(
    "https://query1.finance.yahoo.com/v8/finance/chart/",
    encoded, "?interval=1d&range=1d"
  )
  tryCatch({
    resp <- GET(url, add_headers("User-Agent" = "Mozilla/5.0"))
    if (status_code(resp) != 200) return(NA_real_)
    dat  <- fromJSON(content(resp, "text", encoding = "UTF-8"), simplifyVector = FALSE)
    as.numeric(dat$chart$result[[1]]$meta$regularMarketPrice)
  }, error = function(e) NA_real_)
}

##### Cache Logic #####
load_or_fetch <- function() {
  if (file.exists(CACHE)) {
    age <- as.numeric(difftime(Sys.time(), file.info(CACHE)$mtime, units = "secs"))
    if (age < CACHE_TTL) {
      cat("Using cached VIX data (age:", round(age), "s)\n")
      return(read_csv(CACHE, col_types = cols(), show_col_types = FALSE))
    }
  }
  cat("Fetching fresh VIX data...\n")

  # Term structure nodes (days to expiry, approx)
  symbols <- c("^VIX9D", "^VIX", "^VIX3M", "^VIX6M", "^VIX1Y")
  terms   <- c("9D",     "30D",  "3M",      "6M",      "1Y")
  days    <- c(9L,        30L,    63L,       126L,      252L)
  values  <- sapply(symbols, fetch_vix)

  df <- tibble(term = terms, days = days, value = values)
  write_csv(df, CACHE)
  df
}

# VVIX fetched separately (vol-of-vol - different scale, secondary panel)
cat("Fetching VVIX...\n")
vvix_val <- fetch_vix("^VVIX")

df <- load_or_fetch()

if (all(is.na(df$value))) {
  stop("All VIX term structure fetches failed.")
}

fmt <- function(x) ifelse(is.na(x), "N/A", sprintf("%.2f", x))

vix9d <- df$value[df$term == "9D"]
vix30 <- df$value[df$term == "30D"]
vix3m <- df$value[df$term == "3M"]
vix6m <- df$value[df$term == "6M"]
vix1y <- df$value[df$term == "1Y"]

contango_flag <- if (!is.na(vix9d) && !is.na(vix6m)) {
  if (vix9d < vix6m) "Yes (Contango)" else "No (Backwardation)"
} else "N/A"

# Point labels for term structure
point_labels <- c(
  "9D"  = "VIX9D",
  "30D" = "VIX",
  "3M"  = "VIX3M",
  "6M"  = "VIX6M",
  "1Y"  = "VIX1Y"
)

df_plot <- df %>% filter(!is.na(value))

y_min <- max(0, min(df_plot$value, na.rm = TRUE) * 0.88)
y_max <- max(df_plot$value, na.rm = TRUE) * 1.15

##### Main term structure plot #####
p_term <- ggplot(df_plot, aes(x = days, y = value)) +
  geom_area(fill = ACCENT, alpha = 0.12) +
  geom_hline(
    yintercept = vix30,
    linetype   = "dashed", color = "white",
    linewidth  = 0.5, alpha = 0.45
  ) +
  geom_line(color = ACCENT, linewidth = 1.6) +
  geom_point(color = "white", size = 4.5) +
  geom_text(
    aes(label = paste0(point_labels[term], "\n", fmt(value))),
    color      = "white",
    vjust      = -0.55,
    size       = 3.4,
    lineheight = 0.9
  ) +
  scale_x_continuous(
    name   = "Expiry (days)",
    breaks = c(9, 30, 63, 126, 252),
    labels = c("9D", "30D", "3M", "6M", "1Y")
  ) +
  scale_y_continuous(name = "VIX Level") +
  coord_cartesian(ylim = c(y_min, y_max)) +
  labs(
    title    = "VIX Term Structure - Fear Curve",
    subtitle = paste0(
      "9D=", fmt(vix9d), " | VIX=", fmt(vix30),
      " | 3M=", fmt(vix3m), " | 6M=", fmt(vix6m),
      " | 1Y=", fmt(vix1y),
      "   |   ", contango_flag
    )
  ) +
  navy_theme

##### VVIX gauge panel (vol-of-vol) #####
vvix_color <- if (is.na(vvix_val)) "white" else
              if (vvix_val >= 120) "#ff1744" else
              if (vvix_val >= 100) ORANGE else
              if (vvix_val >= 85)  "white" else ACCENT

# Historical VVIX zones for reference band
vvix_zones <- tibble(
  ymin  = c(0,   85,  100, 120),
  ymax  = c(85, 100,  120, 180),
  label = c("Low", "Elevated", "High", "Extreme"),
  col   = c(ACCENT, "white", ORANGE, "#ff1744")
)

p_vvix <- ggplot() +
  # Background bands
  geom_rect(data = vvix_zones,
            aes(xmin = 0, xmax = 1, ymin = ymin, ymax = ymax, fill = col),
            alpha = 0.08) +
  scale_fill_identity() +
  # Zone labels
  geom_text(data = vvix_zones,
            aes(x = 0.5, y = (ymin + ymax) / 2, label = label, color = col),
            size = 3.5, fontface = "bold") +
  scale_color_identity() +
  # VVIX value line
  {if (!is.na(vvix_val))
    geom_hline(yintercept = vvix_val, color = vvix_color,
               linewidth = 1.8, linetype = "solid")
  } +
  # VVIX label
  {if (!is.na(vvix_val))
    annotate("text", x = 0.02, y = vvix_val + 6,
             label = paste0("VVIX: ", fmt(vvix_val)),
             color = vvix_color, size = 3.8, hjust = 0, fontface = "bold")
  } +
  scale_y_continuous(name = "VVIX", limits = c(60, 180), breaks = seq(60, 180, 20)) +
  scale_x_continuous(name = NULL, breaks = NULL) +
  labs(
    title    = "VVIX - Vol of VIX",
    subtitle = if (is.na(vvix_val)) "Unavailable" else
               paste0("VIX implied vol: ", fmt(vvix_val))
  ) +
  navy_theme +
  theme(axis.text.x = element_blank(), axis.ticks.x = element_blank())

##### Combine: term structure wide left, VVIX narrow right #####
combined <- p_term + p_vvix +
  plot_layout(widths = c(3, 1)) +
  plot_annotation(
    caption = "Source: CBOE / Yahoo Finance | JHCV",
    theme   = theme(
      plot.background = element_rect(fill = BG, color = NA),
      plot.caption    = element_text(color = "white", size = 8)
    )
  )

ggsave(
  OUTPUT, plot = combined,
  width = 1300 / 150, height = 520 / 150, dpi = 150,
  bg = BG
)
cat("Saved:", OUTPUT, "\n")
