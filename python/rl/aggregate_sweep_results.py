"""
aggregate_sweep_results.py
===========================
Pivot the long-format sweep CSV (one row per model x eval-mode, written
incrementally by eval_mm_policy.py --out-csv during run_sweep_night.sh) into
one ranked wide CSV: one row per trained config, with synthetic and
historical results side by side plus a combined rank.

Usage:
  venv/bin/python3 python/rl/aggregate_sweep_results.py \
      --in-csv python/rl/models/sweep_results_20261017_0200.csv \
      --out-csv python/rl/models/sweep_results_20261017_0200_ranked.csv
"""
import argparse
import csv
from collections import defaultdict


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in-csv", required=True)
    parser.add_argument("--out-csv", required=True)
    args = parser.parse_args()

    by_tag = defaultdict(dict)
    meta = {}
    with open(args.in_csv, newline="") as fh:
        for row in csv.DictReader(fh):
            tag = row["tag"]
            by_tag[tag][row["mode"]] = row
            meta[tag] = row  # config metadata is the same across modes for a given tag

    out_rows = []
    for tag, modes in by_tag.items():
        m = meta[tag]
        syn = modes.get("synthetic", {})
        hist = modes.get("historical", {})
        syn_diff = f(syn.get("diff_mean"))
        hist_diff = f(hist.get("diff_mean"))
        combined = [v for v in (syn_diff, hist_diff) if v == v]  # drop NaNs
        combined_diff_mean = sum(combined) / len(combined) if combined else float("nan")
        out_rows.append({
            "tag": tag,
            "group": m.get("group"),
            "config_seed": m.get("config_seed"),
            "ent_coef": m.get("ent_coef"),
            "net_arch": m.get("net_arch"),
            "historical_frac_cfg": m.get("historical_frac_cfg"),
            "train_timesteps": m.get("train_timesteps"),
            "model_path": m.get("model_path"),
            "syn_diff_mean": syn_diff, "syn_diff_std": f(syn.get("diff_std")),
            "syn_tstat": f(syn.get("tstat")), "syn_win_rate": f(syn.get("win_rate")),
            "hist_diff_mean": hist_diff, "hist_diff_std": f(hist.get("diff_std")),
            "hist_tstat": f(hist.get("tstat")), "hist_win_rate": f(hist.get("win_rate")),
            "combined_diff_mean": combined_diff_mean,
            "both_positive": (syn_diff > 0 and hist_diff > 0) if (syn_diff == syn_diff and hist_diff == hist_diff) else "",
        })

    out_rows.sort(key=lambda r: (r["combined_diff_mean"] if r["combined_diff_mean"] == r["combined_diff_mean"] else -1e9),
                   reverse=True)
    for i, r in enumerate(out_rows, 1):
        r["rank"] = i

    fieldnames = ["rank", "tag", "group", "config_seed", "ent_coef", "net_arch", "historical_frac_cfg",
                  "train_timesteps", "syn_diff_mean", "syn_diff_std", "syn_tstat", "syn_win_rate",
                  "hist_diff_mean", "hist_diff_std", "hist_tstat", "hist_win_rate",
                  "combined_diff_mean", "both_positive", "model_path"]
    with open(args.out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)

    print(f"Wrote {len(out_rows)} ranked configs to {args.out_csv}")
    print("\nTop 5:")
    for r in out_rows[:5]:
        print(f"  #{r['rank']} {r['tag']}: combined_diff_mean={r['combined_diff_mean']:.3f} "
              f"(syn={r['syn_diff_mean']:.3f} t={r['syn_tstat']:.2f}, "
              f"hist={r['hist_diff_mean']:.3f} t={r['hist_tstat']:.2f})")

    print("\nSeed-sweep rows (is the edge real or one lucky seed?):")
    seed_rows = [r for r in out_rows if r["group"] == "seed_sweep"]
    for r in sorted(seed_rows, key=lambda r: r["config_seed"]):
        print(f"  seed={r['config_seed']}: combined_diff_mean={r['combined_diff_mean']:.3f} "
              f"(syn={r['syn_diff_mean']:.3f}, hist={r['hist_diff_mean']:.3f})")


if __name__ == "__main__":
    main()
