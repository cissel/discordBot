# MLB Park Factors Research — 2024-2026

## Summary

**Best freely available source: Fangraphs HTML scrape** via `https://www.fangraphs.com/guts.aspx?type=pf&teamid=0&season={YEAR}`

---

## Source 1: pybaseball library

### Status
- **Installed**: YES (just installed: `pybaseball 2.2.7`)
- **Install command**: `/home/jhcv/discordBot/venv/bin/pip install pybaseball`

### Park Factor Function
- **`park_factors()` does NOT exist** in pybaseball 2.2.7
- Available park-related functions:
  - `pybaseball.park_codes()` — Returns Retrosheet Park IDs (metadata only, no offensive factors)
  - `pybaseball.parks()` — Returns Lahman Parks.csv (stadium metadata only — currently broken, `BadZipFile` error on Lahman download)

### Verdict
❌ **pybaseball has no park_factors() function.** Its park-related functions only return stadium metadata (park names, IDs), not offensive multipliers.

---

## Source 2: Fangraphs Park Factors (HTML Scrape) ✅ BEST SOURCE

### URLs
```
https://www.fangraphs.com/guts.aspx?type=pf&teamid=0&season=2024
https://www.fangraphs.com/guts.aspx?type=pf&teamid=0&season=2025
https://www.fangraphs.com/guts.aspx?type=pf&teamid=0&season=2026
```

### Granularity
**Per-team** (one row per MLB franchise per season, 30 rows total). Maps to home park, so effectively per-park.

### Columns Returned
| Column | Description |
|--------|-------------|
| `Season` | MLB season year |
| `Team` | Team name (e.g., "Angels", "Red Sox") |
| `Basic (5yr)` | 5-year weighted park factor for overall offense (100 = neutral) |
| `3yr` | 3-year weighted park factor |
| `1yr` | Current-season 1-year park factor |
| `1B` | Singles park factor |
| `2B` | Doubles park factor |
| `3B` | Triples park factor |
| `HR` | Home runs park factor |
| `SO` | Strikeouts park factor |
| `BB` | Walks park factor |
| `GB` | Ground balls park factor |
| `FB` | Fly balls park factor |
| `LD` | Line drives park factor |
| `IFFB` | Infield fly balls park factor |
| `FIP` | FIP-based overall park factor |

**Scale**: 100 = perfectly neutral. >100 = hitter-friendly, <100 = pitcher-friendly.

### Python Scraping Function
```python
import urllib.request, re, json

def fetch_fangraphs_park_factors(season: int) -> list[dict]:
    """
    Fetch Fangraphs park factors for a given MLB season.
    Returns a list of 30 dicts, one per team.
    """
    url = f"https://www.fangraphs.com/guts.aspx?type=pf&teamid=0&season={season}"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml'
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode('utf-8', errors='replace')

    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)

    def clean(s):
        return re.sub(r'<[^>]+>', '', s).strip()

    headers = []
    data_rows = []
    seen = set()

    for row in rows:
        cells_th = re.findall(r'<th[^>]*>(.*?)</th>', row, re.DOTALL | re.IGNORECASE)
        cells_td = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)

        if cells_th:
            h = [clean(c) for c in cells_th]
            if len(h) > 3 and not headers:
                headers = h
        elif cells_td and headers:
            r_clean = tuple(clean(c) for c in cells_td)
            if r_clean not in seen:
                seen.add(r_clean)
                data_rows.append(list(r_clean))

    return [dict(zip(headers, r)) for r in data_rows if r]

# Usage:
pf_2024 = fetch_fangraphs_park_factors(2024)
pf_2025 = fetch_fangraphs_park_factors(2025)
pf_2026 = fetch_fangraphs_park_factors(2026)
```

