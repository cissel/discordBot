#!/usr/bin/env Rscript
# Navy-theme plot for the SPY block print event study (Spec B, one event per day).
suppressPackageStartupMessages({ library(ggplot2); library(dplyr); library(readr); library(patchwork) })

out <- "/home/jhcv/discordBot/outputs/research/structural"
d <- read_csv(file.path(out, "block_plot_data.csv"), show_col_types = FALSE) %>%
  mutate(horizon = factor(horizon, levels = c("1d", "3d", "1w", "2w", "1mo")),
         direction = factor(paste0("block ", direction, " market"),
                            levels = c("block above market", "block below market")),
         excess_bp = mean_excess * 1e4, lo_bp = ci90_lo * 1e4, hi_bp = ci90_hi * 1e4)

navy <- "#02233F"; grid <- "#274066"
th <- theme_minimal(base_size = 12) +
  theme(plot.background = element_rect(fill = navy, colour = navy),
        panel.background = element_rect(fill = navy, colour = navy),
        panel.grid.major = element_line(colour = grid), panel.grid.minor = element_blank(),
        text = element_text(colour = "white"), axis.text = element_text(colour = "white"),
        axis.title = element_text(colour = "white"), plot.title = element_text(colour = "white", face = "bold"),
        plot.subtitle = element_text(colour = "white"), plot.caption = element_text(colour = "white"),
        strip.text = element_text(colour = "white", face = "bold", size = 12),
        legend.text = element_text(colour = "white"), legend.title = element_text(colour = "white"),
        legend.background = element_rect(fill = navy, colour = navy), legend.position = "bottom")

p1 <- ggplot(d, aes(horizon, excess_bp)) +
  geom_hline(yintercept = 0, colour = "white", linetype = "dashed") +
  geom_errorbar(aes(ymin = lo_bp, ymax = hi_bp), width = 0.25, colour = "#7FB3FF", linewidth = 0.9) +
  geom_point(size = 3.5, colour = "#FFD166") +
  geom_text(aes(label = paste0("n=", n)), y = max(d$hi_bp) + 25, colour = "white", size = 3.2) +
  scale_y_continuous(expand = expansion(mult = c(0.08, 0.18))) +
  facet_wrap(~direction) +
  labs(title = "SPY after large block prints: excess return over matched unconditional SPY",
       subtitle = "Spec B (same-day close reference), one event per day, 90% bootstrap CI (10k draws), 2022-01 to 2026-10",
       x = NULL, y = "mean excess return (bp)") + th

hd <- d %>% select(direction, horizon, hit_rate, hit_ci_lo, hit_ci_hi, hit_base) %>%
  tidyr::pivot_longer(c(hit_rate, hit_base), names_to = "series", values_to = "rate") %>%
  mutate(series = recode(series, hit_rate = "observed gap-fill hit rate", hit_base = "matched unconditional baseline"),
         hit_ci_lo = ifelse(series == "observed gap-fill hit rate", hit_ci_lo, NA),
         hit_ci_hi = ifelse(series == "observed gap-fill hit rate", hit_ci_hi, NA))

p2 <- ggplot(hd, aes(horizon, rate, colour = series, group = series)) +
  geom_hline(yintercept = 0.5, colour = "white", linetype = "dashed") +
  geom_errorbar(aes(ymin = hit_ci_lo, ymax = hit_ci_hi), width = 0.25, linewidth = 0.9, na.rm = TRUE) +
  geom_line(linewidth = 0.8) + geom_point(size = 3.2) +
  scale_colour_manual(values = c("observed gap-fill hit rate" = "#FFD166", "matched unconditional baseline" = "#7FB3FF"), name = NULL) +
  scale_y_continuous(labels = scales::percent_format(accuracy = 1)) +
  facet_wrap(~direction) +
  labs(title = "Gap fill: did the close reach the block price? Hit rate, 90% Wilson CI, matched baseline",
       x = "horizon", y = "hit rate",
       caption = "Hit = close at horizon crossed the block price. Baseline = unconditional chance the same-horizon SPY return crosses the same deviation.") + th

p <- p1 / p2
ggsave(file.path(out, "block_event_study.png"), p, bg = navy, width = 11, height = 7, dpi = 150)
cat("saved", file.path(out, "block_event_study.png"), "\n")
