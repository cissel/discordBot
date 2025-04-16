library(gifski)

# Create a temp directory to store images
dir.create("/Users/jamescissel/discordBot/outputs/weather/winds", showWarnings = FALSE)
setwd("/Users/jamescissel/discordBot/outputs/weather/winds")

# Generate image ID strings
img_seq <- seq(0, 144, 3)
img_id <- sprintf("%03d", img_seq)

# Construct URLs
base_url <- "https://polar.ncep.noaa.gov/nwps/images/rtimages/jax/nwps/CG1/swan_wind_hr"
urls <- paste0(base_url, img_id, ".png")

# Download images
for (i in seq_along(urls)) {
  download.file(urls[i], destfile = paste0("img", img_id[i], ".png"), method = "libcurl", quiet = TRUE)
}

# Get list of downloaded PNGs
png_files <- list.files(".", pattern = "img\\d+\\.png$", full.names = TRUE)

# Create gif
output_gif <- "/Users/jamescissel/discordBot/outputs/weather/wind_animation.gif"
gifski(png_files, gif_file = output_gif, width = 913 / 1.15, height = 656 / 1.65, delay = 0.25)

# Reset working directory
setwd("..")

cat("GIF saved to:", output_gif, "\n")
