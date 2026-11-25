- **The KB read gate reads a command word as zsh runs it** (`hooks/prefilter.sh`,
  `engine/scout/kb/raw_read.py`). A scheduled run could read knowledge-base notes from the vault root in three
  ways the gate missed (found by the #820 security review under real `zsh -f`):
  - zsh's precommand modifiers: `noglob grep -r TODO .`, `nocorrect …` and `noglob cat knowledge-base/a.md`
    were judged as a program called `noglob`. `noglob`, `nocorrect` and `nohup` are now prefixes like `sudo`,
    `exec -a NAME` no longer reads NAME as the command, and a prefix or program spelled with quotes
    (`no''glob`, `c''at`) is read without them.
  - A quote or backslash inside the command word (`gr''ep -r TODO .`, `g\rep -R TODO`, `fi''nd . -exec cat {} +`):
    the shell pre-filter matched program names only unquoted, so these never reached Python. Any word with a
    quote or backslash between its letters now goes to Python, which judges it unquoted. A command with an
    apostrophe inside a word (`-m "don't"`) now starts Python too; it is still allowed.
  - Reads of what an archive holds, which no file name shows: BSD grep's `-Z`, ugrep's `-z`, `--decompress`,
    `zgrep` and `zcat` and their kin, and the archive tools (`unzip -p`/`-c`, `funzip`, `tar`/`bsdtar -xO` and
    `@archive`, `7z e|x -so`, `ditto -x`, `cpio -i`, `pax -r`, and `gunzip -c` or `gzip -dc` of a `.tar.gz`). An
    archive in the vault, or one whose path the gate can't place (stdin, `$VAR`), is denied, since its members
    can't be named; one outside the vault is allowed. A file filter that lets compressed files or archives
    through (`grep -rZ --include='*.gz'`) no longer keeps a vault-root search out of the KB, and an archive or
    compression tool naming a KB path (`tar -czf out.tgz knowledge-base`, `gzip -c knowledge-base/a.md`) is a
    KB read.
