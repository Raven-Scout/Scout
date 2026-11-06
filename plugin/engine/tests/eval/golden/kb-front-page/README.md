# kb-front-page golden snapshots

Canned post-run states of the `kb-front-page` scenario, for the KB-writing gate's scorer tests
(`tests/unit/test_eval_kb_gate.py`). "Before" is always the scenario's own `vault/` overlay. Each
case lists only the files a run changed or added, as vault-relative paths; every case except
`good` is laid over `good`.

| Case | What the run did | Fails |
|---|---|---|
| `good` | Rewrote the front page in place (it shrinks by 94 bytes); filed the facts, cited, in `knowledge/` | nothing |
| `narrated` | Left the page as it was and added an update paragraph with narration (+243 bytes) | structure, narration |
| `grown` | Kept every new detail on the front page as extra bullets and rows (+996 bytes) | growth |
| `uncited` | Filed the facts in `knowledge/` with no source links | facts |
| `broken` | Also put an updates file at the top of the project folder | structure |
