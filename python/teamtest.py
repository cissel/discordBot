import requests, json
# Test with Jonah Heim (665489)
r = requests.get("https://statsapi.mlb.com/api/v1/people/665489?hydrate=currentTeam", timeout=10)
print(json.dumps(r.json()["people"][0], indent=2))