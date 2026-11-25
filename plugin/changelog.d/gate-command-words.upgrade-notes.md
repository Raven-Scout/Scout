- **Scheduled runs now start with `GIT_CONFIG_GLOBAL` set, once this release is installed and the vault upgraded**
  (`templates/run-*.sh.tmpl`). Each runner exports `GIT_CONFIG_GLOBAL=<your home>/.gitconfig` before Claude
  starts. That is the file git reads anyway, so git behaves as before, but a session command that points HOME
  elsewhere can no longer hand git a config that turns the commit hook off. The runner leaves it unset when
  pinning would change what git reads:
  - `preset`: GIT_CONFIG_GLOBAL is already set (the eval harness's `/dev/null`, or your own);
  - `xdg`: your account also has an XDG git config (`$XDG_CONFIG_HOME/git/config` or `~/.config/git/config`),
    which git reads as well;
  - `home`: HOME at launch isn't your account's home;
  - `unknown`: the runner couldn't look your home up.

  Which one happened is in each run's log (`=== git config: pinned ===`, or the reason) and in its
  `.scout-logs/run-outcomes.jsonl` row (`git_config_pin`). GIT_CONFIG_NOSYSTEM isn't set, so the system git
  config (a credential helper, say) is read as before.
