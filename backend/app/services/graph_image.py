"""Render a fund-flow graph to PNG (for Telegram and exports).

Layers go left to right: sources (negative depth), the wallet, destinations.
"""

import io
from collections import defaultdict
from decimal import Decimal

import matplotlib

matplotlib.use("Agg")  # no display needed

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch  # noqa: E402

from app.services.graph import Graph  # noqa: E402
from app.services.labels import RISKY_CATEGORIES, TERMINAL_CATEGORIES  # noqa: E402

COLORS = {
    "root": "#2563eb",
    "risky": "#dc2626",
    "exchange": "#16a34a",
    "hub": "#9333ea",
    "plain": "#64748b",
}


def short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}" if len(address) > 14 else address


def _fmt(amount: Decimal) -> str:
    value = float(amount)
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= size:
            return f"{value / size:.1f}{unit}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def render_png(graph: Graph, max_nodes: int = 40) -> bytes:
    # Keep the root plus the nodes connected by the largest edges.
    ranked = sorted(graph.edges, key=lambda e: e.amount, reverse=True)
    keep = {graph.root}
    for e in ranked:
        if len(keep) >= max_nodes:
            break
        keep.update((e.from_address, e.to_address))
    nodes = [n for n in graph.nodes if n.address in keep]
    edges = [e for e in graph.edges if e.from_address in keep and e.to_address in keep]

    layers: dict[int, list] = defaultdict(list)
    for n in nodes:
        layers[n.depth].append(n)
    pos = {}
    for depth, members in layers.items():
        members.sort(key=lambda n: n.address)
        for i, n in enumerate(members):
            pos[n.address] = (depth * 3.0, (i - (len(members) - 1) / 2) * 1.2)

    height = max(4, max((len(m) for m in layers.values()), default=1) * 0.55)
    width = max(6, len(layers) * 3.2)
    fig, ax = plt.subplots(figsize=(width, height), dpi=110)
    ax.set_axis_off()

    top = max((e.amount for e in edges), default=Decimal(1)) or Decimal(1)
    for e in edges:
        (x1, y1), (x2, y2) = pos[e.from_address], pos[e.to_address]
        weight = 0.6 + 3.5 * float(e.amount / top)
        ax.add_patch(
            FancyArrowPatch(
                (x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=10, lw=weight,
                color="#94a3b8", alpha=0.8, connectionstyle="arc3,rad=0.08", shrinkA=12, shrinkB=12,
            )
        )
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.12, f"{_fmt(e.amount)} {e.token_symbol}",
                fontsize=6.5, ha="center", color="#334155")

    for n in nodes:
        x, y = pos[n.address]
        if n.address == graph.root:
            color = COLORS["root"]
        elif n.label and n.label.category in RISKY_CATEGORIES:
            color = COLORS["risky"]
        elif n.label and n.label.category in TERMINAL_CATEGORIES:
            color = COLORS["exchange"]
        elif n.is_hub:
            color = COLORS["hub"]
        else:
            color = COLORS["plain"]
        ax.scatter([x], [y], s=260, color=color, zorder=3, edgecolors="white", linewidths=1.5)
        text = short(n.address) + (f"\n{n.label.name[:24]}" if n.label else "")
        ax.text(x, y - 0.38, text, fontsize=6.5, ha="center", va="top", color="#0f172a")

    xs = [p[0] for p in pos.values()] or [0]
    ys = [p[1] for p in pos.values()] or [0]
    ax.set_xlim(min(xs) - 1.5, max(xs) + 1.5)
    ax.set_ylim(min(ys) - 1.0, max(ys) + 0.8)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()