### Notable 2025 Extremes
| Team | Basic(5yr) | HR | Notes |
|------|-----------|-----|-------|
| Rockies | **113** | 107 | Most extreme hitter park (Coors Field) |
| Reds | **105** | **114** | Best HR park (Great American Ball Park) |
| Mariners | **94** | 96 | Most extreme pitcher park (T-Mobile Park) |
| Padres | **96** | 101 | Pitcher-friendly (Petco Park) |
| Athletics | **103** | 103 | New park in 2025 (Sutter Health Park) |

### 2024 vs 2025 Key Differences (1yr column changes)
- **Rays**: 100 → 102 (moved to Steinbrenner Field temporarily)
- **Athletics**: 100 → 105 (moved from Oakland Coliseum to Sacramento)
- **Rockies**: 108 → 119 (1yr spiked, Coors still extreme)
- **Guardians**: 105 → 100 (1yr normalized)

### Notes on 2026
- The `1yr` column for 2026 is currently identical to 2025 (season not yet complete as of mid-2025)
- The `Basic (5yr)` and `3yr` columns roll in prior seasons; use those for stable features
- Fangraphs updates the `1yr` factor in-season as games accumulate

---

## Source 3: Baseball Reference Park Factors

### URL Pattern
```
https://www.baseball-reference.com/leagues/majors/2024-park-factors.shtml
```

### Status
Not scraped (Fangraphs is cleaner to parse and more feature-rich). BBRef uses heavy JS-rendered tables and aggressive bot-blocking. Fangraphs is preferred.

---

## Source 4: statsapi.mlb.com

### Endpoint Tested
```
GET https://statsapi.mlb.com/api/v1/venues?sportId=1&hydrate=location,fieldInfo
```

### What It Returns
Physical stadium dimensions only — **NO park factors**:
- `id`, `name`, `active`
- `location` (city, state, coordinates)
- `fieldInfo`: `leftLine`, `leftCenter`, `center`, `rightCenter`, `rightLine`, `roof`, `turfType`, `capacity`

### Verdict
❌ **statsapi.mlb.com has no park factor statistics.** It provides physical venue dimensions as a proxy (useful for derived features) but does not compute offensive multipliers.

Sample:
```json
{"id": 3, "name": "Fenway Park", "fieldInfo": {"leftLine": 310, "center": 420, "rightLine": 302, "capacity": 37755}}
```

---

## Recommendation for Fantasy Baseball Model

### Feature Strategy
1. **Primary**: Use `Basic (5yr)` from Fangraphs — most stable, multi-year smoothed
2. **Secondary**: Use `1yr` for current-season recency
3. **Split features**: Use `HR`, `1B`, `2B`, `3B` individually for batted-ball-type models
4. **FIP column**: Use for pitcher-specific adjustments

### Team Name Mapping (Fangraphs → Standard)
Fangraphs uses informal names. Map to team codes if needed:
```python
FG_TEAM_MAP = {
    'Angels': 'LAA', 'Orioles': 'BAL', 'Red Sox': 'BOS', 'White Sox': 'CWS',
    'Guardians': 'CLE', 'Tigers': 'DET', 'Royals': 'KC', 'Twins': 'MIN',
    'Yankees': 'NYY', 'Athletics': 'OAK', 'Mariners': 'SEA', 'Rays': 'TB',
    'Rangers': 'TEX', 'Blue Jays': 'TOR', 'Diamondbacks': 'ARI', 'Braves': 'ATL',
    'Cubs': 'CHC', 'Reds': 'CIN', 'Rockies': 'COL', 'Marlins': 'MIA',
    'Astros': 'HOU', 'Dodgers': 'LAD', 'Brewers': 'MIL', 'Nationals': 'WSH',
    'Mets': 'NYM', 'Phillies': 'PHI', 'Pirates': 'PIT', 'Cardinals': 'STL',
    'Padres': 'SD', 'Giants': 'SF'
}
```

---

## Data Files
- `/tmp/fg_park_factors_all.json` — Combined JSON with 2024+2025+2026 data (30 teams × 3 seasons)

