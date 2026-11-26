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
import shlex
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb.other_reads import COVERS, FILE, OtherReads, other_reads
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
    # #817's review: a copy out of the KB, then a read of the copy, by every copier, to a folder outside the vault
    "rsync -a knowledge-base/projects /tmp/x && cat /tmp/x/projects/a/a.md",
    "rsync -a knowledge-base/ /tmp/x/",
    "ditto knowledge-base /tmp/k",
    "ditto knowledge-base/a.md /tmp/k.md",
    "install knowledge-base/a.md /tmp/a.md",
    "install -m 644 knowledge-base/a.md /tmp/a.md",
    "find knowledge-base | cpio -p /tmp/x",
    "cd knowledge-base && find . | cpio -pd /tmp/x",
    "pax -rw knowledge-base /tmp/x",
    "tar cf - knowledge-base | tar xf - -C /tmp/x",
    "tar -C knowledge-base -cf - . | (cd /tmp/x && tar xf -)",
    "cp -r knowledge-base /tmp/x",
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
    # #819 review: perl's list forms, @ARGV, q{} / qx{}; awk's joined and held commands, ENVIRON; tar's filter;
    # a path the shell fills into sed's script; echo/printf words read by a loop
    'perl -e \'system "cat", "knowledge-base/a.md"\'',
    'perl -e \'exec("cat", "knowledge-base/a.md")\'',
    'cd action-items; perl -e \'system "cat", "context/x.md"\'',
    "perl -e 'local @ARGV=(\"knowledge-base/a.md\"); print <>'",
    "perl -e '@ARGV = qw(knowledge-base/a.md); print while <>'",
    "perl -e 'push @ARGV, \"knowledge-base/a.md\"; print <>'",
    "perl -e 'print qx{cat knowledge-base/a.md}'",
    "perl -e 'open(my $f, \"<\", q{knowledge-base/a.md}); print <$f>'",
    "perl -e '$f = q{knowledge-base/a.md}; open(F, $f); print <F>'",
    'perl -e \'my $f = "knowledge-base/a.md"; open(F, "<", $f); print <F>\'',
    'awk \'BEGIN{system("cat kn" "owledge-base/a.md")}\'',
    "awk 'BEGIN{c = \"cat knowledge-base/a.md\"; system(c)}'",
    'awk \'BEGIN{("cat kn" "owledge-base/a.md") | getline l; print l}\'',
    'awk \'BEGIN{c = "cat kn" "owledge-base/a.md"; while ((c | getline l) > 0) print l}\'',
    'awk \'BEGIN{print "cat knowledge-base/a.md" | "sh"}\'',
    'awk \'BEGIN{while ((getline l < ("kn" "owledge-base/a.md")) > 0) print l}\'',
    "F=knowledge-base/a.md awk 'BEGIN{getline l < ENVIRON[\"F\"]; print l}'",
    "cd action-items; env F=context/x.md awk 'BEGIN{getline l < ENVIRON[\"F\"]; print l}'",
    "tar cf knowledge-base/x.tar --use-compress-program='tee /dev/stderr' knowledge-base/a.md",
    "tar -I 'tee /dev/stderr' -cf knowledge-base/x.tar knowledge-base/a.md",
    'D=knowledge-base; sed "r $D/a.md" docs/a.md',
    "printf '%s\\n' */*/* | while read f; do cat \"$f\"; done",
    'echo knowledge-base/a.md | while read f; do cat "$f"; done',
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
    "awk 'BEGIN{getline l < f; print l}'",
    "awk 'BEGIN{f = d \"/x.md\"; getline l < f}'",
    "perl -e 'open(F, $ENV{X}); print <F>'",
    'perl -e \'open(F, "<", "$d/x.md"); print <F>\'',
    "perl -e 'print `cat $f`'",
    "tar cf - -T ../list.txt",
    # #819 review: a script file's text isn't in the command; a command or path built from what isn't a literal
    "sed -f ../script.sed docs/a.md",
    "awk -f ../prog.awk docs/a.md",
    "awk 'BEGIN{system(x)}'",
    "awk 'BEGIN{system(\"cat \" substr(x, 1))}'",
    "awk 'BEGIN{(c d) | getline l}'",
    "awk 'BEGIN{print \"x\" | cmd}'",
    "awk 'BEGIN{print $0 | \"sh\"}'",
    "perl -e 'exec \"cat\", $f'",
    "perl -e 'print qx{cat $f}'",
    "perl -e '@ARGV = ($f); print <>'",
]


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ('sed "r $(echo knowledge-base/a.md)" docs/a.md', "command_output"),  # a path the shell fills in
        ("awk 'BEGIN{getline l < ENVIRON[\"F\"]; print l}'", "unset_variable"),  # F set neither here nor in the run
    ],
)
def test_a_path_the_shell_or_environment_fills_in_is_recorded(home: Path, command: str, category: str) -> None:
    assert _run(home, command) is None
    (row,) = [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]
    assert row["payload"]["categories"] == [category]


