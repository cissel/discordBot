# sprChart.R - U.S. Strategic Petroleum Reserve (SPR) full history
# Source: EIA API v2 - petroleum/stoc/wstk, series WCSSTUS1
#         "U.S. Ending Stocks of Crude Oil in SPR (Thousand Barrels)"
# Usage: Rscript sprChart.R [output_path]

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
ADD_COLOR <- "#00e676"  # weekly build (barrels added to SPR)
LOSS_COLOR<- "#ff1744"  # weekly draw (barrels released from SPR)
EIA_KEY   <- "2XNFWuCjMhOoRkLQKxtFdwORdSXdKemmTtW9rnqT"
CACHE     <- path.expand("~/discordBot/outputs/markets/cache/spr_history.csv")
CACHE_TTL <- 21600  # 6 hours - EIA petroleum stocks update weekly

args   <- commandArgs(trailingOnly = TRUE)
OUTPUT <- if (length(args) >= 1) args[1] else
          path.expand("~/discordBot/outputs/markets/sprChart.png")

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
    legend.position    = "none"
  )

##### Fetch full SPR history from EIA #####
fetch_spr <- function() {
  url <- "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
  resp <- GET(url, query = list(
    "frequency"          = "weekly",
    "data[0]"            = "value",
    "facets[series][]"   = "WCSSTUS1",
    "sort[0][column]"    = "period",
    "sort[0][direction]" = "asc",
    "offset"             = 0,
    "length"             = 5000,
    "api_key"            = EIA_KEY
  ))
  if (status_code(resp) != 200) {
    stop("EIA API request failed with HTTP ", status_code(resp))
  }
  dat  <- fromJSON(content(resp, "text", encoding = "UTF-8"), simplifyVector = FALSE)
  rows <- dat$response$data
  if (is.null(rows) || length(rows) == 0) {
    stop("EIA API returned no rows for series WCSSTUS1")
  }
  df <- tibble(
    date       = as.Date(sapply(rows, function(r) r$period)),
    barrels_mb = as.numeric(sapply(rows, function(r) r$value))  # thousand barrels
  ) %>%
    filter(!is.na(barrels_mb)) %>%
    arrange(date) %>%
    mutate(barrels_mmb = barrels_mb / 1000)  # -> million barrels
  df
}

##### Cache Logic #####
load_or_fetch <- function() {
  if (file.exists(CACHE)) {
    age <- as.numeric(difftime(Sys.time(), file.info(CACHE)$mtime, units = "secs"))
    if (age < CACHE_TTL) {
      cat("Cache hit: SPR history (age:", round(age), "s)\n")
      return(read_csv(CACHE, col_types = cols(), show_col_types = FALSE))
    }
  }
  cat("Fetching fresh SPR history from EIA...\n")
  df <- fetch_spr()
  write_csv(df, CACHE)
  df
}

df <- load_or_fetch()

if (nrow(df) < 2) {
  stop("Not enough SPR data points to plot.")
}

##### Weekly volume change (barrels added/withdrawn) #####
df <- df %>%
  mutate(
    weekly_chg_kb  = barrels_mb - lag(barrels_mb),   # thousand barrels, +build/-draw
    weekly_chg_bbl = weekly_chg_kb * 1000,            # raw barrels
    chg_dir        = case_when(
      is.na(weekly_chg_kb)       ~ NA_character_,
      weekly_chg_kb >= 0         ~ "add",
      TRUE                        ~ "loss"
    )
  )

MIN_LEVEL_MMB <- 252  # statutory minimum SPR level, 42 U.S.C. Sec 6241(h)(2)(D) (as of May 2026 per GAO-26-106918)
SEPT11_DATE   <- as.Date("2001-09-11")

##### Stats #####
latest      <- tail(df$barrels_mmb, 1)
latest_date <- tail(df$date, 1)
peak        <- max(df$barrels_mmb, na.rm = TRUE)
trough      <- min(df$barrels_mmb, na.rm = TRUE)
prev        <- df$barrels_mmb[nrow(df) - 1]
chg         <- latest - prev
chg_label   <- sprintf("%+.2f MMbbl vs prior week", chg)
pct_of_peak <- latest / peak * 100

subtitle_str <- paste0(
  format(latest_date, "%b %d, %Y"), ":  ",
  format(round(latest), big.mark = ","), " MMbbl  (", chg_label, ")",
  "   |   ", sprintf("%.0f%%", pct_of_peak), " of all-time peak"
)

##### Plot #####
p_top <- ggplot(df, aes(x = date, y = barrels_mmb)) +
  geom_area(fill = ACCENT, alpha = 0.15) +
  geom_line(color = ACCENT, linewidth = 0.9) +
  geom_vline(xintercept = SEPT11_DATE, color = "white", linewidth = 0.6, alpha = 0.5) +
  annotate("text", x = SEPT11_DATE, y = trough,
           label = "Sept 11, 2001", color = "white", alpha = 0.7,
           size = 2.9, angle = 90, hjust = -0.1, vjust = -0.4) +
  geom_hline(yintercept = MIN_LEVEL_MMB, color = "#ff1744", linewidth = 0.8) +
  annotate("text", x = min(df$date), y = MIN_LEVEL_MMB,
           label = paste0("Min. Operational Level: ", MIN_LEVEL_MMB, " MMbbl"),
           color = "#ff1744", size = 3.0, hjust = 0, vjust = -0.5, fontface = "bold") +
  geom_hline(yintercept = latest, linetype = "dashed", color = "white", linewidth = 0.4, alpha = 0.5) +
  annotate("point", x = latest_date, y = latest, color = "white", size = 2.6) +
  scale_x_date(date_breaks = "5 years", date_labels = "%Y") +
  scale_y_continuous(labels = comma, name = "Million Barrels",
                      expand = expansion(mult = c(0.02, 0.1))) +
  labs(
    title    = "U.S. Strategic Petroleum Reserve - Full History",
    subtitle = subtitle_str,
    x        = NULL,
    y        = "Million Barrels"
  ) +
  navy_theme +
  theme(
    axis.text.x        = element_blank(),
    axis.ticks.x        = element_blank(),
    plot.margin         = margin(t = 10, r = 12, b = 2, l = 12)
  )

p_bot <- ggplot(df, aes(x = date, y = weekly_chg_kb, fill = chg_dir)) +
  geom_col(width = 5.5, na.rm = TRUE) +
  geom_hline(yintercept = 0, color = "white", linewidth = 0.3, alpha = 0.6) +
  scale_fill_manual(values = c("add" = ADD_COLOR, "loss" = LOSS_COLOR), na.value = GRID) +
  scale_x_date(date_breaks = "5 years", date_labels = "%Y") +
  scale_y_continuous(labels = comma) +
  labs(x = NULL, y = "Weekly Chg\n(Thous. Bbl)",
       caption = "Source: U.S. EIA (series WCSSTUS1) | Min level per 42 U.S.C. Sec 6241(h)(2)(D) | green=added to SPR, red=released from SPR | JHCV") +
  navy_theme +
  theme(
    plot.margin = margin(t = 2, r = 12, b = 10, l = 12)
  )

p <- p_top / p_bot + plot_layout(heights = c(3, 1))

ggsave(OUTPUT, plot = p, width = 1300 / 150, height = 800 / 150, dpi = 150, bg = BG)
cat("Saved:", OUTPUT, "\n")
