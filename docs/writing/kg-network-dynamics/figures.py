#!/usr/bin/env python3
"""Render the article's figures as inline SVG / HTML from the aggregate JSON.

    python3 figures.py DATADIR TEMPLATE OUT

Replaces every {{fig:NAME}} and {{n:NAME}} placeholder in TEMPLATE. Colours are CSS
custom properties defined in the page, so every chart follows the light/dark theme.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import math
import os
import re
import sys

W, H = 680, 230
PAD_L, PAD_R, PAD_T, PAD_B = 46, 14, 14, 26


def d2x(d, d0, d1):
    t = (dt.date.fromisoformat(d) - d0).days / max(1, (d1 - d0).days)
    return PAD_L + t * (W - PAD_L - PAD_R)


def fmt(v):
    if v >= 1000:
        return f"{v/1000:.0f}k" if v >= 10000 else f"{v/1000:.1f}k"
    if v < 1 and v != 0:
        return f"{v*100:.0f}%"
    return f"{v:.0f}" if v == int(v) else f"{v:.1f}"


def line_chart(series, d0, d1, ymax, label, ticks, events=(), pct=False, height=H, aria=""):
    """series: list of (css_class, [(date, value)], label_text or None)."""
    h = height
    plot_h = h - PAD_T - PAD_B
    y = lambda v: PAD_T + plot_h * (1 - v / ymax)
    out = [f'<svg class="chart" viewBox="0 0 {W} {h}" role="img" aria-label="{aria}">']
    for t in ticks:
        out.append(f'<line class="grid" x1="{PAD_L}" x2="{W-PAD_R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        txt = f"{t*100:.0f}%" if pct else fmt(t)
        out.append(f'<text class="ax" x="{PAD_L-6}" y="{y(t)+4:.1f}" text-anchor="end">{txt}</text>')
    m = dt.date(d0.year, d0.month, 1)
    while m <= d1:
        if m >= d0:
            x = d2x(m.isoformat(), d0, d1)
            out.append(f'<line class="tick" x1="{x:.1f}" x2="{x:.1f}" y1="{h-PAD_B}" y2="{h-PAD_B+4}"/>')
            out.append(f'<text class="ax" x="{x+3:.1f}" y="{h-8}">{m.strftime("%b")}</text>')
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    for d, txt in events:
        x = d2x(d, d0, d1)
        out.append(f'<line class="ev" x1="{x:.1f}" x2="{x:.1f}" y1="{PAD_T}" y2="{h-PAD_B}"/>')
        out.append(f'<text class="evt" x="{x+4:.1f}" y="{PAD_T+10}">{txt}</text>')
    for cls, pts, lab in series:
        path = " ".join(f"{'M' if i == 0 else 'L'}{d2x(d, d0, d1):.1f},{y(v):.1f}" for i, (d, v) in enumerate(pts))
        out.append(f'<path class="ln {cls}" d="{path}"/>')
        if lab:
            d, v = pts[-1]
            out.append(f'<text class="lab {cls}" x="{d2x(d, d0, d1)-4:.1f}" y="{y(v)-7:.1f}" text-anchor="end">{lab}</text>')
    out.append(f'<line class="base" x1="{PAD_L}" x2="{W-PAD_R}" y1="{h-PAD_B}" y2="{h-PAD_B}"/>')
    if label:
        out.append(f'<text class="ax" x="{PAD_L}" y="{PAD_T-2}">{label}</text>')
    out.append("</svg>")
    return "\n".join(out)


def bars(rows, vmax, cls="one", unit=""):
    """rows: (label, value, value_text, small)."""
    out = ['<div class="bars">']
    for lab, v, vt, small in rows:
        w = 100 * v / vmax if vmax else 0
        sm = f"<small>{small}</small>" if small else ""
        out.append(f'<div class="bar-row"><div class="lab">{lab}</div><div class="track plain">'
                   f'<div class="bseg {cls}" style="width:{w:.2f}%"></div></div>'
                   f'<div class="val">{vt}{sm}</div></div>')
    out.append("</div>")
    return "\n".join(out)


def stacked(rows, keys):
    """rows: (label, {key: share}, value_text); keys: [(key, css_class)]."""
    out = ['<div class="bars">']
    for lab, parts, vt in rows:
        segs = "".join(f'<div class="bseg {c}" style="width:{100*parts.get(k,0):.2f}%" title="{k}: {100*parts.get(k,0):.0f}%"></div>'
                       for k, c in keys if parts.get(k, 0) > 0)
        out.append(f'<div class="bar-row"><div class="lab">{lab}</div><div class="track">{segs}</div>'
                   f'<div class="val">{vt}</div></div>')
    out.append("</div>")
    return "\n".join(out)


def main():
    data, tpl, outp = sys.argv[1:4]
    p = json.load(open(os.path.join(data, "public.json")))
    x = json.load(open(os.path.join(data, "public_extra.json")))
    s = p["series"]
    d0 = dt.date.fromisoformat(s[0]["date"])
    d1 = dt.date.fromisoformat(s[-1]["date"])
    ev_restr = ("2026-10-04", "new layers")
    ev_move = ("2026-10-10", "")
    ev_drop = ("2026-07-28", "doc drop")
    figs, nums = {}, {}

    # 1. growth
    figs["growth"] = (
        '<div class="small-mult"><div>' + line_chart(
            [("a", [(m["date"], m["N"]) for m in s], f'{s[-1]["N"]:,} notes')], d0, d1, 1600,
            "notes", [0, 400, 800, 1200, 1600], [ev_restr], height=180, aria="Notes over time") +
        '</div><div>' + line_chart(
            [("b", [(m["date"], m["E"]) for m in s], f'{s[-1]["E"]:,} links')], d0, d1, 14000,
            "distinct note-to-note links", [0, 3500, 7000, 10500, 14000], [ev_restr], height=180, aria="Links over time") +
        '</div></div>')
    figs["outdeg"] = line_chart(
        [("a", [(m["date"], m["mean_out"]) for m in s], None)], d0, d1, 12,
        "links out per note (mean)", [0, 3, 6, 9, 12], [ev_restr], height=170, aria="Mean out-links per note")

    # 2. preferential attachment
    t = p["pref_attachment"]["table"]
    labs = {(0, 0): "0", (1, 1): "1", (2, 3): "2–3", (4, 7): "4–7", (8, 15): "8–15", (16, 31): "16–31",
            (32, 63): "32–63", (64, 127): "64–127", (128, 255): "128–255", (256, 10**9): "256+"}
    rows = [(f'{labs[(r["k_lo"], r["k_hi"])]} in-links', r["mean_gain"], f'{r["mean_gain"]:.1f}',
             f'{r["n"]:,} obs') for r in t]
    figs["pa"] = bars(rows, max(r[1] for r in rows), "agent")
    alphas = [w["alpha"] for w in p["pref_attachment"]["windows"] if w["alpha"] is not None]
    alphas.sort()
    nums["alpha_med"] = f"{alphas[len(alphas)//2]:.1f}"
    nums["alpha_lo"] = f"{alphas[0]:.1f}"
    nums["alpha_hi"] = f"{alphas[-1]:.1f}"
    nums["alpha_n"] = str(len(alphas))
    nums["share_existing"] = f'{100*p["pref_attachment"]["share_new_edges_to_existing"]:.0f}%'

    # 3. concentration
    figs["conc"] = line_chart(
        [("a", [(m["date"], m["top1pct_in_share"]) for m in s], None),
         ("b", [(m["date"], m["gini_in"]) for m in s], None)], d0, d1, 0.8,
        "", [0, 0.2, 0.4, 0.6, 0.8], [ev_restr], pct=True, aria="Concentration of in-links")

    # 4. what the links point at
    world = {"work projects", "people", "owner's personal life", "topics and sources", "ontology entities"}
    def grp(r):
        if r in world:
            return "world"
        if r in ("agent bookkeeping", "the agent itself (its project notes)"):
            return "self"
        return "other"
    rows = []
    seen = set()
    for r in x["roles"]:
        mkey = r["date"][:7]
        if mkey in seen:
            continue
        seen.add(mkey)
        g = collections.Counter()
        for k, v in r["edges_into"].items():
            g[grp(k)] += v
        rows.append((dt.date.fromisoformat(r["date"]).strftime("%-d %b"), g, f'{100*g["self"]:.0f}%'))
    last = x["roles"][-1]
    g = collections.Counter()
    for k, v in last["edges_into"].items():
        g[grp(k)] += v
    rows.append((dt.date.fromisoformat(last["date"]).strftime("%-d %b"), g, f'{100*g["self"]:.0f}%'))
    figs["roles"] = stacked(rows, [("self", "agent"), ("world", "me"), ("other", "unclear")])
    hub_rows = []
    for d, hubs in x["hub_roles"].items():
        if d[8:10] <= "07" or d == s[-1]["date"]:
            hub_rows.append((d, hubs))
    dedup, seenm = [], set()
    for d, hubs in hub_rows:
        if d[:7] in seenm and d != s[-1]["date"]:
            continue
        seenm.add(d[:7])
        dedup.append((d, hubs))
    cls = lambda r: {"self": "agent", "world": "me", "other": "unclear"}[grp(r)]
    short = {"agent bookkeeping": "agent log / queue", "the agent itself (its project notes)": "agent's own project",
             "work projects": "work project", "people": "person / people index",
             "owner's personal life": "owner's personal note", "topics and sources": "topic / source",
             "ontology entities": "ontology entity", "other index / reference": "index / reference",
             "archived copy": "archived copy"}
    tbl = ['<div class="tbl-wrap"><table class="hubs"><thead><tr><th>Snapshot</th>' +
           "".join(f'<th>#{i}</th>' for i in range(1, 6)) + "</tr></thead><tbody>"]
    for d, hubs in dedup:
        tbl.append(f'<tr><td>{dt.date.fromisoformat(d).strftime("%-d %b")}</td>' + "".join(
            f'<td><span class="chip {cls(r)}">{short[r]}</span><b>{k}</b></td>' for r, k in hubs[:5]) + "</tr>")
    tbl.append("</tbody></table></div>")
    figs["hubs"] = "\n".join(tbl)
    fin_nodes = last["nodes"]
    nums["bookkeeping_notes"] = f'{fin_nodes["agent bookkeeping"]:,}'
    nums["bookkeeping_share"] = f'{100*fin_nodes["agent bookkeeping"]/s[-1]["N"]:.0f}%'

    # 5. hygiene
    figs["hygiene"] = line_chart(
        [("a", [(m["date"], m["dangling_rate"]) for m in s], None),
         ("b", [(m["date"], m["orphan_frac"]) for m in s], None)], d0, d1, 0.25,
        "", [0, 0.05, 0.10, 0.15, 0.20, 0.25], [ev_drop, ev_restr], pct=True, aria="Dangling links and orphan notes")

    # 6. freeze: edge survival + renames
    sv = p["survival"]
    # (survival y-axis starts at 60%: rescale by mapping)
    figs["survival"] = line_chart_offset(sv, d0, d1)
    ren = [("Apr", 7), ("May", 4), ("Jun", 14), ("Jul", 0), ("Aug", 0), ("Sep", 0), ("Oct", 169), ("Nov", 86)]
    figs["renames"] = bars([(m, v, str(v), None) for m, v in ren], 169, "agent")

    # 7. churn by run type
    bk = p["by_kind"]
    order = [("research", "Research (scheduled)"), ("consolidation", "Consolidation (scheduled)"),
             ("briefing", "Briefing (scheduled)"), ("dreaming", "Dreaming (scheduled)"),
             ("scout:", "Interactive sessions"), ("work", "Interactive work sessions")]
    rows = []
    for k, lab in order:
        v = bk[k]
        rows.append((lab, v["links_removed"] / v["links_added"],
                     f'{100*v["links_removed"]/v["links_added"]:.0f}',
                     f'{v["links_added"]:,} added · {v["commits"]:,} runs'))
    figs["churn"] = bars(rows, 1.0, "agent")
    sched = ["consolidation", "research", "dreaming", "briefing", "weekend", "other-scheduled"]
    inter = ["scout:", "work"]
    tot = lambda ks, f: sum(bk[k][f] for k in ks if k in bk)
    allc = sum(v["commits"] for v in bk.values())
    nums["inter_commit_share"] = f'{100*tot(inter,"commits")/allc:.0f}%'
    nums["inter_add_share"] = f'{100*tot(inter,"links_added")/sum(v["links_added"] for v in bk.values()):.0f}%'
    nums["inter_rem_share"] = f'{100*tot(inter,"links_removed")/sum(v["links_removed"] for v in bk.values()):.0f}%'
    nums["sched_commits"] = f'{tot(sched,"commits"):,}'

    # 8. communities vs folders
    hv = p["heavy"]
    figs["comm"] = line_chart(
        [("a", [(h["date"], h["modularity_louvain"]) for h in hv], None),
         ("b", [(h["date"], h["modularity_folders"]) for h in hv], None)], d0, d1, 0.5,
        "modularity", [0, 0.1, 0.2, 0.3, 0.4, 0.5], [ev_restr], aria="Modularity: communities vs folders")
    figs["nmi"] = line_chart(
        [("a", [(h["date"], h["nmi_fine"]) for h in hv], None)], d0, d1, 0.6,
        "agreement between communities and folders (NMI)", [0, 0.2, 0.4, 0.6], [ev_restr], height=170,
        aria="NMI between communities and folders")

    # 9. ontology
    on = x["ontology"]
    figs["ont"] = line_chart(
        [("a", [(o["date"], o["entities"]) for o in on], None),
         ("b", [(o["date"], o["typed_edges"]) for o in on], None),
         ("c", [(o["date"], o["isolated_entities"]) for o in on], None)], d0, d1, 1000,
        "", [0, 250, 500, 750, 1000], [ev_restr], aria="Typed ontology layer over time")

    html = open(tpl).read()
    html = re.sub(r"\{\{fig:(\w+)\}\}", lambda m: figs[m.group(1)], html)
    html = re.sub(r"\{\{n:(\w+)\}\}", lambda m: nums[m.group(1)], html)
    left = re.findall(r"\{\{[^}]+\}\}", html)
    if left:
        sys.exit(f"unfilled placeholders: {left}")
    open(outp, "w").write(html)
    print("wrote", outp, {k: v for k, v in nums.items()})


def line_chart_offset(sv, d0, d1):
    lo = 0.6
    pts_a = [(w["start"], (w["edge_survival"] - lo) / (1 - lo)) for w in sv]
    pts_b = [(w["start"], (w["node_survival"] - lo) / (1 - lo)) for w in sv]
    svg = line_chart([("a", pts_a, None), ("b", pts_b, None)],
                     d0, d1, 1.0, "", [0, 0.25, 0.5, 0.75, 1.0],
                     [("2026-06-16", "last move"), ("2026-10-10", "moves allowed")], pct=True,
                     aria="Four-week survival of links and notes")
    # relabel the y axis to the true range (60%..100%)
    for frac, true in [(0, 60), (0.25, 70), (0.5, 80), (0.75, 90), (1.0, 100)]:
        svg = svg.replace(f'>{frac*100:.0f}%</text>', f'>{true}%</text>', 1)
    return svg


if __name__ == "__main__":
    main()
