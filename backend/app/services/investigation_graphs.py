"""Print-quality graph images for an investigation report (PNG at 300 DPI and SVG).

Text inside the images is English/neutral on purpose (addresses as
"#4 TCrnJ...FLDi", amounts as "10,304 USDT", Gregorian dates): matplotlib does
not shape Persian text, and the images are meant to be pasted into a report
whose captions are written separately.

Colours: each focus wallet keeps one colour in every image; inflow is green and
outflow red (and they also differ by position); exchanges are yellow squares.
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display needed

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, LogNorm  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, PathPatch, Rectangle  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from matplotlib.transforms import Bbox  # noqa: E402

from app.services.investigation import FocusProfile, Investigation, TransferRec, is_service, short  # noqa: E402

DPI = 300
SURFACE = "#ffffff"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
FOCUS_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#e87ba4"]
MEMBER = "#52514e"  # another wallet of the list
SERVICE = "#4a3aa7"  # list wallet that behaves like an exchange/service
EXCHANGE = "#eda100"  # outside wallet labelled as an exchange/service
OUTSIDE = "#b9b8b0"  # outside wallet without a label
ISOLATED = "#d9d8d2"  # list wallet with no link found
GROUP_COLORS = [MEMBER, "#8a5a2b", "#2f6f73", "#7a3b69"]
INFLOW = "#008300"
OUTFLOW = "#e34948"
SEQ = ["#eef4fd", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

FONT = ["Segoe UI", "Arial", "DejaVu Sans"]


@dataclass
class GraphFile:
    key: str  # e.g. "focus_network", "flow_2"
    title: str
    png: Path
    svg: Path
    width: int = 0  # pixels of the PNG
    height: int = 0


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": FONT,
            "font.size": 9,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": INK_2,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "axes.titlecolor": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelcolor": INK_2,
            "ytick.labelcolor": INK_2,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "svg.fonttype": "path",  # text as shapes: looks the same on every computer
            "legend.frameon": False,
        }
    )


def fmt_amount(value: Decimal | float, token: str | None = None) -> str:
    v = float(value)
    text = f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{text} {token}" if token else text


def fmt_compact(value: Decimal | float) -> str:
    v = float(value)
    for unit, size in (("M", 1e6), ("K", 1e3)):
        if abs(v) >= size:
            text = f"{v / size:.1f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    return f"{v:,.0f}" if abs(v) >= 10 else f"{v:,.1f}".rstrip("0").rstrip(".")


def focus_color(inv: Investigation, address: str) -> str | None:
    for i, p in enumerate(inv.focus):
        if p.address == address:
            return FOCUS_COLORS[i % len(FOCUS_COLORS)]
    return None


def _index(inv: Investigation) -> dict[str, int]:
    return {m.address: m.index for m in inv.members}


def short_tag(label: str, limit: int = 24) -> str:
    """"Exchange hot wallet (likely Binance)" -> "Binance (likely)"; long names are cut."""
    m = re.search(r"\(likely ([^)]+)\)", label)
    if m:
        label = f"{m.group(1)} (likely)"
    return label if len(label) <= limit else label[: limit - 1].rstrip() + "…"


def node_name(inv: Investigation, address: str, tag: bool = True) -> str:
    text = short(address, _index(inv).get(address))
    label = inv.labels.get(address)
    if tag and label:
        text += f"\n{short_tag(label)}"
    return text


def _kind(inv: Investigation, address: str) -> str:
    index = _index(inv)
    if focus_color(inv, address):
        return "focus"
    if address in index:
        return "service_member" if is_service(inv, address) else "member"
    return "exchange" if is_service(inv, address) else "outside"


def _node_color(inv: Investigation, address: str) -> str:
    kind = _kind(inv, address)
    if kind == "focus":
        return focus_color(inv, address)
    return {"member": MEMBER, "service_member": SERVICE, "exchange": EXCHANGE, "outside": OUTSIDE}[kind]


def _save(fig, out_dir: Path, key: str, title: str) -> GraphFile:
    out_dir.mkdir(parents=True, exist_ok=True)
    png, svg = out_dir / f"{key}.png", out_dir / f"{key}.svg"
    fig.savefig(png, dpi=DPI, bbox_inches="tight", pad_inches=0.15)
    fig.savefig(svg, bbox_inches="tight", pad_inches=0.15)
    width, height = (int(round(v * DPI)) for v in fig.get_size_inches())
    plt.close(fig)
    return GraphFile(key=key, title=title, png=png, svg=svg, width=width, height=height)


# --- network drawing ---------------------------------------------------------------


@dataclass
class Node:
    address: str
    x: float
    y: float
    kind: str  # focus, member, service_member, exchange, outside, isolated
    color: str
    radius: float
    label: str
    inside: str = ""  # short text drawn on the node ("#4")


@dataclass
class Edge:
    src: str
    dst: str
    amount: Decimal
    count: int
    strong: bool = False  # between two focus wallets
    faint: bool = False  # context only (drawn lighter)
    width_amount: Decimal | None = None  # amount the line width is based on (default: `amount`)
    dashed: bool = False  # a tie to an outside wallet: thin, dashed, no label

    @property
    def weight(self) -> float:
        return float(self.width_amount if self.width_amount is not None else self.amount)


class _Placer:
    """Puts labels where they do not cover nodes or each other."""

    def __init__(self, ax):
        self.ax = ax
        self.fig = ax.figure
        self.fig.canvas.draw()
        self.renderer = self.fig.canvas.get_renderer()
        self.taken: list[Bbox] = []  # nodes and labels: never to be covered
        self.lines: list[Bbox] = []  # points along the arrows: node labels keep off them

    def block(self, x: float, y: float, radius: float, line: bool = False) -> None:
        (x0, y0), (x1, y1) = self.ax.transData.transform([(x - radius, y - radius), (x + radius, y + radius)])
        (self.lines if line else self.taken).append(Bbox.from_extents(x0, y0, x1, y1))

    @staticmethod
    def _overlap(box: Bbox, others: list[Bbox]) -> float:
        total = 0.0
        for other in others:
            w = min(box.x1, other.x1) - max(box.x0, other.x0)
            h = min(box.y1, other.y1) - max(box.y0, other.y0)
            if w > 0 and h > 0:
                total += w * h
        return total

    def place(self, candidates: list[tuple[float, float, str, str]], text: str, avoid_lines: bool = False, **kwargs):
        """Draw `text` at the first candidate (x, y, ha, va) that is free; else the least covered one."""
        best, best_cost = None, None
        for x, y, ha, va in candidates:
            artist = self.ax.text(x, y, text, ha=ha, va=va, **kwargs)
            box = artist.get_window_extent(self.renderer).expanded(1.04, 1.12)
            cost = self._overlap(box, self.taken)
            if avoid_lines:
                cost += 0.5 * self._overlap(box, self.lines)
            if cost == 0:
                if best is not None:
                    best[0].remove()
                self.taken.append(box)
                return artist
            if best_cost is None or cost < best_cost:
                if best is not None:
                    best[0].remove()
                best, best_cost = (artist, box), cost
            else:
                artist.remove()
        if best is not None:
            self.taken.append(best[1])
            return best[0]
        return None


def _bezier(p0, p2, rad: float):
    """Control point and a point function of matplotlib's "arc3" curve from p0 to p2."""
    (x1, y1), (x2, y2) = p0, p2
    cx, cy = (x1 + x2) / 2 + rad * (y2 - y1), (y1 + y2) / 2 - rad * (x2 - x1)

    def at(t: float) -> tuple[float, float]:
        a, b, c = (1 - t) ** 2, 2 * t * (1 - t), t**2
        return a * x1 + b * cx + c * x2, a * y1 + b * cy + c * y2

    return at


