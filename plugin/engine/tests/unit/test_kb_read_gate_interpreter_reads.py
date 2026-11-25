"""kb-read-gate judges the files an interpreter's inline program reads (scout.kb.program_reads): ``python3 -c``,
``ruby -e``/``-n``, ``node -e``, ``php -r``, ``lua -e``, ``Rscript -e`` and a program read from stdin, by the paths
their read calls spell. A path the program builds at run time is recorded as ``program_path``, not denied
(DENY_UNRESOLVED_READS is off), unless the part it spells is a KB folder. And ``mv``, ``ln`` and ``link`` with a KB
source and a target outside the KB are denied: a moved note, or a hard link (the note's own inode, which no path
check sees through), is a note a later call reads.

Synthetic vault only. tests/integration/test_kb_read_gate_zsh.py runs the forms that print a note under zsh;
tests/unit/test_hook_prefilter_differential.py holds the shell pre-filter to sending them all to Python.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb.other_reads import COVERS, FILE, other_reads
from scout.kb.program_reads import FOLDER, program_reads
from scout.kb.raw_read import raw_kb_read
from tests.unit.test_hook_prefilter_differential import INTERPRETER_FORMS, World, _judged
from tests.unit.test_hook_prefilter_differential import world as world  # noqa: PLC0414 - the shell stage's fixture
from tests.unit.test_hooks_kb_read_gate import RUN, _denied, _payload

KB_DENY = "Scheduled runs don't read knowledge-base/"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with a vault at ``~/Vault``: a KB, action items with their context folder, and ordinary folders."""
    h = tmp_path / "home"
    v = h / "Vault"
    for rel in (
        "knowledge-base/a.md",
        "knowledge-base/projects/a/a.md",
        "action-items/context/x.md",
        "action-items/action-items-2026-11-23.md",
        "docs/a.md",
        "docs/b.md",
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
    # python: every way a read call spells its path
    """python3 -c 'print(open("knowledge-base/a.md").read())'""",
    """python3 -c 'import io; print(io.open("action-items/context/x.md").read())'""",
    """python3 -c 'import codecs; print(codecs.open("knowledge-base/a.md").read())'""",
    """python3 -c 'from pathlib import Path; print(Path("knowledge-base/a.md").read_text())'""",
    """python3 -c 'import pathlib; print((pathlib.Path("knowledge-base") / "a.md").read_text())'""",
    """python3 -c 'from pathlib import Path; p = Path("knowledge-base", "a.md"); print(p.read_bytes())'""",
    """python3 -c 'from pathlib import Path; print(Path("~/Vault/knowledge-base/a.md").expanduser().read_text())'""",
    """python3 -c 'from pathlib import Path; print(Path("knowledge-base/a.md").open().read())'""",
    """python3 -c 'import os; print(open(os.path.join("knowledge-base", "projects", "a", "a.md")).read())'""",
    """python3 -c 'import os; print(open(os.path.abspath("knowledge-base/a.md")).read())'""",
    """python3 -c 'p = "knowledge-base/a.md"; print(open(p).read())'""",
    """python3 -c 'p: str = "knowledge-base/a.md"; print(open(p).read())'""",
    """python3 -c 'p = "kn" + "owledge-base/a.md"; print(open(p).read())'""",
    """python3 -c 'd = "knowledge-base"; print(open(d + "/a.md").read())'""",
    """python3 -c 'import sys; n=sys.argv[1]; print(open("knowledge-base/" + n).read())' a.md""",
    """python3 -c 'n="a"; print(open(f"knowledge-base/{n}.md").read())'""",
    """python3 -c 'print(open(r"knowledge-base/a.md").read())'""",
    """python3 -c 'print(open("knowledge-base/a.md", "r").read())'""",
    """python3 -c 'print(open("knowledge-base/a.md", mode="rb").read())'""",
    """python3 -c 'print(open("knowledge-base/a.md", "a+").read())'""",
    """python3 -c 'import sys; print(open(sys.argv[1]).read())' knowledge-base/a.md""",
    """python3 -c 'import fileinput; [print(l) for l in fileinput.input()]' knowledge-base/a.md""",
    """python3 -c 'import fileinput; [print(l) for l in fileinput.input(["knowledge-base/a.md"])]'""",
    """python3 -c 'import os; fd=os.open("knowledge-base/a.md", os.O_RDONLY); print(os.read(fd, 99))'""",
    """python3 -c 'import pandas as pd; print(pd.read_csv("knowledge-base/a.md"))'""",
    """python3 -c 'import subprocess; subprocess.run(["cat", "knowledge-base/a.md"])'""",
    """python3 -c 'import subprocess; subprocess.run("cat knowledge-base/a.md", shell=True)'""",
    """python3 -c 'import subprocess; print(subprocess.check_output(["head", "-1", "action-items/context/x.md"]))'""",
    """python3 -c 'import os; os.system("cat knowledge-base/a.md")'""",
    """python3 -c 'import os; print(os.popen("cat knowledge-base/a.md").read())'""",
    """python3 -c 'import shutil; shutil.copy("knowledge-base/a.md", "docs/y.md")'""",
    """python3 -c 'import shutil; shutil.copytree("knowledge-base", "docs/kb")'""",
    """python3 -c 'import os; os.rename("knowledge-base/a.md", "docs/y.md")'""",
    """python3 -c 'import os; os.link("knowledge-base/a.md", "docs/y.md")'""",
    """python3 -c 'import glob; [print(open(f).read()) for f in glob.glob("knowledge-base/*.md")]'""",
    """python3 -c 'import glob; [print(open(f).read()) for f in glob.glob("**/*.md", recursive=True)]'""",
    """python3 -c 'import os; [print(open(os.path.join(r, f)).read()) for r, d, fs in os.walk(".") for f in fs]'""",
    """python3 -c 'import os; [print(open("knowledge-base/" + f).read()) for f in os.listdir("knowledge-base")]'""",
    """python3 -c 'from pathlib import Path; [print(p.read_text()) for p in Path("knowledge-base").glob("*.md")]'""",
    """python3 -c 'from pathlib import Path; [print(p.read_text()) for p in Path(".").rglob("*.md")]'""",
    """python3 -c 'import os; os.chdir("knowledge-base"); print(open("a.md").read())'""",
    """python3 -c 'print(open("../Vault/knowledge-base/a.md").read())'""",
    """python3 -c 'print(open("docs/../knowledge-base/a.md").read())'""",
    """cd action-items && python3 -c 'print(open("context/x.md").read())'""",
    """cd knowledge-base && python3 -c 'print(open("a.md").read())'""",
    """python3 -I -c 'print(open("knowledge-base/a.md").read())'""",
    """python3 -W ignore -c 'print(open("knowledge-base/a.md").read())'""",
    """/usr/bin/python3 -c 'print(open("knowledge-base/a.md").read())'""",
    """env python3 -c 'print(open("knowledge-base/a.md").read())'""",
    """pypy3 -c 'print(open("knowledge-base/a.md").read())'""",
    "python3 - <<'EOF'\nprint(open('knowledge-base/a.md').read())\nEOF",
    "python3 <<EOF\nprint(open('knowledge-base/a.md').read())\nEOF",
    "python3 - knowledge-base/a.md <<'EOF'\nimport sys\nprint(open(sys.argv[1]).read())\nEOF",
    "python3 <<'EOF'\nimport subprocess\nsubprocess.run(['cat', 'knowledge-base/a.md'])\nEOF",
    """echo 'print(open("knowledge-base/a.md").read())' | python3""",
    """printf 'print(open("knowledge-base/a.md").read())' | python3 -""",
    """python3 <<< 'print(open("knowledge-base/a.md").read())'""",
    # ruby
    """ruby -e 'puts File.read("knowledge-base/a.md")'""",
    """ruby -e 'puts File.read "knowledge-base/a.md"'""",
    """ruby -e 'puts IO.readlines("knowledge-base/a.md")'""",
    """ruby -e 'File.foreach("knowledge-base/a.md") { |l| puts l }'""",
    """ruby -e 'File.open("knowledge-base/a.md") { |f| puts f.read }'""",
    """ruby -e 'File.open("knowledge-base/a.md", "r") { |f| puts f.read }'""",
    """ruby -e 'puts File.read(File.join("knowledge-base", "a.md"))'""",
    """ruby -e 'puts File.read(File.expand_path("knowledge-base/a.md"))'""",
    """ruby -e 'require "pathname"; puts Pathname.new("knowledge-base/a.md").read'""",
    """ruby -e 'require "csv"; p CSV.read("knowledge-base/a.md")'""",
    """ruby -e 'puts open("knowledge-base/a.md").read'""",
    """ruby -e 'puts `cat knowledge-base/a.md`'""",
    """ruby -e 'puts %x(cat knowledge-base/a.md)'""",
    """ruby -e 'system("cat", "knowledge-base/a.md")'""",
    """ruby -e 'system("cat knowledge-base/a.md")'""",
    """ruby -e 'puts IO.popen(["cat", "knowledge-base/a.md"]).read'""",
    """ruby -e 'puts open("|cat knowledge-base/a.md").read'""",
    """ruby -e 'require "fileutils"; FileUtils.cp("knowledge-base/a.md", "docs/y.md")'""",
    """ruby -ne 'print' knowledge-base/a.md""",
    """ruby -pe '' knowledge-base/a.md""",
    """ruby -n -e 'print' knowledge-base/a.md""",
    """ruby -e 'puts ARGF.read' knowledge-base/a.md""",
    """ruby -e 'puts $<.read' knowledge-base/a.md""",
    """ruby -e 'while l = gets do puts l end' knowledge-base/a.md""",
    """ruby -e 'puts File.read(ARGV[0])' knowledge-base/a.md""",
    """ruby -C knowledge-base -e 'puts File.read("a.md")'""",
    """ruby -rjson -e 'puts File.read("knowledge-base/a.md")'""",
    """ruby -e 'Dir.glob("knowledge-base/**/*.md").each { |f| puts File.read(f) }'""",
    """ruby -e 'Dir["knowledge-base/*.md"].each { |f| puts File.read(f) }'""",
    """ruby -e 'Dir.chdir("knowledge-base"); puts File.read("a.md")'""",
    """ruby -e 'n="a"; puts File.read("knowledge-base/#{n}.md")'""",
    "ruby <<'EOF'\nputs File.read('knowledge-base/a.md')\nEOF",
    # node, bun, deno
    """node -e 'console.log(require("fs").readFileSync("knowledge-base/a.md","utf8"))'""",
    """node -e 'const fs=require("fs"); fs.createReadStream("knowledge-base/a.md").pipe(process.stdout)'""",
    """node -e 'require("fs").readFile("knowledge-base/a.md", "utf8", (e, d) => console.log(d))'""",
    """node -e 'require("fs").promises.readFile("knowledge-base/a.md","utf8").then(console.log)'""",
    """node -e 'const p=require("path"); console.log(require("fs").readFileSync(p.join("knowledge-base","a.md")))'""",
    """node -e 'const n="a"; console.log(require("fs").readFileSync(`knowledge-base/${n}.md`,"utf8"))'""",
    """node -e 'const f="knowledge-base/a.md"; console.log(require("fs").readFileSync(f,"utf8"))'""",
    """node -e 'const fd=require("fs").openSync("knowledge-base/a.md"); console.log(fd)'""",
    """node -e 'const fd=require("fs").openSync("knowledge-base/a.md", "r+"); console.log(fd)'""",
    """node -e 'console.log(require("child_process").execSync("cat knowledge-base/a.md").toString())'""",
    """node -e 'require("child_process").spawnSync("cat", ["knowledge-base/a.md"], {stdio: "inherit"})'""",
    """node -e 'require("child_process").execFileSync("cat", ["knowledge-base/a.md"], {stdio: "inherit"})'""",
    """node -e 'require("fs").copyFileSync("knowledge-base/a.md", "docs/y.md")'""",
    """node -e 'console.log(require("fs").readFileSync(process.argv[1],"utf8"))' knowledge-base/a.md""",
    """node -e 'const fs=require("fs"); for (const f of fs.readdirSync("knowledge-base")) fs.readFileSync(f)'""",
    """node -e 'const fs=require("fs"); fs.readdirSync(".").map(f => fs.readFileSync("knowledge-base/" + f))'""",
    """node -e 'require("./knowledge-base/a.md")'""",
    """node -p 'require("fs").readFileSync("knowledge-base/a.md","utf8")'""",
    """node --eval 'console.log(require("fs").readFileSync("knowledge-base/a.md","utf8"))'""",
    """node --print='require("fs").readFileSync("knowledge-base/a.md","utf8")'""",
    """bun -e 'console.log(await Bun.file("knowledge-base/a.md").text())'""",
    """bun -e 'await $`cat knowledge-base/a.md`'""",
    """deno eval 'console.log(Deno.readTextFileSync("knowledge-base/a.md"))'""",
    "node <<'EOF'\nconsole.log(require('fs').readFileSync('knowledge-base/a.md', 'utf8'))\nEOF",
    # php
    """php -r 'echo file_get_contents("knowledge-base/a.md");'""",
    """php -r 'readfile("knowledge-base/a.md");'""",
    """php -r 'print_r(file("knowledge-base/a.md"));'""",
    """php -r '$f = fopen("knowledge-base/a.md", "r"); echo fread($f, 99);'""",
    """php -r '$p = "knowledge-base/a.md"; echo file_get_contents($p);'""",
    """php -r '$n = "a"; echo file_get_contents("knowledge-base/$n.md");'""",
    """php -r 'echo file_get_contents("knowledge-base/" . "a.md");'""",
    """php -r 'echo shell_exec("cat knowledge-base/a.md");'""",
    """php -r 'echo `cat knowledge-base/a.md`;'""",
    """php -r 'copy("knowledge-base/a.md", "docs/y.md");'""",
    """php -r 'include "knowledge-base/a.md";'""",
    """php -r 'echo file_get_contents($argv[1]);' knowledge-base/a.md""",
    """php -r 'foreach (glob("knowledge-base/*.md") as $f) echo file_get_contents($f);'""",
    "php <<'EOF'\n<?php echo file_get_contents('knowledge-base/a.md');\nEOF",
    # lua
    """lua -e 'print(io.open("knowledge-base/a.md"):read("a"))'""",
    """lua -e 'print(io.open("knowledge-base/a.md", "r"):read("a"))'""",
    """lua -e 'for l in io.lines("knowledge-base/a.md") do print(l) end'""",
    """lua -e 'for l in io.lines"knowledge-base/a.md" do print(l) end'""",
    """lua -e 'p = "kn" .. "owledge-base/a.md" for l in io.lines(p) do print(l) end'""",
    """lua -e 'local p = "knowledge-base/a.md" for l in io.lines(p) do print(l) end'""",
    """lua -e 'dofile("knowledge-base/a.md")'""",
    """lua -e 'os.execute("cat knowledge-base/a.md")'""",
    """lua -e 'print(io.popen("cat knowledge-base/a.md"):read("a"))'""",
    """lua -e 'for l in io.lines(arg[1]) do print(l) end' knowledge-base/a.md""",
    """luajit -e 'print(io.open("knowledge-base/a.md"):read("*a"))'""",
    "lua <<'EOF'\nprint(io.open('knowledge-base/a.md'):read('a'))\nEOF",
    # R
    """Rscript -e 'cat(readLines("knowledge-base/a.md"))'""",
    """Rscript -e 'cat(readLines(file.path("knowledge-base", "a.md")))'""",
    """Rscript -e 'cat(readLines(paste0("knowledge-base/", "a.md")))'""",
    """Rscript -e 'cat(readLines(paste("knowledge-base/a", "md", sep=".")))'""",
    """Rscript -e 'p <- "knowledge-base/a.md"; cat(readLines(p))'""",
    """Rscript -e 'print(read.csv("knowledge-base/a.md"))'""",
    """Rscript -e 'print(readr::read_lines("knowledge-base/a.md"))'""",
    """Rscript -e 'con <- file("knowledge-base/a.md"); cat(readLines(con))'""",
    """Rscript -e 'n <- "a"; cat(readLines(sprintf("knowledge-base/%s.md", n)))'""",
    """Rscript -e 'system("cat knowledge-base/a.md")'""",
    """Rscript -e 'system2("cat", c("knowledge-base/a.md"))'""",
    """Rscript -e 'file.copy("knowledge-base/a.md", "docs/y.md")'""",
    """Rscript -e 'cat(readLines(commandArgs(TRUE)[1]))' knowledge-base/a.md""",
    """Rscript -e 'for (f in list.files("knowledge-base", full.names=TRUE)) cat(readLines(f))'""",
    """Rscript -e 'setwd("knowledge-base"); cat(readLines("a.md"))'""",
    """R -e 'cat(readLines("knowledge-base/a.md"))'""",
    """R --vanilla -e 'cat(readLines("knowledge-base/a.md"))'""",
    "R --no-save <<'EOF'\ncat(readLines('knowledge-base/a.md'))\nEOF",
    # perl reading its program from stdin
    "perl <<'EOF'\nopen(F, '<', 'knowledge-base/a.md'); print <F>;\nEOF",
    "perl - knowledge-base/a.md <<'EOF'\nwhile (<>) { print }\nEOF",
    # moves and links out of the KB
    "mv knowledge-base/a.md docs/y.md",
    "mv -t docs knowledge-base/a.md",
    "mv -f knowledge-base/a.md docs/",
    "mv knowledge-base/projects docs/",
    "mv action-items /tmp/ai-old",
    "mv kn*/a.md docs/y.md",
    "mv knowledge-base/a.md knowledge-base/projects/a/a.md docs/",
    "cd knowledge-base && mv a.md ../docs/y.md",
    "cd action-items && mv context ../docs/",
    "ln knowledge-base/a.md docs/y.md",
    "ln -f knowledge-base/a.md docs/y.md",
    "ln knowledge-base/a.md docs",
    "link knowledge-base/a.md docs/y.md",
    "ln -s ../knowledge-base/a.md docs/y.md",
    "ln -sf ../knowledge-base docs/kb",
    "ln -s ../knowledge-base docs/kb",
    "ln -sr knowledge-base/a.md docs/y.md",
    "ln -s ~/Vault/knowledge-base/a.md docs/y.md",
    "ln -s ../../knowledge-base/a.md -t docs/sub",
    "cd docs && ln ../knowledge-base/a.md y.md",
    "cd docs && ln -s ../knowledge-base/a.md",
    "gmv knowledge-base/a.md docs/y.md",
    "gln knowledge-base/a.md docs/y.md",
    # a name in another letter case runs the same program on a case-insensitive disk
    """PYTHON3 -c 'print(open("knowledge-base/a.md").read())'""",
    """Ruby -e 'puts File.read("knowledge-base/a.md")'""",
    """RSCRIPT -e 'cat(readLines("knowledge-base/a.md"))'""",
    "MV knowledge-base/a.md docs/y.md",
    "Ln knowledge-base/a.md docs/y.md",
    # a folder change, then a path built there or a listing of it
    """python3 -c 'import os; os.chdir("knowledge-base"); [print(open(f).read()) for f in os.listdir()]'""",
    """python3 -c 'import os; os.chdir("knowledge-base"); print(open(input()).read())'""",
    """python3 -c 'import os, glob; os.chdir("knowledge-base"); [print(open(f).read()) for f in glob.glob("*.md")]'""",
    """ruby -e 'Dir.chdir("knowledge-base"); Dir.glob("*.md").each { |f| puts File.read(f) }'""",
    """node -e 'process.chdir("knowledge-base"); console.log(require("fs").readFileSync("a.md", "utf8"))'""",
    """python3 -c 'import os; os.chdir("knowledge-base"); print(1)'""",  # as perl's chdir: a folder read
    """Rscript -e 'setwd("action-items/context")'""",
]


