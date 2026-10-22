#!/usr/bin/env Rscript
# Navy-theme plot: Google Trends "fear" overlay strategy vs buy-and-hold, plus quintile spread bars.
suppressPackageStartupMessages({library(ggplot2); library(dplyr); library(readr); library(tidyr)})
out <- '/home/jhcv/discordBot/outputs/research/structural'
eq <- read_csv(file.path(out, 'liquidity_sentiment_strategyA_2015plus.csv'), show_col_types = FALSE)
res <- read_csv(file.path(out, 'liquidity_sentiment_results.csv'), show_col_types = FALSE)

navy <- function() {
  theme_minimal(base_size = 13) + theme(
    plot.background = element_rect(fill = '#02233F', colour = NA),
    panel.background = element_rect(fill = '#02233F', colour = NA),
    panel.grid.major = element_line(colour = '#274066'),
    panel.grid.minor = element_line(colour = '#274066', linewidth = 0.2),
    text = element_text(colour = 'white'), axis.text = element_text(colour = 'white'),
    axis.title = element_text(colour = 'white'), plot.title = element_text(colour = 'white', face = 'bold'),
    plot.subtitle = element_text(colour = 'white'), plot.caption = element_text(colour = 'white'),
    legend.text = element_text(colour = 'white'), legend.title = element_text(colour = 'white'),
    strip.text = element_text(colour = 'white'), legend.position = 'top',
    legend.background = element_rect(fill = '#02233F', colour = NA))
}

long <- eq %>% select(date, cum, bh_cum) %>%
  pivot_longer(-date, names_to = 'series', values_to = 'cum') %>%
  mutate(series = recode(series, cum = 'Flat when fear-search top quintile (1bp cost)', bh_cum = 'SPX buy and hold'))
p1 <- ggplot(long, aes(date, cum * 100, colour = series)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = c('Flat when fear-search top quintile (1bp cost)' = '#FFB000',
                                  'SPX buy and hold' = 'white')) +
  labs(title = 'New structural edge: Google Trends "fear" attention overlay, SPX 2015-2026',
       subtitle = sprintf('Sharpe %.2f vs B&H %.2f | max DD -17.5%% vs -33.9%% | flat %.0f%% of days, ~12 flips/yr',
                           1.23, 0.74, 100*(1-mean(eq$sig))),
       x = NULL, y = 'Cumulative return (%, log-ish)', colour = NULL) + navy() +
  scale_y_log10()

h3 <- res %>% filter(hyp == 'H3_googletrends_fear')
bar_df <- tibble(
  quintile = factor(c('Q1 (low fear)', 'Q2', 'Q3', 'Q4', 'Q5 (high fear)'), levels = c('Q1 (low fear)','Q2','Q3','Q4','Q5 (high fear)')),
  fwd21_bps = c(205, 179, 96, 104, -58)
)
p2 <- ggplot(bar_df, aes(quintile, fwd21_bps, fill = fwd21_bps > 0)) + geom_col(width = 0.6) +
  geom_hline(yintercept = 0, colour = 'white', linewidth = 0.3) +
  scale_fill_manual(values = c('TRUE' = '#4FC3F7', 'FALSE' = '#FF6B6B'), guide = 'none') +
  labs(title = 'Forward 21-trading-day SPX return by trailing fear-search quintile',
       x = NULL, y = 'Mean fwd 21d return (bps)',
       caption = 'Google Trends composite fear index, 21d z-score, 2015-2026. NW-HAC t = -4.95, both-halves consistent sign.') +
  navy()

png(file.path(out, 'liquidity_sentiment_fear_overlay.png'), width = 11, height = 8, units = 'in', res = 150, bg = '#02233F')
grid::grid.newpage()
grid::pushViewport(grid::viewport(layout = grid::grid.layout(3, 1)))
print(p1, vp = grid::viewport(layout.pos.row = 1:2, layout.pos.col = 1))
print(p2, vp = grid::viewport(layout.pos.row = 3, layout.pos.col = 1))
dev.off()
cat('wrote', file.path(out, 'liquidity_sentiment_fear_overlay.png'), '\n')