def test_tars_filter_program_is_a_command_of_its_own(home: Path) -> None:
    assert KB_DENY in _denied(_run(home, "tar -I 'cat knowledge-base/a.md' -cf ../x.tar docs"))
    assert _run(home, "tar -czf knowledge-base/backup.tgz knowledge-base/a.md") is None  # into the KB, no filter


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
    [["diff", "a", "b"], ["dd", "of=x"], ["cat", "x"], ["perl", "-v"], ["cp", "--help"], ["source"]],
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
        (["perl", "-e", "print qx(date)"], {"programs": ["date"]}),
        (["perl", "-e", "print qx{cat $f}"], {"built": True}),
        # awk's evaluator: escapes, where an expression starts and ends, words inside strings, mixed ENVIRON
        (["awk", 'BEGIN{getline l < "a\\"b"}'], {"literal": ['a"b']}),
        (["awk", 'print "a" | "sort"'], {"programs": ["sort"]}),
        (["awk", 'BEGIN{c = "ls"; while ((c | getline l) > 0) n++}'], {"programs": ["ls"]}),
        (["awk", 'BEGIN{x = "date" | getline}'], {"programs": ["date"]}),
        (["awk", "BEGIN{system()}"], {"built": True}),
        (["awk", 'BEGIN{print "f = x"; f = "a"; getline < f}'], {"literal": ["a"], "built": False}),
        (["awk", 'BEGIN{getline < (ENVIRON["H"] "/x")}'], {"built": True, "environ": []}),
        (["awk", 'BEGIN{print "getline < x | getline; system(y)"}'], {"built": False, "programs": []}),
        (["awk", 'BEGIN{getline < "a" x}'], {"literal": ["a"], "built": True}),
        # perl's quote-like strings: escapes, nesting, a same-character delimiter, a literal with more after it
        (["perl", "-e", "open(F, q{a\\}b})"], {"literal": ["a}b"]}),
        (["perl", "-e", "open(F, q{a{b}c})"], {"literal": ["a{b}c"]}),
        (["perl", "-e", "open(F, q/a b/)"], {"literal": ["a b"]}),
        (["perl", "-e", 'open(F, q{a} . "b")'], {"literal": ["ab"], "built": False}),
        (["perl", "-e", '$a=$b; $b=$c; $c=$d; $d=$e; $e="x"; open(F, $a)'], {"built": True}),
        (["perl", "-e", 'open(F, $mode, "x")'], {"built": True}),
        (["perl", "-e", 'open(F, "-"); open(G, ">out")'], {"literal": [], "built": False}),
        # escapes each language decodes, and those it can't
        (["perl", "-e", 'open(F, "<", "a\\tb\\cAc")'], {"literal": ["a\tb\x01c"]}),
        (["perl", "-e", 'open(F, "<", "\\x{110000}")'], {"literal": [], "built": True}),
        (["perl", "-e", 'open(F, "<", "abc)'], {"built": True}),
        (["perl", "-e", "@ARGV = qw(a b"], {"built": True}),
        (["awk", 'BEGIN{y = 1; x = "date" | getline}'], {"programs": ["date"]}),
        (["awk", 'BEGIN{print "x f = y"; f = "a"; getline < f}'], {"literal": ["a"], "built": False}),
        (["awk", 'BEGIN{x = "print me"; getline < "a"}'], {"literal": ["a"], "programs": []}),
        (["awk", "BEGIN{" + "; ".join(f'f = "n{i}"' for i in range(17)) + "; getline < (f f)}"], {"built": True}),
    ],
)
def test_other_reads_edges(words: list[str], expected: dict) -> None:
    found = other_reads(words[0], words[1:])
    assert found is not None
    assert {key: getattr(found, key) for key in expected} == expected


