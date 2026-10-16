#!/usr/bin/env python3
"""
monitor_training.py
====================
Live terminal dashboard for watching an in-progress train_mm_rl.py run over
SSH, instead of staring at a scrolling SB3 log or waiting blind for a
background job notification.

Tails the training log (redirected stdout of train_mm_rl.py, which is SB3's
verbose=1 table output), parses each completed stats block as it's written,
and redraws a compact live view: progress bar + ETA, current fps, an ASCII
sparkline of ep_rew_mean over the run, and the latest train/ diagnostics
(explained_variance, entropy, policy std) so you can see at a glance whether
the run is still improving or has plateaued/diverged.

Usage:
  venv/bin/python3 python/rl/monitor_training.py
      # auto-finds the most recently modified /tmp/train_*.log and the
      # --timesteps value from the matching running train_mm_rl.py process

  venv/bin/python3 python/rl/monitor_training.py --log /tmp/train_1m_v5_sizepenalty.log --total-timesteps 1000000

  venv/bin/python3 python/rl/monitor_training.py --once
      # print one snapshot and exit (no live loop) - useful for a quick check
      # or for piping into something else, instead of the live redraw loop

Ctrl-C to exit at any time; it never touches the training process itself.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque

BLOCK_RE = re.compile(r"\|\s*([a-zA-Z_/]+)\s*\|\s*([^|]+?)\s*\|")


def find_latest_log():
    candidates = sorted(
        glob.glob("/tmp/train_*.log"),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    return candidates[0] if candidates else None


def find_total_timesteps(log_path):
    """Best-effort: find the running train_mm_rl.py process and read its
    --timesteps arg from /proc/<pid>/cmdline. Falls back to None (progress
    bar just shows raw timesteps with no %/ETA)."""
    try:
        out = subprocess.run(["pgrep", "-af", "train_mm_rl.py"], capture_output=True, text=True, timeout=5)
        for line in out.stdout.splitlines():
            m = re.search(r"--timesteps[ =](\d+)", line)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def parse_blocks(text):
    """Split the raw log text into stats blocks (one per PPO rollout/update)
    and parse each into a flat dict of {metric_name: value}. SB3's table
    format uses '-----' separator lines around each block."""
    blocks = []
    current = {}
    in_block = False
    for line in text.splitlines():
        if line.startswith("---"):
            if in_block and current:
                blocks.append(current)
                current = {}
            in_block = not in_block if not current else in_block
            in_block = True
            continue
        m = BLOCK_RE.match(line)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            try:
                val = float(val)
            except ValueError:
                pass
            current[key] = val
    if current:
        blocks.append(current)
    return blocks


def sparkline(values, width=50):
    if not values:
        return ""
    blocks = " ▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    span = hi - lo if hi > lo else 1.0
    # downsample to `width` points
    n = len(values)
    if n > width:
        step = n / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values
    chars = []
    for v in sampled:
        idx = int((v - lo) / span * (len(blocks) - 1))
        idx = max(0, min(len(blocks) - 1, idx))
        chars.append(blocks[idx])
    return "".join(chars)


def fmt_eta(seconds):
    if seconds is None or seconds < 0:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def render(blocks, total_timesteps, log_path, term_width):
    if not blocks:
        return "Waiting for first training update block in log...\n(SB3 writes the first block after n_steps*n_envs timesteps - may take ~30-60s)"

    latest = blocks[-1]
    rewards = [b.get("ep_rew_mean") for b in blocks if isinstance(b.get("ep_rew_mean"), float)]
    timesteps = latest.get("total_timesteps")
    fps = latest.get("fps")
    elapsed = latest.get("time_elapsed")

    lines = []
    lines.append("=" * term_width)
    lines.append(f" MM-RL TRAINING MONITOR   log={os.path.basename(log_path)}".ljust(term_width))
    lines.append("=" * term_width)

    if timesteps is not None and total_timesteps:
        pct = min(timesteps / total_timesteps, 1.0)
        bar_width = max(20, term_width - 30)
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)
        remaining_steps = max(total_timesteps - timesteps, 0)
        eta_s = remaining_steps / fps if fps else None
        lines.append(f" [{bar}] {pct*100:5.1f}%")
        lines.append(f" timesteps: {int(timesteps):>9,} / {int(total_timesteps):,}   fps: {fps:.0f}   "
                      f"elapsed: {fmt_eta(elapsed)}   ETA: {fmt_eta(eta_s)}")
    elif timesteps is not None:
        lines.append(f" timesteps: {int(timesteps):,}   fps: {fps}   elapsed: {fmt_eta(elapsed)}")

    lines.append("")
    lines.append(f" ep_rew_mean (latest): {latest.get('ep_rew_mean')}")
    if rewards:
        prefix, suffix = " reward trend: [", "]"
        spark_width = max(20, term_width - len(prefix) - len(suffix) - 1)
        lines.append(f"{prefix}{sparkline(rewards, spark_width)}{suffix}")
        lines.append(f"   min={min(rewards):.2f}  max={max(rewards):.2f}  "
                      f"last10_avg={sum(rewards[-10:])/len(rewards[-10:]):.2f}")

    lines.append("")
    lines.append(" train/ diagnostics (latest update):")
    for key in ["explained_variance", "entropy_loss", "std", "approx_kl", "clip_fraction", "value_loss", "loss"]:
        if key in latest:
            lines.append(f"   {key:<20} {latest[key]}")

    lines.append("")
    lines.append(" (Ctrl-C to exit monitor - does NOT stop training)")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=None, help="training log path (default: most recently modified /tmp/train_*.log)")
    parser.add_argument("--total-timesteps", type=int, default=None, help="target timesteps for % and ETA (default: auto-detect from running process)")
    parser.add_argument("--interval", type=float, default=2.0, help="refresh interval in seconds")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit, no live loop")
    args = parser.parse_args()

    log_path = args.log or find_latest_log()
    if not log_path or not os.path.exists(log_path):
        print(f"No training log found (looked for {args.log or '/tmp/train_*.log'}). "
              "Pass --log explicitly if training writes elsewhere.")
        sys.exit(1)

    total_timesteps = args.total_timesteps or find_total_timesteps(log_path)

    try:
        while True:
            with open(log_path) as f:
                text = f.read()
            blocks = parse_blocks(text)
            term_width = shutil.get_terminal_size((100, 24)).columns
            output = render(blocks, total_timesteps, log_path, term_width)

            if args.once:
                print(output)
                return

            os.system("clear")
            print(output)

            if "TRAIN_EXIT_CODE=" in text:
                m = re.search(r"TRAIN_EXIT_CODE=(\d+)", text)
                code = m.group(1) if m else "?"
                print(f"\n*** Training process exited (code {code}). Monitor stopping. ***")
                return

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped (training unaffected).")


if __name__ == "__main__":
    main()
