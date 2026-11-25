#!/usr/bin/env python3
"""Second-pass numbers: self-reference, hub roles, the typed ontology layer, link thrash.

    python3 extras.py /path/to/vault OUTDIR --ref SHA

Writes OUTDIR/public_extra.json (folder classes and counts only) and
OUTDIR/private_extra.json (real paths; never publish).
"""
from __future__ import annotations

import collections
import json
import os
import pickle
import sys

from replay import Blobs, KB, iter_commits, is_kb_note, run_kind
from analyze import coarse_class, folder_class

# Notes about the agent's own operation rather than about the owner's world.
META = {"scout-mistake-audit", "review-queue", "research-queue", "session-log", "kg-audits",
        "enrichment-qa-log", "connector-health", "staleness-rules", "chrome-task-queue"}


def role(path: str) -> str:
    rel = path[len(KB):]
    stem = rel.rsplit("/", 1)[-1][:-3]
    top = rel.split("/")[0]
    if "/" not in rel:
        top = stem
    if top in META or stem in META:
        return "agent bookkeeping"
    if rel.startswith("projects/scout") or rel.startswith("projects/archived/scout"):
        return "the agent itself (its project notes)"
    if "/archive/" in rel or rel.startswith("archive/") or "pre-restructure" in rel:
        return "archived copy"
    if top in ("people",) or rel == "people.md":
        return "people"
    if top == "personal":
        return "owner's personal life"
    if top == "projects":
        return "work projects"
    if top in ("topics", "sources"):
        return "topics and sources"
    if top == "ontology":
        return "ontology entities"
    return "other index / reference"


def main():
    repo, outdir = sys.argv[1], sys.argv[2]
    ref = sys.argv[sys.argv.index("--ref") + 1]
    snaps = pickle.load(open(os.path.join(outdir, "snapshots.pkl"), "rb"))
    days = sorted(snaps)

    role_series, ont_series, hub_roles = [], [], {}
    for i, d in enumerate(days):
        s = snaps[d]
        if not (i % 7 == 0 or d == days[-1]):
            continue
        into = collections.Counter()
        for (_, t) in s["edges"]:
            into[role(t)] += 1
        nodes = collections.Counter(role(n) for n in s["nodes"])
        E = len(s["edges"]) or 1
        role_series.append({"date": d, "edges_into": {k: v / E for k, v in into.items()},
                            "nodes": dict(nodes)})
        indeg = collections.Counter(t for (_, t) in s["edges"])
        hub_roles[d] = [(role(n), k) for n, k in indeg.most_common(10)]
        # typed ontology layer
        ents = {n for n, v in s["nodes"].items() if v["etype"]}
        typed = [(a, r, b) for (a, r, b, _miss) in s["ont_edges"]]
        res = [(a, r, b) for (a, r, b) in typed if b is not None and b in ents]
        miss = sum(1 for (_, _, b) in typed if b is None or b not in ents)
        etypes = collections.Counter(s["nodes"][n]["etype"] for n in ents)
        rtypes = collections.Counter(r for (_, r, _) in res)
        und = set()
        for a, _, b in res:
            if a != b:
                und.add(tuple(sorted((a, b))))
        deg = collections.Counter()
        for a, b in und:
            deg[a] += 1
            deg[b] += 1
        top = deg.most_common(5)
        ont_series.append({
            "date": d, "entities": len(ents), "typed_edges": len(res), "unresolved": miss,
            "unresolved_rate": miss / len(typed) if typed else 0, "entity_types": len(etypes),
            "relation_types": len(rtypes),
            "mean_degree": 2 * len(und) / len(ents) if ents else 0,
            "top_hub_types": [s["nodes"][n]["etype"] for n, _ in top],
            "top_hub_degrees": [k for _, k in top],
            "isolated_entities": sum(1 for n in ents if deg[n] == 0),
        })

    # Link thrash: for each (file, target) pair, how often it was removed and later re-added.
    blobs = Blobs(repo)
    state = {}
    removed_at = collections.defaultdict(int)
    readded = collections.Counter()
    removed_kind = collections.Counter()
    readded_kind = collections.Counter()
    thrash_files = collections.Counter()
    for c in iter_commits(repo, ref):
        kind = run_kind(c["subject"])
        for status, path, sha_old, sha_new, mode in c["changes"]:
            if not is_kb_note(path, mode if status != "D" else "100644"):
                continue
            old = blobs.get(sha_old)["links"] if path in state else {}
            new = {} if status == "D" else blobs.get(sha_new)["links"]
            if status == "D":
                state.pop(path, None)
            else:
                state[path] = sha_new
            for t in set(old) | set(new):
                dlt = new.get(t, 0) - old.get(t, 0)
                if dlt < 0 and new.get(t, 0) == 0:
                    removed_at[(path, t)] += 1
                    removed_kind[kind] += 1
                elif dlt > 0 and old.get(t, 0) == 0 and removed_at.get((path, t)):
                    readded[(path, t)] += 1
                    readded_kind[kind] += 1
                    thrash_files[path] += 1
    tot_removed = sum(removed_at.values())
    tot_readded = sum(readded.values())
    by_class = collections.Counter()
    for p, n in thrash_files.items():
        by_class[role(p)] += n

    public = {"roles": role_series, "ontology": ont_series,
              "hub_roles": hub_roles,
              "thrash": {"pair_removals": tot_removed, "pair_readds": tot_readded,
                         "pairs_ever_readded": len(readded),
                         "removed_by_kind": dict(removed_kind), "readded_by_kind": dict(readded_kind),
                         "readds_by_role": dict(by_class)}}
    private = {"thrash_files": thrash_files.most_common(25)}
    json.dump(public, open(os.path.join(outdir, "public_extra.json"), "w"), indent=1)
    json.dump(private, open(os.path.join(outdir, "private_extra.json"), "w"), indent=1)
    print("ok")


if __name__ == "__main__":
    main()