def _relax(nodes: dict[str, Node], fixed: set[str], gap: float = 0.12, rounds: int = 200) -> None:
    """Push overlapping nodes apart (the fixed ones stay)."""
    items = list(nodes.values())
    for _ in range(rounds):
        moved = False
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                dx, dy = b.x - a.x, b.y - a.y
                dist = math.hypot(dx, dy) or 1e-6
                need = a.radius + b.radius + gap
                if dist >= need:
                    continue
                push = (need - dist) / 2 + 1e-3
                ux, uy = dx / dist, dy / dist
                fa, fb = a.address in fixed, b.address in fixed
                if fa and fb:
                    continue
                if not fa:
                    a.x -= ux * push * (2 if fb else 1)
                    a.y -= uy * push * (2 if fb else 1)
                if not fb:
                    b.x += ux * push * (2 if fa else 1)
                    b.y += uy * push * (2 if fa else 1)
                moved = True
        if not moved:
            break


def _draw_network(ax, nodes: dict[str, Node], edges: list[Edge], token: str, pad: float = 0.9, max_width: float = 4.6):
    xs, ys = [n.x for n in nodes.values()], [n.y for n in nodes.values()]
    ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.set_aspect("equal")
    ax.set_axis_off()
    fig = ax.figure
    fig.canvas.draw()
    px_per_unit = ax.transData.transform((1, 0))[0] - ax.transData.transform((0, 0))[0]
    pt_per_unit = px_per_unit * 72 / fig.dpi

    top = max((e.weight for e in edges if not e.dashed), default=1.0) or 1.0
    pairs = {(e.src, e.dst) for e in edges}
    placer = _Placer(ax)
    for n in nodes.values():
        placer.block(n.x, n.y, n.radius * 1.08)

    label_jobs = []
    for e in sorted(edges, key=lambda e: (not e.dashed, e.strong, e.amount)):
        a, b = nodes[e.src], nodes[e.dst]
        share = min(1.0, math.sqrt(e.weight / top))
        both = (e.dst, e.src) in pairs
        rad = 0.2 if both else 0.08
        if e.dashed:
            ax.add_patch(
                FancyArrowPatch(
                    (a.x, a.y), (b.x, b.y), arrowstyle="-|>,head_length=0.45,head_width=0.2", mutation_scale=11,
                    lw=0.9, color=MUTED, alpha=0.55, linestyle=(0, (4, 3)), connectionstyle=f"arc3,rad={rad}",
                    shrinkA=a.radius * pt_per_unit + 1.5, shrinkB=b.radius * pt_per_unit + 2.5, zorder=1,
                )
            )  # fmt: skip
            continue
        width = 0.8 + (max_width - 0.8) * share
        color = INK if e.strong else (MUTED if e.faint else INK_2)
        ax.add_patch(
            FancyArrowPatch(
                (a.x, a.y),
                (b.x, b.y),
                arrowstyle=f"-|>,head_length={0.5 + 0.22 * share:.2f},head_width={0.22 + 0.14 * share:.2f}",
                mutation_scale=14,
                lw=width,
                color=color,
                alpha=0.95 if e.strong else (0.5 if e.faint else 0.62),
                connectionstyle=f"arc3,rad={rad}",
                shrinkA=a.radius * pt_per_unit + 1.5,
                shrinkB=b.radius * pt_per_unit + 2.5,
                zorder=3 if e.strong else 2,
                capstyle="round",
            )
        )
        at = _bezier((a.x, a.y), (b.x, b.y), rad)
        label_jobs.append((e, at, (b.x - a.x, b.y - a.y)))
        for k in range(1, 16):
            placer.block(*at(k / 16), 0.05, line=True)

    for n in nodes.values():
        if n.kind in ("exchange", "service_member"):
            side = n.radius * 1.75
            ax.add_patch(
                FancyBboxPatch(
                    (n.x - side / 2, n.y - side / 2), side, side,
                    boxstyle=f"round,pad=0,rounding_size={side * 0.18}",
                    fc=n.color, ec=SURFACE, lw=2, zorder=4,
                )
            )  # fmt: skip
        else:
            ax.add_patch(Circle((n.x, n.y), n.radius, fc=n.color, ec=SURFACE, lw=2, zorder=4))
        if n.inside:
            dark_text = n.kind in ("exchange", "isolated", "outside")
            ax.text(
                n.x, n.y, n.inside, ha="center", va="center", zorder=5, fontweight="bold",
                fontsize=11.5 if n.kind == "focus" else 9, color=INK if dark_text else "#ffffff",
            )  # fmt: skip

    # Node labels: outward from the middle of the picture first.
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    for n in sorted(nodes.values(), key=lambda n: (n.kind != "focus", -n.radius)):
        angle = math.atan2(n.y - cy, n.x - cx) if math.hypot(n.x - cx, n.y - cy) > 0.2 else -math.pi / 2
        candidates = []
        for turn in (0, 35, -35, 70, -70, 110, -110, 150, -150, 180):
            a = angle + math.radians(turn)
            dx, dy = math.cos(a), math.sin(a)
            off = n.radius * (1.3 if n.kind in ("exchange", "service_member") else 1.0) + 0.09
            ha = "left" if dx > 0.4 else ("right" if dx < -0.4 else "center")
            va = "bottom" if dy > 0.4 else ("top" if dy < -0.4 else "center")
            candidates.append((n.x + dx * off, n.y + dy * off, ha, va))
        placer.place(
            candidates, n.label, avoid_lines=True, fontsize=9.4 if n.kind == "focus" else 8.2, color=INK,
            fontweight="bold" if n.kind == "focus" else "normal", linespacing=1.25, zorder=6,
        )  # fmt: skip

    # Edge labels, largest first, so the important ones get the best spot.
    for e, at, (dx, dy) in sorted(label_jobs, key=lambda job: (job[0].strong, job[0].amount), reverse=True):
        text = fmt_amount(e.amount, token) + (f" · {e.count} tx" if e.count > 1 else "")
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length * 0.16, dx / length * 0.16  # a step sideways off the arrow
        spots = [(*at(t), "center", "center") for t in (0.5, 0.42, 0.58, 0.34, 0.66, 0.27, 0.73, 0.2, 0.8)]
        for t in (0.5, 0.38, 0.62):
            x, y = at(t)
            spots += [(x + nx, y + ny, "center", "center"), (x - nx, y - ny, "center", "center")]
        placer.place(
            spots,
            text, fontsize=8 if e.strong else 7.3, color=INK if e.strong else INK_2, zorder=7,
            fontweight="bold" if e.strong else "normal",
            bbox={"boxstyle": "round,pad=0.16", "fc": SURFACE, "ec": "none", "alpha": 0.9},
        )  # fmt: skip


