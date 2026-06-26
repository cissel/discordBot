# repoTreemap.R
# Current snapshot treemap of discordBot repo by file size (LOC)
# Box size = LOC, grouped by directory, labeled by filename

library(ggplot2)
library(dplyr)
library(treemapify)
library(scales)

##### Theme #####

myTheme <- theme(
  plot.background  = element_rect(fill = "#02233F", color = NA),
  panel.background = element_rect(fill = "#02233F", color = NA),
  plot.title       = element_text(color = "white", hjust = 0.5, size = 14, face = "bold"),
  plot.subtitle    = element_text(color = "#8BAAC8", hjust = 0.5, size = 10),
  plot.caption     = element_text(color = "#8BAAC8", size = 8),
  legend.background = element_rect(fill = "#02233F", color = NA),
  legend.key        = element_rect(fill = "#02233F", color = NA),
  legend.text       = element_text(color = "white", size = 9),
  legend.title      = element_text(color = "#8BAAC8", size = 10),
  plot.margin       = margin(12, 16, 8, 16)
)

##### Load Data - use latest snapshot from repo_filesize.csv #####

csv_path <- path.expand("~/discordBot/outputs/server/repo_filesize.csv")
if (!file.exists(csv_path)) stop("repo_filesize.csv not found - run repoFileSize.py first")

df <- read.csv(csv_path, stringsAsFactors = FALSE) %>%
  mutate(commit_date = as.Date(commit_date))

latest_date <- max(df$commit_date)

df_now <- df %>%
  filter(commit_date == latest_date) %>%
  filter(loc > 0) %>%
  mutate(
    label      = basename(file),
    # Shorten label for small tiles: truncate at 18 chars
    label_short = ifelse(nchar(label) > 18, paste0(substr(label, 1, 16), ".."), label)
  ) %>%
  arrange(desc(loc))

##### Directory color palette #####

dir_colors <- c(
  "python" = "#1565C0",  # deep blue
  "r"      = "#2E7D32",  # deep green
  "root"   = "#F57F17",  # amber
  "other"  = "#4A148C"   # purple
)

df_now <- df_now %>%
  mutate(dir_color = case_when(
    directory == "python" ~ "python/",
    directory == "r"      ~ "r/",
    directory == "root"   ~ "root",
    TRUE                  ~ "other"
  ))

##### Summary stats #####

total_files  <- nrow(df_now)
total_loc    <- sum(df_now$loc)
as_of        <- format(latest_date, "%b %d, %Y")

##### Plot #####

p <- ggplot(df_now, aes(
    area  = loc,
    fill  = dir_color,
    label = label_short,
    subgroup = dir_color
  )) +
  geom_treemap(color = "#02233F", size = 1.5) +
  geom_treemap_subgroup_border(color = "#8BAAC8", size = 2) +
  geom_treemap_subgroup_text(
    place    = "topleft",
    color    = "white",
    alpha    = 0.35,
    fontface = "bold",
    size     = 14,
    grow     = FALSE
  ) +
  geom_treemap_text(
    color    = "white",
    place    = "centre",
    size     = 8,
    min.size = 4,
    grow     = FALSE,
    reflow   = TRUE
  ) +
  scale_fill_manual(
    values = c(
      "python/" = "#1565C0",
      "r/"      = "#2E7D32",
      "root"    = "#F57F17",
      "other"   = "#4A148C"
    ),
    name = "Directory"
  ) +
  labs(
    title    = "discordBot - Repo File Size Treemap",
    subtitle = paste0("As of ", as_of, "  |  ", total_files, " files  |  ",
                      comma(total_loc), " total LOC  |  box area proportional to lines of code"),
    caption  = "Source: git ls-tree | JHCV"
  ) +
  myTheme +
  theme(
    legend.position = "right"
  )

##### Save #####

out_path <- path.expand("~/discordBot/outputs/server/repo_treemap.png")
png(out_path, width = 1400, height = 850, res = 120, bg = "#02233F")
print(p)
dev.off()

cat("ok:", out_path, "\n")
