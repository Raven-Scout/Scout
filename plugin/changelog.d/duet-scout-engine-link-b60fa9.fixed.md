- **A delegate's mirror keeps the folders it was granted when a hidden note shares their name**
  (`engine/scout/delegate_mirror.py`, `engine/scout/kb/outbound.py`). The mirror treats each hidden
  note's name as secret, so a private `ontology/entities/acme.md` dropped every file under the granted
  `topics/acme/` and cut every line saying "Acme". A private `knowledge-base/knowledge-base.md` did the
  same to every note under `knowledge-base/`, so a vault with a `privacy.sync_only` allowlist got a
  mirror holding only its config. Now a name or path that a folder the delegate sees already holds is
  no longer secret: the mirror shows that folder's name anyway. The note's other names (aliases, a
  title no shown folder holds) and links to it are still cut. A file's own name never clears a term,
  and neither does a folder holding only hidden files. The scan fingerprint now includes the shown
  folders, so the first sync after upgrading rescans every file.
