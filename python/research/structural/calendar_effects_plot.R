#!/usr/bin/env Rscript
# Navy-theme plot of calendar effect differences (event minus other days) with bootstrap 90% CI.
suppressPackageStartupMessages({library(ggplot2); library(dplyr); library(readr)})
out <- "/home/jhcv/discordBot/outputs/research/structural"
d <- read_csv(file.path(out, "calendar_effects_wide.csv"), show_col_types = FALSE) %>%
  filter(period %in% c("pre2019", "post2019")) %>%
  mutate(period = factor(period, levels = c("pre2019", "post2019"),
                         labels = c("pre-2019", "2019+")),
         effect = factor(effect, levels = c("TOM","preFOMC","FOMC","OpExFri","OpExWeek","CPI","NFP",
                                            "Mon","Tue","Wed","Thu","Fri")),
         series = factor(series, levels = c("SPX","SPY"),
                         labels = c("SPX index close, 2011-2026", "SPY ETF, 2016-2026")))
navy <- "#02233F"; grid <- "#274066"
p <- ggplot(d, aes(x = effect, y = diff_bps, fill = period)) +
  geom_hline(yintercept = 0, colour = "white", linewidth = 0.4) +
  geom_col(position = position_dodge(width = 0.8), width = 0.7) +
  geom_errorbar(aes(ymin = ci90_lo_bps, ymax = ci90_hi_bps),
                position = position_dodge(width = 0.8), width = 0.3, colour = "white", linewidth = 0.5) +
  geom_text(aes(label = paste0("n=", n_event), y = ifelse(ci90_lo_bps < 0, ci90_lo_bps - 4, ci90_hi_bps + 4),
                group = period), position = position_dodge(width = 0.8), colour = "white", size = 2.4) +
  facet_wrap(~series, ncol = 1) +
  scale_fill_manual(values = c("pre-2019" = "#F2A93B", "2019+" = "#4FC3F7")) +
  labs(title = "Calendar and event-day effects: mean daily return on event days minus all other days",
       subtitle = "Bars = bps per day, whiskers = 10,000-draw bootstrap 90% CI. No effect clears zero in both halves. 72 cells tested.",
       x = NULL, y = "Event minus other days (bps per day)", fill = "Period",
       caption = "Flags knowable the day before. TOM = last trading day through first 3 of next month. OpExFri = 3rd Friday. CPI and NFP use 2016+ calendar coverage only.") +
  theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = navy, colour = navy),
        panel.background = element_rect(fill = navy, colour = navy),
        panel.grid.major = element_line(colour = grid), panel.grid.minor = element_line(colour = grid),
        text = element_text(colour = "white"), axis.text = element_text(colour = "white"),
        axis.title = element_text(colour = "white"), plot.title = element_text(colour = "white", face = "bold"),
        plot.subtitle = element_text(colour = "white", size = 9), plot.caption = element_text(colour = "white", size = 8),
        strip.text = element_text(colour = "white", face = "bold"),
        legend.text = element_text(colour = "white"), legend.title = element_text(colour = "white"),
        legend.background = element_rect(fill = navy, colour = navy), legend.key = element_rect(fill = navy),
        legend.position = "top")
ggsave(file.path(out, "calendar_effects.png"), p, bg = navy, width = 11, height = 7, dpi = 150)
cat("saved", file.path(out, "calendar_effects.png"), "\n")
