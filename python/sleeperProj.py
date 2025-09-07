import json
import requests
import pandas as pd

GRAPHQL_URL = "https://sleeper.com/graphql"

PAYLOAD = {
    "operationName": "get_player_score_and_projections_batch",
    "variables": {},
    "query": "query get_player_score_and_projections_batch {\n        \n        nfl__regular__2025__1__stat: stats_for_players_in_week(sport: \"nfl\",season: \"2025\",category: \"stat\",season_type: \"regular\",week: 1,player_ids: [\"6797\",\"8150\",\"8138\",\"7547\",\"9997\",\"1466\",\"5045\",\"6650\",\"SF\",\"4984\",\"6790\",\"12507\",\"11631\",\"8146\",\"10859\",\"11620\",\"11786\",\"BAL\",\"7523\",\"9224\",\"5892\",\"7564\",\"12530\",\"8130\",\"12501\",\"4195\",\"JAX\",\"4892\",\"6813\",\"7594\",\"11632\",\"8148\",\"5844\",\"5872\",\"4666\",\"MIN\",\"11563\",\"12527\",\"8155\",\"7569\",\"3321\",\"5022\",\"11624\",\"8259\",\"HOU\",\"4881\",\"4035\",\"4199\",\"6794\",\"5859\",\"7553\",\"11628\",\"1945\",\"DET\",\"11566\",\"9509\",\"11584\",\"5927\",\"7525\",\"4066\",\"4983\",\"11539\",\"PHI\",\"4046\",\"4866\",\"8151\",\"11635\",\"6801\",\"5012\",\"6783\",\"7839\",\"WAS\",\"8183\",\"3198\",\"9226\",\"9493\",\"12526\",\"4217\",\"8137\",\"11533\",\"PIT\",\"3294\",\"4034\",\"8205\",\"8112\",\"9488\",\"12518\",\"12529\",\"3678\",\"DEN\",\"6904\",\"9221\",\"4137\",\"2216\",\"12514\",\"11604\",\"4981\",\"12711\",\"GB\",\"6770\",\"5850\",\"5967\",\"6786\",\"5846\",\"4033\",\"7526\",\"4227\",\"BUF\"]){\n          game_id\nopponent\nplayer_id\nstats\nteam\nweek\nseason\n        }\n      \n\n        nfl__regular__2025__1__proj: stats_for_players_in_week(sport: \"nfl\",season: \"2025\",category: \"proj\",season_type: \"regular\",week: 1,player_ids: [\"6797\",\"8150\",\"8138\",\"7547\",\"9997\",\"1466\",\"5045\",\"6650\",\"SF\",\"4984\",\"6790\",\"12507\",\"11631\",\"8146\",\"10859\",\"11620\",\"11786\",\"BAL\",\"7523\",\"9224\",\"5892\",\"7564\",\"12530\",\"8130\",\"12501\",\"4195\",\"JAX\",\"4892\",\"6813\",\"7594\",\"11632\",\"8148\",\"5844\",\"5872\",\"4666\",\"MIN\",\"11563\",\"12527\",\"8155\",\"7569\",\"3321\",\"5022\",\"11624\",\"8259\",\"HOU\",\"4881\",\"4035\",\"4199\",\"6794\",\"5859\",\"7553\",\"11628\",\"1945\",\"DET\",\"11566\",\"9509\",\"11584\",\"5927\",\"7525\",\"4066\",\"4983\",\"11539\",\"PHI\",\"4046\",\"4866\",\"8151\",\"11635\",\"6801\",\"5012\",\"6783\",\"7839\",\"WAS\",\"8183\",\"3198\",\"9226\",\"9493\",\"12526\",\"4217\",\"8137\",\"11533\",\"PIT\",\"3294\",\"4034\",\"8205\",\"8112\",\"9488\",\"12518\",\"12529\",\"3678\",\"DEN\",\"6904\",\"9221\",\"4137\",\"2216\",\"12514\",\"11604\",\"4981\",\"12711\",\"GB\",\"6770\",\"5850\",\"5967\",\"6786\",\"5846\",\"4033\",\"7526\",\"4227\",\"BUF\"]){\n          game_id\nopponent\nplayer_id\nstats\nteam\nweek\nseason\n        }\n      \n      }"
}

HEADERS = {"content-type": "application/json"}

def find_player_list_with_stats(obj):
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        if isinstance(obj[0].get("stats"), dict):
            return obj
    if isinstance(obj, dict):
        for v in obj.values():
            found = find_player_list_with_stats(v)
            if found is not None:
                return found
    return None

def extract_row(rec):
    s = rec.get("stats", {}) or {}
    return {
        "player_id": rec.get("player_id"),
        "team": rec.get("team"),
        "opponent": rec.get("opponent"),
        "season": rec.get("season"),
        "week": rec.get("week"),
        "pts_ppr": s.get("pts_ppr"),
        "adp_dd_ppr": s.get("adp_dd_ppr"),
    }

resp = requests.post(GRAPHQL_URL, json=PAYLOAD, headers=HEADERS, timeout=30)
resp.raise_for_status()
data = resp.json()

if "errors" in data and data["errors"]:
    print(json.dumps(data["errors"], indent=2))
    raise SystemExit(1)

records = find_player_list_with_stats(data["data"])
if records is None:
    print("Could not find records")
    raise SystemExit(1)

rows = [extract_row(r) for r in records]
df = pd.DataFrame(rows)

# keep only ids + stats (no names)
cols = ["player_id","team","opponent","season","week","pts_ppr","adp_dd_ppr"]
df = df[cols]

df.to_csv("outputs/sports/nfl/sleeper_proj_pts.csv", index=False)
print(f"Saved {len(df)} rows to outputs/nfl/sleeper_proj_pts.csv")
print(df.head(10).to_string(index=False))
