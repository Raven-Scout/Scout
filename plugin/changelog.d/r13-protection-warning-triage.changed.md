- **Vault scripts that read the knowledge base around `scoutctl kb` are split by risk** (#505). A script
  stays a `privacy.script-raw-kb-read` warning, ranked high, when what it reads can leave the vault: it
  mentions a way to send (chat, mail, a web or network call, `ssh`/`rsync`/`git push`, a notifier or the
  broker, `gh`, a Claude session, a synced folder, `drafts/`, code it builds at run time), it uses a vault script that does, another
  vault script or runner runs it (so what it prints may be piped on), or it is too long or can't be read. Any
  other, a checker whose output stays in the vault, is now a separate low-risk warning,
  `privacy.script-raw-kb-read.internal`, grouped the same way, so a mature vault's checkers become one
  low-risk line the owner can review once.
