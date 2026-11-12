---
status: accepted
---

# One engine-owned tick reads the vault's schedule; the app only mirrors it

Slots live in the vault's `schedule.yaml`, and a single launchd job runs the tick every five minutes, starting at most one due slot per tick. It replaced seven per-slot launchd jobs and the app's own runner, so Scout runs on schedule without the app open, and catch-up after sleep uses the same code path as a normal fire. The app reads and edits the schedule and never starts a run on its own; its "Run now" buttons ask the engine to fire a slot (`scoutctl schedule fire-now`). Waking the Mac with `pmset` was rejected as the primary mechanism because it is unreliable on battery with the lid closed; it survives as an opt-in, AC-only wake schedule. Decided in [the schedule v2 spec](../superpowers/specs/2026-05-04-schedule-v2-design.md), landed in [#11](https://github.com/Raven-Scout/Scout/pull/11) (engine) and [scout-app-legacy#6](https://github.com/Raven-Scout/scout-app-legacy/pull/6) (app), both 2026-05-07.
