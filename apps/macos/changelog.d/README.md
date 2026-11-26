# Changelog fragments (app)

A PR never edits `apps/macos/CHANGELOG.md`. It adds `apps/macos/changelog.d/<branch-slug>.<section>.md`
holding its entry's `- ` bullets, one file per section (`added`, `changed`, `fixed`, `removed`, …).
`scripts/release.sh prepare` assembles them into the release's section. The format and the rules are in
[`plugin/changelog.d/README.md`](../../../plugin/changelog.d/README.md); paths in app entries are relative
to `apps/macos/`.
