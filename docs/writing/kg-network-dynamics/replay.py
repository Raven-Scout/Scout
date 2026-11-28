#!/usr/bin/env python3
"""Replay a vault's git history and rebuild its [[wikilink]] graph over time.

Read-only against the vault: it only runs `git log` and `git cat-file`.

    python3 replay.py /path/to/vault OUTDIR [--ref main]

Writes to OUTDIR (keep it out of any public repo, it holds real note paths):
    snapshots.pkl   one graph per local calendar day (state at that day's last commit)
    commits.jsonl   per-commit churn: notes added/deleted, link targets added/removed

How links are parsed and resolved is documented in METHOD below and in the article.
"""
from __future__ import annotations

import collections
import json
import os
import pickle
import re
import subprocess
import sys

import yaml

METHOD = """
Nodes:     every *.md file under knowledge-base/ (the symlink knowledge_base/ is skipped).
Links:     [[target]], [[target|alias]], [[target#heading]], ![[embed]]; fenced and inline
           code are stripped first. Markdown-style [text](x.md) links are NOT counted.
Resolve:   Obsidian rules, vault root = repo root.
           * target with '/' -> the .md file whose path equals it or ends with '/'+target
           * bare target     -> the .md file whose filename stem equals it (case-insensitive);
                                several matches -> shortest path wins (counted as ambiguous)
           * './' and '../'  -> relative to the linking file
           * knowledge_base/ in a target is read as knowledge-base/
           * target with a non-.md extension that exists -> attachment (not an edge)
           Lenient tier (reported separately): an unresolved target that equals a note's
           `aliases:` entry or ontology `name:` field.
Dangling:  anything else. Targets shaped like issue keys (ABC-123) are counted as their own
           class because the vault uses them on purpose as references to an issue tracker.
"""

LINK_RE = re.compile(r"!?\[\[([^\[\]\n]+?)\]\]")
FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.M | re.S)
INLINE_RE = re.compile(r"`[^`\n]*`")
ISSUE_RE = re.compile(r"^[a-z][a-z0-9]{1,9}-\d+$")
KB = "knowledge-base/"


def norm_target(raw: str) -> str:
    t = raw.split("|", 1)[0]
    t = t.rstrip("\\")
    t = t.split("#", 1)[0].split("^", 1)[0].strip()
    if t.lower().endswith(".md"):
        t = t[:-3]
    t = t.replace("knowledge_base/", "knowledge-base/")
    return t.strip().lower()


def parse_blob(text: str):
    body = FENCE_RE.sub("", text)
    body = INLINE_RE.sub("", body)
    targets = collections.Counter()
    for m in LINK_RE.finditer(body):
        t = norm_target(m.group(1))
        if t:
            targets[t] += 1
    fm = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            try:
                d = yaml.safe_load(text[3:end])
                if isinstance(d, dict):
                    fm = d
            except Exception:
                fm = {"_yaml_error": True}
    name = fm.get("name") if isinstance(fm.get("name"), str) else None
    etype = fm.get("type") if isinstance(fm.get("type"), str) else None
    aliases = fm.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if not isinstance(aliases, list):
        aliases = []
    aliases = [a.strip().lower() for a in aliases if isinstance(a, str)]
    rels = []
    rel_raw = fm.get("relationships")
    for r in (rel_raw if isinstance(rel_raw, list) else []):
        if isinstance(r, dict) and isinstance(r.get("target"), str):
            rels.append((str(r.get("type")), norm_target(r["target"].strip().strip("[]"))))
    return {
        "links": dict(targets),
        "name": name.strip().lower() if name else None,
        "etype": etype,
        "aliases": aliases,
        "rels": rels,
        "bytes": len(text.encode("utf-8", "replace")),
    }


