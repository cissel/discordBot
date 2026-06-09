#!/usr/bin/env python3
"""
inviteGraph.py
Reads invite_seed.csv + invite_log.csv and generates a hierarchical family-tree
invite network rooted at JHCV.

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

BASE      = Path(os.path.expanduser("~/discordBot"))
LOG_CSV   = BASE / "outputs/server/invite_log.csv"
SEED_CSV  = BASE / "outputs/server/invite_seed.csv"
OUT_PNG   = BASE / "outputs/server/invite_graph.png"
OUT_PNG.parent.mkdir(parents=True, exist_ok=True)

# members who have left the server - their branch gets styled red/dim
LEFT_SERVER = {"freedos"}

# name normalisation - uppercase/alternate spellings -> canonical display name
NAME_MAP = {
    "cverv":  "cverv",
    "CVERV":  "cverv",
    "jhcv":   "JHCV",
    "JHCV":   "JHCV",
}

def normalise(name: str) -> str:
    n = name.split("#")[0].strip()
    return NAME_MAP.get(n, n)

def short(name: str) -> str:
    return name[:14] + ".." if len(name) > 14 else name

def hierarchical_layout(G, root):
    """Use graphviz dot layout for a clean top-down tree, JHCV at top."""
    try:
        # dot ranks nodes by longest path from a source node.
        # To get root at top: reverse the graph so root becomes the only source,
        # then flip y so it reads top-down.
        G_rev = G.reverse()
        pos = nx.nx_agraph.graphviz_layout(G_rev, prog="dot", root=root)
        # dot y-axis: higher y = higher on page. Flip so root (max y) is at top.
        max_y = max(y for x, y in pos.values())
        return {n: (x, max_y - y) for n, (x, y) in pos.items()}
    except Exception:
        return nx.spring_layout(G, k=3.0, iterations=80, seed=42)

def main():
    if not SEED_CSV.exists() and not LOG_CSV.exists():
        print("[inviteGraph] no invite data found", file=sys.stderr)
        sys.exit(1)

    edges      = []
    all_people = set()
    seen       = set()

    # -- seed file (historical, hand-filled) -----------------------------------
    if SEED_CSV.exists():
        with open(SEED_CSV) as f:
            first = f.readline().strip()
            if first != "member,display_name,joined_at,inviter,inviter_display_name,notes":
                f.seek(0)
                f.readline()  # skip "invite_seed" label line
            for row in csv.DictReader(f):
                member  = normalise(row.get("display_name", "").strip() or row.get("member", "").strip())
                inviter = normalise(row.get("inviter_display_name", "").strip() or row.get("inviter", "").strip())
                if not member or member in seen:
                    continue
                seen.add(member)
                all_people.add(member)
                if inviter and inviter.upper() not in ("", "UNKNOWN"):
                    all_people.add(inviter)
                    edges.append((inviter, member))

    # -- live log (on_member_join since bot came online) -----------------------
    if LOG_CSV.exists():
        with open(LOG_CSV) as f:
            for row in csv.DictReader(f):
                member  = normalise(row.get("member", "").strip())
                inviter = normalise(row.get("inviter", "").strip())
                if not member or "bot" in member.lower() or "hermes" in member.lower() or member in seen:
                    continue
                seen.add(member)
                all_people.add(member)
                if inviter and inviter.lower() != "unknown":
                    all_people.add(inviter)
                    edges.append((inviter, member))

    if not all_people:
        print("[inviteGraph] no data to plot", file=sys.stderr)
        sys.exit(1)

    # -- build directed graph --------------------------------------------------
    G = nx.DiGraph()
    G.add_nodes_from(all_people)
    G.add_edges_from(edges)

    invite_counts = defaultdict(int)
    for inv, _ in edges:
        invite_counts[inv] += 1

    # -- hierarchical layout rooted at JHCV ------------------------------------
    root = "JHCV"
    if root not in G.nodes:
        root = max(invite_counts, key=invite_counts.get)

    pos = hierarchical_layout(G, root)

    # -- classify nodes for colour ---------------------------------------------
    # a node's branch is "left" if it is in LEFT_SERVER or all paths to root
    # pass through a left-server node
    def in_left_branch(node):
        # walk up via predecessors - if we hit a LEFT_SERVER node before root, red
        visited = set()
        queue = list(G.predecessors(node))
        while queue:
            n = queue.pop()
            if n in visited:
                continue
            visited.add(n)
            if n in LEFT_SERVER:
                return True
            queue.extend(G.predecessors(n))
        return node in LEFT_SERVER

    node_colors   = []
    node_alphas   = []
    edge_colors   = []
    edge_alphas   = []

    for node in G.nodes:
        if node == root:
            node_colors.append("#00ffaa")   # root - bright green
            node_alphas.append(1.0)
        elif node in LEFT_SERVER or in_left_branch(node):
            node_colors.append("#cc2200")   # left server branch - red
            node_alphas.append(0.75)
        elif invite_counts.get(node, 0) > 0:
            node_colors.append("#00ccff")   # invited others - cyan
            node_alphas.append(0.92)
        else:
            node_colors.append("#004466")   # leaf - dark blue
            node_alphas.append(0.92)

    for u, v in G.edges:
        if u in LEFT_SERVER or in_left_branch(v) or v in LEFT_SERVER:
            edge_colors.append("#cc2200")
            edge_alphas.append(0.5)
        else:
            edge_colors.append("#00ffaa")
            edge_alphas.append(0.4)

    # -- node sizes based on invite count --------------------------------------
    min_size = 500
    max_size = 3500
    max_inv  = max(invite_counts.values()) if invite_counts else 1
    node_sizes = []
    for node in G.nodes:
        count = invite_counts.get(node, 0)
        size  = min_size + (count / max_inv) * (max_size - min_size)
        node_sizes.append(size)

    # -- draw ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(36, 16))
    fig.patch.set_facecolor("#0a0a0f")
    ax.set_facecolor("#0a0a0f")

    # draw edges grouped by color so we can set alpha per group
    green_edges = [(u, v) for (u, v), c in zip(G.edges, edge_colors) if c == "#00ffaa"]
    red_edges   = [(u, v) for (u, v), c in zip(G.edges, edge_colors) if c == "#cc2200"]

    for edge_list, color, alpha in [(green_edges, "#00ffaa", 0.4), (red_edges, "#cc2200", 0.55)]:
        if edge_list:
            nx.draw_networkx_edges(
                G, pos, edgelist=edge_list, ax=ax,
                edge_color=color,
                alpha=alpha,
                arrows=True,
                arrowsize=14,
                arrowstyle="-|>",
                width=1.4,
                connectionstyle="arc3,rad=0.0",
                min_source_margin=18,
                min_target_margin=18,
            )

    # draw nodes grouped by color for correct alpha
    node_order = list(G.nodes)
    for color, alpha in [("#0a0a0f", 1.0), ("#cc2200", 0.75), ("#004466", 0.92), ("#00ccff", 0.92), ("#00ffaa", 1.0)]:
        subset      = [n for n, c in zip(node_order, node_colors) if c == color]
        sub_sizes   = [s for n, s in zip(node_order, node_sizes) if n in subset]
        sub_ec      = "#cc2200" if color == "#cc2200" else "#00ffaa"
        if subset:
            nx.draw_networkx_nodes(
                G, pos, nodelist=subset, ax=ax,
                node_size=sub_sizes,
                node_color=color,
                alpha=alpha,
                linewidths=1.5,
                edgecolors=sub_ec,
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
        mpatches.Patch(facecolor="#00ffaa", edgecolor="#00ffaa", label="root (JHCV)"),
        mpatches.Patch(facecolor="#00ccff", edgecolor="#00ffaa", label="invited others"),
        mpatches.Patch(facecolor="#004466", edgecolor="#00ffaa", label="joined via invite"),
        mpatches.Patch(facecolor="#cc2200", edgecolor="#cc2200", label="left server"),
        Line2D([0], [0], color="#00ffaa", alpha=0.6, label="invite link", linewidth=1.5),
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