@pytest.mark.parametrize("command", [*INTERPRETER_FORMS, *MORE_FORMS])
def test_an_interpreter_or_a_move_that_reads_a_kb_note_is_denied_in_a_run(home: Path, command: str) -> None:
    assert KB_DENY in _denied(_run(home, command)), command


@pytest.mark.parametrize("shell", ["sh", "/bin/bash"])
def test_the_shell_pre_filter_sends_every_one_to_python(world: World, shell: str) -> None:
    assert [c for c in MORE_FORMS if not _judged(world, c, shell)] == []


def test_a_hard_link_into_the_kb_is_what_the_path_checks_miss(home: Path) -> None:
    """Why ``ln`` without ``-s`` is denied: a hard link's path names no KB folder, so a later read of it is
    allowed. A symbolic link is resolved on every read."""
    v = home / "Vault"
    os.link(v / "knowledge-base/a.md", v / "docs/hard.md")
    (v / "docs/sym.md").symlink_to("../knowledge-base/a.md")
    assert _run(home, "cat docs/hard.md") is None
    assert KB_DENY in _denied(_run(home, "cat docs/sym.md"))


@pytest.mark.parametrize(
    "command",
    [
        """python3 -c 'print(open("knowledge-base/a.md").read())'""",
        """python3 -c 'from pathlib import Path; print(Path("knowledge-base/a.md").read_text())'""",
        """ruby -e 'puts File.read("knowledge-base/a.md")'""",
        "ruby -ne print knowledge-base/a.md",
        """node -e 'console.log(require("fs").readFileSync("knowledge-base/a.md","utf8"))'""",
        """php -r 'echo file_get_contents("knowledge-base/a.md");'""",
        """lua -e 'print(io.open("knowledge-base/a.md"):read("a"))'""",
        """Rscript -e 'cat(readLines("knowledge-base/a.md"))'""",
        """python3 -c 'import os; os.system("cat knowledge-base/a.md")'""",
        """python3 -c 'print(open("knowledge-base/" + n).read())'""",
        "mv knowledge-base/a.md docs/y.md",
        "ln knowledge-base/a.md docs/y.md",
        "ln -s ../knowledge-base/a.md docs/y.md",
        """cd knowledge-base && python3 -c 'print(open("a.md").read())'""",
    ],
)
def test_the_evals_judge_counts_them_by_spelling_too(command: str) -> None:
    """raw_kb_read is what the eval's privacy_hygiene check counts: the same programs, judged by how they're
    spelled."""
    assert raw_kb_read(command, cwd="/v", vault="/v"), command


