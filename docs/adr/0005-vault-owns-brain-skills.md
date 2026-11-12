---
status: accepted
---

# The vault owns its brain skills; upgrades three-way merge them and never block

Brain skills are edited by the user and by dreaming, so an upgrade cannot simply overwrite them: an early upgrade silently replaced 85 KB of vault edits. An upgrade three-way merges the plugin's new assembly into the vault's copy, using the last assembly as the base. On a conflict it leaves a merge sidecar beside the brain skill, keeps the vault copy running, and goes on with the rest of the upgrade; conflict markers never go into the live file, because the next run would read them as instructions. Rejected: always writing a sidecar, always fast-forwarding (which wipes older vaults), fast-forwarding and parking the vault copy, and relying on a fingerprint alone, in favour of a recorded provenance per file (the fingerprint survives as a one-time fallback). Decided in [Plan 8](../superpowers/specs/2026-05-09-plan-8-scout-setup-repair-design.md) §4.5 and [sidecars never block](../superpowers/specs/2026-10-02-brain-sidecars-never-block-upgrade-design.md); landed in [#16](https://github.com/Raven-Scout/Scout/pull/16) (2026-05-11) and [#266](https://github.com/Raven-Scout/Scout/pull/266) (2026-10-04).

## Consequences

A merge sidecar for `parser.py` or `docs/kb-page-standard.md` left by an older engine still blocks an upgrade.
