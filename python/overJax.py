import requests
import pandas as pd

CSV_PATH = "/Users/jamescissel/discordBot/outputs/aerospace/adsb250nm.csv"

# API endpoint
url = "https://api.adsb.lol/v2/point/30.270962/-81.385741/250"

# Headers
headers = {"accept": "application/json"}

# Request
response = requests.get(url, headers=headers)

# Handle response
if response.status_code == 200:
    data = response.json()
    
    if "ac" in data:
        df = pd.DataFrame(data["ac"])
        df.to_csv(CSV_PATH, index=False)
        print("✅ Aircraft data saved to outputs/aerospace/adsb250nm.csv")
    else:
        print("⚠️ 'ac' key not found in response.")
else:
    print(f"❌ Request failed with status code {response.status_code}")
