# Knowledge-graph dynamics: analysis scripts

The code behind [`../knowledge-graph-dynamics.html`](../knowledge-graph-dynamics.html).
It replays a git-tracked Obsidian vault commit by commit and measures its `[[wikilink]]`
graph over time. It only reads the vault (`git log`, `git cat-file`) and never writes to it.

```bash
python3 replay.py /path/to/vault /tmp/kg --ref main   # daily snapshots + per-commit churn
python3 analyze.py /tmp/kg                            # growth, hubs, communities, attachment, survival
python3 extras.py /path/to/vault /tmp/kg --ref main   # note roles, typed ontology layer, link thrash
python3 figures.py data article.template.html ../knowledge-graph-dynamics.html
```

Requires `networkx`, `numpy`, `scikit-learn` and `PyYAML`.

The `METHOD` string in `replay.py` documents how links are parsed and resolved. The scripts
assume Scout's vault layout: notes under `knowledge-base/`, and commit messages prefixed
with the run type. For another vault, adjust `KB`, `run_kind()` and `extras.role()`.

`analyze.py` and `extras.py` write `private*.json` files with real note paths alongside the
public numbers. Keep the output directory out of any public repository. `data/` holds only
the aggregate numbers the article uses: folder classes and counts, no note names. There,
the three smallest scheduled run types are merged into `other-scheduled`.
