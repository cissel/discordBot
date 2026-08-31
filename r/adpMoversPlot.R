#!/usr/bin/env Rscript
# adpMoversPlot.R
# Sleeper ADP movers (gaining/losing steam) heading into the season, navy theme.
# Reads adp_movers_sleeper.csv (built by python/buildAdpMovers.py).
suppressMessages({
  library(readr); library(dplyr); library(ggplot2); library(forcats)
})

OUT_DIR <- path.expand("~/discordBot/outputs/sports/nfl/fantasy")
OUT_PNG <- file.path(OUT_DIR, "adp_movers_sleeper.png")

navy <- theme(
  plot.background   = element_rect(fill = "#02233F", color = NA),
  panel.background  = element_rect(fill = "#02233F", color = NA),
  panel.grid.major  = element_line(color = "#274066", linewidth = 0.4),
  panel.grid.minor  = element_blank(),
  axis.text         = element_text(color = "white", size = 8),
  axis.title        = element_text(color = "white", size = 9),
  plot.title        = element_text(color = "white", size = 13, face = "bold", hjust = 0),
  plot.subtitle     = element_text(color = "white", size = 9,  hjust = 0),
  plot.caption      = element_text(color = "white", size = 7),
  strip.background  = element_rect(fill = "#0a2840"),
  strip.text        = element_text(color = "white", size = 9, face = "bold"),
  legend.background = element_rect(fill = "#02233F"),
  legend.text       = element_text(color = "white", size = 8),
  legend.title      = element_text(color = "white", size = 8),
  legend.key        = element_rect(fill = "#02233F"),
  plot.margin       = margin(10, 16, 10, 12)
)

POS_COLORS <- c(QB = "#7B4CE0", RB = "#2E9E4E", WR = "#2E7BE0", TE = "#E0A02E")

m <- read_csv(file.path(OUT_DIR, "adp_movers_sleeper.csv"), show_col_types = FALSE)

# restrict to draftable-relevant range (was or is inside top 200) and drop the
# "fell off the ranked pool" sentinel (ADP 999) - those are handled separately
draftable <- m %>%
  filter(adp_first <= 200 | adp_last <= 200) %>%
  filter(adp_last < 999)

gainers <- draftable %>% arrange(adp_delta) %>% head(15) %>% mutate(direction = "Gaining Steam")
losers  <- draftable %>% arrange(desc(adp_delta)) %>% head(15) %>% mutate(direction = "Losing Steam")
combined <- bind_rows(gainers, losers) %>%
  mutate(label = paste0(full_name, " (", position, ")"))

date_range <- paste0(min(m$first_snapshot_date), " to ", max(m$last_snapshot_date))

p <- ggplot(combined, aes(x = fct_reorder(label, -adp_delta), y = -adp_delta, fill = position)) +
  geom_hline(yintercept = 0, color = "white", linewidth = 0.5) +
  geom_col(width = 0.7) +
  geom_text(aes(label = paste0(round(adp_first), " -> ", round(adp_last)),
                hjust = ifelse(-adp_delta >= 0, -0.05, 1.05)),
            color = "white", size = 2.7) +
  coord_flip(clip = "off") +
  facet_wrap(~direction, scales = "free_y") +
  scale_fill_manual(values = POS_COLORS) +
  scale_y_continuous(expand = expansion(mult = 0.25)) +
  labs(
    title = "2026 Draft: ADP Movers (Sleeper)",
    subtitle = paste0("Draftable range (ADP <= 200) | Snapshot window: ", date_range,
                       " | Positive bar = climbing draft boards, negative = falling"),
    x = NULL, y = "ADP Improvement (positive = drafted earlier now)", fill = "Position",
    caption = "Source: Sleeper GraphQL ADP, daily snapshots via fetchNflAdpAndRoster.py | JHCV"
  ) +
  navy

ggsave(OUT_PNG, p, width = 13, height = 8.5, dpi = 150, bg = "#02233F")
cat("Wrote:", OUT_PNG, "\n")