# ----- must allow -----------------------------------------------------------------------------------------

ALLOWED = [
    "python3 -c 'print(1+1)'",
    """python3 -c 'print(open("docs/a.md").read())'""",
    """python3 -c 'from pathlib import Path; print(Path("docs/a.md").read_text())'""",
    """python3 -c 'import json,sys; print(json.load(sys.stdin))'""",
    """python3 -c 'import json; print(json.load(open("docs/a.md")))'""",
    """python3 -c 'open("knowledge-base/new.md", "w").write("x")'""",
    """python3 -c 'open("knowledge-base/new.md", mode="a").write("x")'""",
    """python3 -c 'import os; os.close(os.open("knowledge-base/new.md", os.O_WRONLY | os.O_CREAT))'""",
    """python3 -c 'from pathlib import Path; Path("knowledge-base/new.md").write_text("x")'""",
    """python3 -c 'from pathlib import Path; Path("knowledge-base/new.md").open("w").write("x")'""",
    """python3 -c 'import shutil; shutil.copy("docs/a.md", "knowledge-base/new.md")'""",
    """python3 -c 'import shutil; shutil.copy("knowledge-base/a.md", "knowledge-base/b.md")'""",
    """python3 -c 'import subprocess; subprocess.run(["git", "status"])'""",
    """python3 -c 'import subprocess; print(subprocess.check_output("date", shell=True))'""",
    """python3 -c 'import os; print(os.listdir("docs"))'""",
    """python3 -c 'import os; print(os.listdir("knowledge-base"))'""",
    """python3 -c 'import os; print(len(list(os.walk("."))))'""",
    """python3 -c 'import datetime; print(datetime.date.today())'""",
    """python3 -c 'import sys; print(sys.argv[1])' knowledge""",
    """python3 -c 'print("knowledge-base/a.md")'""",
    """python3 -c '# open("knowledge-base/a.md")\nprint(1)'""",
    """python3 -c 'import webbrowser; webbrowser.open("https://example.com")'""",
    """python3 -c 'import sys; print(sys.stdin.read().upper())' < docs/a.md""",
    """python3 -m json.tool docs/a.md""",
    """python3 scripts/tool.py knowledge-base/a.md""",
    "python3 - <<'EOF'\nprint(open('docs/a.md').read())\nEOF",
    "python3 <<'EOF'\nprint('knowledge-base is the KB')\nEOF",
    """ruby -e 'puts File.read("docs/a.md")'""",
    """ruby -ne 'print' docs/a.md""",
    """ruby -e 'File.write("knowledge-base/new.md", "x")'""",
    """ruby -e 'File.open("knowledge-base/new.md", "w") { |f| f.puts "x" }'""",
    """ruby -e 'puts 1+1'""",
    """ruby -e 'puts `date`'""",
    """node -e 'console.log(require("fs").readFileSync("docs/a.md","utf8"))'""",
    """node -e 'require("fs").writeFileSync("knowledge-base/new.md","x")'""",
    """node -e 'require("fs").openSync("knowledge-base/new.md", "w")'""",
    """node -e 'console.log(1+1)'""",
    """node -e 'console.log(require("path").join("knowledge-base", "a.md"))'""",
    """node -e 'require("child_process").execSync("git status", {stdio: "inherit"})'""",
    """node script.js knowledge-base/a.md""",
    """php -r 'echo 1+1;'""",
    """php -r 'file_put_contents("knowledge-base/new.md", "x");'""",
    """php -r '$f = fopen("knowledge-base/new.md", "w"); fwrite($f, "x");'""",
    """lua -e 'print(1+1)'""",
    """lua -e 'io.open("knowledge-base/new.md", "w"):write("x")'""",
    """Rscript -e 'cat(1+1)'""",
    """Rscript -e 'writeLines("x", "knowledge-base/new.md")'""",
    """Rscript analysis.R knowledge-base/a.md""",
    "R CMD BATCH analysis.R",
    "mv docs/a.md docs/c.md",
    "mv -t docs/sub docs/a.md",
    "ln -s docs/a.md b",
    "ln -s a.md docs/b-link.md",
    "ln docs/a.md docs/hard.md",
    "mv knowledge-base/a.md knowledge-base/b.md",
    "mv knowledge-base/a.md knowledge-base/projects/",
    "mv -t knowledge-base/projects knowledge-base/a.md",
    "cd knowledge-base && mv a.md b.md",
    "mv docs/a.md knowledge-base/new.md",
    "ln -s ../docs/a.md knowledge-base/link.md",
    "mv action-items/action-items-2026-11-23.md action-items/archive.md",
    "mv --help",
    "ln --version",
    """python3 -c 'import os; os.chdir("docs"); print(open("a.md").read())'""",
    "scripts/scoutctl kb read knowledge-base/a.md | python3 -c 'import sys; print(len(sys.stdin.read()))'",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_an_ordinary_one_liner_or_move_is_allowed(home: Path, command: str) -> None:
    assert _run(home, command) is None, command
    assert not [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"], command


# ----- a path a program builds at run time ----------------------------------------------------------------

BUILT = [
    """python3 -c 'import os; os.chdir(os.environ["D"]); print(1)'""",
    """python3 -c 'import sys; print(open(sys.stdin.readline().strip()).read())'""",
    """python3 -c 'import os; print(open(os.environ["X"]).read())'""",
    """python3 -c 'import os; print(open(f"{os.getcwd()}/x.md").read())'""",
    """python3 -c 'from pathlib import Path; print(Path(input()).read_text())'""",
    """python3 -c 'import subprocess, sys; subprocess.run(["cat", sys.stdin.read()])'""",
    """python3 -c 'p = input(); print(open("kn" + p).read())'""",
    """ruby -e 'puts File.read(ENV["X"])'""",
    """ruby -e 'puts `cat #{ENV["X"]}`'""",
    """node -e 'console.log(require("fs").readFileSync(process.env.X,"utf8"))'""",
    """php -r 'echo file_get_contents(getenv("X"));'""",
    """lua -e 'print(io.open(os.getenv("X")):read("a"))'""",
    """Rscript -e 'cat(readLines(Sys.getenv("X")))'""",
]


# A program that opens what it lists, where the listing holds no KB note: allowed, and the built path recorded.
LISTS_NO_NOTE = [
    """python3 -c 'import os; print([open(f).read() for f in os.listdir(".") if f.endswith(".md")])'""",
    """python3 -c 'import glob; print([open(f).read() for f in glob.glob("docs/*.md")])'""",
    """ruby -e 'Dir.glob("docs/*.md").each { |f| puts File.read(f) }'""",
]


@pytest.mark.parametrize("command", [*BUILT, *LISTS_NO_NOTE])
def test_a_programs_path_built_at_run_time_is_allowed_and_recorded(home: Path, command: str) -> None:
    assert gate.DENY_UNRESOLVED_READS is False
    assert _run(home, command) is None, command
    (row,) = [r for r in _events(home) if r["kind"] == "kb_read_gate.unresolved_read"]
    assert row["payload"] == {"categories": ["program_path"], "mode": RUN["SCOUT_MODE"]}


@pytest.mark.parametrize("command", BUILT)
def test_with_the_switch_on_a_built_path_is_denied(home: Path, monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.setattr(gate, "DENY_UNRESOLVED_READS", True)
    assert "can't work out" in _denied(_run(home, command))


# ----- what program_reads finds ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["python3", "-c", "open('a')"], {"literal": ["a"]}),
        (["python3", "-c", "open('a', 'w'); open('b', 'r+'); open('c', 'wb')"], {"literal": ["b"]}),
        (["python3", "-c", "open('a', mode)"], {"literal": ["a"]}),  # a mode it builds: a read
        (["python3", "-c", "open('x' 'y')"], {"literal": [], "built": True}),
        (["python3", "-c", "open('''a''')"], {"literal": ["a"]}),
        (["python3", "-c", "open(b'a\\x')"], {"literal": ["ax"]}),
        (["python3", "-c", "open('d/' + x)"], {"spelled": [("d", FOLDER, "as-is")], "built": True}),
        (["python3", "-c", "open('d/pre' + x)"], {"spelled": [("d", FOLDER, "as-is")], "built": True}),
        (["python3", "-c", "p = 'a'\np = 'b'\nopen(p)"], {"literal": ["a", "b"]}),
        (["python3", "-c", "for p in q: open(p)"], {"built": True}),
        (["python3", "-c", "open(sys.argv[1])", "f1", "f2"], {"reads": [("f1", FILE), ("f2", FILE)]}),
        (["python3", "-c", "import os; os.chdir('d'); open('a')"], {"literal": ["a", "d/a"]}),
        (["python3", "-c", "import os; os.chdir(x); open('a')"], {"literal": ["a"], "built": True}),
        (["python3", "-c", "import os; os.chdir('d'); open(x)"], {"spelled": [("d", FOLDER, "as-is")], "built": True}),
        (
            ["python3", "-c", "import os; os.chdir('d/'); open('e/' + x)"],
            {"spelled": [("e", FOLDER, "as-is"), ("d/e", FOLDER, "as-is"), ("d", FOLDER, "as-is")]},
        ),
        (["python3", "-c", "import os; os.chdir('d'); print(1)"], {"spelled": [("d", FOLDER, "as-is")]}),
        (["python3", "-c", "import os; os.chdir(x); print(1)"], {"built": True}),
        (["RUBY", "-e", "File.read('a')"], {"literal": ["a"]}),
        (["Python3.12", "-c", "open('a')"], {"literal": ["a"]}),
        (["python3", "-c", "import os; print(os.listdir('d'))"], None),
        (["python3", "-c", "import subprocess; subprocess.run(['cat', x])"], {"built": True}),
        (["python3", "-c", "import subprocess; subprocess.run(['ls', '-l'])"], {"programs": ["ls -l"]}),
        (["python3", "-c", "import os; os.system(cmd)"], {"built": True}),
        (["python3", "-c", "import shutil; shutil.copy(a, 'knowledge-base/x')"], None),
        (["python3", "-c", "import shutil; shutil.copy(a, 'd/x')"], {"built": True}),
        (["python3", "-c", "x = Path('a') / 'b' / 'c'\nx.read_text()"], {"literal": ["a/b/c"]}),
        (["python3", "-c", "Path('/a', 'b').read_text()"], {"literal": ["/a/b"]}),
        (["python3", "-c", "Path(x).read_text()"], {"built": True}),
        (["python3", "-m", "http.server"], None),
        (["python3", "script.py", "a"], None),
        (["python3", "-"], None),  # stdin, but nothing given for it
        (["python3", "-c"], None),
        (["ruby", "-e", "File.read 'a' if true"], {"literal": ["a"]}),
        (["ruby", "-e", "File.read('a\\'b')"], {"literal": ["a'b"]}),
        (["ruby", "-e", "puts %x(ls #{d})"], {"built": True}),
        (["ruby", "-e", "puts `ls #{d}`"], {"built": True}),
        (["ruby", "-e", "puts 1", "f"], None),
        (["ruby", "-ne", "print", "f"], {"reads": [("f", FILE)]}),
        (["ruby", "-Cd", "-e", "File.read('a')"], {"literal": ["d/a"]}),
        (["ruby", "-I", "lib", "-e", "File.read('a')"], {"literal": ["a"]}),
        (["ruby", "-eFile.read('a')"], {"literal": ["a"]}),
        (["ruby", "x.rb"], None),
        (["node", "-e", "require('fs')"], None),
        (["node", "-e", "fs.openSync('a', {flag: 'w'})"], None),
        (["node", "-e", "fs.openSync('a', {mode: 0o644})"], {"literal": ["a"]}),
        (["node", "-e", "$`ls ${d}`"], {"built": True}),
        (["node", "-e", "$`ls -l`"], {"programs": ["ls -l"]}),
        (["node", "-r", "x", "-e", "fs.readFileSync('a')"], {"literal": ["a"]}),
        (["node", "--input-type=module", "-e", "fs.readFileSync('a')"], {"literal": ["a"]}),
        (["deno", "run", "x.ts"], None),
        (["deno", "eval"], None),
        (["bun", "run", "x.ts"], None),
        (["php", "-f", "x.php"], None),
        (["php", "-d", "a=b", "-r", "readfile('a');"], {"literal": ["a"]}),
        (["php", "-r", "echo hash_file('md5', 'a');"], {"literal": ["a"]}),
        (["php", "-r", "echo `ls $d`;"], {"built": True}),
        (["lua", "-l", "x", "-e", "io.open('a')"], {"literal": ["a"]}),
        (["lua", "-e", "io.open([[a]])"], {"literal": ["a"]}),
        (["lua", "-eio.open('a')"], {"literal": ["a"]}),
        (["Rscript", "-e", "readLines(paste('a', x))"], {"built": True}),
        (["Rscript", "-e", "readLines(paste('a', 'b'))"], {"literal": ["a b"]}),  # paste's sep is a space
        (["Rscript", "-e", "readLines(file='a')"], {"literal": ["a"]}),
        (["Rscript", "-e", "file('a', open='w')"], None),
        (["Rscript", "--file=x.R"], None),
        (["Rscript", "-f", "x.R"], None),
        (["R", "--args", "x"], None),
        (["cat", "x"], None),
    ],
)
def test_program_reads_finds_what_a_program_reads(words: list[str], expected: dict | None) -> None:
    found = program_reads(words[0], words[1:])
    if expected is None:
        assert found is None
        return
    assert found is not None
    assert {key: getattr(found, key) for key in expected} == expected


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        # command lines: `--`, a long option's value, R's --file, a bare -e
        (["ruby", "--", "x.rb"], None),
        (["node", "--require", "x", "-e", "fs.readFileSync('a')"], {"literal": ["a"]}),
        (["Rscript", "--file", "x.R"], None),
        (["ruby", "-e"], None),
        # expressions: an operand first in a join, an absolute part, a part that's built after a literal one
        (["python3", "-c", "n = sys.argv[1]\nopen(n + '.md')", "f"], {"reads": [("f", FILE)]}),
        (["python3", "-c", "open(name_of('a'))"], {"built": True}),
        (["python3", "-c", "open(os.path.join('a', '/b'))"], {"literal": ["/b"]}),
        (["python3", "-c", "open(os.path.join('a', f'c{x}'))"], {"spelled": [("a", FOLDER, "as-is")], "built": True}),
        (["python3", "-c", "open(os.path.join())"], {"built": True}),
        (["python3", "-c", "open(b'a' + b'b')"], {"literal": ["ab"]}),
        (["python3", "-c", "open(a // b)"], {"built": True}),
        (["python3", "-c", "p = 'a' or q\nopen(p)"], {"literal": [], "built": True}),
        (["python3", "-c", "p = 'a' if c else 'b'\nopen(p)"], {"literal": ["a"]}),
        (["python3", "-c", "a = 'x'\na = 'y'\na = 'z'\na = 'w'\na = 'v'\nopen(a + a)"], {"built": True}),
        (["ruby", "-e", "File.read(`ls`)"], {"built": True, "programs": ["ls"]}),
        (["node", "-e", "fs.readFileSync(__dirname)"], {"built": True}),
        (["php", "-r", "echo file_get_contents($d.$e);"], {"built": True}),
        # calls with no arguments, or none the gate can read
        (["python3", "-c", "x = open\nprint(x)"], None),
        (["python3", "-c", "x = glob.glob\nprint(x)"], None),
        (["python3", "-c", "import os; os.system()"], None),
        (["python3", "-c", "import subprocess; subprocess.run([])"], None),
        (["python3", "-c", "import shutil; shutil.copy(sys.argv[1], 'x')", "f"], {"reads": [("f", FILE)]}),
        (["python3", "-c", "import shutil; shutil.copy\n"], None),
        (["python3", "-c", "import os; [open(f) for f in os.listdir(d)]"], {"built": True, "spelled": []}),
        # unbalanced text: whatever it finds, no crash
        (["python3", "-c", "x)).read_text()"], None),
        (["python3", "-c", "open('a'"], {"literal": []}),
    ],
)
def test_program_reads_edges(words: list[str], expected: dict | None) -> None:
    found = program_reads(words[0], words[1:])
    if expected is None:
        assert found is None
        return
    assert found is not None
    assert {key: getattr(found, key) for key in expected} == expected


