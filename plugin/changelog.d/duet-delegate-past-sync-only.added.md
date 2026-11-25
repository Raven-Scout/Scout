- **A delegate can see past `privacy.sync_only`** (`engine/scout/kb/privacy.py`, `engine/scout/delegate_mirror.py`).
  `sync_only` is the allowlist for what reaches Agnes, and every delegate mirror kept to it too, so an agent
  of your own got only the Agnes folders. Set `privacy.delegates.<id>.ignore_sync_only: true` to let that one
  delegate's mirror see the rest of the vault. Like `include_private`, it counts only when written exactly
  `true`, and only when both `scout-config.yaml` and its committed version say so. Local-only paths and the
  phone inbox still never go, a private note still needs `include_private: true`, and other delegates and
  Agnes keep the allowlist.
