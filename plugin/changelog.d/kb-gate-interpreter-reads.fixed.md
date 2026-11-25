- In a scheduled run, the KB read gate now judges what an interpreter's inline program reads: `python3 -c`,
  `ruby -e`/`-n`/`-p`, `node -e` (and Bun, Deno), `php -r`, `lua -e`, `Rscript -e`, and a program read from stdin
  (`python3 - <<EOF`, `echo '…' | python3`). A read call (`open()`, `Path(…).read_text()`, `File.read`,
  `fs.readFileSync`, `file_get_contents`, `io.open`, `readLines` and kin), a copy out, a shell-out
  (`subprocess.run`, `os.system`, backticks, `execSync`) or the program's operands (`sys.argv`, `ARGF`) that reach
  a note are denied, as is a path that starts in a KB folder. A path the program builds at run time is recorded as
  `program_path`, not denied.
- `mv`, `ln` (hard or symbolic) and `link` with a KB source and a target outside the KB are denied in a run. A
  hard link is the note's own file under another name, so no later read of it could be caught.
