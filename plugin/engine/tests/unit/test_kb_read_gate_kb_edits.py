"""An interpreter program that reads a KB note and writes only into the KB is an edit in place: kb-read-gate allows
it and records one ``kb_read_gate.kb_edit`` event per command (``{"mode", "targets", "non_kb_targets"}``, counts
only, no paths), unless
``DENY_INTERPRETER_KB_EDITS`` is on. What it does with what it read isn't followed (a file name, an exit code or an
exception carries data), so the record is the safety net. Every other interpreter read of a KB note is denied:
read-only, or read there and written outside the KB (a copy out), or written somewhere the gate can't place.

Synthetic vault only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb.raw_read import raw_kb_read
from tests.unit.test_hooks_kb_read_gate import RUN, _denied, _payload

KB_DENY = "Scheduled runs don't read knowledge-base/"
H = "python3 - <<'EOF'\n{}\nEOF"
P = "p = 'knowledge-base/a.md'\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    v = h / "Vault"
    for rel in ("knowledge-base/a.md", "knowledge-base/projects/a/a.md", "action-items/context/x.md", "docs/a.md"):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text("x\n")
    (v / "scout-config.yaml").write_text("user: {}\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return h.resolve()


def _run(home: Path, command: str):
    return gate.run(stdin=_payload("Bash", {"command": command}, home / "Vault"), env={**RUN, "HOME": str(home)})


def _events(home: Path, kind: str) -> list[dict]:
    rows: list[dict] = []
    for path in sorted((home / "Vault" / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines()]
    return [r for r in rows if r["kind"] == kind]


# ----- edits: allowed and recorded -------------------------------------------------------------------------

# (command, how many KB files it writes)
EDITS = [
    # the replay's shape: a path from a literal, read, changed, written back
    (H.format(P + "s = open(p).read()\ns = s.replace('x', 'y')\nopen(p, 'w').write(s)"), 1),
    (
        H.format(P + "with open(p) as f:\n    s = f.read()\nwith open(p, 'w') as f:\n    f.write(s.replace('x', 'y'))"),
        1,
    ),
    (
        H.format(
            P + "import re\ns = open(p).read()\nn = len(re.findall('x', s))\nif n:\n    s = re.sub('x', 'y', s)\n"
            "    open(p, 'w').write(s)\nprint('replaced', n)"
        ),
        1,
    ),
    (
        H.format(
            P + "def fix(t):\n    lines = t.splitlines()\n    return '\\n'.join(l.rstrip() for l in lines)\n"
            "open(p, 'w').write(fix(open(p).read()))"
        ),
        1,
    ),
    (H.format(P + "import json\nd = json.load(open(p))\nd['k'] = 1\njson.dump(d, open(p, 'w'), indent=2)"), 1),
    (H.format("from pathlib import Path\nq = Path('knowledge-base/a.md')\nq.write_text(q.read_text() + 'x')"), 1),
    (
        H.format(
            "for p in ['knowledge-base/a.md', 'knowledge-base/projects/a/a.md']:\n    s = open(p).read()\n"
            "    open(p, 'w').write(s.replace('x', 'y'))"
        ),
        2,
    ),
    (H.format(P + "s = open(p).read()\nprint(s)\nopen(p, 'w').write(s)"), 1),  # what it does with it isn't followed
    ("python3 -c \"p='knowledge-base/a.md'; s=open(p).read(); open(p,'w').write(s.replace('x','y'))\"", 1),
    ('python3 -c \'open("knowledge-base/a.md", "a+").read()\'', 1),
    ('python3 -c \'import shutil; shutil.copy("knowledge-base/a.md", "knowledge-base/b.md")\'', 1),
    ("cd knowledge-base && python3 - <<'EOF'\ns = open('a.md').read()\nopen('a.md', 'w').write(s + 'x')\nEOF", 1),
    # the in-place editors
    ("ruby -i -pe 'gsub(/x/, \"y\")' knowledge-base/a.md", 1),
    ("ruby -i.bak -pe 'gsub(/x/, \"y\")' knowledge-base/a.md knowledge-base/projects/a/a.md", 2),
    ("perl -pi -e 's/x/y/' knowledge-base/a.md", 1),
    ("perl -i -pe 's/x/y/' knowledge-base/a.md", 1),
    # ruby, node, php, lua, R
    ('ruby -e \'s = File.read("knowledge-base/a.md"); File.write("knowledge-base/a.md", s.upcase)\'', 1),
    (
        'node -e \'const fs=require("fs"); const p="knowledge-base/a.md"; '
        'fs.writeFileSync(p, fs.readFileSync(p, "utf8").trim())\'',
        1,
    ),
    ("php -r '$p = \"knowledge-base/a.md\"; file_put_contents($p, strtoupper(file_get_contents($p)));'", 1),
    ('lua -e \'local p = "knowledge-base/a.md" local s = io.open(p):read("a") io.open(p, "w"):write(s)\'', 1),
    ("Rscript -e 'p <- \"knowledge-base/a.md\"; writeLines(toupper(readLines(p)), p)'", 1),
]


@pytest.mark.parametrize(("command", "targets"), EDITS)
def test_an_edit_of_kb_notes_is_allowed_and_recorded(home: Path, command: str, targets: int) -> None:
    assert gate.DENY_INTERPRETER_KB_EDITS is False
    assert _run(home, command) is None, command
    (row,) = _events(home, "kb_read_gate.kb_edit")
    assert row["payload"] == {"mode": RUN["SCOUT_MODE"], "targets": targets, "non_kb_targets": 0}
    assert row["source"] == "hook:kb-read-gate"
    assert "knowledge" not in json.dumps(row["payload"])  # no paths


@pytest.mark.parametrize(("command", "targets"), EDITS)
def test_with_the_switch_on_an_edit_is_denied_and_not_recorded(
    home: Path, monkeypatch: pytest.MonkeyPatch, command: str, targets: int
) -> None:
    monkeypatch.setattr(gate, "DENY_INTERPRETER_KB_EDITS", True)
    assert KB_DENY in _denied(_run(home, command)), command
    assert _events(home, "kb_read_gate.kb_edit") == []


@pytest.mark.parametrize(("command", "targets"), EDITS)
def test_the_evals_judge_does_not_count_an_edit(command: str, targets: int) -> None:
    in_kb = command.startswith("cd knowledge-base")
    cwd = "/v/knowledge-base" if in_kb else "/v"
    assert not raw_kb_read(command, in_kb=in_kb, cwd=cwd, vault="/v"), command


def test_the_unresolved_switch_leaves_edits_alone(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "DENY_UNRESOLVED_READS", True)
    assert _run(home, EDITS[0][0]) is None
    assert len(_events(home, "kb_read_gate.kb_edit")) == 1


# ----- not edits: denied -----------------------------------------------------------------------------------

NOT_EDITS = [
    H.format(P + "print(open(p).read())"),  # read only
    H.format(P + "s = open(p).read()\nopen('docs/x.md', 'w').write(s)"),  # a copy out
    H.format(P + "s = open(p).read()\nopen(p, 'w').write(s)\nopen('docs/log.txt', 'a').write('done')"),  # and outside
    H.format(P + "s = open(p).read()\nopen(p, 'w').write(s)\nopen('/tmp/knowledge-base/x', 'w').write(s)"),
    H.format(P + "s = open(p).read()\nopen('knowledge-base/../docs/y.md', 'w').write(s)"),  # lands outside
    H.format(P + "import sys\ns = open(p).read()\nopen(sys.argv[1], 'w').write(s)"),  # a target it builds
    H.format(P + "s = open(p).read()\nopen('/dev/stdout', 'w').write(s)"),
    'python3 -c \'import shutil; shutil.copy("knowledge-base/a.md", "docs/y.md")\'',
    "ruby -pe 'gsub(/x/, \"y\")' knowledge-base/a.md",  # no -i: it prints
    'node -e \'const fs=require("fs"); fs.writeFileSync("docs/y.md", fs.readFileSync("knowledge-base/a.md"))\'',
]


@pytest.mark.parametrize("command", NOT_EDITS)
def test_a_read_that_isnt_an_edit_is_denied(home: Path, command: str) -> None:
    assert KB_DENY in _denied(_run(home, command)), command
    assert _events(home, "kb_read_gate.kb_edit") == []


# ----- writes into the KB alone are no read ----------------------------------------------------------------

WRITES_ONLY = [
    'python3 -c \'open("knowledge-base/new.md", "w").write("x")\'',
    'python3 -c \'import shutil; shutil.copy("docs/a.md", "knowledge-base/new.md")\'',
    'ruby -e \'File.write("knowledge-base/new.md", "x")\'',
    'node -e \'require("fs").writeFileSync("knowledge-base/new.md", "x")\'',
    "ruby -i -pe 'gsub(/x/, \"y\")' docs/a.md",
]


@pytest.mark.parametrize("command", WRITES_ONLY)
def test_a_write_into_the_kb_alone_is_allowed_and_not_recorded(home: Path, command: str) -> None:
    assert _run(home, command) is None, command
    assert _events(home, "kb_read_gate.kb_edit") == []
    assert _events(home, "kb_read_gate.unresolved_read") == []


@pytest.mark.parametrize(
    "command",
    [
        "ruby -i -pe 'gsub(/x/, \"y\")' knowledge-base/a.md docs/a.md",
        "perl -pi -e 's/x/y/' knowledge-base/a.md docs/a.md",
    ],
)
def test_an_in_place_edit_of_a_kb_note_and_a_vault_file_counts_the_vault_file(home: Path, command: str) -> None:
    """Each operand rewritten in place, one in the KB and one outside: an edit, recorded with the file outside the KB
    counted apart (what it read in the KB may now be there). DENY_INTERPRETER_KB_EDITS denies it."""
    assert _run(home, command) is None, command
    (row,) = _events(home, "kb_read_gate.kb_edit")
    assert row["payload"] == {"mode": RUN["SCOUT_MODE"], "targets": 1, "non_kb_targets": 1}


@pytest.mark.parametrize(
    "command",
    [
        "ruby -i -pe 'gsub(/x/, \"y\")' knowledge-base/a.md docs/a.md",
        """python3 -c 'p = "knowledge-base/a.md"; q = "docs/a.md"; a = open(p).read(); b = open(q).read(); """
        """open(p, "w").write(a); open(q, "w").write(b + a)'""",
    ],
)
def test_with_the_switch_on_an_edit_that_rewrites_a_vault_file_is_denied(
    home: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(gate, "DENY_INTERPRETER_KB_EDITS", True)
    assert KB_DENY in _denied(_run(home, command)), command