def _legend_handles(inv: Investigation, kinds: set[str]) -> list:
    handles = []
    for i, p in enumerate(inv.focus):
        handles.append(
            Line2D([], [], marker="o", ls="", ms=11, mfc=FOCUS_COLORS[i % len(FOCUS_COLORS)], mec=SURFACE,
                   label=f"Key wallet {short(p.address, p.index)}")
        )  # fmt: skip
    for kind, color, marker, label in (
        ("member", MEMBER, "o", "Other wallet of the list"),
        ("service_member", SERVICE, "s", "List wallet acting like an exchange / service"),
        ("isolated", ISOLATED, "o", "List wallet with no link found"),
        ("exchange", EXCHANGE, "s", "Exchange / service (outside the list)"),
        ("outside", OUTSIDE, "o", "Outside wallet (intermediary / shared)"),
    ):
        if kind in kinds:
            handles.append(Line2D([], [], marker=marker, ls="", ms=9, mfc=color, mec=SURFACE, label=label))
    return handles


def _legend(ax, inv: Investigation, kinds: set[str], note: str, loc="lower center") -> None:
    ax.legend(
        handles=_legend_handles(inv, kinds), loc="upper center", bbox_to_anchor=(0.5, 0.0), ncol=3, fontsize=8.6,
        handletextpad=0.4, columnspacing=1.6, labelcolor=INK_2,
    )  # fmt: skip
    ax.text(0.5, -0.085, note, transform=ax.transAxes, ha="center", va="top", fontsize=8, color=MUTED)


# --- a) focus network -----------------------------------------------------------------


def _token_history(inv: Investigation, address: str) -> list[TransferRec]:
    return [t for t in inv.focus_history.get(address, []) if _is_token(inv, t)]


def _is_token(inv: Investigation, t: TransferRec) -> bool:
    if inv.token_contract is not None:
        return t.token_contract == inv.token_contract
    return t.token_contract is None and t.token_symbol.upper() == inv.token.upper()


