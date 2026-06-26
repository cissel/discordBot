#!/usr/bin/env python3
"""
repoFileSize.py
Samples git history to extract per-file LOC at evenly-spaced commits.
Writes: outputs/server/repo_filesize.csv
Columns: commit_date, file, loc, directory
"""

import subprocess
import os
import sys
import csv
from datetime import datetime

REPO_DIR  = os.path.expanduser("~/discordBot")
OUT_DIR   = os.path.expanduser("~/discordBot/outputs/server")
OUT_CSV   = os.path.join(OUT_DIR, "repo_filesize.csv")

# File extensions to track
EXTS = {".py", ".r", ".rmd", ".sh", ".js", ".md", ".yaml", ".yml", ".json", ".toml"}
# Directories to exclude (mostly data/output dirs)
EXCLUDE_PREFIXES = ("outputs/", "python/references/")

# Sample up to this many commits across full history
MAX_SAMPLES = 40


def get_commits():
    """Return list of (hash, date_str) oldest-first."""
    result = subprocess.run(
        ["git", "log", "--format=%H %ad", "--date=short", "--reverse"],
        cwd=REPO_DIR, capture_output=True, text=True, timeout=30
    )
    commits = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) == 2:
            commits.append((parts[0], parts[1]))
    return commits


def sample_commits(commits, n):
    """Pick n evenly-spaced commits from the list (always include first and last)."""
    if len(commits) <= n:
        return commits
    step = (len(commits) - 1) / (n - 1)
    indices = sorted(set(round(i * step) for i in range(n)))
    return [commits[i] for i in indices]


def get_file_loc_at(commit_hash):
    """
    Return dict {filepath: loc} for tracked code files at the given commit.
    Uses git ls-tree + git show to count lines.
    """
    # List all files at this commit
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit_hash],
        cwd=REPO_DIR, capture_output=True, text=True, timeout=15
    )
    files = []
    for f in result.stdout.strip().splitlines():
        ext = os.path.splitext(f)[1].lower()
        if ext not in EXTS:
            continue
        if any(f.startswith(p) for p in EXCLUDE_PREFIXES):
            continue
        files.append(f)

    file_loc = {}
    for f in files:
        show = subprocess.run(
            ["git", "show", f"{commit_hash}:{f}"],
            cwd=REPO_DIR, capture_output=True, timeout=10
        )
        if show.returncode == 0:
            try:
                text = show.stdout.decode("utf-8", errors="ignore")
                loc = len(text.splitlines())
                file_loc[f] = loc
            except Exception:
                pass  # skip unparseable files

    return file_loc


def directory_of(filepath):
    parts = filepath.split("/")
    if len(parts) == 1:
        return "root"
    return parts[0]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    commits = get_commits()
    if not commits:
        print("error: no commits found")
        sys.exit(1)

    sampled = sample_commits(commits, MAX_SAMPLES)
    print(f"Processing {len(sampled)} sampled commits (of {len(commits)} total)...", flush=True)

    rows = []
    for i, (h, d) in enumerate(sampled):
        file_loc = get_file_loc_at(h)
        for filepath, loc in file_loc.items():
            rows.append({
                "commit_date": d,
                "file":        filepath,
                "loc":         loc,
                "directory":   directory_of(filepath),
            })
        print(f"  [{i+1}/{len(sampled)}] {d}  {h[:8]}  ({len(file_loc)} files)", flush=True)

    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["commit_date", "file", "loc", "directory"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"ok: {OUT_CSV} ({len(rows)} rows, {len(sampled)} snapshots)")


if __name__ == "__main__":
    main()