class Blobs:
    def __init__(self, repo):
        self.p = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.cache = {}

    def get(self, sha):
        if sha in self.cache:
            return self.cache[sha]
        self.p.stdin.write((sha + "\n").encode())
        self.p.stdin.flush()
        header = self.p.stdout.readline().split()
        size = int(header[2])
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)
        info = parse_blob(data.decode("utf-8", "replace"))
        self.cache[sha] = info
        return info


def iter_commits(repo, ref):
    cmd = ["git", "-C", repo, "log", "--first-parent", "-m", "--raw", "--no-renames",
           "--no-abbrev", "--reverse", "--format=C %H %cI %s", ref]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    cur = None
    for line in p.stdout:
        line = line.decode("utf-8", "replace").rstrip("\n")
        if line.startswith("C "):
            if cur:
                yield cur
            _, sha, iso, *subj = line.split(" ", 3)
            cur = {"sha": sha, "iso": iso, "subject": subj[0] if subj else "", "changes": []}
        elif line.startswith(":"):
            meta, path = line.split("\t", 1)
            mode_old, mode_new, sha_old, sha_new, status = meta.split()
            cur["changes"].append((status, path, sha_old, sha_new, mode_new))
    if cur:
        yield cur


def run_kind(subject: str) -> str:
    s = subject.lower()
    for k in ("consolidation", "dreaming", "research", "briefing", "weekend", "work",
              "trading", "action-items", "chrome"):
        if s.startswith(k):
            return k
    if s.startswith("scout:") or s.startswith("scout "):
        return "scout:"
    if s.startswith("merge"):
        return "merge"
    return "other"


def is_kb_note(path, mode):
    return path.startswith(KB) and path.endswith(".md") and mode != "120000"


def build_resolver(md_paths):
    """md_paths: iterable of repo-relative paths ending .md (whole vault)."""
    by_stem = collections.defaultdict(list)
    by_path = {}
    for p in md_paths:
        if any(part.startswith(".") for part in p.split("/")):
            continue
        noext = p[:-3].lower()
        by_path[noext] = p
        by_stem[noext.rsplit("/", 1)[-1]].append(p)
    for v in by_stem.values():
        v.sort(key=lambda x: (len(x), x))
    suffix_cache = {}

    def resolve(src, t):
        if t.startswith("./") or t.startswith("../"):
            base = os.path.dirname(src)
            t2 = os.path.normpath(os.path.join(base, t)).lower()
            return (by_path.get(t2), False)
        if "/" in t:
            t = t.lstrip("/")
            if t in by_path:
                return (by_path[t], False)
            if t not in suffix_cache:
                hits = sorted((p for k, p in by_path.items() if k.endswith("/" + t)),
                              key=lambda x: (len(x), x))
                suffix_cache[t] = hits
            hits = suffix_cache[t]
            return (hits[0] if hits else None, len(hits) > 1)
        hits = by_stem.get(t)
        if hits:
            return (hits[0], len(hits) > 1)
        return (None, False)

    return resolve