def focus_edges(inv: Investigation) -> dict[tuple[str, str], tuple[Decimal, int]]:
    """(from, to) -> (amount, count) of the token for every pair that involves a focus wallet."""
    out: dict[tuple[str, str], list] = defaultdict(lambda: [Decimal(0), 0])
    seen: set[tuple] = set()
    for p in inv.focus:
        for t in _token_history(inv, p.address):
            key = (t.tx_hash, t.from_address, t.to_address, t.amount, t.timestamp)
            if key in seen:  # a transfer between two focus wallets is in both histories
                continue
            seen.add(key)
            cell = out[(t.from_address, t.to_address)]
            cell[0] += t.amount
            cell[1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def focus_network(inv: Investigation, max_outside: int = 9):
    _style()
    index = _index(inv)
    focus = [p.address for p in inv.focus]
    flows = focus_edges(inv)
    linked: dict[str, set[str]] = defaultdict(set)  # other address -> focus wallets it dealt with
    volume: dict[str, Decimal] = defaultdict(Decimal)
    for (src, dst), (amount, _) in flows.items():
        for f, other in ((src, dst), (dst, src)):
            if f in focus and other not in focus:
                linked[other].add(f)
                volume[other] += amount

    members = [a for a in linked if a in index]
    outside = sorted(
        (a for a in linked if a not in index and len(linked[a]) >= 2),
        key=lambda a: (len(linked[a]), volume[a]),
        reverse=True,
    )[:max_outside]
    shown = set(focus) | set(members) | set(outside)

    # Layout: focus wallets on a circle. A wallet tied to one of them sits behind
    # it (outside), one tied to two sits on the arc between them, one tied to all
    # sits in the middle.
    n = len(focus)
    base = {a: 90 + 360 * i / n for i, a in enumerate(focus)}
    r_focus = 2.0 if n > 1 else 0.0
    r_outer = 4.1
    nodes: dict[str, Node] = {}
    for a in focus:
        ang = math.radians(base[a])
        nodes[a] = Node(a, r_focus * math.cos(ang), r_focus * math.sin(ang), "focus", focus_color(inv, a), 0.3,
                        node_name(inv, a), f"#{index[a]}")  # fmt: skip
    buckets: dict[frozenset, list[str]] = defaultdict(list)
    for a in members + outside:
        buckets[frozenset(linked[a])].append(a)
    for key, group in buckets.items():
        group.sort(key=lambda a: (a not in index, -volume[a]))
        angles = sorted(base[f] for f in key)
        if len(key) == 1:
            step = min(24.0, 56.0 / max(1, len(group) - 1)) if len(group) > 1 else 0.0
            for i, a in enumerate(group):
                ang = math.radians(angles[0] + (i - (len(group) - 1) / 2) * step)
                nodes[a] = _other_node(inv, a, r_outer * math.cos(ang), r_outer * math.sin(ang))
        elif len(key) == n and n > 2:
            for i, a in enumerate(group):
                ang = math.radians(90 + 360 * i / len(group) + 180 / n)
                radius = 0.0 if len(group) == 1 else 0.62
                nodes[a] = _other_node(inv, a, radius * math.cos(ang), radius * math.sin(ang))
        else:
            # The arc between the two focus wallets, the shorter way round.
            lo, hi = angles[0], angles[-1]
            if hi - lo > 180:
                lo, hi = hi, lo + 360
            margin = 36.0 if hi - lo > 90 else (hi - lo) * 0.3
            lo, hi = lo + margin, hi - margin
            for i, a in enumerate(group):
                frac = 0.5 if len(group) == 1 else i / (len(group) - 1)
                # Alternate two radii so neighbours on the arc do not touch.
                radius = 3.25 + (0.75 if i % 2 else 0.0) if len(group) > 3 else 3.5
                ang = math.radians(lo + (hi - lo) * frac)
                nodes[a] = _other_node(inv, a, radius * math.cos(ang), radius * math.sin(ang))
    _relax(nodes, fixed=set(focus), gap=0.35)

    edges: list[Edge] = []
    for (src, dst), (amount, count) in flows.items():
        if src in shown and dst in shown:
            edges.append(Edge(src, dst, amount, count, strong=src in focus and dst in focus))
    # Context: transfers between the other list wallets shown, unless both are
    # exchange-like (two exchanges paying each other says nothing here).
    for link in inv.links_all.direct:
        a, b = link.from_address, link.to_address
        if a in shown and b in shown and a not in focus and b not in focus:
            if not (is_service(inv, a) and is_service(inv, b)):
                edges.append(Edge(a, b, link.total, link.count, faint=True))

    fig, ax = plt.subplots(figsize=(13, 12.4))
    _draw_network(ax, nodes, edges, inv.token)
    ax.set_title(
        f"How the {len(focus)} key wallets are connected ({inv.token} on {inv.chain.value.capitalize()})", pad=10
    )
    _legend(
        ax, inv, {nd.kind for nd in nodes.values()},
        "Arrow = direction of the money; width grows with the amount. A wallet drawn between two key wallets dealt with both.",
    )  # fmt: skip
    return fig


def _other_node(inv: Investigation, address: str, x: float, y: float) -> Node:
    index = _index(inv)
    kind = _kind(inv, address)
    inside = f"#{index[address]}" if address in index else ""
    radius = 0.22 if address in index else 0.15
    return Node(address, x, y, kind, _node_color(inv, address), radius, node_name(inv, address), inside)


# --- b) flow of one wallet ---------------------------------------------------------------


def _ribbon(ax, x0, x1, y0a, y0b, y1a, y1b, color, alpha=0.42):
    """A band from [y0a, y0b] at x0 to [y1a, y1b] at x1 with smooth sides."""
    xm = (x0 + x1) / 2
    verts = [
        (x0, y0a), (xm, y0a), (xm, y1a), (x1, y1a),
        (x1, y1b), (xm, y1b), (xm, y0b), (x0, y0b), (x0, y0a),
    ]  # fmt: skip
    codes = [MplPath.MOVETO, *[MplPath.CURVE4] * 3, MplPath.LINETO, *[MplPath.CURVE4] * 3, MplPath.CLOSEPOLY]
    ax.add_patch(PathPatch(MplPath(verts, codes), fc=color, ec="none", alpha=alpha, zorder=1))


def flow(inv: Investigation, profile: FocusProfile, top: int = 9):
    """Sources -> the wallet -> destinations, band height = amount."""
    _style()
    token = inv.token
    totals = next((t for t in profile.totals if _same_token(inv, t.token_symbol, t.token_contract)), None)
    total_in = totals.total_in if totals else Decimal(0)
    total_out = totals.total_out if totals else Decimal(0)

    def side(parties, total, count_total):
        rows = [(p.address, p.amount, p.count) for p in parties[:top]]
        rest = total - sum((r[1] for r in rows), Decimal(0))
        rest_count = count_total - sum(r[2] for r in rows)
        if rest > 0:
            rows.append((None, rest, rest_count))
        return rows

    sources = side(profile.top_senders, total_in, totals.count_in if totals else 0)
    targets = side(profile.top_receivers, total_out, totals.count_out if totals else 0)
    color = focus_color(inv, profile.address)

    fig, ax = plt.subplots(figsize=(13, 7.6))
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    gap = 0.012
    full = 0.84  # height available to a column
    slot_min = 0.058  # room for a two-line label
    scale_total = float(max(total_in, total_out)) or 1.0

    def slots(rows):
        """(bar height, slot height) per row: a small bar still gets a slot tall enough for its label."""
        room = full - gap * (len(rows) - 1)
        bars = [room * float(a) / scale_total for _, a, _ in rows]
        slot = [max(b, slot_min) for b in bars]
        over = sum(slot) - room
        if over > 0:
            big = sum(h - slot_min for h in slot if h > slot_min) or 1.0
            shrink = min(1.0, over / big)
            slot = [h - (h - slot_min) * shrink if h > slot_min else h for h in slot]
            bars = [min(b, h) for b, h in zip(bars, slot)]
        return [(max(b, 0.004), h) for b, h in zip(bars, slot)]

    def draw_side(rows, x_node, x_wallet, label_x, ha, incoming: bool, wallet_span):
        sized = slots(rows)
        height = sum(h for _, h in sized) + gap * (len(sized) - 1)
        y = 0.47 + height / 2
        w0, w1 = wallet_span
        wy = w1
        total = float(sum((a for _, a, _ in rows), Decimal(0))) or 1.0
        for (address, amount, count), (bar, slot) in zip(rows, sized):
            fill = OUTSIDE if address is None else _node_color(inv, address)
            top_y = y - (slot - bar) / 2
            ax.add_patch(Rectangle((x_node - 0.006, top_y - bar), 0.012, bar, fc=fill, ec="none", zorder=3))
            wh = (w1 - w0) * float(amount) / total
            if incoming:
                _ribbon(ax, x_node + 0.006, x_wallet, top_y, top_y - bar, wy, wy - wh, fill)
            else:
                _ribbon(ax, x_wallet, x_node - 0.006, wy, wy - wh, top_y, top_y - bar, fill)
            wy -= wh
            share = f"{100 * float(amount) / total:.1f}%"
            if address is None:
                name = "Other wallets"
                detail = f"{fmt_amount(amount, token)} · {share} · {count} tx"
            else:
                name = node_name(inv, address, tag=False)
                tag = inv.labels.get(address)
                if tag:
                    name += f"  ·  {short_tag(tag)}"
                detail = f"{fmt_amount(amount, token)} · {share} · {count} tx"
            mid = y - slot / 2
            ax.text(label_x, mid + 0.003, name, ha=ha, va="bottom", fontsize=8.6, color=INK,
                    fontweight="bold" if address and focus_color(inv, address) else "normal")  # fmt: skip
            ax.text(label_x, mid - 0.005, detail, ha=ha, va="top", fontsize=7.8, color=INK_2)
            y -= slot + gap

    wallet_h = 0.6
    w_in = wallet_h * float(total_in) / scale_total
    w_out = wallet_h * float(total_out) / scale_total
    span_in = (0.47 - w_in / 2, 0.47 + w_in / 2)
    span_out = (0.47 - w_out / 2, 0.47 + w_out / 2)
    ax.add_patch(
        FancyBboxPatch((0.47, 0.47 - wallet_h / 2), 0.06, wallet_h, boxstyle="round,pad=0,rounding_size=0.01",
                       fc=color, ec="none", zorder=4)
    )  # fmt: skip
    if sources:
        draw_side(sources, 0.27, 0.47, 0.258, "right", True, span_in)
    if targets:
        draw_side(targets, 0.73, 0.53, 0.742, "left", False, span_out)
    ax.text(0.5, 0.47, f"#{profile.index}", ha="center", va="center", color="#ffffff", fontsize=16, fontweight="bold",
            zorder=5)  # fmt: skip
    ax.text(0.5, 0.47 + wallet_h / 2 + 0.045, short(profile.address), ha="center", va="bottom", fontsize=9.8,
            color=INK, fontweight="bold")  # fmt: skip
    ax.text(0.5, 0.47 + wallet_h / 2 + 0.012, f"{profile.counterparty_count:,} counterparties", ha="center",
            va="bottom", fontsize=8.2, color=INK_2)  # fmt: skip
    count_in = totals.count_in if totals else 0
    count_out = totals.count_out if totals else 0
    ax.text(0.27, 0.955, f"SOURCES  ·  received {fmt_amount(total_in, token)} in {count_in:,} transfers",
            ha="center", va="bottom", fontsize=9.2, color=INK_2, fontweight="bold")  # fmt: skip
    ax.text(0.73, 0.955, f"DESTINATIONS  ·  sent {fmt_amount(total_out, token)} in {count_out:,} transfers",
            ha="center", va="bottom", fontsize=9.2, color=INK_2, fontweight="bold")  # fmt: skip
    ax.set_title(f"Where the {token} of key wallet {short(profile.address, profile.index)} came from and went", pad=14)
    shown = {a for a, _, _ in sources + targets if a}
    kinds = {_kind(inv, a) for a in shown} - {"focus"}
    handles = [h for h in _legend_handles(inv, kinds | {"outside"})]
    ax.legend(
        handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.02), ncol=4, fontsize=8,
        handletextpad=0.4, columnspacing=1.4, labelcolor=INK_2,
    )  # fmt: skip
    return fig


