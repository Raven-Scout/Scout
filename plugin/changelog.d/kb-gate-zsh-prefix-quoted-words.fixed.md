- **The KB read gate reads a command word as zsh runs it** (`hooks/prefilter.sh`,
  `engine/scout/kb/raw_read.py`, `engine/scout/kb/live_run.py`). A scheduled run could read knowledge-base notes
  in ways the gate missed (found by the #820 and #822 security reviews under real `zsh -f`):
  - zsh's precommand modifiers and other launchers: `noglob grep -r TODO .`, `nocorrect …` and
    `noglob cat knowledge-base/a.md` were judged as a program called `noglob`. `noglob`, `nocorrect`, `nohup`,
    `gstdbuf`, `xcrun` and `arch` are now prefixes like `sudo`. A prefix's options are read getopt-style
    (`exec -la x cat …`, `stdbuf -oL`), with the values `exec -a`, `caffeinate -t`/`-w`, `env -P`,
    `xcrun --sdk` and `arch -arch` take. `env -S 'cat …'` is judged as the command it runs, as `sh -c` is. A
    prefix or program spelled with quotes (`no''glob`, `c''at`) is read without them.
  - A quote or backslash inside the command word (`gr''ep -r TODO .`, `g\rep -R TODO`, `fi''nd . -exec cat {} +`),
    a backslash-newline that joins a word (`ca\<newline>t knowledge-base/a.md`, `gr\<newline>ep -r TODO .`, which
    main let through too), and ANSI-C quoting (`$'gr\x65p' -r TODO .`). The shell pre-filter now sends any of them
    to Python, which judges the word unquoted, joined and decoded, as well as the command as written. A command
    with an apostrophe inside a word (`-m "don't"`) now starts Python too; it is still allowed.
  - Reads of what an archive holds, which no file name shows: BSD grep's `-Z`, ugrep's `-z`, `rg -z`,
    `--decompress`, `zgrep` and `zcat` and their kin, and the archive tools (`unzip -p`/`-c`, `funzip`, `zipgrep`,
    `tar`/`bsdtar -xO` and `@archive`, `7z e|x -so`, `ditto -x`, `cpio -i`, `pax -r`, and `gunzip -c` or
    `gzip -dc`). An archive in the vault, or one whose path the gate can't place (stdin, `$VAR`, a glob, a link it
    can't resolve), is denied, since its members can't be named; one outside the vault is allowed. It is placed
    by its real path, as a `cat` operand is, so a link into the vault counts as in it. In the vault, only
    `<stem>.<ext>.<compression>` (`app.log.gz`) is read as one plain file; `kb.gz` or a tarball with no suffix may
    be an archive. A recursive decompressing search of a vault folder, and an archive tool fed by `xargs`, are
    denied. A file filter that lets compressed files or archives through (`grep -rZ --include='*.gz'`) no longer
    keeps a vault-root search out of the KB.
  - Archives and compressed copies written from notes: `tar -cf - .`, `pax -w .`, `zip -r`, `7z a`, `ditto -c`
    and the compressors read their inputs, which are judged as a recursive read's roots (`tar -cf - context` from
    `action-items/`, `zip -qr ~/x.zip .`). A file list on stdin (`| cpio -o`, `| pax -w`, `tar -T -`, `zip -@`)
    that names notes is a KB read, and one the gate can't see (`tar -T list`) is denied. An archive or compression
    tool naming a KB path (`tar -czf out.tgz knowledge-base`, `gzip -c knowledge-base/a.md`) is a KB read.
