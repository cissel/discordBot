# jaxRealestateSqft.R by JHCV
# Scatter plot: sqft vs sale_price, colored by zip code
# OLS regression line + median/mean horizontal reference lines
# Filtered to qualified sales, outliers removed (top/bottom 1%)

library(ggplot2)
library(dplyr)
library(scales)

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

##### Remove outliers: top/bottom 1% of sale_price and sqft #####

price_lo <- quantile(df_raw$sale_price, 0.01, na.rm = TRUE)
price_hi <- quantile(df_raw$sale_price, 0.99, na.rm = TRUE)
sqft_lo  <- quantile(df_raw$sqft,       0.01, na.rm = TRUE)
sqft_hi  <- quantile(df_raw$sqft,       0.99, na.rm = TRUE)

df <- df_raw %>%
  filter(
    sale_price >= price_lo, sale_price <= price_hi,
    sqft       >= sqft_lo,  sqft       <= sqft_hi,
    !is.na(sale_price), !is.na(sqft)
  )

##### Summary stats #####

n_sales     <- nrow(df)
date_min    <- format(min(df$sale_date, na.rm = TRUE), '%m/%d/%Y')
date_max    <- format(max(df$sale_date, na.rm = TRUE), '%m/%d/%Y')
med_price   <- median(df$sale_price, na.rm = TRUE)
mean_price  <- mean(df$sale_price,   na.rm = TRUE)
med_sqft    <- median(df$sqft,       na.rm = TRUE)

subtitle_txt <- paste0(
  n_sales, ' sales | ',
  date_min, ' - ', date_max, ' | ',
  'Median: ', dollar(med_price), ' | ',
  'Median sqft: ', comma(round(med_sqft))
)

##### Color palette #####
# Use a hand-picked set of bright, distinct colors that read well on dark bg.
# If there are more zip codes than colors, scale_color_brewer Paired is used as fallback.

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

##### Y-axis label positions for median/mean lines #####
# Offset them slightly so they don't overlap if close together

med_label_x  <- quantile(df$sqft, 0.98, na.rm = TRUE)
mean_label_x <- quantile(df$sqft, 0.98, na.rm = TRUE)

price_offset <- (price_hi - price_lo) * 0.02  # 2% of range as nudge

med_label_y  <- med_price  + price_offset
mean_label_y <- mean_price - price_offset

# If median and mean are very close, separate labels more
if (abs(med_price - mean_price) < price_offset * 2) {
  med_label_y  <- med_price  + price_offset * 2
  mean_label_y <- mean_price - price_offset * 2
}

##### Plot #####

p <- ggplot(df, aes(x = sqft, y = sale_price, color = zip)) +

  # Scatter points
  geom_point(size = 2, alpha = 0.7) +

  # ONE overall OLS regression line (aes inheritance disabled via inherit.aes=FALSE)
  geom_smooth(
    data        = df,
    inherit.aes = FALSE,
    aes(x = sqft, y = sale_price),
    method      = 'lm',
    formula     = y ~ x,
    color       = 'white',
    fill        = '#FFFFFF33',
    linewidth   = 0.8,
    se          = TRUE
  ) +

  # Median price horizontal line
  geom_hline(
    yintercept = med_price,
    color      = '#FFD700',
    linetype   = 'dashed',
    linewidth  = 0.6
  ) +

  # Mean price horizontal line
  geom_hline(
    yintercept = mean_price,
    color      = '#00BFFF',
    linetype   = 'dashed',
    linewidth  = 0.6
  ) +

  # Median label
  annotate(
    'text',
    x     = med_label_x,
    y     = med_label_y,
    label = paste0('Median: ', dollar(round(med_price))),
    color = '#FFD700',
    size  = 3,
    hjust = 1
  ) +

  # Mean label
  annotate(
    'text',
    x     = mean_label_x,
    y     = mean_label_y,
    label = paste0('Mean: ', dollar(round(mean_price))),
    color = '#00BFFF',
    size  = 3,
    hjust = 1
  ) +

  color_scale +

  scale_x_continuous(labels = comma_format(), expand = c(0.02, 0)) +
  scale_y_continuous(labels = dollar_format(), expand = c(0.03, 0)) +

  labs(
    title    = 'Duval County - Single Family Sales (Last 90 Days)',
    subtitle = subtitle_txt,
    x        = 'Square Feet',
    y        = 'Sale Price',
    caption  = 'Source: Duval County Property Appraiser | Qualified sales only | JHCV'
  ) +

  myTheme

##### Save #####

out_path <- '/home/jhcv/discordBot/outputs/jax/realestate_sqft.png'

ggsave(
  filename = out_path,
  plot     = p,
  width    = 1200,
  height   = 800,
  units    = 'px',
  dpi      = 120,
  bg       = '#02233F'
)

cat('ok:', out_path, '\n')