def _same_token(inv: Investigation, symbol: str, contract: str | None) -> bool:
    if inv.token_contract is not None:
        return contract == inv.token_contract
    return contract is None and symbol.upper() == inv.token.upper()


# --- c) monthly timeline of one wallet --------------------------------------------------------


def _month_date(period: str) -> datetime:
    return datetime(int(period[:4]), int(period[5:7]), 1)


def _month_axis(ax, first: datetime, last: datetime) -> None:
    months = (last.year - first.year) * 12 + last.month - first.month + 1
    interval = 1 if months <= 14 else (2 if months <= 26 else (3 if months <= 40 else 6))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=interval))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax.xaxis.set_minor_locator(mdates.MonthLocator())
    ax.tick_params(axis="x", which="minor", length=2, color=AXIS)


def timeline(inv: Investigation, profile: FocusProfile):
    """Monthly in/out bars, and below them the amount held after every transfer."""
    _style()
    token = inv.token
    rows = profile.monthly.get(token, [])
    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(13, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.1}
    )
    title = f"Monthly {token} activity of key wallet {short(profile.address, profile.index)}"
    if not rows:
        ax.set_title(title, pad=12)
        ax.text(0.5, 0.5, f"No {token} transfers", transform=ax.transAxes, ha="center", color=MUTED)
        return fig
    xs = [_month_date(r.period) + timedelta(days=14) for r in rows]
    ins = [float(r.amount_in) for r in rows]
    outs = [-float(r.amount_out) for r in rows]
    width = 19
    ax.bar(xs, ins, width=width, color=INFLOW, label="Received in the month", zorder=3)
    ax.bar(xs, outs, width=width, color=OUTFLOW, label="Sent in the month", zorder=3)
    ax.axhline(0, color=AXIS, lw=1, zorder=4)
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(lambda v, _: fmt_compact(abs(v)))
    ax.set_ylabel(f"{token} per month", fontsize=8.8)
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(axis="x", which="both", length=0)
    peak = max(range(len(rows)), key=lambda i: ins[i] - outs[i])
    limit = max(max(ins), -min(outs)) * 1.3 or 1
    ax.set_ylim(-limit, limit)
    ax.annotate(
        f"busiest month: {_month_date(rows[peak].period):%b %Y}\n"
        f"in {fmt_amount(ins[peak])} · out {fmt_amount(-outs[peak])} {token}",
        (xs[peak], ins[peak]), xytext=(0, 7), textcoords="offset points", ha="center", va="bottom", fontsize=7.8,
        color=INK_2,
    )  # fmt: skip
    ax.legend(loc="lower right", ncol=2, fontsize=8.4, labelcolor=INK_2, handlelength=1.2, borderaxespad=0.3)
    ax.set_title(title, pad=24)
    total = next((t for t in profile.totals if _same_token(inv, t.token_symbol, t.token_contract)), None)
    if total:
        ax.text(
            0.5, 1.025,
            f"total received {fmt_amount(total.total_in, token)} in {total.count_in:,} transfers   ·   "
            f"total sent {fmt_amount(total.total_out, token)} in {total.count_out:,} transfers",
            transform=ax.transAxes, ha="center", va="bottom", fontsize=8.4, color=INK_2,
        )  # fmt: skip

    # Amount held after each transfer (the history is complete, so this is the real balance).
    color = focus_color(inv, profile.address) or INK
    history = _token_history(inv, profile.address)
    step_x, step_y, held = [], [], 0.0
    for t in history:
        held += float(t.amount) if t.to_address == profile.address else -float(t.amount)
        step_x.append(t.timestamp.replace(tzinfo=None))
        step_y.append(max(held, 0.0) if abs(held) < 1e-6 else held)
    if step_x:
        step_x = [step_x[0]] + step_x
        step_y = [0.0] + step_y
        ax2.step(step_x, step_y, where="post", color=color, lw=1.4, solid_joinstyle="round", zorder=3)
        ax2.fill_between(step_x, step_y, step="post", color=color, alpha=0.12, zorder=2, lw=0)
        peak_i = max(range(len(step_y)), key=lambda i: step_y[i])
        ax2.annotate(
            f"most held at once: {fmt_amount(step_y[peak_i], token)} ({step_x[peak_i]:%d %b %Y})",
            (step_x[peak_i], step_y[peak_i]), xytext=(8, 0), textcoords="offset points", ha="left", va="center",
            fontsize=7.6, color=INK_2,
        )  # fmt: skip
        ax2.set_ylim(0, max(max(step_y) * 1.3, 1))
    ax2.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    ax2.set_axisbelow(True)
    ax2.yaxis.set_major_formatter(lambda v, _: fmt_compact(v))
    ax2.set_ylabel(f"{token} held\n(after each transfer)", fontsize=8.8)
    _month_axis(ax2, _month_date(rows[0].period), _month_date(rows[-1].period))
    ax2.set_xlim(_month_date(rows[0].period) - timedelta(days=10), _month_date(rows[-1].period) + timedelta(days=41))
    return fig


