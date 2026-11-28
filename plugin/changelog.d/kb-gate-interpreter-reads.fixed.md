- In a scheduled run, the KB read gate now judges what an interpreter's program reads: `python3 -c`,
  `ruby -e`/`-n`/`-p`, `node -e` (and Bun, Deno), `php -r` (and `-B`/`-R`/`-E`), `lua -e`, `Rscript -e`, ipython,
  groovy, expect, tclsh, jshell and swift, and a program read from stdin (`python3 - <<EOF`, `echo '…' | python3`).
  A read call (`open()`, `Path(…).read_text()`, `File.read`, `fs.readFileSync`, `file_get_contents`, `io.open`,
  `readLines` and kin), a copy out, a shell-out (`subprocess.run`, `os.system`, backticks, `execSync`) or the
  program's operands (`sys.argv`, `ARGF`) that reach a note are denied, as is a path that starts in a KB folder.
  A loop over a list, a dict (its keys, values or items) or a list of tuples of paths is judged by those paths.
- It fails closed: a program whose text names the KB anywhere the gate doesn't judge as a path (an alias, `exec`,
  code inside an f-string or a template literal, an escaped literal like `"\x6bnowledge-base"`, a comment) is denied.
  A Python literal that is only ever text is the exception: a KB name in what the program writes, prints or compares
  (`s.replace("[[old]]", "[[knowledge-base/x]]")`, a line it inserts) is no read. The gate shows that on Python's own
  syntax tree, and only for a program that runs nothing, opens no path it builds, uses no lambda, `getattr` or
  `__builtins__`, and calls nothing through an import outside a short list; a use counts only when every other
  argument of that call is text too. A literal that reaches any other call (`from subprocess import run`, `zipfile`,
  `import os as o; o.replace(…)`, `xs.sort(key=o.system)`, `re.sub(…, lambda m: …, s)`) is denied.
  So is a module (`python3 -m json.tool`), a `-r`/`-l` library or php's `auto_prepend_file` that names a KB file,
  `sqlite3`'s `readfile()`, and a program built by the shell (`python3 -c "$(…)"`) or piped in from something the
  gate can't read, when the command names the KB. Otherwise those, and any path a program builds at run time
  (code in an f-string's `{…}` that opens something included), are recorded as `program_path` and allowed. So is a
  call the gate's scan doesn't model that may open what it's handed (`io.open_code(x)`, `__builtins__.open(x)`,
  `netrc.netrc(x)`, `ConfigParser().read(x)`, `xs.sort(key=io.open_code)`): fail closed, recorded.
  `uv run`, `poetry run`, `pipx run` and kin are read through.
- A script file an interpreter runs is judged as a file. Scout's own is trusted, allowed and recorded as
  `program_path:trusted_script`: a file of the plugin (not under its virtualenvs), or a vault copy of one the plugin
  installs (`knowledge-base/ontology/parser.py`, `scripts/kb-shard-log.py`…), unchanged from the plugin's template or
  from what bootstrap last rendered, with no module beside it the plugin didn't put there. It is trusted only run bare
  (`python3 <script> args`): not with an interpreter flag (`-i`, `-X`, `-m pdb`), a prefix (`env`, `uv run`), an
  environment that picks the code Python loads (`PYTHONPATH`, `PYTHONSTARTUP`, `PYTHONUSERBASE`…), anything on stdin,
  a substitution, or after a step of the same command that may have written it or a module beside it. Given a KB note
  and a path outside the KB (`render.py <note> docs/out.html`, `> docs/out.txt`), it is denied as the copy out it is.
  Any other script given a KB note is denied; one in the vault is recorded as `program_path:vault_script`, and one
  elsewhere as `program_path`. Each is recorded once per run while its content stays the same, kept as hashes in the
  owner's store (`~/.local/state/scout/scout-kb-read-gate-scripts.json`, which runs can't write).
- What that trust rests on can't be written by a run: bootstrap's record of what it rendered
  (`.scout-state/last-rendered/`), the vault copies of the plugin's scripts (`action-items/render.py`,
  `knowledge-base/ontology/__init__.py`; `parser.py` stays the vault's to grow, and isn't trusted once it does), and
  Python's user site-packages (`~/Library/Python`, `~/.local/lib`), whose `usercustomize.py` and `.pth` files run in
  every `python3` a run starts.
- A program that reads a KB note and writes only into the KB (an edit in place, `ruby -i -pe`, `perl -pi -e`
  included), or that edits KB notes and vault files in place (each file it reads it writes back, and each it writes
  outside the KB it read), is allowed and recorded as a `kb_read_gate.kb_edit` event:
  `{"mode", "targets", "non_kb_targets"}`, counts only, no paths. `non_kb_targets` counts the files outside the KB it
  rewrote alongside the notes, which may now hold what it read there. `DENY_INTERPRETER_KB_EDITS` in
  `scout/hooks/kb_read_gate.py`, off for now, denies them all. The variable, literal and pathlib spellings of the
  same edit are judged the same, and `ruby -i`/`perl -pi` over a note and a vault file is an edit too. A program that
  reads a note and writes outside the KB anything but a file it edits in place is denied as the copy it is. Listing
  a KB folder (`os.listdir`, `os.scandir`, `glob`, `Path.iterdir`, `os.walk`, `Dir.glob`, `fs.readdirSync`,
  `list.files`), there or after a `chdir` into it, is denied as `ls knowledge-base` is; testing a name
  (`os.path.exists`) is allowed, as `test -e` is.
- The gate's Python lexer keeps a backslash-escaped quote inside a triple-quoted string (`"""…\""""`): it ended the
  string one character early, so every string after it was misread.
- `mv`, `ln` (hard or symbolic) and `link` with a KB source and a target outside the KB are denied in a run, through
  brace lists and `xargs` input too. A hard link is the note's own file under another name, so no later read of it
  could be caught.
