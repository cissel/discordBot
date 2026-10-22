#!/usr/bin/env Rscript
# Navy-theme plot: cumulative overnight vs intraday vs close-to-close, plus per-year means panel.
suppressPackageStartupMessages({library(ggplot2); library(dplyr); library(readr)})
out <- '/home/jhcv/discordBot/outputs/research/structural'
cum <- read_csv(file.path(out, 'overnight_cum.csv'), show_col_types = FALSE)
res <- read_csv(file.path(out, 'overnight_results.csv'), show_col_types = FALSE)

navy <- function(base) {
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

long <- cum %>% select(date, cum_overnight, cum_intraday, cum_close_to_close) %>%
  tidyr::pivot_longer(-date, names_to = 'leg', values_to = 'cum') %>%
  mutate(leg = recode(leg, cum_overnight = 'Overnight (close to open)', cum_intraday = 'Intraday (open to close)',
                      cum_close_to_close = 'Close to close (buy and hold)'))
p1 <- ggplot(long, aes(date, cum * 100, colour = leg)) + geom_line(linewidth = 0.9) +
  scale_colour_manual(values = c('Overnight (close to open)' = '#FFB000', 'Intraday (open to close)' = '#4FC3F7',
                                 'Close to close (buy and hold)' = 'white')) +
  labs(title = 'SPY return decomposition 2021-2026 (1-minute bars, regular session 09:30-16:00 ET)',
       subtitle = 'Cumulative compounded return by leg', x = NULL, y = 'Cumulative return (%)', colour = NULL) + navy()

yr <- res %>% filter(test == 'T2_year', leg %in% c('r_overnight', 'r_intraday')) %>%
  mutate(leg = recode(leg, r_overnight = 'Overnight', r_intraday = 'Intraday'))
p2 <- ggplot(yr, aes(condition, mean_bps, fill = leg)) + geom_col(position = 'dodge', width = 0.7) +
  geom_hline(yintercept = 0, colour = 'white', linewidth = 0.3) +
  scale_fill_manual(values = c('Overnight' = '#FFB000', 'Intraday' = '#4FC3F7')) +
  labs(title = 'Mean daily return by year (bps)', x = NULL, y = 'bps per day', fill = NULL,
       caption = 'Source: SPY 1-min bars; prior-day conditioning; 1bp per side cost baseline') + navy()

png(file.path(out, 'overnight_intraday.png'), width = 11, height = 7, units = 'in', res = 150, bg = '#02233F')
grid::grid.newpage()
grid::pushViewport(grid::viewport(layout = grid::grid.layout(3, 1)))
print(p1, vp = grid::viewport(layout.pos.row = 1:2, layout.pos.col = 1))
print(p2, vp = grid::viewport(layout.pos.row = 3, layout.pos.col = 1))
dev.off()
cat('wrote', file.path(out, 'overnight_intraday.png'), '\n')
