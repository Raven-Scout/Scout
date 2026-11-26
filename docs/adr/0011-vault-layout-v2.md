---
status: accepted
---

# The vault keeps Scout's system, records, output, knowledge and input apart, and the engine owns the layout

A vault used to mix brain skills, runners and scripts into its root, keep Scout's run records inside the knowledge base, and give meetings two homes. Layout v2 gives each kind of file one place: Scout's system in a visible `scout/` folder (brain skills, runners, scripts, hooks), Scout's records in `scout/records/`, generated content in `action-items/`, `drafts/` and `reports/`, knowledge in `knowledge-base/`, and user input in `inbox/`. `scout-config.yaml` and the dot-folders stay where they are. What is about the knowledge base stays in it, so the review and research queues and all meetings live there, and the knowledge base is divided into space folders now (`knowledge-base/<space>/`) instead of in a second restructure for Feature 1. A person or organization that spans spaces has one Scout-wide identity note and a role note per space, so privacy stays per file and nothing moves when a relationship changes space. Each space has a default visibility.

It ships as the plugin default. Existing vaults move in two parts: a deterministic layout move that runs as a stage of every upgrade, attended or not, and a space review the user approves afterwards. The engine is the one owner of vault paths: a `layout` module serves every caller, and `scoutctl vault layout --json` with a contract fixture is how clients learn them, so the next move doesn't mean editing path constants in three codebases. Rejected: a vault-only layout (every hardcoded path would need an override), a hidden `.scout/` folder (brain skills are meant to be edited), moving the dot-folders (cost without clarity), one person note per space or one note with a section per space (both break per-file privacy), and approving the whole space mapping before anything moves (self-update and the app would have to hold the release). Decided with David on 2026-11-26 in [the layout v2 spec](../superpowers/specs/2026-11-26-vault-layout-v2-design.md).

## Consequences

The move rewrites old paths only where they are followed literally or in current files. Archived daily files keep their old link prefixes, which still resolve by name. Compatibility symlinks at the old entry points stay for one upgrade cycle. Undoing the move is a documented manual procedure (revert two commits, upgrade on the previous release), not a command.
