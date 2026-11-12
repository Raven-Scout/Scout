# Scout

Scout is a personal chief-of-staff agent: scheduled Claude Code runs read the user's connected tools, keep a
knowledge base and a daily list of action items in a git repository the user owns, and report back. This
glossary covers both the plugin with its engine (`plugin/`) and the macOS app (`apps/macos/`).

It sets the words Scout uses going forward. Where code or UI still uses a word listed under _Avoid_, that is
a rename waiting to happen, not a second meaning.

## Language

### Vault

**Vault**:
The user's own Scout git repository, holding the knowledge base, the action items and the brain skills.
_Avoid_: Scout folder, data dir, repo

**Space**:
A separate area of the user's life, such as work, personal or a homeowners' association, that Scout keeps apart while still
reasoning across them.
_Avoid_: domain, realm, area

**Inbox**:
The vault file where the user drops notes for the next run to route into action items or the knowledge base.
_Avoid_: using "Inbox" for the daily action-items file's section of unsorted mentions

### Knowledge base

**Knowledge base**:
The part of the vault holding what Scout durably knows about the user's people, projects, domains and
decisions.
_Avoid_: knowledge graph, memory

**Entity**:
One knowledge-base note about a single person, organization, project or other thing, whose type comes from
the ontology.

**Ontology**:
The entity types and relationships the knowledge base allows.
_Avoid_: schema, knowledge graph

**Domain**:
A subject area of durable knowledge within the knowledge base, such as data-platform, that groups topics.
_Avoid_: subject, using "domain" for a space or for an organization's website

**Topic**:
One concept within a domain, written up as a single note, such as a platform's permission model.
_Avoid_: topic note, using "topic" for a group of action items

**Review item**:
A claim or decision Scout is not sure enough about to write into the knowledge base, waiting to be confirmed
or rejected.
_Avoid_: proposal, pending item

**Review queue**:
The list of open review items.

### Runs

**Run**:
One execution of Scout, started by a slot, by the heartbeat or by hand, that ends with a wrap.
_Avoid_: session, Scout session

**Session**:
A Claude Code conversation. A run happens inside one, but most sessions are not runs.
_Avoid_: using it for a run

**Connector**:
A tool Scout reads from or writes to, such as Slack, Linear or Gmail, listed in the connector roster.
_Avoid_: integration, source, MCP

**Slot**:
A named, recurring time in the vault's schedule at which Scout starts a run.
_Avoid_: schedule entry, job, using "slot" for an insertion point in a phase

**Slot key**:
The ID of a slot, such as `morning-briefing`, recorded by every run it starts; a manual run or an extra run
records a slot key of its own kind instead.
_Avoid_: run mode, mode

**Tick**:
The check every five minutes that starts a due slot, at most one per tick.
_Avoid_: heartbeat, using "tick" for a heartbeat check

**Heartbeat**:
The separate housekeeping check every thirty minutes: it sweeps run health, starts the daily self-update and
may start an extra run.
_Avoid_: tick, heartbeat schedule

**Extra run**:
A dreaming or research run that the heartbeat starts outside any slot, when there is budget to spare and work
waiting.
_Avoid_: heartbeat session, bonus run

**Run health**:
The engine's findings on whether runs fire when they should and finish well.
_Avoid_: doctor, health dashboard

**Run type**:
What kind of work a run does: briefing, consolidation, dreaming, research or manual. A slot has exactly one.
_Avoid_: slot type, session type, lane, mode

**Briefing**:
The run type that does a full scan of the connected tools and writes the day's action items.
_Avoid_: morning mode

**Consolidation**:
The run type that does a lighter scan of what changed since the last run and updates action items and the
knowledge base.

**Dreaming**:
The run type that works on Scout itself: processes feedback, does deep knowledge-base work and works the
wishlist.

**Research**:
The run type that investigates queued research items in depth.

**Research item**:
One entry in the research queue, waiting for a research run.
_Avoid_: research topic

**Wrap**:
The message a run sends the user when it finishes, through the user's chosen channels.
_Avoid_: wrap DM, wrap-up, wrap notification, notification