def test_a_program_on_stdin_and_an_echoed_one() -> None:
    found = program_reads("python3", ["-", "f"], ["import sys\nopen(sys.argv[1])"])
    assert found is not None and found.reads == [("f", FILE)]
    found = program_reads("python3", [], ["echo -n 'open(\"a\")'"])
    assert found is not None and found.literal == ["a"]
    found = program_reads("python3", [], ["echo 'unbalanced"])  # not one shlex reads: the text as it is
    assert found is None
    assert program_reads("python3", ["script.py"], ["open('a')"]) is None


@pytest.mark.parametrize(
    ("words", "reads", "dests"),
    [
        (["mv", "a", "b", "d"], [("a", COVERS), ("b", COVERS)], ["d"]),
        (["mv", "-t", "d", "a"], [("a", COVERS)], ["d"]),
        (["mv", "a"], [], []),
        (["ln", "a", "b"], [("a", FILE)], ["b"]),
        (["ln", "-s", "a"], [("a", COVERS)], []),
        (["ln", "-s", "../a", "d/b"], [("../a", COVERS), ("d/b/../a", COVERS), ("d/../a", COVERS)], ["d/b"]),
        (["ln", "-s", "/abs/a", "d/b"], [("/abs/a", COVERS)], ["d/b"]),
        (["ln", "-sr", "a", "d/b"], [("a", COVERS)], ["d/b"]),
        (["link", "a", "b"], [("a", FILE)], ["b"]),
    ],
)
def test_other_reads_finds_what_a_move_or_link_takes(
    words: list[str], reads: list[tuple[str, str]], dests: list[str]
) -> None:
    found = other_reads(words[0], words[1:])
    assert found is not None
    assert (found.reads, found.dests) == (reads, dests)


def test_perl_on_stdin_is_perl_e() -> None:
    found = other_reads("perl", [], ["open(F, '<', 'a')"])
    assert found is not None and found.literal == ["a"]
    found = other_reads("perl", ["-", "f"], ["print while <>"])
    assert found is not None and found.reads == [("f", FILE)]
    assert other_reads("perl", ["script.pl"], ["open(F, 'a')"]) == other_reads("perl", ["script.pl"])  # stdin is data
    assert other_reads("perl", ["-e", "1"], ["open(F, 'a')"]) is not None  # -e wins: stdin is data
