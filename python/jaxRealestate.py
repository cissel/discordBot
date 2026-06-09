#!/usr/bin/env python3
"""
jaxRealestate.py
Fetches recent single-family home sales from Duval County Property Appraiser.
Strategy:
  1. POST to Sales/Results.aspx to get list of qualified improved sales in date range
  2. Trigger Export to plain text to get all RE numbers + addresses + sale dates
  3. Scrape detail page for each RE to get sale price, sqft, property use
  4. Filter to single family (property use 0100), write CSV

Usage: python jaxRealestate.py [days=90] [max_properties=500]
Output: outputs/jax/realestate_sales.csv
"""

import os, sys, csv, re, time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

SEARCH_URL  = "https://paopropertysearch.coj.net/Sales/Search.aspx"
RESULTS_URL = "https://paopropertysearch.coj.net/Sales/Results.aspx"
DETAIL_URL  = "https://paopropertysearch.coj.net/Basic/Detail.aspx"
OUT_PATH    = os.path.expanduser("~/discordBot/outputs/jax/realestate_sales.csv")
HEADERS     = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://paopropertysearch.coj.net/",
}

# Core Duval County zip codes (excludes very rural outliers)
CORE_ZIPS = {
    "32202","32204","32205","32206","32207","32208","32209","32210",
    "32211","32212","32214","32216","32217","32218","32219","32220",
    "32221","32222","32223","32224","32225","32226","32227","32228",
    "32233","32234","32244","32246","32250","32254","32256","32257",
    "32258","32266",
}

