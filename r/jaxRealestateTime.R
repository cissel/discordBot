# jaxRealestateTime.R by JHCV
# Top panel:    scatter of sale_price over time, colored by zip, LOESS trend
# Bottom panel: weekly sale volume bar chart
# Grid viewport layout (same pattern as repoGraph.R)

library(ggplot2)
library(dplyr)
library(scales)
library(grid)

##### Theme #####

myTheme <- theme(
  plot.background  = element_rect(fill = '#02233F', color = NA),
  panel.background = element_rect(fill = '#02233F', color = NA),
  panel.grid.major = element_line(color = '#274066'),
  panel.grid.minor = element_blank(),
  axis.ticks       = element_line(color = '#274066'),
  axis.text        = element_text(color = 'white', size = 9),
  axis.title       = element_text(color = 'white', size = 10),
  plot.title       = element_text(color = 'white', hjust = 0.5, size = 14, face = 'bold'),
  plot.subtitle    = element_text(color = '#8BAAC8', hjust = 0.5, size = 10),
  plot.caption     = element_text(color = '#8BAAC8', size = 8),
  legend.background = element_rect(fill = '#02233F', color = NA),
  legend.text      = element_text(color = 'white', size = 8),
  legend.title     = element_text(color = 'white', size = 9),
  strip.background = element_rect(fill = '#02233F', color = NA),
  strip.text       = element_text(color = 'white'),
  plot.margin      = margin(12, 16, 8, 16)
)

##### Load Data #####

csv_path <- '/home/jhcv/discordBot/outputs/jax/realestate_sales.csv'
if (!file.exists(csv_path)) stop('realestate_sales.csv not found')

df_raw <- read.csv(csv_path, stringsAsFactors = FALSE)

# Parse date, keep qualified sales only
df_raw <- df_raw %>%
  mutate(
    sale_date = as.Date(sale_date, format = '%m/%d/%Y'),
    zip       = as.character(zip)
  ) %>%
  filter(tolower(qualified) == 'qualified')

##### Remove outliers: top/bottom 1% of sale_price #####

price_lo <- quantile(df_raw$sale_price, 0.01, na.rm = TRUE)
price_hi <- quantile(df_raw$sale_price, 0.99, na.rm = TRUE)

df <- df_raw %>%
  filter(
    sale_price >= price_lo, sale_price <= price_hi,
    !is.na(sale_price), !is.na(sale_date)
  )

##### Summary stats #####

n_sales   <- nrow(df)
date_min  <- format(min(df$sale_date, na.rm = TRUE), '%m/%d/%Y')
date_max  <- format(max(df$sale_date, na.rm = TRUE), '%m/%d/%Y')

subtitle_txt <- paste0(
  n_sales, ' sales | ',
  date_min, ' - ', date_max, ' | ',
  'Total volume: ', n_sales, ' transactions'
)

##### Color palette - same approach as jaxRealestateSqft.R #####

zip_levels <- sort(unique(df$zip))
n_zips     <- length(zip_levels)

bright_palette <- c(
  '#FF6B6B', '#FFD93D', '#6BCB77', '#4D96FF', '#FF9F1C',
  '#A259FF', '#00C9A7', '#F72585', '#4CC9F0', '#FFA07A',
  '#98FF98', '#FF69B4', '#7B68EE', '#20B2AA', '#FFB347',
  '#87CEEB', '#DDA0DD', '#90EE90', '#F0E68C', '#CD853F'
)

if (n_zips <= length(bright_palette)) {
  zip_colors <- setNames(bright_palette[seq_len(n_zips)], zip_levels)
  color_scale <- scale_color_manual(values = zip_colors, name = 'Zip Code')
} else {
  color_scale <- scale_color_brewer(palette = 'Paired', name = 'Zip Code')
}

##### X-axis date breaks - auto-scale to date range #####

date_range_days <- as.numeric(max(df$sale_date) - min(df$sale_date))
if (date_range_days <= 30) {
  x_breaks <- '1 week'
} else if (date_range_days <= 90) {
  x_breaks <- '2 weeks'
} else {
  x_breaks <- '1 month'
}

##### Panel 1 - Sale price over time scatter + LOESS #####

p1 <- ggplot(df, aes(x = sale_date, y = sale_price, color = zip)) +

  # Scatter points
  geom_point(size = 2, alpha = 0.7) +

  # ONE overall LOESS smoothing line (inherit.aes=FALSE for single line)
  geom_smooth(
    inherit.aes = FALSE,
    aes(x = sale_date, y = sale_price),
    method    = 'loess',
    formula   = y ~ x,
    span      = 0.4,
    color     = 'white',
    fill      = '#FFFFFF33',
    linewidth = 0.8,
    se        = TRUE
  ) +

  color_scale +

  scale_x_date(
    date_breaks  = x_breaks,
    date_labels  = '%m/%d/%y',
    expand       = c(0.02, 0)
  ) +
  scale_y_continuous(labels = dollar_format(), expand = c(0.03, 0)) +

  labs(
    title    = 'Duval County - Sale Price Over Time (Last 90 Days)',
    subtitle = subtitle_txt,
    x        = NULL,
    y        = 'Sale Price',
    caption  = NULL
  ) +

  myTheme +
  theme(
    axis.text.x  = element_text(angle = 45, hjust = 1, color = 'white', size = 8),
    axis.ticks.x = element_line(color = '#274066'),
    plot.margin  = margin(12, 16, 2, 16)
  )

##### Panel 2 - Weekly volume bars #####

# Floor each sale date to the Monday of its week for grouping
df_weekly <- df %>%
  mutate(week_start = as.Date(floor(as.numeric(sale_date) / 7) * 7, origin = '1970-01-01')) %>%
  group_by(week_start) %>%
  summarise(count = n(), .groups = 'drop')

p2 <- ggplot(df_weekly, aes(x = week_start, y = count)) +
  geom_col(fill = '#4A90D9', width = 5) +

  scale_x_date(
    date_breaks  = x_breaks,
    date_labels  = '%m/%d/%y',
    expand       = c(0.02, 0)
  ) +
  scale_y_continuous(
    breaks = function(x) unique(floor(pretty(x))),
    expand = c(0, 0.5)
  ) +

  labs(
    x       = NULL,
    y       = 'Sales / Week',
    caption = 'Source: Duval County Property Appraiser | Qualified sales only | JHCV'
  ) +

  myTheme +
  theme(
    axis.text.x = element_text(angle = 45, hjust = 1, color = 'white', size = 8),
    plot.margin = margin(2, 16, 10, 16)
  )

##### Combine with grid viewport #####

out_path <- '/home/jhcv/discordBot/outputs/jax/realestate_time.png'

png(out_path, width = 1200, height = 900, res = 120, bg = '#02233F')

grid.newpage()
pushViewport(viewport(layout = grid.layout(2, 1, heights = unit(c(3, 1), 'null'))))

print(p1, vp = viewport(layout.pos.row = 1, layout.pos.col = 1))
print(p2, vp = viewport(layout.pos.row = 2, layout.pos.col = 1))

dev.off()

cat('ok:', out_path, '\n')