def test_perl_with_its_program_on_stdin_is_built_and_a_script_file_is_judged_as_one() -> None:
    assert other_reads("perl", ["-w"]) == OtherReads(built=True)
    # a script file: the gate judges the file (scout.kb.trusted_scripts), as python3's
    assert other_reads("perl", ["-w", "x.pl", "a"]) == OtherReads(reads=[("x.pl", FILE)], script="x.pl")


# ----- the rule: a construct that opens, lists, reads or runs is never silent over what it can't resolve ----

# Every perl and awk construct that opens, lists, reads or runs something, with ARG where its file or command
# goes (%ARG%). Its argument must resolve to literals for the gate to judge it; anything else must be recorded.
PERL_CONSTRUCTS = [
    "open(F, '<', %ARG%); print <F>",
    "open(F, %ARG%); print <F>",
    "open F => '<', %ARG%; print <F>",
    "sysopen(F, %ARG%, 0); print <F>",
    "opendir(D, %ARG%); print readdir D",
    "print for glob(%ARG%)",
    "do %ARG%",
    "require %ARG%",
    "print readpipe(%ARG%)",
    "system(%ARG%)",
    "exec 'cat', %ARG%",
    "@ARGV = (%ARG%); print <>",
    "push @ARGV, %ARG%; print <>",
    "copy(%ARG%, '/tmp/x')",
]
PERL_UNRESOLVED = ["$f", "'kn' . $x", "sprintf('%s', $d)", "join('/', $a, 'b')", '"$d/a.md"', "uc($f)", "f()",
                   '"\\Lx"', "$h{k}", "$x->[0]"]  # fmt: skip
AWK_CONSTRUCTS = [
    "getline l < %ARG%",
    "getline l < (%ARG%)",
    "%ARG% | getline l",
    "(%ARG%) | getline l",
    "system(%ARG%)",
    'print "x" | %ARG%',
    'print %ARG% | "sh"',
    'printf "%s", %ARG% | "sh"',
    "ARGV[ARGC++] = %ARG%",
]
AWK_UNRESOLVED = ["f", 'd "/a.md"', 'sprintf("%s", d)', "toupper(d)", "$1", "x[1]", '"kn\\qx"', "substr(d, 1)"]


def _never_silent(home: Path, command: str) -> None:
    out = _run(home, command)
    recorded = [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]
    assert out is not None or recorded, f"neither denied nor recorded: {command}"


@pytest.mark.parametrize("construct", PERL_CONSTRUCTS)
@pytest.mark.parametrize("arg", PERL_UNRESOLVED)
def test_a_perl_construct_over_what_it_cant_resolve_is_never_silent(home: Path, construct: str, arg: str) -> None:
    program = construct.replace("%ARG%", arg)
    _never_silent(home, "perl -MFile::Copy -e " + shlex.quote(program))


@pytest.mark.parametrize("construct", AWK_CONSTRUCTS)
@pytest.mark.parametrize("arg", AWK_UNRESOLVED)
def test_an_awk_construct_over_what_it_cant_resolve_is_never_silent(home: Path, construct: str, arg: str) -> None:
    program = "BEGIN{" + construct.replace("%ARG%", arg) + "}"
    _never_silent(home, "awk " + shlex.quote(program))


@pytest.mark.parametrize(
    "command",
    [
        "perl -e 'print <$d/*>'",
        "perl -e 'print `cat $f`'",
        "perl -e 'print qx{cat $f}'",
        'perl -e \'open(P, "|-", "sh"); print P $cmd\'',
        'awk \'BEGIN{f = "x"; sub(/x/, "k", f); getline l < f}\'',
        "awk -f ../prog.awk docs/a.md",
        "sed -f ../script.sed docs/a.md",
    ],
)
def test_the_other_unresolved_forms_are_never_silent(home: Path, command: str) -> None:
    _never_silent(home, command)