def snapshot(state, blobs, all_paths):
    """state: path -> (blob sha, mode) for KB notes. all_paths: set of every file path."""
    md_paths = [p for p in all_paths if p.endswith(".md")]
    resolve = build_resolver(md_paths)
    names = {}
    for p, (sha, _) in state.items():
        info = blobs.get(sha)
        for key in ([info["name"]] if info["name"] else []) + info["aliases"]:
            names.setdefault(key, p)
    att_paths = {p.lower() for p in all_paths if not p.endswith(".md")}
    att_names = {p.rsplit("/", 1)[-1] for p in att_paths}
    lenient = collections.Counter()
    nodes = {}
    edges = collections.Counter()      # (src, dst) -> multiplicity, KB -> KB
    out_kb = collections.Counter()     # KB -> non-KB vault md
    stats = collections.Counter()
    dangling_targets = collections.Counter()
    ont_edges = []
    for p, (sha, _) in state.items():
        info = blobs.get(sha)
        nodes[p] = {"bytes": info["bytes"], "etype": info["etype"], "name": info["name"]}
        for t, n in info["links"].items():
            stats["occ"] += n
            dst, amb = resolve(p, t)
            if amb:
                stats["ambiguous"] += n
            if dst is None:
                base = t.rsplit("/", 1)[-1]
                if "." in base and (t in att_paths or base in att_names):
                    stats["attachment"] += n
                    continue
                if t in names:
                    stats["lenient"] += n
                    if names[t] != p:
                        lenient[(p, names[t])] += n
                    continue
                if ISSUE_RE.match(t):
                    stats["issue_key"] += n
                    continue
                stats["dangling"] += n
                dangling_targets[t] += n
                continue
            if dst.startswith(KB):
                if dst != p:
                    edges[(p, dst)] += n
                    stats["resolved_kb"] += n
                else:
                    stats["self"] += n
            else:
                out_kb[(p, dst)] += n
                stats["resolved_out"] += n
        for rtype, t in info["rels"]:
            if not info["etype"]:
                continue
            dst, _ = resolve(p, t)
            if dst is None:
                dst = names.get(t)
            ont_edges.append((p, rtype, dst, t if dst is None else None))
    return {"nodes": nodes, "edges": dict(edges), "lenient": dict(lenient), "out_kb": dict(out_kb), "stats": dict(stats),
            "dangling_top": dangling_targets.most_common(40), "ont_edges": ont_edges}


def main():
    repo, outdir = sys.argv[1], sys.argv[2]
    ref = sys.argv[sys.argv.index("--ref") + 1] if "--ref" in sys.argv else "main"
    os.makedirs(outdir, exist_ok=True)
    blobs = Blobs(repo)
    all_paths = {}
    state = {}
    snapshots = {}
    last_day = None
    commits_out = open(os.path.join(outdir, "commits.jsonl"), "w")

    def take(day, sha):
        snapshots[day] = {"sha": sha, **snapshot(state, blobs, set(all_paths))}

    prev = None
    for c in iter_commits(repo, ref):
        day = c["iso"][:10]
        if last_day is not None and day != last_day:
            take(last_day, prev["sha"])
        added_notes = deleted_notes = modified = 0
        l_add = l_rem = 0
        bytes_delta = 0
        for status, path, sha_old, sha_new, mode_new in c["changes"]:
            if status == "D":
                all_paths.pop(path, None)
            else:
                all_paths[path] = mode_new
            was = path in state
            if is_kb_note(path, mode_new if status != "D" else "100644"):
                old = blobs.get(sha_old)["links"] if was else {}
                if status == "D":
                    new = {}
                    if was:
                        bytes_delta -= blobs.get(sha_old)["bytes"]
                        state.pop(path)
                        deleted_notes += 1
                else:
                    if mode_new == "120000":
                        continue
                    info = blobs.get(sha_new)
                    new = info["links"]
                    bytes_delta += info["bytes"] - (blobs.get(sha_old)["bytes"] if was else 0)
                    state[path] = (sha_new, mode_new)
                    if was:
                        modified += 1
                    else:
                        added_notes += 1
                keys = set(old) | set(new)
                for k in keys:
                    d = new.get(k, 0) - old.get(k, 0)
                    if d > 0:
                        l_add += d
                    elif d < 0:
                        l_rem -= d
        commits_out.write(json.dumps({
            "sha": c["sha"][:10], "iso": c["iso"], "kind": run_kind(c["subject"]),
            "notes_added": added_notes, "notes_deleted": deleted_notes, "notes_modified": modified,
            "links_added": l_add, "links_removed": l_rem, "bytes_delta": bytes_delta,
            "n_notes": len(state)}) + "\n")
        last_day = day
        prev = c
    if prev:
        take(last_day, prev["sha"])
    commits_out.close()
    with open(os.path.join(outdir, "snapshots.pkl"), "wb") as f:
        pickle.dump(snapshots, f)
    print(f"{len(snapshots)} daily snapshots, {len(blobs.cache)} blobs parsed")


if __name__ == "__main__":
    main()
