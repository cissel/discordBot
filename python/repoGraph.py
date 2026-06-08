#!/usr/bin/env python3
"""
repoGraph.py
Extracts weekly commit counts and cumulative LOC from git history.
Writes: outputs/server/repo_graph.csv
Columns: week, commits, loc_added, loc_removed, net_loc, cumulative_loc
"""

import subprocess
import os
import sys
import csv
from datetime import datetime, timedelta
from collections import defaultdict

REPO_DIR = os.path.expanduser("~/discordBot")
OUT_DIR  = os.path.expanduser("~/discordBot/outputs/server")
OUT_CSV  = os.path.join(OUT_DIR, "repo_graph.csv")

def week_start(date_str):
    """Return the Monday of the ISO week for a given YYYY-MM-DD string."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")

def get_commit_hashes():
    result = subprocess.run(
        ["git", "log", "--format=%H %ad", "--date=short"],
        cwd=REPO_DIR,
        capture_output=True, text=True, timeout=30
    )
    commits = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) == 2:
            commits.append((parts[0], parts[1]))
    return commits  # [(hash, date), ...] newest first

def get_diff_stats(commit_hash):
    """Return (added, removed) line counts for a commit vs its parent."""
    result = subprocess.run(
        ["git", "diff", "--numstat", f"{commit_hash}^", commit_hash, "--",
         # only count actual code/script files - exclude binary outputs, csvs, images
         "*.py", "*.R", "*.Rmd", "*.js", "*.sh", "*.md", "*.txt", "*.yaml", "*.yml", "*.json", "*.toml"],
        cwd=REPO_DIR,
        capture_output=True, text=True, timeout=15
    )
    added = removed = 0
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            try:
                added   += int(parts[0])
                removed += int(parts[1])
            except ValueError:
                pass  # binary files show '-'
    return added, removed

def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    commits = get_commit_hashes()
    if not commits:
        print("error: no commits found")
        sys.exit(1)

    # For LOC we only sample commits - doing full numstat on all 183 commits is slow
    # Strategy: get diff stats for every commit (manageable for 183)
    print(f"Processing {len(commits)} commits...", flush=True)

    # Aggregate by week
    week_commits  = defaultdict(int)
    week_added    = defaultdict(int)
    week_removed  = defaultdict(int)

    for i, (h, d) in enumerate(commits):
        w = week_start(d)
        week_commits[w] += 1
        added, removed = get_diff_stats(h)
        week_added[w]   += added
        week_removed[w] += removed
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(commits)} commits processed", flush=True)

    # Build sorted weekly series
    all_weeks = sorted(set(list(week_commits.keys()) + list(week_added.keys())))

    # Compute cumulative LOC (net lines added)
    rows = []
    cumulative = 0
    for w in all_weeks:
        net = week_added[w] - week_removed[w]
        cumulative += net
        rows.append({
            "week":           w,
            "commits":        week_commits[w],
            "loc_added":      week_added[w],
            "loc_removed":    week_removed[w],
            "net_loc":        net,
            "cumulative_loc": cumulative,
        })

    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["week","commits","loc_added","loc_removed","net_loc","cumulative_loc"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"ok: {OUT_CSV} ({len(rows)} weeks)")

if __name__ == "__main__":
    main()