@pytest.mark.parametrize(
    "command",
    [
        "awk 'BEGIN{getline l < \"kn\\x6fwledge-base/a.md\"; print l}'",
        "awk 'BEGIN{getline l < \"\\153nowledge-base/a.md\"; print l}'",
        "awk 'BEGIN{getline l < \"knowledge-base\\/a.md\"; print l}'",
        'perl -e \'open(F, "<", "kn\\x6fwledge-base/a.md"); print <F>\'',
        'perl -e \'open(F, "<", "\\153nowledge-base/a.md"); print <F>\'',
        'perl -e \'open(F, "<", "kn\\x{6f}wledge-base/a.md"); print <F>\'',
        'perl -e \'open(F, "<", "kn\\o{157}wledge-base/a.md"); print <F>\'',
        'perl -e \'open(F, "<", "kn\\N{U+6F}wledge-base/a.md"); print <F>\'',
        "perl -e 'print `cat kn\\x6fwledge-base/a.md`'",
        "perl -e 'print qx{cat kn\\x6fwledge-base/a.md}'",
        'perl -e \'open(F, "<", "kn" . "owledge-base/a.md"); print <F>\'',
        "perl -e 'print for glob(\"knowledge-base/*\")'",
        "perl -e 'print <knowledge-base/*>'",
        "perl -e 'sysopen(F, \"knowledge-base/a.md\", 0); print <F>'",
        "perl -e 'opendir(D, \"knowledge-base\"); print readdir D'",
        'perl -MFile::Copy -e \'copy("knowledge-base/a.md", "/tmp/x")\'',
        "perl -e 'do \"knowledge-base/a.md\"'",
        "perl -e 'require \"knowledge-base/a.md\"'",
        "perl -e 'print readpipe(\"cat knowledge-base/a.md\")'",
        "awk 'BEGIN{ARGV[ARGC++] = \"knowledge-base/a.md\"} {print}'",
        'awk \'BEGIN{printf "cat %s\\n", "knowledge-base/a.md" | "sh"}\'',
    ],
)
def test_an_escaped_or_joined_literal_that_names_the_kb_is_denied(home: Path, command: str) -> None:
    assert KB_DENY in _denied(_run(home, command)), command


@pytest.mark.parametrize(
    "command",
    [
        "perl -e 'print \"do it now\"'",
        "perl -e 'require strict; print 1'",
        "perl -e 'my @x = do { 1 }; print @x'",
        "perl -e 'print 1 if 2 < 3 and 4 > 1'",
        'perl -e \'open(F, "<", "docs/\\x61.md"); print <F>\'',
        "awk 'BEGIN{getline l < \"docs/\\141.md\"; print l}'",
    ],
)
def test_a_harmless_program_stays_quiet(home: Path, command: str) -> None:
    assert _run(home, command) is None
    assert _events(home) == []


# ----- what moves where perl looks for a relative path: @INC and chdir ----------------------------------------

# Each puts DIR in @INC, as a perl flag, an environment assignment or program text before the program.
INC_CHANGERS = [
    ("-IDIR", ""),
    ("-I DIR", ""),
    ("-Mlib=DIR", ""),
    ("", 'use lib "DIR"; '),
    ("", "use lib qw(DIR); "),
    ("", 'BEGIN{unshift @INC, "DIR"} '),
    ("", 'push @INC, "DIR"; '),
    ("", 'BEGIN{@INC = ("DIR")} '),
    ("ENV PERL5LIB=DIR", ""),
    ("ENV PERLLIB=DIR", ""),
    ("EXPORT PERL5LIB=DIR", ""),
]
REQUIRERS = ['do "FILE"; print $@', 'require "FILE"', 'do("FILE")']
# (folder, the file in it): the KB itself, and a KB folder's parent with a path into it.
INC_PLACES = [("knowledge-base", "a.md"), ("knowledge-base/projects", "a/a.md"), ("action-items", "context/x.md")]


