"""kb-read-gate judges the readers whose files aren't simply their operands (scout.kb.other_reads): a copy or an
archive out of the KB, ``diff -r`` over a folder holding it, ``dd if=``, ``source``, and the files a sed, awk or
perl program names in its own text, plus the plain readers ``_READ_COMMANDS`` lacked (join, expand, look, shuf…).
A program's path built at run time is recorded as ``program_path``, not denied (DENY_UNRESOLVED_READS is off).
And two here-document forms from #790's review: ``"$(cat <<'EOF' … )"`` is judged by the path it prints, and an
unquoted body's substitutions see the variables the command set before it.

Synthetic vault only. tests/integration/test_kb_read_gate_zsh.py runs the forms that print a note under zsh;
tests/unit/test_hook_prefilter_differential.py holds the shell pre-filter to sending them all to Python.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb.other_reads import COVERS, FILE, other_reads
from scout.kb.raw_read import raw_kb_read
from tests.unit.test_hook_prefilter_differential import READER_FORMS
from tests.unit.test_hooks_kb_read_gate import RUN, _denied, _payload

KB_DENY = "Scheduled runs don't read knowledge-base/"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with a vault at ``~/Vault``: a KB, action items with their context folder, and ordinary folders."""
    h = tmp_path / "home"
    v = h / "Vault"
    for rel in (
        "knowledge-base/a.md",
        "knowledge-base/x.md",
        "knowledge-base/projects/a/a.md",
        "action-items/context/x.md",
        "action-items/action-items-2026-11-23.md",
        "docs/a.md",
        "docs/d.md",
        "scripts/a.md",
    ):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text("x\n")
    (v / "scout-config.yaml").write_text("user: {}\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return h.resolve()


def _run(home: Path, command: str, *, cwd: Path | None = None):
    return gate.run(stdin=_payload("Bash", {"command": command}, cwd or home / "Vault"), env={**RUN, "HOME": str(home)})


def _events(home: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted((home / "Vault" / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines()]
    return rows


# ----- must deny ------------------------------------------------------------------------------------------

MORE_FORMS = [
    # copies and archives, every way they name their sources
    "cp -t ../x action-items/con*/x.md",
    "cp -R action-items/con* /tmp/x",
    "env -C action-items tar cf - context",
    "tar -C action-items -cf - context",
    "tar -cf ../a.tar knowledge-base",
    "bsdtar -cf - action-items/con*",
    "install -m 644 action-items/con*/x.md ../x.md",
    "scp -r action-items/con* ../x",
    "rsync -r . ../backup",
    "ditto action-items ../x",
    "pax -rw action-items ../x",
    "cd action-items; csplit con*/x.md 1",
    "gzip -rc . > ../all.gz",
    "bzip2 -c action-items/con*/x.md",
    "zstd -c action-items/con*/x.md",
    "printf 'action-items/context/x.md\\n' | cpio -o",
    "find action-items | cpio -o",
    "bash -c 'tar cf - .'",
    "tar cf - knowledge-base | tar xOf -",
    # diff -r, from wherever it starts
    "diff -r . ../other",
    "cd action-items; diff -r . ../other",
    "diff -rq docs knowledge-base",
    # dd, source
    "dd if=knowledge-base/a.md",
    "cd action-items; dd if=con*/x.md",
    "source knowledge-base/a.md",
    # sed, awk and perl programs that name a file
    "cd action-items; sed 'R context/x.md' ../docs/a.md",
    "cd action-items; sed -n -e 1p -e '$r context/x.md' ../docs/a.md",
    "cd action-items; sed -e 's/x/y/w ../out' -e 'r context/x.md' ../docs/a.md",
    "sed -f knowledge-base/a.md docs/a.md",
    "cd action-items; sed '1e cat context/x.md' ../docs/a.md",
    "cd action-items; nawk 'BEGIN{getline l < \"context/x.md\"; print l}'",
    "cd action-items; gawk 'BEGIN{system(\"cat context/x.md\")}'",
    "cd action-items; awk 'BEGIN{print | \"cat context/x.md\"}'",
    "cd action-items; awk 'BEGIN{c=\"cat context/x.md\"; c | getline l; print l}'",
    "cd action-items; awk 'BEGIN{getline l < f; print l}' f=context/x.md",
    "awk -f knowledge-base/a.md docs/a.md",
    "cd action-items; perl -e 'open(my $fh, \"<context/x.md\") or die; print <$fh>'",
    "cd action-items; perl -e 'open F, q(<), \"x\"; print <>' context/x.md",
    "cd action-items; perl -lne 'print' con*/x.md",
    "cd action-items; perl -0777 -ne print con*/x.md",
    "cd action-items; perl -e 'print `cat context/x.md`'",
    "cd action-items; perl -e 'system(\"cat context/x.md\")'",
    'cd action-items; perl -e \'open(F, "-|", "cat", "context/x.md"); print <F>\'',
    # plain readers
    "cd action-items; unexpand con*/x.md",
    "cd action-items; sdiff con*/x.md ../docs/a.md",
    "cd action-items; zless con*/x.md",
    "cd action-items; pandoc -t plain con*/x.md",
]


@pytest.mark.parametrize("command", [*READER_FORMS, *MORE_FORMS])
def test_a_reader_of_a_kb_note_is_denied_in_a_run(home: Path, command: str) -> None:
    assert KB_DENY in _denied(_run(home, command)), command


@pytest.mark.parametrize(
    "command",
    [
        "cp -r action-items/context ../x",
        "tar cf - knowledge-base",
        "diff -rN action-items e",
        "sed 'r knowledge-base/a.md' docs/a.md",
        "awk 'BEGIN{getline l < \"knowledge-base/a.md\"}'",
        'perl -e \'open(F, "<", "knowledge-base/a.md"); print <F>\'',
        "perl -ne print knowledge-base/a.md",
        "dd if=knowledge-base/a.md",
        ". knowledge-base/a.md",
        "join knowledge-base/a.md docs/a.md",
        "find knowledge-base | cpio -o",
        "awk 'BEGIN{system(\"cat knowledge-base/a.md\")}'",
        "tar cf - . | tar xOf -",
        "diff -rN . e",
        "zip -qr - .",
        "cd knowledge-base && tar cf - .",
    ],
)
def test_the_evals_judge_counts_them_by_spelling_too(command: str) -> None:
    """raw_kb_read is what the eval's privacy_hygiene check counts: the same readers, judged by how they're spelled
    (and, given the folders, by where a recursive one starts)."""
    assert raw_kb_read(command, cwd="/v", vault="/v"), command


# ----- must allow -----------------------------------------------------------------------------------------

ALLOWED = [
    "tar cf ../out.tar docs/",
    "tar -czf ../out.tgz docs scripts",
    "tar xf ../a.tar -C ../x",
    "cp -r docs/ /tmp/x",
    "cp docs/a.md ../x.md",
    "rsync -a docs/ ../x/",
    "zip -qr ../z.zip docs",
    "pax -w docs > ../p.tar",
    "diff -r scripts/ ../other/",
    "diff -u docs/a.md docs/d.md",
    "diff action-items/action-items-2026-11-23.md docs/a.md",
    "git diff --stat",
    "sed 's/a/b/' docs/a.md",
    "sed -n '1p;$p' docs/a.md",
    "sed -i '' 's/a/b/' docs/a.md",
    "sed 'r docs/d.md' docs/a.md",
    "sed 's/x/y/w ../out' docs/a.md",
    "awk '{print $1}' docs/a.md",
    "awk 'BEGIN{print \"x\"}'",
    "awk 'BEGIN{while((getline l < \"docs/a.md\")>0) print l}'",
    "awk -v f=docs/a.md 'BEGIN{getline l < f; print l}'",
    "awk '{ while ((getline line < ARGV[1]) > 0) n++ } END {print n}' docs/a.md",
    "awk 'BEGIN{\"date\" | getline d; print d}'",
    "awk '{print | \"sort\"}' docs/a.md",
    "perl -ne print docs/a.md",
    "perl -pi -e 's/a/b/' docs/a.md",
    'perl -e \'open(F, "<", "docs/a.md"); print <F>\'',
    'perl -e \'open(F, ">", "knowledge-base/new.md")\'',
    "perl -e 'print \"hello\\n\"'",
    "dd if=/dev/zero of=../x count=1 2>/dev/null",
    "dd if=docs/a.md of=/dev/null",
    "gzip -c docs/a.md",
    "join docs/a.md docs/d.md",
    "source scripts/a.md",
    "scripts/scoutctl kb read projects/a/a.md",
    # a copy inside the KB writes there, and reads nothing out of it (the write gates judge the write)
    "cp knowledge-base/a.md knowledge-base/b.md",
    "cd knowledge-base; cp a.md a-copy.md",
    "cd action-items; cp context/x.md context/y.md",
    # a lister's output read through a substitution or a loop, from a folder that holds no KB note
    "cat $(find docs -name '*.md')",
    'find docs | while read f; do cat "$f"; done',
    'for f in $(rg --files scripts); do wc -l "$f"; done',
    'git ls-files docs | while IFS= read -r f; do head -1 "$f"; done',
    'cd action-items; find . | while read f; do echo "$f"; done',
    'fd . docs | while read f; do cat "$f"; done',
    "cat $(fd -e py)",
    "cat $(ag -g . scripts)",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_an_ordinary_copy_compare_or_program_is_allowed(home: Path, command: str) -> None:
    assert _run(home, command) is None, command
    assert not [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"], command


# ----- a path a program builds at run time ----------------------------------------------------------------

BUILT = [
    'awk \'BEGIN{getline l < ("kn" "owledge-base/a.md"); print l}\'',
    "awk 'BEGIN{getline l < f; print l}'",
    "awk 'BEGIN{f = d \"/x.md\"; getline l < f}'",
    "perl -e 'open(F, $ENV{X}); print <F>'",
    'perl -e \'open(F, "<", "$d/x.md"); print <F>\'',
    "perl -e 'print `cat $f`'",
    "tar cf - -T ../list.txt",
]


@pytest.mark.parametrize("command", BUILT)
def test_a_programs_path_built_at_run_time_is_allowed_and_recorded(home: Path, command: str) -> None:
    assert gate.DENY_UNRESOLVED_READS is False
    assert _run(home, command) is None
    (row,) = [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]
    assert row["payload"] == {"categories": ["program_path"], "mode": RUN["SCOUT_MODE"]}
    assert "program_path" in gate.UNRESOLVED_CATEGORIES


@pytest.mark.parametrize("command", BUILT)
def test_with_the_switch_on_a_built_path_is_denied(home: Path, monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.setattr(gate, "DENY_UNRESOLVED_READS", True)
    assert "can't work out" in _denied(_run(home, command))


def test_a_copy_to_a_target_the_gate_cant_work_out_still_judges_its_sources(home: Path) -> None:
    assert KB_DENY in _denied(_run(home, "cp -r action-items/con* $(mktemp -d)"))
    assert _run(home, "cp -r docs $(mktemp -d)") is None
    assert not [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]


# ----- the two here-document forms from #790's review -----------------------------------------------------


def test_a_heredoc_inside_a_quoted_substitution_is_judged_by_its_path(home: Path) -> None:
    assert _run(home, "cat \"$(cat <<'EOF'\ndocs/d.md\nEOF\n)\"") is None
    assert KB_DENY in _denied(_run(home, "cat \"$(cat <<'EOF'\nknowledge-base/a.md\nEOF\n)\""))
    assert KB_DENY in _denied(_run(home, 'cat "$(cat <<EOF\nknowledge-base/a.md\nEOF\n)"'))


def test_a_heredoc_with_more_than_a_path_in_it_is_command_output(home: Path) -> None:
    assert _run(home, "cat \"$(cat <<'EOF'\ndocs/a.md docs/d.md\nEOF\n)\"") is None
    (row,) = [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]
    assert row["payload"]["categories"] == ["command_output"]


def test_an_unquoted_heredoc_sees_the_variables_set_before_its_command(home: Path) -> None:
    assert KB_DENY in _denied(_run(home, "X=kn; cat <<EOF\n$(cat ${X}owledge-base/a.md)\nEOF"))
    assert KB_DENY in _denied(_run(home, "X=kn; Y=x; cat <<EOF\nnote $(cat ${X}owledge-base/${Y}.md)\nEOF"))
    # Set after the command: unset when the body runs.
    assert _run(home, "cat <<EOF\n$(cat ${X}owledge-base/a.md)\nEOF\nX=kn") is None
    assert [r["payload"]["categories"] for r in _events(home)] == [["unset_variable"]]


def test_a_quoted_heredoc_body_runs_nothing(home: Path) -> None:
    assert _run(home, "X=kn; cat <<'EOF'\n$(cat ${X}owledge-base/a.md)\nEOF") is None
    assert _events(home) == []


# ----- what other_reads finds -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("words", "reads", "literal", "dests"),
    [
        (["cp", "-r", "a", "b", "c"], [("a", COVERS), ("b", COVERS)], [], ["c"]),
        (["cp", "a", "b"], [("a", FILE)], [], ["b"]),
        (["cp", "--target-directory=d", "a"], [("a", FILE)], [], ["d"]),
        (["rsync", "-avz", "--exclude", "x", "src/", "dst/"], [("src/", COVERS)], [], ["dst/"]),
        (["tar", "czvf", "out.tgz", "d1", "d2"], [("d1", COVERS), ("d2", COVERS)], [], ["out.tgz"]),
        (["tar", "-C", "base", "-cf", "-", "a", "-C", "/abs", "b"], [("base/a", COVERS), ("/abs/b", COVERS)], [], []),
        (["tar", "--create", "--file=x.tar", "d"], [("d", COVERS)], [], ["x.tar"]),
        (["tar", "xf", "x.tar", "member"], [("x.tar", FILE)], [], []),
        (["zip", "-r", "-x", "*.log", "*.tmp", "-q", "out.zip", "d"], [("d", COVERS)], [], ["out.zip"]),
        (["pax", "-w", "-f", "out.pax", "d"], [("d", COVERS)], [], ["out.pax"]),
        (["split", "-l", "10", "f", "pre"], [("f", FILE)], [], ["pre"]),
        (["gzip", "-r", "d"], [("d", COVERS)], [], []),
        (["diff", "-x", "*.o", "-r", "a", "b"], [("a", COVERS), ("b", COVERS)], [], []),
        (["dd", "if=f", "of=g", "bs=1"], [("f", FILE)], [], []),
        (["sed", "-n", "/x/{r f1\n}", "g"], [], ["f1"], []),
        (["sed", "-e", "s|a|b|g", "-e", "2r f2", "g"], [], ["f2"], []),
        (["sed", "y/ab/cd/;3r f3", "g"], [], ["f3"], []),
        (["awk", 'BEGIN{getline < "f1"; getline x < "f2"}'], [], ["f1", "f2"], []),
        (["perl", "-e", "open(F, '<', 'f1'); open(G, 'f2'); open(H, '>f3')"], [], ["f1", "f2"], []),
    ],
)
def test_other_reads_finds_what_a_command_reads(
    words: list[str], reads: list[tuple[str, str]], literal: list[str], dests: list[str]
) -> None:
    found = other_reads(words[0], words[1:])
    assert found is not None
    assert (found.reads, found.literal, found.dests) == (reads, literal, dests)


@pytest.mark.parametrize(
    "words",
    [["diff", "a", "b"], ["dd", "of=x"], ["cat", "x"], ["perl", "script.pl", "x"], ["cp", "--help"], ["source"]],
)
def test_other_reads_finds_nothing_more_for_these(words: list[str]) -> None:
    assert other_reads(words[0], words[1:]) is None


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        # options: `--`, an attached value, a value flag with nothing after it
        (["cp", "--", "-a", "b"], {"reads": [("-a", FILE)], "dests": ["b"]}),
        (["cp", "-tdest", "a"], {"reads": [("a", FILE)], "dests": ["dest"]}),
        (["cp", "a", "b", "-t"], {"reads": [("a", FILE)], "dests": ["b"]}),
        (["rsync", "-r", "src"], {"reads": [("src", COVERS)], "dests": []}),
        # archives that read stdin's list, or read an archive
        (["tar", "cf", "-", "-T", "-"], {"stdin_list": True}),
        (["pax", "-r", "-f", "in.pax"], {"reads": [("in.pax", FILE)]}),
        (["cpio", "-i", "-I", "in.cpio"], {"reads": [("in.cpio", FILE)]}),
        (["cpio", "-p", "dest"], {"stdin_list": True, "dests": ["dest"]}),
        (["zip", "-@", "out.zip"], {"stdin_list": True, "dests": ["out.zip"]}),
        # sed: an address alone, text and write commands, labels, `r /dev/stdin`, an empty `e`
        (["sed", "1p;2", "f"], {"literal": []}),
        (["sed", "s/a\\/b/c/g;r f1", "f"], {"literal": ["f1"]}),
        (["sed", "1a text r f1", "f"], {"literal": []}),
        (["sed", "w out", "f"], {"literal": []}),
        (["sed", ":a;N;ba;r f1", "f"], {"literal": ["f1"]}),
        (["sed", "b", "f"], {"literal": []}),
        (["sed", "r /dev/stdin", "f"], {"literal": []}),
        (["sed", "1e", "f"], {"programs": []}),
        (["sed", "1e date", "f"], {"programs": ["date"]}),
        # awk: a piped command it builds, a program file
        (["awk", 'BEGIN{c = x "y"; c | getline}'], {"built": True}),
        (["awk", "-f", "prog.awk", "f"], {"reads": [("prog.awk", FILE)]}),
        # perl: an escape in a literal, an empty open, a piped open (built or literal), `-`, ARGV, system, qx
        (["perl", "-e", 'open(F, "<", "a\\"b")'], {"literal": ['a"b']}),
        (["perl", "-e", "open(F, )"], {"literal": [], "built": False}),
        (["perl", "-e", 'open(F, "-|", "cat", $x)'], {"built": True}),
        (["perl", "-e", 'open(F, "cat x |")'], {"programs": ["cat x"]}),
        (["perl", "-e", 'open(F, "<", "-"); open(G, "<", $ARGV[0])'], {"literal": [], "built": False}),
        (["perl", "-e", "open(F, shift)"], {"built": False}),
        (["perl", "-e", 'system("cat $x")'], {"built": True}),
        (["perl", "-e", "print `date`"], {"programs": ["date"]}),
        (["perl", "-e", "print qx(date)"], {"built": True}),
    ],
)
def test_other_reads_edges(words: list[str], expected: dict) -> None:
    found = other_reads(words[0], words[1:])
    assert found is not None
    assert {key: getattr(found, key) for key in expected} == expected


def test_perl_with_no_inline_program_reads_nothing_more() -> None:
    assert other_reads("perl", ["-w"]) is None