def get_export(days):
    """Fetch all qualified improved sales for the date range via export."""
    s = requests.Session()
    r = s.get(SEARCH_URL, headers=HEADERS, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    def v(name, src=None):
        if src is None: src = soup
        t = src.find("input", {"name": name})
        return t["value"] if t else ""

    date_begin = (datetime.now() - timedelta(days=days)).strftime("%m/%d/%Y")
    date_end   = datetime.now().strftime("%m/%d/%Y")

    payload = {
        "__LASTFOCUS":                         "",
        "__EVENTTARGET":                       "",
        "__EVENTARGUMENT":                     "",
        "__PREVIOUSPAGE":                      v("__PREVIOUSPAGE"),
        "__VIEWSTATE":                         v("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR":                v("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION":                   v("__EVENTVALIDATION"),
        "ctl00$cphBody$txtSalesDateBegin":     date_begin,
        "ctl00$cphBody$txtSalesDateEnd":       date_end,
        "ctl00$cphBody$chkQualified":          "on",
        "ctl00$cphBody$chkImproved":           "on",
        "ctl00$cphBody$ddResultsPerPage":      "100",
        "ctl00$cphBody$bSearch":               "Search",
    }

    r2 = s.post(RESULTS_URL, data=payload, headers={**HEADERS, "Referer": SEARCH_URL}, timeout=30)
    soup2 = BeautifulSoup(r2.text, "html.parser")

    # Trigger plain text export
    export_payload = {
        "__EVENTTARGET":        "ctl00$cphBody$bExportToText",
        "__EVENTARGUMENT":      "",
        "__LASTFOCUS":          "",
        "__VIEWSTATE":          v("__VIEWSTATE", soup2),
        "__VIEWSTATEGENERATOR": v("__VIEWSTATEGENERATOR", soup2),
        "__EVENTVALIDATION":    v("__EVENTVALIDATION", soup2),
    }
    r3 = s.post(RESULTS_URL, data=export_payload,
                headers={**HEADERS, "Referer": RESULTS_URL,
                         "Content-Type": "application/x-www-form-urlencoded"},
                timeout=60)

    if "text/plain" not in r3.headers.get("content-type", ""):
        raise RuntimeError(f"Export didn't return text/plain: {r3.headers.get('content-type')}")

    import io
    rows = list(csv.DictReader(io.StringIO(r3.content.decode("windows-1252"))))
    return rows, date_begin, date_end

def scrape_detail(re_number):
    """Scrape sale price, sqft, property use from the detail page for one RE."""
    s = requests.Session()
    url = f"{DETAIL_URL}?RE={re_number}"
    try:
        r = s.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        # Property use - vertical layout: each field is its own <tr><td>
        # Row order: RE#, Tax District, Property Use, # Buildings, Legal, Subdivision, Total Area
        prop_use = ""
        for t in soup.find_all("table"):
            ths = [th.get_text(strip=True) for th in t.find_all("th")]
            if "Property Use" in ths:
                pu_idx = ths.index("Property Use")
                all_tds = [tr.find("td") for tr in t.find_all("tr") if tr.find("td")]
                if len(all_tds) > pu_idx:
                    prop_use = all_tds[pu_idx].get_text(strip=True)
                break

        # Filter: single family only (0100 prefix)
        if prop_use and not prop_use.startswith("0100"):
            return None

        # Sale price + date (Sales History table)
        sale_price = 0
        sale_date_detail = ""
        for t in soup.find_all("table"):
            ths = [th.get_text(strip=True) for th in t.find_all("th")]
            if "Sale Price" in ths and "Sale Date" in ths:
                # Get most recent qualified sale
                for tr in t.find_all("tr")[1:]:
                    tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                    if len(tds) >= 6:
                        qualified = tds[4] if len(tds) > 4 else ""
                        if "qualified" in qualified.lower() and "unqualified" not in qualified.lower():
                            price_str = tds[2] if len(tds) > 2 else ""
                            # Strip $ commas and cents: "$280,000.00" -> 280000
                            price_clean = re.sub(r"\.\d+$", "", price_str)  # remove .00
                            sale_price = int(re.sub(r"[^\d]", "", price_clean)) if price_clean else 0
                            sale_date_detail = tds[1] if len(tds) > 1 else ""
                            break
                break

        # Heated sqft (Building tables - sum all buildings' heated area)
        total_sqft = 0
        for t in soup.find_all("table"):
            ths = [th.get_text(strip=True) for th in t.find_all("th")]
            if "Heated Area" in ths:
                idx = ths.index("Heated Area")
                for tr in t.find_all("tr")[1:]:
                    tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                    if len(tds) > idx:
                        try:
                            val = int(re.sub(r"[^\d]", "", tds[idx]))
                            total_sqft += val
                        except ValueError:
                            pass

        # Year built (first building table with Year Built)
        year_built = ""
        for t in soup.find_all("table"):
            ths = [th.get_text(strip=True) for th in t.find_all("th")]
            if "Year Built" in ths:
                idx = ths.index("Year Built")
                for tr in t.find_all("tr")[1:]:
                    tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                    if len(tds) > idx:
                        year_built = tds[idx]
                        break
                if year_built:
                    break

        return {
            "prop_use":   prop_use,
            "sale_price": sale_price,
            "sqft":       total_sqft,
            "year_built": year_built,
            "sale_date_detail": sale_date_detail,
        }
    except Exception:
        return None


def main():
    days         = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    max_props    = int(sys.argv[2]) if len(sys.argv) > 2 else 600

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    print(f"Step 1: fetching export for last {days} days...", flush=True)
    export_rows, date_begin, date_end = get_export(days)
    print(f"  Got {len(export_rows)} qualified improved sales", flush=True)

    # Filter to core JAX zip codes
    filtered = []
    for row in export_rows:
        z = row.get("Zip Code", "").split("-")[0].strip()
        if z in CORE_ZIPS:
            filtered.append(row)

    print(f"  After zip filter: {len(filtered)} properties", flush=True)

    # Cap at max_props to keep runtime reasonable
    if len(filtered) > max_props:
        # Sample evenly across the date range to avoid recency bias
        step = len(filtered) / max_props
        filtered = [filtered[int(i * step)] for i in range(max_props)]
        print(f"  Sampling down to {len(filtered)} properties", flush=True)

    print(f"Step 2: enriching {len(filtered)} properties from detail pages (10 threads)...", flush=True)

    records = []
    done = 0

    def enrich(row):
        re_raw = row["Real Estate Number"].replace("-", "")
        detail = scrape_detail(re_raw)
        return row, detail

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(enrich, row): row for row in filtered}
        for fut in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(filtered)} done...", flush=True)
            row, detail = fut.result()
            if detail is None:
                continue

            sale_price = detail["sale_price"]
            sqft       = detail["sqft"]
            if sale_price < 10000 or sqft < 100:
                continue

            price_per_sqft = round(sale_price / sqft, 2)

            # Build address
            parts = [
                row.get("Street Number", ""),
                row.get("Street", ""),
                row.get("Street Type", ""),
                row.get("Street Direction", ""),
            ]
            address = " ".join(p for p in parts if p).strip()

            sale_date = row.get("Sale Date", "").split(" ")[0]  # strip time part

            records.append({
                "sale_date":      sale_date,
                "sale_price":     sale_price,
                "address":        address,
                "zip":            row.get("Zip Code", "").split("-")[0].strip(),
                "sqft":           sqft,
                "price_per_sqft": price_per_sqft,
                "year_built":     detail.get("year_built", ""),
                "land_use":       detail.get("prop_use", ""),
                "qualified":      row.get("Is Qualified?", ""),
            })

    if not records:
        print("error: no records after enrichment", flush=True)
        sys.exit(1)

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "sale_date","sale_price","address","zip","sqft",
            "price_per_sqft","year_built","land_use","qualified"
        ])
        writer.writeheader()
        writer.writerows(records)

    print(f"ok: {len(records)} records -> {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
