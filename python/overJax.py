import requests
import pandas as pd
import folium

CSV_PATH = "/Users/jamescissel/discordBot/outputs/aerospace/adsb250nm.csv"
MAP_PATH = "/Users/jamescissel/discordBot/outputs/aerospace/adsb250nm_map.html"

# API endpoint
url = "https://api.adsb.lol/v2/point/30.270962/-81.385741/250"

# Headers
headers = {"accept": "application/json"}

# Request
response = requests.get(url, headers=headers)

if response.status_code == 200:
    data = response.json()

    if "ac" in data:
        df = pd.DataFrame(data["ac"])
        df.to_csv(CSV_PATH, index=False)
        print("✅ Aircraft data saved to CSV")

        # Initialize map centered around your query point
        m = folium.Map(location=[30.270962, -81.385741], zoom_start=7)

        # Add aircraft markers
        for _, row in df.iterrows():
            if pd.notna(row.get("lat")) and pd.notna(row.get("lon")):
                folium.Marker(
                    location=[row["lat"], row["lon"]],
                    popup=f"Flight: {row.get('flight', 'N/A')}<br>Alt: {row.get('alt_geom', 'N/A')} ft",
                    icon=folium.Icon(color="blue", icon="plane", prefix='fa')
                ).add_to(m)

        m.save(MAP_PATH)
        print(f"🗺️  Map saved to {MAP_PATH}")

    else:
        print("⚠️ 'ac' key not found in response.")
else:
    print(f"❌ Request failed with status code {response.status_code}")

