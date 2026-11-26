- In a scheduled run, the KB read gate now judges what an interpreter's program reads: `python3 -c`,
  `ruby -e`/`-n`/`-p`, `node -e` (and Bun, Deno), `php -r` (and `-B`/`-R`/`-E`), `lua -e`, `Rscript -e`, ipython,
  groovy, expect, tclsh, jshell and swift, and a program read from stdin (`python3 - <<EOF`, `echo '…' | python3`).
  A read call (`open()`, `Path(…).read_text()`, `File.read`, `fs.readFileSync`, `file_get_contents`, `io.open`,
  `readLines` and kin), a copy out, a shell-out (`subprocess.run`, `os.system`, backticks, `execSync`) or the
  program's operands (`sys.argv`, `ARGF`) that reach a note are denied, as is a path that starts in a KB folder.
- It fails closed: a program whose text names the KB anywhere the gate doesn't judge as a path (an alias, `exec`,
  code inside an f-string or a template literal, an escaped literal like `"\x6bnowledge-base"`, a comment) is denied.
  So is a script file, a module (`python3 -m json.tool`), a `-r`/`-l` library or php's `auto_prepend_file` that names
  a KB file, `sqlite3`'s `readfile()`, and a program built by the shell (`python3 -c "$(…)"`) or piped in from
  something the gate can't read, when the command names the KB. Otherwise those, and any path a program builds at
  run time, are recorded as `program_path` and allowed. `uv run`, `poetry run`, `pipx run` and kin are read through.
- A program that reads a KB note and writes only into the KB (an edit in place, `ruby -i -pe`, `perl -pi -e`
  included) is allowed and recorded as a `kb_read_gate.kb_edit` event (`{"mode", "targets"}`, no paths).
  `DENY_INTERPRETER_KB_EDITS` in `scout/hooks/kb_read_gate.py`, off for now, denies them. A program that reads a
  note and writes outside the KB is denied as the copy it is. Listing a KB folder (`os.listdir`, `os.scandir`,
  `glob`, `Path.iterdir`, `os.walk`, `Dir.glob`, `fs.readdirSync`, `list.files`), there or after a `chdir` into it,
  is denied as `ls knowledge-base` is; testing a name (`os.path.exists`) is allowed, as `test -e` is.
- `mv`, `ln` (hard or symbolic) and `link` with a KB source and a target outside the KB are denied in a run, through
  brace lists and `xargs` input too. A hard link is the note's own file under another name, so no later read of it
  could be caught.
