# pitcherVhitte.R by JHCV
#!/usr/bin/env Rscript

# ── pitcher_vs_hitters.R ──────────────────────────────────────────────────────
# Pulls Statcast pitch-by-pitch in 2-week chunks across last 5 seasons,
# filters to PA-ending events only, aggregates batter matchup stats.
# Usage: Rscript pitcher_vs_hitters.R "Sandy Alcantara" "/abs/path/to/outputs"
# ─────────────────────────────────────────────────────────────────────────────

suppressPackageStartupMessages({
  library(baseballr)
  library(dplyr)
  library(readr)
  library(jsonlite)
  library(httr)
})

# ── 0. Args ───────────────────────────────────────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
if (length(args) == 0) stop("Usage: Rscript pitcher_vs_hitters.R \"First Last\" [/path/to/outputs]")

pitcher_name <- trimws(args[1])
cat(sprintf("[INFO] Pitcher: %s\n", pitcher_name))

if (length(args) >= 2) {
  out_dir <- file.path(trimws(args[2]), "sports", "mlb")
} else {
  out_dir <- file.path(getwd(), "outputs", "sports", "mlb")
}
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
out_file <- file.path(out_dir, "top10_vs_pitcher.csv")
cat(sprintf("[INFO] Output path: %s\n", out_file))

# ── 1. Resolve pitcher MLBAM ID ───────────────────────────────────────────────
name_parts <- strsplit(trimws(pitcher_name), "\\s+")[[1]]
first_name <- paste(name_parts[-length(name_parts)], collapse = " ")
last_name  <- name_parts[length(name_parts)]
cat(sprintf("[INFO] Looking up: first='%s' last='%s'\n", first_name, last_name))

pitcher_id <- NULL

player_info <- tryCatch(
  playerid_lookup(last_name = last_name, first_name = first_name),
  error = function(e) { cat(sprintf("[WARN] playerid_lookup error: %s\n", e$message)); data.frame() }
)
cat(sprintf("[INFO] playerid_lookup returned %d rows\n", nrow(player_info)))

if (nrow(player_info) > 0 && any(!is.na(player_info$mlbam_id))) {
  row        <- player_info %>% filter(!is.na(mlbam_id)) %>% slice(1)
  pitcher_id <- row$mlbam_id
  cat(sprintf("[INFO] Found via Chadwick: %s %s (ID: %s)\n",
              row$first_name, row$last_name, pitcher_id))
}

if (is.null(pitcher_id) || is.na(pitcher_id)) {
  cat("[INFO] Falling back to Baseball Savant search...\n")
  savant_result <- tryCatch({
    resp <- GET(sprintf("https://baseballsavant.mlb.com/player/search-all?search=%s",
                        utils::URLencode(pitcher_name, reserved = TRUE)))
    fromJSON(content(resp, as = "text", encoding = "UTF-8"))
  }, error = function(e) { cat(sprintf("[WARN] Savant search failed: %s\n", e$message)); NULL })
  
  if (!is.null(savant_result) && length(savant_result) > 0) {
    df_s <- as.data.frame(savant_result)
    if ("id" %in% names(df_s) && nrow(df_s) > 0) {
      pitcher_id <- df_s$id[1]
      cat(sprintf("[INFO] Using Savant ID: %s\n", pitcher_id))
    }
  }
}

if (is.null(pitcher_id) || is.na(pitcher_id)) {
  stop(sprintf("Could not resolve MLBAM ID for '%s'.", pitcher_name))
}

# ── 2. Build 2-week date chunks across last 5 seasons ────────────────────────
# Chunking by 2 weeks keeps each API call small and avoids the column-count
# mismatch that kills full-season pulls on older data.
current_year <- as.integer(format(Sys.Date(), "%Y"))
today        <- Sys.Date()

make_chunks <- function(yr) {
  season_start <- as.Date(sprintf("%d-03-20", yr))
  season_end   <- if (yr == current_year) today else as.Date(sprintf("%d-10-31", yr))
  if (season_start > today) return(list())  # future season, skip
  
  starts <- seq(season_start, season_end, by = "14 days")
  ends   <- pmin(starts + 13, season_end)
  mapply(function(s, e) list(start = format(s, "%Y-%m-%d"),
                             end   = format(e, "%Y-%m-%d")),
         starts, ends, SIMPLIFY = FALSE)
}

all_chunks <- do.call(c, lapply(seq(current_year - 4, current_year), make_chunks))
cat(sprintf("[INFO] Total 2-week chunks to fetch: %d\n", length(all_chunks)))

