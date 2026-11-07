# kb-front-page golden snapshots

Canned post-run states of the `kb-front-page` scenario, for the KB-writing gate's scorer tests
(`tests/unit/test_eval_kb_gate.py`). "Before" is always the scenario's own `vault/` overlay. Each
case lists only the files a run changed or added, as vault-relative paths; every case except
`good` is laid over `good`.

| Case | What the run did | Fails |
|---|---|---|
| `good` | Rewrote the front page in place (it shrinks by 94 bytes); filed the facts, cited, in `knowledge/` | nothing |
| `placeholder` | Wrote the emptied Open questions section as `_None right now._` | nothing |
| `lazy-wrap` | Wrapped a bullet onto an unindented line (CommonMark lazy continuation) | nothing |
| `meeting-cite` | Cited the row cap to the meeting's own note under `knowledge-base/meetings/` | nothing |
| `narrated` | Left the page as it was and added an update paragraph with narration (+243 bytes) | structure, narration |
| `bullet-diary` | Added the run's update as a dated bullet ("… update: checked gh … this morning") | narration |
| `grown` | Kept every new detail on the front page as extra bullets and rows (+996 bytes, 7 bullets) | growth, bullets |
| `long-bullet` | Rewrote one "Where it stands" bullet in place past the 300-char cap | bullets |
| `uncited` | Filed the facts in `knowledge/` with no source links | facts |
| `dangling-cite` | Cited the row cap to a source note that was never written | facts |
| `negated` | Wrote "is not merged yet" and "isn't done", with the right links | facts |
| `broken` | Also put an updates file at the top of the project folder | structure |
