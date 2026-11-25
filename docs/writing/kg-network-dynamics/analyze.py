#!/usr/bin/env python3
"""Turn replay.py output into the aggregate numbers behind the article.

    python3 analyze.py OUTDIR

Reads OUTDIR/snapshots.pkl and OUTDIR/commits.jsonl. Writes:
    OUTDIR/private.json   includes real note paths (for the author's eyes only)
    OUTDIR/public.json    folder classes and numbers only; this is what the article uses
"""
from __future__ import annotations

import collections
import json
import math
import os
import pickle
import random
import sys
from datetime import date

import networkx as nx
import numpy as np
from sklearn.metrics import normalized_mutual_info_score

KB = "knowledge-base/"


def folder_class(path: str) -> str:
    rel = path[len(KB):]
    parts = rel.split("/")
    if len(parts) == 1:
        return "root"
    top = parts[0]
    if top == "projects":
        if len(parts) >= 3 and parts[1] == "archived":
            return "projects/archived"
        return "projects/" + parts[1] if len(parts) > 2 else "projects"
    if top == "topics" and len(parts) > 2:
        return "topics/" + parts[1]
    return top


def coarse_class(path: str) -> str:
    fc = folder_class(path)
    return fc.split("/")[0]


def gini(values):
    v = np.sort(np.asarray(values, dtype=float))
    if v.sum() == 0:
        return 0.0
    n = len(v)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(v) / (n * v.sum()))


def day_metrics(s):
    nodes = list(s["nodes"])
    N = len(nodes)
    E = len(s["edges"])
    st = s["stats"]
    indeg = collections.Counter()
    outdeg = collections.Counter()
    for (a, b) in s["edges"]:
        indeg[b] += 1
        outdeg[a] += 1
    ins = [indeg[n] for n in nodes]
    G = nx.Graph()
    G.add_nodes_from(nodes)
    G.add_edges_from(s["edges"])
    comps = sorted((len(c) for c in nx.connected_components(G)), reverse=True) if N else [0]
    recip = sum(1 for (a, b) in s["edges"] if (b, a) in s["edges"]) / E if E else 0
    linkable = st.get("occ", 0) - st.get("attachment", 0) - st.get("issue_key", 0)
    top = sorted(ins, reverse=True)
    k1 = max(1, math.ceil(N * 0.01))
    sizes = [v["bytes"] for v in s["nodes"].values()]
    return {
        "N": N, "E": E, "occ": st.get("occ", 0),
        "mean_out": E / N if N else 0,
        "dangling_rate": st.get("dangling", 0) / linkable if linkable else 0,
        "lenient_rate": st.get("lenient", 0) / linkable if linkable else 0,
        "issue_key_occ": st.get("issue_key", 0),
        "ambiguous_occ": st.get("ambiguous", 0),
        "out_of_kb_occ": st.get("resolved_out", 0),
        "kb_occ": st.get("resolved_kb", 0),
        "orphan_frac": sum(1 for x in ins if x == 0) / N if N else 0,
        "orphans": sum(1 for x in ins if x == 0),
        "isolates": sum(1 for n in nodes if indeg[n] == 0 and outdeg[n] == 0),
        "no_out": sum(1 for n in nodes if outdeg[n] == 0),
        "components": len(comps),
        "giant_frac": comps[0] / N if N else 0,
        "second_comp": comps[1] if len(comps) > 1 else 0,
        "top1pct_in_share": sum(top[:k1]) / E if E else 0,
        "top10_in_share": sum(top[:10]) / E if E else 0,
        "gini_in": gini(ins), "max_in": top[0] if top else 0,
        "reciprocity": recip,
        "mb": sum(sizes) / 1e6, "median_kb": float(np.median(sizes)) / 1e3 if sizes else 0,
    }