**Stage**:
One of the ordered parts of a dreaming or research run, such as Stage 2, knowledge-base deep work.
_Avoid_: phase, Phase N

**Step**:
A numbered instruction inside a phase or a runner prompt.

### Brain

**Brain skill**:
A vault-owned set of instructions that a run follows from start to finish: SKILL.md, DREAMING.md or
RESEARCH.md. It is assembled from phases and then edited by the user and by dreaming.
_Avoid_: brain file, kind, a bare "skill" where it could mean a plugin skill

**Plugin skill**:
A Claude Code skill shipped read-only with the plugin, such as scout-ask or scout-work, used in interactive
sessions.

**Phase**:
A prompt fragment file in the plugin; brain skills are assembled from phases.
_Avoid_: using "phase" for a stage, or for a phase block's category

**Phase block**:
One frontmatter-plus-body block inside a phase, carrying the run types it applies to and where it is inserted.
_Avoid_: phase section, section

### Self-improvement

**Feedback**:
The user's reactions and replies to Scout's messages, which dreaming learns from.
_Avoid_: notes, comments

**Wishlist item**:
A request for a change to Scout itself, kept in the vault and worked on by dreaming.
_Avoid_: feature request, wish

**Dreaming proposal**:
A change to a brain skill that dreaming writes down instead of applying straight away. An opt-out one is
applied after its date unless the user rejects it; a governance one waits for the user's approval.
_Avoid_: a bare "proposal", suggestion, using "proposal" for a review item, a merge sidecar or a draft plan

### Action items

**Daily action-items file**:
The vault file, one per day, that lists the day's action items in sections and that the app shows.
_Avoid_: daily note, briefing file

**Action item**:
One tagged thing the user should do or keep an eye on, listed in the daily action-items file.
_Avoid_: task, todo, a bare "item", board item, using "step" for an action item

**Priority**:
An action item's level: Urgent, To Do or Watching.
_Avoid_: tier, category

**Context note**:
The file holding the full context behind one action item, named by its tag.
_Avoid_: full context, details file

**Side entry**:
An untagged bullet in one of the daily action-items file's side sections, such as unsorted mentions or loose
ends. It is not an action item.

**Section**:
A user-facing part of the daily action-items file, such as Urgent, To Do, Watching, Parked, Meetings or Digest.
_Avoid_: using "section" for a phase block

**Tag**:
The `[#XXXX]` identifier of one action item or one workstream. A tag identifies; it never categorises.
_Avoid_: prefix, short prefix, label, using a tag as a category

**Workstream**:
A short-lived, tagged group of open action items that belong to the same piece of work.
_Avoid_: topic

**Digest**:
The section of the daily action-items file where all of the day's runs summarise what Scout has been doing.
One per day, shared by every run.
_Avoid_: wrap, run summary

**Run notes**:
The footer of the daily action-items file where each run logs its counts and connector availability.
_Avoid_: run log, digest

### Install

**Engine**:
`scoutctl`, Scout's deterministic program: runs, brain skills and the app all call it for anything that must
not be left to a prompt.
_Avoid_: using "engine" for the triggers dispatcher

**Engine install**:
One copy of the engine on the user's machine, together with who manages it: the app, Claude Code or a
developer checkout.
_Avoid_: engine checkout, venv

**Setup**:
The first-time creation of a vault and an engine install.
_Avoid_: bootstrap, onboarding, using "install" for setup

**Upgrade**:
Bringing a vault to the version of its engine install: reassembling the brain skills, re-rendering the
scripts and merging in the user's edits.
_Avoid_: update, bootstrap, reinstall

**Self-update**:
The heartbeat's daily, unattended move of an engine install that the app does not manage to the latest
release, followed by an upgrade.
_Avoid_: auto-update, headless update

**Drift**:
A vault's own edit to a plugin-owned file, which an upgrade parks beside the new version for the user to
resolve instead of overwriting it.
_Avoid_: using "drift" for version, snapshot or privacy mismatches

**Merge sidecar**:
The file an upgrade or a sync leaves beside a vault file it will not overwrite, holding its own version or a
conflict-marked merge. The vault file keeps working until the user resolves it.
_Avoid_: proposal, proposed merge, conflict file, a bare "sidecar"
