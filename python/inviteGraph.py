#!/usr/bin/env python3
"""
inviteGraph.py
Reads outputs/server/invite_log.csv and generates a bubble network graph
showing who invited who to the server.

Usage: python3 inviteGraph.py
Writes: outputs/server/invite_graph.png
"""

import os, sys, csv
from pathlib import Path
from collections import defaultdict

import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

BASE     = Path(os.path.expanduser("~/discordBot"))
LOG_CSV  = BASE / "outputs/server/invite_log.csv"
OUT_PNG  = BASE / "outputs/server/invite_graph.png"
OUT_PNG.parent.mkdir(parents=True, exist_ok=True)

def short(name: str) -> str:
    """Strip discriminator and truncate for label."""
    n = name.split("#")[0]
    return n[:14] + ".." if len(n) > 14 else n

def main():
    if not LOG_CSV.exists():
        print("[inviteGraph] no invite_log.csv found", file=sys.stderr)
        sys.exit(1)

    # read log
    edges      = []   # (inviter, member)
    all_people = set()

    with open(LOG_CSV) as f:
        for row in csv.DictReader(f):
            member  = row["member"].strip()
            inviter = row["inviter"].strip()
            if not member:
                continue
            all_people.add(member)
            if inviter and inviter != "unknown":
                all_people.add(inviter)
                edges.append((inviter, member))
            # unknown inviters still add the member node

    if not all_people:
        print("[inviteGraph] no data to plot", file=sys.stderr)
        sys.exit(1)

    # build graph
    G = nx.DiGraph()
    G.add_nodes_from(all_people)
    G.add_edges_from(edges)

    # node sizes based on how many people each person invited
    invite_counts = defaultdict(int)
    for inv, _ in edges:
        invite_counts[inv] += 1

    # layout - spring layout looks best for invite trees
    # use a fixed seed for reproducibility
    if len(G.nodes) > 1:
        pos = nx.spring_layout(G, k=2.5, iterations=80, seed=42)
    else:
        pos = {list(G.nodes)[0]: (0.5, 0.5)}

    # sizing
    min_size  = 600
    max_size  = 4000
    max_inv   = max(invite_counts.values()) if invite_counts else 1
    node_sizes = []
    for node in G.nodes:
        count = invite_counts.get(node, 0)
        if max_inv > 0:
            size = min_size + (count / max_inv) * (max_size - min_size)
        else:
            size = min_size
        node_sizes.append(size)

    # colors - inviters get a warm accent, members-only get a cooler color
    node_colors = []
    for node in G.nodes:
        if invite_counts.get(node, 0) > 0:
            node_colors.append("#00ccff")   # invited someone - cyan
        else:
            node_colors.append("#004466")   # leaf node - dark blue

    # plot
    fig, ax = plt.subplots(figsize=(16, 12))
    fig.patch.set_facecolor("#0a0a0f")
    ax.set_facecolor("#0a0a0f")

    # edges
    nx.draw_networkx_edges(
        G, pos, ax=ax,
        edge_color="#00ffaa",
        alpha=0.4,
        arrows=True,
        arrowsize=15,
        arrowstyle="-|>",
        width=1.2,
        connectionstyle="arc3,rad=0.08",
        min_source_margin=20,
        min_target_margin=20,
    )

    # nodes
    nx.draw_networkx_nodes(
        G, pos, ax=ax,
        node_size=node_sizes,
        node_color=node_colors,
        alpha=0.92,
        linewidths=1.5,
        edgecolors="#00ffaa",
    )

    # labels
    labels = {n: short(n) for n in G.nodes}
    nx.draw_networkx_labels(
        G, pos, labels, ax=ax,
        font_size=7,
        font_color="white",
        font_family="monospace",
    )

    # legend
    legend_elements = [
        mpatches.Patch(facecolor="#00ccff", edgecolor="#00ffaa", label="invited others"),
        mpatches.Patch(facecolor="#004466", edgecolor="#00ffaa", label="joined via invite"),
        Line2D([0], [0], color="#00ffaa", alpha=0.6, label="invite link used", linewidth=1.5),
    ]
    ax.legend(
        handles=legend_elements,
        loc="lower left",
        facecolor="#0a0a0f",
        edgecolor="#00ffaa",
        labelcolor="white",
        fontsize=9,
    )

    total_members = len(all_people)
    total_tracked = len(edges)
    ax.set_title(
        f"server invite network  -  {total_members} members  -  {total_tracked} tracked invites\n"
        f"bubble size = number of people invited",
        color="white",
        fontsize=12,
        fontfamily="monospace",
        pad=15,
    )
    ax.axis("off")

    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()

    print(f"[inviteGraph] saved {OUT_PNG} ({total_members} members, {total_tracked} edges)")

if __name__ == "__main__":
    main()