def heavy_metrics(s, seed=7):
    nodes = list(s["nodes"])
    G = nx.Graph()
    G.add_nodes_from(nodes)
    G.add_edges_from(s["edges"])
    giant = G.subgraph(max(nx.connected_components(G), key=len)).copy()
    comms = nx.algorithms.community.louvain_communities(giant, seed=seed, resolution=1.0)
    lab_comm = {}
    for i, c in enumerate(comms):
        for n in c:
            lab_comm[n] = i
    gn = list(giant.nodes)
    fine = [folder_class(n) for n in gn]
    coarse = [coarse_class(n) for n in gn]
    cm = [lab_comm[n] for n in gn]
    # modularity of the folder partition itself, for comparison
    fpart = collections.defaultdict(set)
    for n, f in zip(gn, fine):
        fpart[f].add(n)
    mod_comm = nx.algorithms.community.modularity(giant, comms)
    mod_folder = nx.algorithms.community.modularity(giant, list(fpart.values()))
    # purity: share of a community's nodes in its most common fine folder
    purity = []
    for c in comms:
        cnt = collections.Counter(folder_class(n) for n in c)
        purity.append((len(c), cnt.most_common(1)[0][1] / len(c)))
    w_purity = sum(sz * p for sz, p in purity) / sum(sz for sz, _ in purity)
    try:
        assort = nx.degree_assortativity_coefficient(G)
    except Exception:
        assort = float("nan")
    rng = random.Random(seed)
    sample = rng.sample(gn, min(200, len(gn)))
    dists = []
    for src in sample:
        d = nx.single_source_shortest_path_length(giant, src)
        dists.extend(v for v in d.values() if v > 0)
    # degree-preserving random baseline for clustering
    clus = nx.average_clustering(G)
    return {
        "clustering": clus,
        "communities": len(comms), "modularity_louvain": mod_comm, "modularity_folders": mod_folder,
        "nmi_fine": normalized_mutual_info_score(fine, cm),
        "nmi_coarse": normalized_mutual_info_score(coarse, cm),
        "purity_weighted": w_purity,
        "assortativity": assort,
        "mean_path": float(np.mean(dists)) if dists else 0,
        "n_folders_fine": len(set(fine)),
    }


def pref_attachment(snaps, days, window=28):
    """For each pair (t, t+window), relate a note's in-degree at t to in-links gained by t+window."""
    gains = collections.defaultdict(list)
    new_to_old = new_to_new = 0
    per_window = []
    for i, d in enumerate(days):
        j = i + window
        if j >= len(days) or i % 7:
            continue
        a, b = snaps[d], snaps[days[j]]
        old_nodes = set(a["nodes"])
        ea, eb = set(a["edges"]), set(b["edges"])
        indeg = collections.Counter(t for (_, t) in ea)
        new = eb - ea
        gain = collections.Counter()
        no = nn = 0
        for (s_, t) in new:
            if t in old_nodes:
                gain[t] += 1
                no += 1
            else:
                nn += 1
        new_to_old += no
        new_to_new += nn
        for n in old_nodes:
            if n in b["nodes"]:
                gains[indeg[n]].append(gain[n])
        # kernel exponent within this window
        ks, gs = [], []
        bins = collections.defaultdict(list)
        for n in old_nodes:
            if n in b["nodes"] and indeg[n] > 0:
                bins[int(round(math.log2(indeg[n])))].append((indeg[n], gain[n]))
        for _, v in bins.items():
            if len(v) >= 5:
                k = np.mean([x for x, _ in v]); g = np.mean([y for _, y in v])
                if g > 0:
                    ks.append(math.log(k)); gs.append(math.log(g))
        alpha = float(np.polyfit(ks, gs, 1)[0]) if len(ks) >= 3 else None
        per_window.append({"start": d, "alpha": alpha, "new_edges": len(new),
                           "share_to_existing": no / len(new) if new else None})
    table = []
    for lo, hi in [(0, 0), (1, 1), (2, 3), (4, 7), (8, 15), (16, 31), (32, 63), (64, 127),
                   (128, 255), (256, 10**9)]:
        v = [g for k, lst in gains.items() if lo <= k <= hi for g in lst]
        if v:
            table.append({"k_lo": lo, "k_hi": hi, "n": len(v), "mean_gain": float(np.mean(v)),
                          "p_any": float(np.mean([x > 0 for x in v]))})
    return {"windows": per_window, "table": table,
            "share_new_edges_to_existing": new_to_old / max(1, new_to_old + new_to_new)}


def edge_survival(snaps, days, lag=28):
    out = []
    for i, d in enumerate(days):
        j = i + lag
        if j >= len(days) or i % 7:
            continue
        ea, eb = set(snaps[d]["edges"]), set(snaps[days[j]]["edges"])
        na_b = set(snaps[days[j]]["nodes"])
        surv = len(ea & eb) / len(ea) if ea else None
        # survival among edges whose two endpoints both still exist under the same path
        both = [e for e in ea if e[0] in na_b and e[1] in na_b]
        surv_same = (sum(1 for e in both if e in eb) / len(both)) if both else None
        nodes_a = set(snaps[d]["nodes"])
        out.append({"start": d, "edge_survival": surv, "edge_survival_same_paths": surv_same,
                    "node_survival": len(nodes_a & na_b) / len(nodes_a) if nodes_a else None})
    return out


