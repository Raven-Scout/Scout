"""kb-read-gate reads a command word as zsh runs it: past zsh's precommand modifiers, without its quotes.

The #820 security review, under real ``zsh -f`` with the run's options, found three ways round the gate:

- zsh's precommand modifiers. ``noglob grep -r TODO .`` is ``grep -r TODO .`` to the shell, but the gate's prefix
  list didn't know ``noglob`` or ``nocorrect`` (nor ``exec -a NAME``, whose NAME it took for the command), so it
  judged a program called ``noglob`` and let the read through. ``noglob cat knowledge-base/a.md`` too.
- A quote or backslash inside the command word (``gr''ep``, ``"gr"ep``, ``g\\rep``, ``fi''nd``). The shell
  pre-filter matched program names only when unquoted, so these never reached Python.
- A read that decompresses (BSD grep's ``-Z``, ugrep's ``-z``, ``zgrep``, ``zcat``) reads what an archive holds,
  which no file name shows. So do the archive tools (``unzip -p``, ``bsdtar -xOf``, ``7z e -so``, ``cpio -i``,
  ``ditto -x``, ``pax -r``, ``gunzip -c kb.tgz``). An archive in the vault, or one whose path the gate can't place,
  may hold notes: denied. One outside the vault is allowed.

Each must-deny case goes through the shell pre-filter (it must reach Python in a run), through the gate, and,
where plain zsh reads a note with it, through real zsh in a synthetic vault: its output carries a line only a KB
note holds. A run's own ``grep`` is Claude Code's ugrep (its shell snapshot), which plain zsh doesn't have, so the
forms that read a note only as ugrep are checked for reaching Python and for being judged as their unquoted
spelling is; their deny comes with the ugrep model (#820).

Synthetic vault only.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from tests.integration.test_kb_read_gate_zsh import OPTIONS
from tests.unit.test_hooks_kb_read_gate import RUN, _denied, _payload

MARK = "kb-only-line"  # appears only in the KB notes, and in the archives that hold them
PREFILTER = Path(__file__).resolve().parents[3] / "hooks" / "prefilter.sh"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    vault = h / "Vault"
    files = {
        "knowledge-base/a.md": f"TODO {MARK} x\n",
        "action-items/context/x.md": f"TODO {MARK}\n",
        "action-items/today.md": "TODO today\n",
        "docs/a.md": "TODO docs-line x\n",
        "plugin/x.py": "TODO py-line\n",
        "file.py": "x = 1\n",
        "scout-config.yaml": "user: {}\n",
    }
    for rel, text in files.items():
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text(text)
    # An archive of the KB, a compressed copy of a note, and a compressed log.
    with tarfile.open(vault / "kb.tgz", "w:gz") as tar:
        tar.add(vault / "knowledge-base", arcname="knowledge-base")
    (vault / "backup").mkdir()
    (vault / "backup" / "a.md.gz").write_bytes(gzip.compress(f"TODO {MARK}\n".encode()))
    (vault / "app.log.gz").write_bytes(gzip.compress(b"TODO log-line\n"))
    with zipfile.ZipFile(vault / "kb.zip", "w") as z:
        z.write(vault / "knowledge-base" / "a.md", "knowledge-base/a.md")
    if shutil.which("cpio"):  # a cpio archive of the KB, as cpio writes one
        subprocess.run(
            ["cpio", "-o", "-F", str(vault / "kb.cpio")], input="knowledge-base/a.md\n", cwd=vault, text=True,
            capture_output=True, check=False, timeout=10,
        )  # fmt: skip
    # Archives outside the vault, holding no note.
    with zipfile.ZipFile(h / "outside.zip", "w") as z:
        z.write(vault / "docs" / "a.md", "docs/a.md")
    with tarfile.open(h / "outside.tgz", "w:gz") as tar:
        tar.add(vault / "docs", arcname="docs")
    # Links from outside the vault: to its archive, to the vault itself, a loop, and one to an outside archive.
    (h / "link.zip").symlink_to(vault / "kb.zip")
    (h / "link.tgz").symlink_to(vault / "kb.tgz")
    (h / "linkdir").symlink_to(vault)
    (h / "loop.zip").symlink_to(h / "loop.zip")
    (h / "out-link.zip").symlink_to(h / "outside.zip")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    return h.resolve()


def _gate(home: Path, command: str, where: str = ""):
    return gate.run(
        stdin=_payload("Bash", {"command": command}, home / "Vault" / where), env={**RUN, "HOME": str(home)}
    )


def _zsh(home: Path, command: str, where: str = "") -> str | None:
    """What ``command`` prints under zsh, with the run's options, from ``where`` in the vault; None without zsh or
    without the program it runs."""
    if shutil.which("zsh") is None:
        return None
    res = subprocess.run(
        ["zsh", "-f", "-c", f'{OPTIONS}; cd "$1" && eval "$2"', "zsh", str(home / "Vault" / where), command],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",  # an archive's bytes
        timeout=10,
    )
    out = res.stdout + res.stderr
    return None if "command not found" in out else out


def _reaches_python(command: str, cwd: str = "/tmp/Vault") -> bool:
    """Whether the shell pre-filter sends a run's Bash ``command`` on to Python (here a stand-in that says so)."""
    payload = json.dumps({"cwd": cwd, "tool_name": "Bash", "tool_input": {"command": command}})
    res = subprocess.run(
        ["/bin/sh", str(PREFILTER), "kb-read-gate", "/bin/sh", "-c", "/bin/cat >/dev/null; echo python"],
        input=payload,
        env={"PATH": "/usr/bin:/bin", "SCOUT_MODE": "research"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert res.returncode == 200, res.stderr  # `verdict 0`: the hooks.json entry exits 0 for it
    return res.stdout.strip() == "python"


# (command, folder it runs from, whether plain zsh reads a note with it: False where only a run's ugrep does, or
# where it reads names or a binary match only; BSD where only BSD grep does: GNU grep's -Z is --null, and GNU zgrep
# has no -r).
BSD = "bsd"
MUST_DENY: list[tuple[str, str, bool | str]] = [
    # zsh's precommand modifiers, and the other prefixes the review asked to check
    ("noglob grep -r TODO .", "", True),
    ("noglob find . -name '*.md'", "", False),  # names, not text: the KB's note names from the vault root
    ("nocorrect grep -r TODO .", "", True),
    ("- grep -r TODO .", "", True),
    ("builtin exec grep -r TODO .", "", True),
    ("exec -a x grep -r TODO .", "", True),
    ("nohup grep -r TODO .", "", True),
    ("nocorrect noglob grep -r TODO .", "", True),  # zsh knows nocorrect only as the first word
    ("no''glob grep -r TODO .", "", True),
    ("\\noglob grep -r TODO .", "", True),
    ("noglob cat knowledge-base/a.md", "", True),
    ("no''glob cat knowledge-base/a.md", "", True),
    ("noglob cat context/x.md", "action-items", True),
    # a quote or backslash inside the command word
    ("gr''ep -r TODO .", "", True),
    ("g\\rep -R TODO .", "", True),
    ('"gr"ep -r -3 TODO .', "", True),
    ("fi''nd . -exec cat {} +", "", True),
    ("fi\\nd . -name '*.md'", "", False),
    ("c''at knowledge-base/a.md", "", True),
    ('"c"at context/x.md', "action-items", True),
    ("x''args cat < /dev/null; gr''ep -rn TODO .", "", True),
    # decompressing reads: an archive's members, and the greps that decompress
    ("grep -Z TODO kb.tgz", "", False),  # BSD grep -Z: "Binary file matches", a read all the same
    ("grep -a -Z TODO kb.tgz", "", BSD),
    ("grep --decompress -a TODO kb.tgz", "", BSD),
    ("zcat kb.tgz", "", False),  # macOS's zcat reads only .Z; GNU's reads this
    ("gzcat kb.tgz", "", True),
    ("grep -rZ --include='*.gz' TODO .", "", BSD),
    ("grep -r -Z --include='*.md.gz' TODO .", "", BSD),  # a `.md.gz` under knowledge-base/ is a note
    ("zgrep -r TODO .", "", BSD),
    ("ZGREP -r TODO .", "", False),  # a case-insensitive disk runs zgrep (#818); not every test disk is one
    ("GREP -rZ --include='*.gz' TODO .", "", False),
    ("zgrep -a TODO kb.tgz", "", True),
    ("zgrep TODO knowledge-base/a.md", "", True),
    ("ugrep -rz --include='*.tgz' TODO .", "", False),
    # archive tools that read an archive in the vault, or one whose path the gate can't place
    ("unzip -p kb.zip", "", True),
    ("unzip -c kb.zip", "", True),
    ("unzip -qp kb.zip", "", True),
    ("unzip -l kb.zip", "", False),  # names only
    ("funzip kb.zip", "", True),
    ("bsdtar -xOf kb.tgz", "", True),
    ("tar -xOf kb.tgz", "", True),
    ("tar xOf kb.tgz", "", True),
    ("tar -xzf kb.tgz -O", "", True),
    ("tar --extract --to-stdout --file=kb.tgz", "", True),
    ("tar -tf kb.tgz", "", False),
    ("bsdtar -cf - @kb.tgz", "", True),  # bsdtar copies the archive's members into its own output
    ("cat kb.tgz | tar -xzO", "", True),
    ("tar -xzO < kb.tgz", "", True),
    ("7z e -so kb.7z", "", False),
    ("7z x -so kb.zip", "", False),
    ("ditto -x -k kb.zip /dev/stdout", "", False),
    ("cpio -i --to-stdout < kb.cpio", "", False),  # GNU cpio's flag; macOS's bsdcpio has none
    ("cpio -i --to-stdout -F kb.cpio", "", False),
    ("cpio -it -F kb.cpio", "", False),
    ("pax -rz -f kb.tgz", "", False),
    ("pax -z -f kb.tgz", "", False),
    ("gunzip -c kb.tgz", "", True),
    ("gzip -dc kb.tgz | tar -xO", "", True),
    ("cat kb.tgz | gunzip | tar -xO", "", True),
    ("gzcat < kb.tgz", "", True),
    ("gzip -c knowledge-base/a.md", "", False),  # compressed bytes of a note: its text all the same
    ("tar -czf ../out.tgz knowledge-base", "", False),
    ("unzip -p $HOME/Vault/kb.zip", "", True),
    ("unzip -p $ARCHIVE", "", False),  # a path the gate can't place
    ("cd backup && unzip -p ../kb.zip", "", True),
    # placed by its real path: a link into the vault spelled from outside it, and one that won't resolve
    ("unzip -p ../link.zip", "", True),
    ("tar -xOf ../link.tgz", "", True),
    ("bsdtar -xOf ../linkdir/kb.tgz", "", True),
    ("grep -a -Z TODO ../link.tgz", "", BSD),
    ("gzcat < ../link.tgz", "", True),
    ("unzip -p ../loop.zip", "", False),  # a link loop: can't place it
]


@pytest.mark.parametrize(("command", "where", "zsh_reads"), MUST_DENY)
def test_a_read_zsh_runs_past_a_prefix_or_a_quote_is_denied(
    home: Path, command: str, where: str, zsh_reads: bool | str
) -> None:
    if zsh_reads is True or (zsh_reads == BSD and sys.platform == "darwin"):
        out = _zsh(home, command, where)
        assert out is None or MARK in out, out  # zsh really reads the note
    assert _reaches_python(command, str(home / "Vault" / where))
    assert _denied(_gate(home, command, where))


# What a run's ugrep reads (`grep TODO context` reads the folder; `-3` turns -r on) and plain zsh's BSD grep
# doesn't: judged as the same words without the prefix or the quotes are. #820 models the ugrep and denies them.
AS_UGREP = [
    ("noglob grep TODO context", "action-items", "grep TODO context"),
    ('"gr"ep -3 TODO', "", "grep -3 TODO"),
    ("g\\rep -3 TODO", "", "grep -3 TODO"),
]


@pytest.mark.parametrize(("command", "where", "plain"), AS_UGREP)
def test_a_ugrep_only_form_reaches_python_and_is_judged_as_written_plainly(
    home: Path, command: str, where: str, plain: str
) -> None:
    assert _reaches_python(command, str(home / "Vault" / where))
    assert (_gate(home, command, where) is None) == (_gate(home, plain, where) is None)


@pytest.mark.parametrize(("command", "where", "plain"), AS_UGREP)
@pytest.mark.xfail(strict=True, reason="the run's grep is ugrep: denied once #820 models it; drop this mark then")
def test_a_ugrep_only_form_is_denied(home: Path, command: str, where: str, plain: str) -> None:
    assert _denied(_gate(home, command, where))
    assert _denied(_gate(home, plain, where))


@pytest.mark.parametrize(
    "command",
    [
        "grep -n x file.py",
        "grep -rn TODO plugin",
        "grep -r --include='*.py' TODO .",
        "find plugin -name '*.py'",
        "noglob find plugin -name '*.py'",
        "nocorrect grep -n x file.py",
        "exec -a x grep -n x file.py",
        "gr''ep -n x file.py",
        "'grep' -rn TODO docs",
        "cat docs/a.md",
        "zcat app.log.gz",
        "grep -Z TODO app.log.gz",
        "zgrep TODO app.log.gz",
        "grep -rZ --include='*.py' TODO .",
        "zgrep -r --include='*.py' TODO .",
        "grep -r --include='*.md.gz' TODO .",  # no decompression: compressed bytes, no note's text
        'git commit -m "don\'t"',
        "echo it''s fine",
        # archives outside the vault, compressed files that hold no note, archives written
        "unzip -p ../outside.zip",
        "unzip -p ../out-link.zip",  # a link that stays outside the vault
        "unzip -l ../outside.zip",
        "tar -xOf ../outside.tgz",
        "bsdtar -xOf ../outside.tgz",
        "grep -Z TODO ../outside.tgz",
        "gunzip -c app.log.gz",
        "gzip -dc < app.log.gz",
        "tar -czf ../out.tgz docs",
        "gzip -c docs/a.md > ../a.md.gz",
    ],
)
def test_an_ordinary_command_is_still_allowed(home: Path, command: str) -> None:
    out = _zsh(home, command)
    assert out is None or MARK not in out, out
    assert _gate(home, command) is None


@pytest.mark.parametrize(
    "command",
    [
        "grep -n x file.py",
        "ls plugin",
        "ls docs",
        "git status",
        "cat 'docs/a.md'",
        'grep -n "a b" file.py',
        "ls docs\nls\tplugin",  # JSON's \n and \t are no backslash in the command
        "git log --format='%h %s' -3",
    ],
)
def test_a_command_with_no_inner_quote_stays_in_the_shell(command: str) -> None:
    assert not _reaches_python(command)


@pytest.mark.parametrize(
    "command",
    [
        "gr''ep -n TODO",
        'f"in"d docs',
        "r\\g x",
        "zgrep x a.gz",
        "bzcat a.bz2",
        "grep -iZ x a.gz",
        "grep -z x a",
        "egrep --decompress x a.gz",
        "unzip -p a.zip",
        "bsdtar -xOf a.tgz",
        "7z e -so a.7z",
        "ditto -x -k a.zip out",
        "cpio -i",
        "pax -r -f a.tar",
        "gunzip -c a.gz",
        "UNZIP -p a.zip",
    ],
)
def test_an_inner_quote_or_a_decompressing_read_reaches_python(command: str) -> None:
    assert _reaches_python(command)
