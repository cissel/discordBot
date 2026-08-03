"""
worldSilliesRisk.py
Computes mean and std dev of daily points for each team from szn_daily.csv
for the risk plot (mean vs volatility scatter).
"""
import csv
import os
from statistics import mean, stdev

DAILY_CSV = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/szn_daily.csv")
OUTPUT_CSV = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/risk.csv")

def fetch_risk_data():
    """Read szn_daily.csv and compute mean/stdev of daily_pts per team."""
    
    team_data = {}  # team_id -> {'name': str, 'daily_pts': [float]}
    
    with open(DAILY_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            team_id = row["team_id"].strip()
            team_name = row["team_name"].strip()
            try:
                daily_pts = float(row["daily_pts"])
            except ValueError:
                daily_pts = 0.0
            
            if team_id not in team_data:
                team_data[team_id] = {
                    "name": team_name,
                    "daily_pts": []
                }
            
            team_data[team_id]["daily_pts"].append(daily_pts)
    
    rows = []
    for team_id, data in team_data.items():
        daily_pts = data["daily_pts"]
        if len(daily_pts) > 0:
            mean_pts = mean(daily_pts)
            std_pts = stdev(daily_pts) if len(daily_pts) > 1 else 0.0
            
            rows.append({
                "team_name": data["name"],
                "mean_daily": round(mean_pts, 2),
                "std_daily": round(std_pts, 2),
            })
    
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["team_name", "mean_daily", "std_daily"])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"Risk data written to {OUTPUT_CSV}")

if __name__ == "__main__":
    fetch_risk_data()