def main():
    outdir = sys.argv[1]
    snaps = pickle.load(open(os.path.join(outdir, "snapshots.pkl"), "rb"))
    days = sorted(snaps)
    series = []
    for d in days:
        m = day_metrics(snaps[d])
        m["date"] = d
        series.append(m)
    heavy = []
    for i, d in enumerate(days):
        if i % 7 == 0 or d == days[-1]:
            if len(snaps[d]["nodes"]) > 20:
                h = heavy_metrics(snaps[d])
                h["date"] = d
                heavy.append(h)
    pa = pref_attachment(snaps, days)
    surv = edge_survival(snaps, days)

    commits = [json.loads(l) for l in open(os.path.join(outdir, "commits.jsonl"))]
    by_kind = collections.defaultdict(lambda: collections.Counter())
    for c in commits:
        k = by_kind[c["kind"]]
        k["commits"] += 1
        k["touched_kb"] += int(c["notes_added"] + c["notes_deleted"] + c["notes_modified"] > 0)
        for f in ("notes_added", "notes_deleted", "notes_modified", "links_added",
                  "links_removed", "bytes_delta"):
            k[f] += c[f]
    by_month_kind = collections.defaultdict(lambda: collections.Counter())
    for c in commits:
        key = c["iso"][:7] + "|" + c["kind"]
        by_month_kind[key]["links_added"] += c["links_added"]
        by_month_kind[key]["links_removed"] += c["links_removed"]
        by_month_kind[key]["commits"] += 1

    final = snaps[days[-1]]
    indeg = collections.Counter(t for (_, t) in final["edges"])
    outdeg = collections.Counter(s for (s, _) in final["edges"])
    ins = sorted(indeg.values(), reverse=True)
    ccdf = {}
    for d in [days[len(days) // 8], days[len(days) // 3], days[2 * len(days) // 3], days[-1]]:
        c = collections.Counter(t for (_, t) in snaps[d]["edges"])
        vals = np.array([c[n] for n in snaps[d]["nodes"]])
        ks = sorted(set(int(x) for x in vals if x > 0))
        ccdf[d] = [(k, float((vals >= k).mean())) for k in ks]
    fold = collections.Counter(folder_class(n) for n in final["nodes"])
    coarse = collections.Counter(coarse_class(n) for n in final["nodes"])
    # within-folder vs cross-folder edges at the end
    within_coarse = sum(1 for (a, b) in final["edges"] if coarse_class(a) == coarse_class(b))
    within_fine = sum(1 for (a, b) in final["edges"] if folder_class(a) == folder_class(b))
    coarse_out = collections.defaultdict(list)
    for n in final["nodes"]:
        coarse_out[coarse_class(n)].append((outdeg[n], indeg[n], final["nodes"][n]["bytes"]))
    folder_profile = {k: {"n": len(v), "mean_out": float(np.mean([x[0] for x in v])),
                          "mean_in": float(np.mean([x[1] for x in v])),
                          "orphan_frac": float(np.mean([x[1] == 0 for x in v])),
                          "median_kb": float(np.median([x[2] for x in v])) / 1e3}
                      for k, v in coarse_out.items()}

    public = {
        "range": [days[0], days[-1]], "n_days": len(days), "n_commits": len(commits),
        "series": series, "heavy": heavy, "pref_attachment": pa, "survival": surv,
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "by_month_kind": {k: dict(v) for k, v in by_month_kind.items()},
        "final": {
            "in_top": ins[:30], "ccdf": ccdf, "folders_fine": len(fold),
            "coarse": dict(coarse), "within_coarse": within_coarse / len(final["edges"]),
            "within_fine": within_fine / len(final["edges"]), "folder_profile": folder_profile,
            "stats": final["stats"],
        },
    }
    hubs = [{"path": n, "in": indeg[n], "out": outdeg[n], "class": folder_class(n),
             "etype": final["nodes"][n]["etype"]} for n, _ in indeg.most_common(40)]
    fanout = [{"path": n, "out": outdeg[n], "class": folder_class(n)} for n, _ in outdeg.most_common(25)]
    private = {"hubs": hubs, "fanout": fanout, "dangling_top": final["dangling_top"],
               "hub_by_month": {}}
    for d in days:
        if d.endswith("-01") or d == days[-1]:
            c = collections.Counter(t for (_, t) in snaps[d]["edges"])
            private["hub_by_month"][d] = c.most_common(8)
    json.dump(public, open(os.path.join(outdir, "public.json"), "w"), indent=1, default=str)
    json.dump(private, open(os.path.join(outdir, "private.json"), "w"), indent=1, default=str)
    print("ok", days[0], days[-1], len(series), len(heavy))


if __name__ == "__main__":
    main()