# ── 3. PA-ending events we care about ────────────────────────────────────────
pa_events <- c(
  "single", "double", "triple", "home_run",
  "field_out", "strikeout", "strikeout_double_play",
  "walk", "intent_walk", "hit_by_pitch",
  "sac_fly", "sac_bunt", "sac_fly_double_play",
  "force_out", "grounded_into_double_play",
  "fielders_choice", "fielders_choice_out",
  "double_play", "triple_play",
  "field_error", "catcher_interf"
)

# Only keep the columns we actually need — avoids the schema mismatch entirely
keep_cols <- c("batter", "events", "matchup.batter.fullName",
               "player_name", "batter_name")

# ── 4. Fetch and filter each chunk ───────────────────────────────────────────
fetch_chunk <- function(chunk) {
  result <- tryCatch(
    suppressWarnings(statcast_search(
      start_date  = chunk$start,
      end_date    = chunk$end,
      pitcherid   = pitcher_id,
      player_type = "pitcher"
    )),
    error = function(e) NULL
  )
  if (is.null(result) || nrow(result) == 0) return(NULL)
  
  result <- as.data.frame(result)
  
  # Filter to PA-ending pitches immediately — shrinks memory dramatically
  result <- result[!is.na(result$events) & result$events %in% pa_events, ]
  if (nrow(result) == 0) return(NULL)
  
  # Only keep columns we need (whichever exist in this chunk)
  cols <- intersect(keep_cols, names(result))
  result[, cols, drop = FALSE]
}

cat("[INFO] Fetching chunks (each is fast — 2 weeks at a time)...\n")
pa_rows <- vector("list", length(all_chunks))
for (i in seq_along(all_chunks)) {
  chunk  <- all_chunks[[i]]
  result <- fetch_chunk(chunk)
  pa_rows[[i]] <- result
  if (!is.null(result)) {
    cat(sprintf("[INFO] %s → %s : %d PA events\n",
                chunk$start, chunk$end, nrow(result)))
  }
}

pa_data <- bind_rows(Filter(Negate(is.null), pa_rows))
cat(sprintf("[INFO] Total PA events across all chunks: %d\n", nrow(pa_data)))

if (nrow(pa_data) == 0) {
  stop(sprintf("No plate appearance data found for '%s'.", pitcher_name))
}

# Resolve batter display name
bname_col <- intersect(c("matchup.batter.fullName", "batter_name", "player_name"), names(pa_data))
pa_data <- pa_data %>%
  mutate(
    batter_name = if (length(bname_col) > 0) .data[[bname_col[1]]] else as.character(batter),
    batter_name = ifelse(is.na(batter_name) | batter_name == "", as.character(batter), batter_name)
  )

# ── 5. Aggregate stats per batter ─────────────────────────────────────────────
stats <- pa_data %>%
  group_by(batter, batter_name) %>%
  summarise(
    PA   = n(),
    AB   = sum(!(events %in% c("walk","intent_walk","hit_by_pitch",
                               "sac_fly","sac_bunt","sac_fly_double_play","catcher_interf"))),
    H    = sum(events %in% c("single","double","triple","home_run")),
    `2B` = sum(events == "double"),
    `3B` = sum(events == "triple"),
    HR   = sum(events == "home_run"),
    BB   = sum(events %in% c("walk","intent_walk")),
    HBP  = sum(events == "hit_by_pitch"),
    SF   = sum(events %in% c("sac_fly","sac_fly_double_play")),
    TB   = sum((events=="single")*1 + (events=="double")*2 +
                 (events=="triple")*3 + (events=="home_run")*4),
    .groups = "drop"
  ) %>%
  mutate(
    AVG = ifelse(AB > 0, round(H/AB, 3), NA_real_),
    OBP = ifelse((AB+BB+HBP+SF) > 0, round((H+BB+HBP)/(AB+BB+HBP+SF), 3), NA_real_),
    SLG = ifelse(AB > 0, round(TB/AB, 3), NA_real_),
    OPS = round(coalesce(OBP, 0) + coalesce(SLG, 0), 3)
  ) %>%
  filter(PA >= 5) %>%
  arrange(desc(OPS), desc(AVG)) %>%
  head(10) %>%
  select(Batter = batter_name, PA, AB, H, `2B`, `3B`, HR, BB, HBP, AVG, OBP, SLG, OPS)

cat(sprintf("[INFO] Batters qualifying (5+ PA, last 5 seasons): %d\n", nrow(stats)))
print(stats)

if (nrow(stats) == 0) stop("No batters met the minimum 5 PA threshold.")

write_csv(stats, out_file)
cat(sprintf("[INFO] Written: %s\n", out_file))
cat(sprintf("[DONE] %s\n", pitcher_name))