# --- d) joint timeline ----------------------------------------------------------------------------


def joint_timeline(inv: Investigation):
    """One lane per key wallet over time: a dot per transfer, connectors where they paid each other."""
    _style()
    token = inv.token
    focus = inv.focus
    fig, ax = plt.subplots(figsize=(13, 2.1 + 1.75 * len(focus)))
    lane = {p.address: len(focus) - 1 - i for i, p in enumerate(focus)}
    histories = {p.address: _token_history(inv, p.address) for p in focus}
    top = max((float(t.amount) for ts in histories.values() for t in ts), default=1.0) or 1.0
    times = [t.timestamp for ts in histories.values() for t in ts]
    if not times:
        ax.text(0.5, 0.5, f"No {token} transfers", transform=ax.transAxes, ha="center", color=MUTED)
        ax.set_axis_off()
        return fig
    start, end = min(times), max(times)
    span = end - start
    ax.set_xlim(start - span * 0.02, end + span * 0.02)
    ax.set_ylim(-0.55, len(focus) - 0.3)

    def size(amount) -> float:
        return 7 + 330 * math.sqrt(float(amount) / top)

    for p in focus:
        y = lane[p.address]
        color = focus_color(inv, p.address)
        ts = histories[p.address]
        ax.axhspan(y - 0.42, y + 0.42, color=color, alpha=0.045, lw=0, zorder=0)
        if ts:
            ax.plot([ts[0].timestamp, ts[-1].timestamp], [y, y], color=color, lw=1.2, alpha=0.55, zorder=1,
                    solid_capstyle="round")  # fmt: skip
        incoming = [t for t in ts if t.to_address == p.address]
        outgoing = [t for t in ts if t.from_address == p.address]
        ax.scatter([t.timestamp for t in incoming], [y + 0.2] * len(incoming), s=[size(t.amount) for t in incoming],
                   color=color, alpha=0.5, lw=0, zorder=3)  # fmt: skip
        ax.scatter([t.timestamp for t in outgoing], [y - 0.2] * len(outgoing), s=[size(t.amount) for t in outgoing],
                   facecolors="none", edgecolors=color, alpha=0.75, lw=1.1, zorder=3)  # fmt: skip
        total_in = sum((t.amount for t in incoming), Decimal(0))
        total_out = sum((t.amount for t in outgoing), Decimal(0))
        ax.text(
            0.0, (y + 0.44 + 0.55) / (len(focus) - 0.3 + 0.55), f"  {short(p.address, p.index)}",
            transform=ax.transAxes, ha="left", va="bottom", fontsize=9.5, fontweight="bold", color=INK,
        )  # fmt: skip
        ax.text(
            0.15, (y + 0.44 + 0.55) / (len(focus) - 0.3 + 0.55),
            f"received {fmt_amount(total_in, token)} ({len(incoming)} tx)  ·  sent {fmt_amount(total_out, token)} "
            f"({len(outgoing)} tx)  ·  {ts[0].timestamp:%d %b %Y} to {ts[-1].timestamp:%d %b %Y}" if ts else "",
            transform=ax.transAxes, ha="left", va="bottom", fontsize=8, color=INK_2,
        )  # fmt: skip

    between = [t for t in inv.focus_transfers if _is_token(inv, t)]
    for t in between:
        y0 = lane[t.from_address] - 0.2
        y1 = lane[t.to_address] + 0.2
        ax.add_patch(
            FancyArrowPatch(
                (mdates.date2num(t.timestamp), y0), (mdates.date2num(t.timestamp), y1),
                arrowstyle="-|>", mutation_scale=7, lw=0.9, color=INK, alpha=0.6, zorder=2, shrinkA=2, shrinkB=2,
            )
        )  # fmt: skip
    pair_totals: dict[tuple[str, str], list[TransferRec]] = defaultdict(list)
    for t in between:
        pair_totals[(t.from_address, t.to_address)].append(t)
    index = _index(inv)
    for (src, dst), ts in pair_totals.items():
        # Put the note in the widest quiet stretch between the connectors, else after the last one.
        stamps = [mdates.date2num(t.timestamp) for t in ts]
        x0, x1 = ax.get_xlim()
        gaps = sorted(((b - a, (a + b) / 2) for a, b in zip(stamps, stamps[1:] + [x1])), reverse=True)
        mid = gaps[0][1] if gaps and gaps[0][0] > (x1 - x0) * 0.16 else None
        if mid is None:
            mid = stamps[0] - (x1 - x0) * 0.09 if stamps[0] - x0 > (x1 - x0) * 0.2 else stamps[-1] + (x1 - x0) * 0.09
        y = (lane[src] + lane[dst]) / 2
        total = sum((t.amount for t in ts), Decimal(0))
        ax.text(
            mid, y,
            f"#{index[src]} → #{index[dst]}: {len(ts)} transfers, {fmt_amount(total, token)}\n"
            f"{ts[0].timestamp:%d %b %Y} to {ts[-1].timestamp:%d %b %Y}",
            ha="center", va="center", fontsize=7.6, color=INK, zorder=5, linespacing=1.3,
            bbox={"boxstyle": "round,pad=0.3", "fc": SURFACE, "ec": AXIS, "lw": 0.6, "alpha": 0.95},
        )  # fmt: skip

    ax.set_yticks([])
    for s in ("left", "top", "right"):
        ax.spines[s].set_visible(False)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax.xaxis.set_minor_locator(mdates.MonthLocator())
    ax.tick_params(axis="x", which="minor", length=2, color=AXIS)
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.set_title(f"Activity of the key wallets over time ({token} transfers)", pad=14)

    ref = [1000, 10000, 50000] if top >= 50000 else ([100, 1000, 5000] if top >= 5000 else [10, 100, 1000])
    ref = [r for r in ref if r <= top * 1.01] or [top]
    handles = [
        Line2D([], [], marker="o", ls="", ms=8, mfc=MUTED, mec="none", alpha=0.6, label="received (above the line)"),
        Line2D([], [], marker="o", ls="", ms=8, mfc="none", mec=MUTED, mew=1.1, label="sent (below the line)"),
        Line2D([], [], marker="|", ls="", ms=11, mew=1.2, color=INK, alpha=0.7, label="payment between two key wallets"),
    ]
    handles += [
        Line2D([], [], marker="o", ls="", ms=math.sqrt(size(r)), mfc=AXIS, mec="none", label=f"{fmt_amount(r, token)}")
        for r in ref
    ]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=len(handles), fontsize=7.8,
              labelcolor=INK_2, handletextpad=0.5, columnspacing=1.5)  # fmt: skip
    return fig


