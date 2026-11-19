- **Vault scripts that read the knowledge base around `scoutctl kb` are split by risk** (#505). A script
  that mentions a way to send what it reads (chat, mail, a web request, a notifier, `gh`, a `claude -p`
  session, `drafts/`) stays a `privacy.script-raw-kb-read` warning, ranked high. One that mentions none,
  a checker whose output stays in the vault, is now a separate low-risk warning,
  `privacy.script-raw-kb-read.internal`, grouped the same way, so a mature vault's checkers become one
  low-risk line the owner can review once.
