# repoFileSize.R
# Per-file LOC timeseries - stacked area chart showing how each file grew over time
# Top N files by final LOC, grouped by directory color

library(ggplot2)
library(dplyr)
library(tidyr)
library(scales)

##### Theme #####

myTheme <- theme(
  plot.background   = element_rect(fill = "#02233F", color = NA),
  panel.background  = element_rect(fill = "#02233F", color = NA),
  panel.grid.major  = element_line(color = "#274066"),
  panel.grid.minor  = element_blank(),
  axis.ticks        = element_line(color = "#274066"),
  axis.text         = element_text(color = "white", size = 9),
  axis.title        = element_text(color = "white", size = 10),
  plot.title        = element_text(color = "white", hjust = 0.5, size = 14, face = "bold"),
  plot.subtitle     = element_text(color = "#8BAAC8", hjust = 0.5, size = 10),
  plot.caption      = element_text(color = "#8BAAC8", size = 8),
  legend.background = element_rect(fill = "#02233F", color = NA),
  legend.key        = element_rect(fill = "#02233F", color = NA),
  legend.text       = element_text(color = "white", size = 7.5),
  legend.title      = element_text(color = "#8BAAC8", size = 8),
  legend.position   = "right",
  plot.margin       = margin(12, 8, 8, 16)
)

##### Load Data #####

csv_path <- path.expand("~/discordBot/outputs/server/repo_filesize.csv")
if (!file.exists(csv_path)) stop("repo_filesize.csv not found - run repoFileSize.py first")

df <- read.csv(csv_path, stringsAsFactors = FALSE) %>%
  mutate(commit_date = as.Date(commit_date))

##### Pick top N files by final (most recent) LOC #####

TOP_N <- 20

latest_date <- max(df$commit_date)

top_files <- df %>%
  filter(commit_date == latest_date) %>%
  arrange(desc(loc)) %>%
  slice_head(n = TOP_N) %>%
  pull(file)

# Also include any file that was top-N at any point? No - keep it clean: top N at HEAD.
df_top <- df %>% filter(file %in% top_files)

# Shorten file labels: strip leading dir/ for readability
df_top <- df_top %>%
  mutate(label = basename(file))

# Ensure all files have a value at every sampled date (fill forward with last known LOC)
all_dates <- sort(unique(df_top$commit_date))
all_files <- unique(df_top$label)

df_full <- df_top %>%
  select(commit_date, label, loc, directory) %>%
  complete(commit_date = all_dates, label = all_files) %>%
  arrange(label, commit_date) %>%
  group_by(label) %>%
  fill(loc, .direction = "down") %>%
  fill(directory, .direction = "down") %>%
  mutate(loc = replace_na(loc, 0)) %>%
  ungroup()

# Ordering for stacked area: largest files on top
file_order <- df_full %>%
  filter(commit_date == max(commit_date)) %>%
  arrange(loc) %>%
  pull(label)

df_full$label <- factor(df_full$label, levels = file_order)

##### Color palette - distinct colors per file, grouped by directory shading #####

dirs <- unique(df_full$directory)

# Get file-to-directory mapping
file_dir <- df_full %>%
  select(label, directory) %>%
  distinct()

# Assign colors: python files in blue family, r files in green family, root in gold
python_files <- file_dir %>% filter(directory == "python") %>% pull(label)
r_files      <- file_dir %>% filter(directory == "r")      %>% pull(label)
root_files   <- file_dir %>% filter(directory == "root")   %>% pull(label)
other_files  <- file_dir %>% filter(!directory %in% c("python","r","root")) %>% pull(label)

# Blue palette for python (10 shades)
blue_pal  <- colorRampPalette(c("#00BFFF","#1565C0","#7EC8E3","#003580","#5BA4CF",
                                  "#2196F3","#0D47A1","#64B5F6","#01579B","#4FC3F7"))(max(1, length(python_files)))
# Green palette for R
green_pal <- colorRampPalette(c("#4CAF50","#1B5E20","#76FF03","#2E7D32","#A5D6A7",
                                  "#66BB6A","#00E676","#388E3C","#C8E6C9","#81C784"))(max(1, length(r_files)))
# Gold/amber for root
gold_pal  <- colorRampPalette(c("#FFD700","#FF8F00","#FFF176","#F57F17"))(max(1, length(root_files)))
# Orange for other
other_pal <- colorRampPalette(c("#FF7043","#BF360C"))(max(1, length(other_files)))

file_colors <- c(
  setNames(blue_pal,  python_files),
  setNames(green_pal, r_files),
  setNames(gold_pal,  root_files),
  setNames(other_pal, other_files)
)

##### Summary stats #####

total_files   <- length(unique(df$file))
snapshots     <- length(unique(df$commit_date))
date_range    <- paste0(format(min(df$commit_date), "%b %Y"), " - ", format(latest_date, "%b %Y"))
total_loc_now <- df %>% filter(commit_date == latest_date) %>% pull(loc) %>% sum()

##### Plot #####

p <- ggplot(df_full, aes(x = commit_date, y = loc, fill = label)) +
  geom_area(position = "stack", alpha = 0.85) +
  scale_fill_manual(
    values = file_colors,
    name   = "File",
    guide  = guide_legend(ncol = 1, keyheight = 0.7, keywidth = 0.7)
  ) +
  scale_x_date(date_breaks = "2 months", date_labels = "%b '%y", expand = c(0.01, 0)) +
  scale_y_continuous(labels = comma_format(), expand = c(0, 0)) +
  labs(
    title    = "discordBot - File Size Evolution (Top 20 by LOC)",
    subtitle = paste0(date_range, "  |  ", total_files, " total files  |  ",
                      comma(total_loc_now), " LOC at HEAD  |  ", snapshots, " snapshots"),
    x        = NULL,
    y        = "Lines of Code",
    caption  = "Source: git log | JHCV"
  ) +
  myTheme +
  # Dir legend annotation
  annotate("text", x = min(df_full$commit_date), y = Inf,
           label = "blue = python  green = R  gold = root",
           color = "#8BAAC8", size = 2.8, hjust = 0, vjust = 1.5)

##### Save #####

out_path <- path.expand("~/discordBot/outputs/server/repo_filesize.png")
png(out_path, width = 1400, height = 750, res = 120, bg = "#02233F")
print(p)
dev.off()

cat("ok:", out_path, "\n")