# --- e) the whole list --------------------------------------------------------------------------------


def list_network(inv: Investigation, max_outside: int = 4):
    """Every list member on a circle (in list order); the few outside wallets that tie
    several non-exchange members together sit in the middle."""
    _style()
    members = inv.members
    n = len(members)
    index = _index(inv)
    linked_members = {m.address for m in members if m.partners > 0 or m.group is not None}
    nodes: dict[str, Node] = {}
    radius = 3.6
    for i, m in enumerate(members):
        ang = math.radians(90 - 360 * i / n)
        color = focus_color(inv, m.address)
        if color:
            kind = "focus"
        elif m.likely_service or is_service(inv, m.address):
            kind, color = "service_member", SERVICE
        elif m.address not in linked_members:
            kind, color = "isolated", ISOLATED
        else:
            kind, color = "member", GROUP_COLORS[((m.group or 1) - 1) % len(GROUP_COLORS)]
        nodes[m.address] = Node(
            m.address, radius * math.cos(ang), radius * math.sin(ang), kind, color, 0.33 if kind == "focus" else 0.26,
            node_name(inv, m.address), f"#{m.index}",
        )  # fmt: skip

    personal = {a for a, nd in nodes.items() if nd.kind != "service_member"}
    ties: dict[str, dict[tuple[str, str], tuple[Decimal, int]]] = defaultdict(dict)
    for s in inv.links_all.shared:
        if s.token_symbol.upper() != inv.token.upper() or is_service(inv, s.address):
            continue  # an exchange in common says little
        for share in s.members:
            if share.address in personal:
                edge = (s.address, share.address) if s.role.value == "common_source" else (share.address, s.address)
                ties[s.address][edge] = (share.amount, share.count)

    def touched(a: str) -> set[str]:
        return {x for edge in ties[a] for x in edge if x in index}

    chosen = sorted(
        (a for a in ties if len(touched(a)) >= 2),
        key=lambda a: (len(touched(a)), sum(v[0] for v in ties[a].values())),
        reverse=True,
    )[:max_outside]
    for a in chosen:
        near = touched(a)
        x = sum(nodes[t].x for t in near) / len(near) * 0.42
        y = sum(nodes[t].y for t in near) / len(near) * 0.42
        nodes[a] = Node(a, x, y, "outside", OUTSIDE, 0.15, node_name(inv, a))
    _relax(nodes, fixed={m.address for m in members}, gap=1.1)

    edges: list[Edge] = []
    focus = {p.address for p in inv.focus}
    for i, row in enumerate(inv.matrix):
        for j, amount in enumerate(row):
            if amount > 0:
                a, b = members[i].address, members[j].address
                edges.append(Edge(a, b, amount, inv.matrix_counts[i][j], strong=a in focus and b in focus))
    for a in chosen:
        for (src, dst), (amount, count) in ties[a].items():
            edges.append(Edge(src, dst, amount, count, dashed=True))

    fig, ax = plt.subplots(figsize=(13, 12.8))
    _draw_network(ax, nodes, edges, inv.token, pad=1.25)
    ax.set_title(f"Money flows inside the list of {n} wallets ({inv.token})", pad=10)
    note = "Solid arrows: direct transfers between list wallets (width grows with the amount)."
    if chosen:
        note += " Dashed: an outside wallet that several non-exchange members dealt with."
    _legend(ax, inv, {nd.kind for nd in nodes.values()}, note)
    return fig


# --- f) matrix ---------------------------------------------------------------------------------------------