def _perl_with_inc(changer: tuple[str, str], program: str, folder: str) -> str:
    flag, text = (part.replace("DIR", folder) for part in changer)
    body = shlex.quote(text + program)
    if flag.startswith("ENV "):
        return f"{flag[4:]} perl -e {body}"
    if flag.startswith("EXPORT "):
        return f"export {flag[7:]}; perl -e {body}"
    return f"perl {flag} -e {body}".replace("  ", " ")


@pytest.mark.parametrize("changer", INC_CHANGERS)
@pytest.mark.parametrize("requirer", REQUIRERS)
@pytest.mark.parametrize(("folder", "file"), INC_PLACES)
def test_a_do_or_require_through_a_kb_folder_in_inc_is_denied(
    home: Path, changer: tuple[str, str], requirer: str, folder: str, file: str
) -> None:
    command = _perl_with_inc(changer, requirer.replace("FILE", file), folder)
    assert KB_DENY in _denied(_run(home, command)), command


@pytest.mark.parametrize("changer", [c for c in INC_CHANGERS if not c[0].startswith(("ENV", "EXPORT", "-"))])
@pytest.mark.parametrize("requirer", REQUIRERS)
def test_a_folder_put_in_inc_by_what_it_cant_resolve_is_never_silent(
    home: Path, changer: tuple[str, str], requirer: str
) -> None:
    text = changer[1].replace('"DIR"', "$d").replace("qw(DIR)", "($d)")
    _never_silent(home, "perl -e " + shlex.quote(text + requirer.replace("FILE", "a.md")))


CHDIRS = ['chdir "DIR"; ', 'chdir("DIR"); ', '$d = "DIR"; chdir $d; ', "chdir q{DIR}; "]
OPENERS = ['open F, "FILE"; print <F>', 'open(F, "<", "FILE"); print <F>', 'do "FILE"', 'print for glob("FILE")']


@pytest.mark.parametrize("change", CHDIRS)
@pytest.mark.parametrize("opener", OPENERS)
@pytest.mark.parametrize(("folder", "file"), INC_PLACES)
def test_an_open_after_a_chdir_into_the_kb_is_denied(
    home: Path, change: str, opener: str, folder: str, file: str
) -> None:
    command = "perl -e " + shlex.quote(change.replace("DIR", folder) + opener.replace("FILE", file))
    assert KB_DENY in _denied(_run(home, command)), command


@pytest.mark.parametrize("change", ["chdir $d; ", "chdir(sprintf('%s', $x)); ", "chdir $ENV{D}; "])
@pytest.mark.parametrize("opener", OPENERS)
def test_an_open_after_a_chdir_it_cant_resolve_is_never_silent(home: Path, change: str, opener: str) -> None:
    _never_silent(home, "perl -e " + shlex.quote(change + opener.replace("FILE", "a.md")))


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("perl x.pl", "program_path:vault_script"),  # a script in the vault that isn't Scout's own
        ("perl ./x.pl docs/a.md", "program_path:vault_script"),
        ("perl -- x.pl", "program_path:vault_script"),
        ("perl -w x.pl", "program_path:vault_script"),
        ("perl -x ../doc.txt", "program_path"),  # a program embedded in a file outside the vault
        ("perl < ../prog.pl", "program_path"),  # stdin
    ],
)
def test_a_perl_program_from_a_file_or_stdin_is_recorded(home: Path, command: str, category: str) -> None:
    assert _run(home, command) is None
    (row,) = [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]
    assert row["payload"]["categories"] == [category]


@pytest.mark.parametrize(
    "command",
    [
        "perl -v",
        "perl -V",
        "perl -Ilib -e 'use Foo; print 1'",
        "perl -Idocs -e 'do \"a.md\"'",
        'perl -e \'chdir "docs"; open F, "a.md"; print <F>\'',
        "PERL5LIB=scripts perl -e 'print 1'",
    ],
)
def test_inc_and_chdir_outside_the_kb_stay_quiet(home: Path, command: str) -> None:
    assert _run(home, command) is None
    assert _events(home) == []


def test_a_perl_script_that_is_a_kb_note_is_denied(home: Path) -> None:
    assert KB_DENY in _denied(_run(home, "perl knowledge-base/a.md"))
