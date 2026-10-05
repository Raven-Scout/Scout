# Scout.app — docs

Design docs and implementation plans for features in this repo. Filed by topic, one design + one plan per feature.

## Current docs

| Topic | Design | Plan |
| --- | --- | --- |
| Schedules tab (full CRUD on `com.scout.*.plist`, auto-reload, scoped git commits) | [schedules-design.md](./schedules-design.md) | [schedules-plan.md](./schedules-plan.md) |

## Where things go

- **Design docs** live here as `<topic>-design.md`. Each captures the spec after brainstorming: what we're building, what we're not, what the architecture looks like, and how to verify. Design docs get committed *before* implementation.
- **Implementation plans** live here as `<topic>-plan.md`. Each breaks a design into bite-sized TDD tasks with failing tests, real code, and commits. They're most useful during execution but stay around as a narrative record of how a feature was built.
- **The `BACKLOG.md` at the repo root** is the running list of things to tackle next. Shipped items move to its "Shipped" section with the date.

## Writing a new one

If you're using the [superpowers](https://github.com/anthropic-experimental/superpowers) plugin, the `brainstorming` and `writing-plans` skills produce these files naturally. Otherwise, mimic the two files already here for structure — prose-first, YAGNI-aware, explicit about non-goals and test strategy.
