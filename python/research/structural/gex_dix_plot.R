#!/usr/bin/env Rscript
# Navy-theme plot for the GEX/DIX study. Reads gex_dix_plotdata.csv written by gex_dix_study.py.
suppressPackageStartupMessages({library(ggplot2); library(dplyr); library(readr)})
out <- "/home/jhcv/discordBot/outputs/research/structural"
d <- read_csv(file.path(out, "gex_dix_plotdata.csv"), show_col_types = FALSE)
d$bucket <- factor(d$bucket)
d$panel <- ifelse(grepl("^A", d$panel), "A: DIX decile vs fwd 21d SPX return (%)", "B: GEX quintile vs realized 5d vol / VIX")

navy <- "#02233F"; grid <- "#274066"
theme_navy <- theme_minimal(base_size = 13) +
  theme(plot.background = element_rect(fill = navy, colour = navy),
        panel.background = element_rect(fill = navy, colour = navy),
        panel.grid.major = element_line(colour = grid),
        panel.grid.minor = element_line(colour = grid, linewidth = 0.25),
        text = element_text(colour = "white"),
        axis.text = element_text(colour = "white"),
        axis.title = element_text(colour = "white"),
        plot.title = element_text(colour = "white", face = "bold"),
        plot.subtitle = element_text(colour = "white"),
        plot.caption = element_text(colour = "white"),
        strip.text = element_text(colour = "white", face = "bold", size = 12),
        legend.text = element_text(colour = "white"),
        legend.title = element_text(colour = "white"),
        strip.background = element_rect(fill = navy, colour = navy))

d <- d %>% mutate(fill = ifelse(grepl("^A", panel), "#4FC3F7", "#FFB74D"),
                  xlab = ifelse(grepl("^A", panel), "DIX decile (0 = lowest, 9 = highest)", "GEX quintile (0 = lowest, 4 = highest)"))

p <- ggplot(d, aes(x = bucket, y = value, fill = fill)) +
  geom_col(width = 0.7) +
  geom_errorbar(aes(ymin = lo, ymax = hi), width = 0.25, colour = "white", linewidth = 0.6) +
  geom_text(aes(label = paste0("n=", n), y = pmin(lo, 0)), vjust = 1.6, colour = "white", size = 3) +
  scale_fill_identity() +
  facet_wrap(~panel, scales = "free", ncol = 2) +
  labs(x = "Bucket (trailing 252-day rank, no lookahead)", y = "Mean, with 90% block-bootstrap CI (10,000 draws)",
       title = "SqueezeMetrics DIX and GEX vs SPX, 2012-2026",
       subtitle = "Buckets use trailing 252-day ranks (no lookahead). Right panel: 1.0 means realized vol equals VIX-implied vol.",
       caption = "DIX top minus bottom decile, 21d: +0.65% (NW t = 1.0, CI spans 0). GEX q0 minus q4 vol ratio: +0.29 (NW t = 9.1), both halves agree.") +
  theme_navy
ggsave(file.path(out, "gex_dix.png"), p, bg = navy, width = 11, height = 7, dpi = 150)
cat("saved", file.path(out, "gex_dix.png"), "\n")