def matrix_heatmap(inv: Investigation):
    _style()
    n = len(inv.members)
    values = [[float(v) for v in row] for row in inv.matrix]
    positive = [v for row in values for v in row if v > 0]
    fig, ax = plt.subplots(figsize=(11.8, 10.6))
    cmap = LinearSegmentedColormap.from_list("seq", SEQ)
    if positive:
        norm = LogNorm(vmin=max(1.0, min(positive)), vmax=max(max(positive), 10.0))
        shown = [[v if v > 0 else float("nan") for v in row] for row in values]
        image = ax.imshow(shown, cmap=cmap, norm=norm, aspect="equal")
        bar = fig.colorbar(image, ax=ax, fraction=0.035, pad=0.11, shrink=0.72)
        bar.outline.set_visible(False)
        bar.ax.tick_params(length=0, labelsize=8)
        bar.set_label(f"{inv.token} sent (logarithmic colour scale)", fontsize=8.5, color=INK_2)
        for i in range(n):
            for j in range(n):
                v = values[i][j]
                if v > 0:
                    dark = norm(v) > 0.55
                    ax.text(j, i - 0.1, fmt_compact(v), ha="center", va="center", fontsize=7.8, fontweight="bold",
                            color="#ffffff" if dark else INK)  # fmt: skip
                    ax.text(j, i + 0.24, f"{inv.matrix_counts[i][j]} tx", ha="center", va="center", fontsize=5.8,
                            color="#ffffff" if dark else INK_2)  # fmt: skip
    else:
        ax.imshow([[float("nan")] * n for _ in range(n)], aspect="equal")
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    labels = [short(m.address, m.index) for m in inv.members]
    ax.set_xticklabels(labels, rotation=55, ha="left", fontsize=7.8)
    ax.set_yticklabels(labels, fontsize=7.8)
    ax.xaxis.tick_top()
    ax.tick_params(length=0, pad=9)
    for tick_labels in (ax.get_xticklabels(), ax.get_yticklabels()):
        for tick, m in zip(tick_labels, inv.members):
            color = focus_color(inv, m.address)
            if color:
                tick.set_fontweight("bold")
                tick.set_color(INK)
    for i, m in enumerate(inv.members):
        color = focus_color(inv, m.address)
        if color:  # a coloured chip next to the key wallets' rows and columns
            ax.add_patch(Rectangle((-0.5 - 0.14, i - 0.46), 0.09, 0.92, fc=color, ec="none", clip_on=False, zorder=5))
            ax.add_patch(Rectangle((i - 0.46, -0.5 - 0.14), 0.92, 0.09, fc=color, ec="none", clip_on=False, zorder=5))
    for i in range(n):  # a wallet cannot pay itself: mark the diagonal
        ax.add_patch(Rectangle((i - 0.5, i - 0.5), 1, 1, fc="#f4f3ef", ec="none", zorder=0.5))
    ax.set_xticks([x - 0.5 for x in range(1, n)], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, n)], minor=True)
    ax.grid(which="minor", color=GRID, lw=0.8)
    ax.tick_params(which="minor", length=0)
    for s in ax.spines.values():
        s.set_visible(True)
        s.set_color(AXIS)
    # Row / column totals in the margins.
    for i, row in enumerate(values):
        total = sum(row)
        ax.text(n - 0.35, i, fmt_compact(total) if total else "–", ha="left", va="center", fontsize=7.6,
                color=INK if total else MUTED, clip_on=False)  # fmt: skip
    for j in range(n):
        total = sum(values[i][j] for i in range(n))
        ax.text(j, n - 0.3, fmt_compact(total) if total else "–", ha="center", va="top", fontsize=7.6,
                color=INK if total else MUTED, clip_on=False)  # fmt: skip
    ax.text(n - 0.35, -0.75, "total\nsent", ha="left", va="bottom", fontsize=7.4, color=INK_2, clip_on=False)
    ax.text(-0.7, n - 0.3, "total received", ha="right", va="top", fontsize=7.4, color=INK_2, clip_on=False)
    ax.set_ylabel("FROM (sender)", fontsize=9, fontweight="bold", color=INK_2, labelpad=8)
    ax.set_xlabel("TO (receiver)", fontsize=9, fontweight="bold", color=INK_2, labelpad=8)
    ax.xaxis.set_label_position("top")
    ax.set_title(f"Who sent {inv.token} to whom inside the list (row = sender, column = receiver)", pad=18)
    return fig


# --- g) ranking ------------------------------------------------------------------------------------------------


def ranking_bar(inv: Investigation):
    _style()
    token = inv.token
    rows = list(inv.ranking)
    fig, ax = plt.subplots(figsize=(11.5, 0.42 * len(rows) + 1.9))
    ys = list(range(len(rows)))
    sent = [float(r.sent_to_members) for r in rows]
    received = [float(r.received_from_members) for r in rows]
    ax.barh(ys, received, height=0.5, color=INFLOW, label="Received from list wallets", zorder=3)
    ax.barh(ys, sent, left=received, height=0.5, color=OUTFLOW, label="Sent to list wallets", zorder=3,
            edgecolor=SURFACE, linewidth=1.2)  # fmt: skip
    top = max((s + r for s, r in zip(sent, received)), default=1.0) or 1.0
    for y, r, s, rec in zip(ys, rows, sent, received):
        total = s + rec
        text = (
            f"{fmt_amount(total, token)}   ·   {r.transfers_with_members} tx with {r.partners} "
            f"{'wallet' if r.partners == 1 else 'wallets'}"
            if total
            else "no transfer with another list wallet"
        )
        ax.text(total + top * 0.012, y, text, va="center", ha="left", fontsize=7.8, color=INK if total else MUTED)
    labels = []
    for r in rows:
        tag = inv.labels.get(r.address)
        name = short(r.address, r.index)
        labels.append(f"{name}  ({short_tag(tag, 18)})" if tag else name)
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=8.3)
    for tick, r in zip(ax.get_yticklabels(), rows):
        if r.is_focus:
            tick.set_fontweight("bold")
            tick.set_color(INK)
    for y, r in zip(ys, rows):
        color = focus_color(inv, r.address)
        if color:
            ax.add_patch(Rectangle((-top * 0.012, y - 0.28), top * 0.006, 0.56, fc=color, ec="none", clip_on=False, zorder=5))
    ax.invert_yaxis()
    ax.set_xlim(0, top * 1.42)
    ax.xaxis.set_major_formatter(lambda v, _: fmt_compact(v))
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0, pad=10)
    ax.set_xlabel(f"{token} moved with other wallets of the list", fontsize=8.5)
    ax.legend(loc="lower right", fontsize=8.2, labelcolor=INK_2, handlelength=1.2)
    ax.set_title(f"List wallets ranked by {token} moved with the other list wallets", pad=12)
    return fig


def render_all(inv: Investigation, out_dir: Path) -> list[GraphFile]:
    """Write every graph as PNG and SVG into `out_dir`; returns them in report order."""
    files = [_save(focus_network(inv), out_dir, "focus_network", "Connections of the key wallets")]
    files.append(_save(joint_timeline(inv), out_dir, "joint_timeline", "Activity of the key wallets over time"))
    for p in inv.focus:
        files.append(_save(flow(inv, p), out_dir, f"flow_{p.index}", f"Sources and destinations of #{p.index}"))
        files.append(_save(timeline(inv, p), out_dir, f"timeline_{p.index}", f"Monthly activity of #{p.index}"))
    files.append(_save(list_network(inv), out_dir, "list_network", "Money flows inside the list"))
    files.append(_save(matrix_heatmap(inv), out_dir, "matrix_heatmap", "Member-to-member matrix"))
    files.append(_save(ranking_bar(inv), out_dir, "ranking_bar", "Members ranked by flows inside the list"))
    return files
