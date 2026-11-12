# Issue tracker

Two trackers, depending on which line of work a ticket belongs to.

## Scout Next (`de-scout-next`): the "Scout Next" project board, for now

While the redesign lives on the `de-scout-next` integration branch, its tickets are **draft items on the
"Scout Next" GitHub project board** (https://github.com/users/davidesner/projects/2, private), not GitHub issues, so
they don't clutter the issue list of the shipping version. When `de-scout-next` merges into `main`, open
drafts get converted to issues in `Raven-Scout/Scout` and this section is deleted.

Use `gh api graphql`. Project id: `PVT_kwHOAO7dbM4BrMiw`.

- **List tickets**:
  `gh api graphql -f query='query{node(id:"PVT_kwHOAO7dbM4BrMiw"){... on ProjectV2{items(first:100){nodes{id fieldValues(first:10){nodes{... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}}} content{... on DraftIssue{id title body}}}}}}}'`
- **Create a ticket**: `addProjectV2DraftIssue(input:{projectId:"PVT_kwHOAO7dbM4BrMiw", title:"...", body:"..."})`.
- **Edit a ticket's text**: `updateProjectV2DraftIssue(input:{draftIssueId:"DI_...", title:"...", body:"..."})`.
- **Set a field**: `updateProjectV2ItemFieldValue` with the item id (`PVTI_...`), the field id and the option id.
  Fields and options:
  - Status `PVTSSF_lAHOAO7dbM4BrMiwzhpgUsk`: Todo `f75ad846`, In Progress `47fc9ee4`, Done `98236657`
  - Priority `PVTSSF_lAHOAO7dbM4BrMiwzhpgVB0`: High `b64e7a80`, Medium `e76390dc`, Low `5b6d4c26`, Nice to have `e77d14c6`
  - Area `PVTSSF_lAHOAO7dbM4BrMiwzhpgVCo`: App `fe810378`, Plugin `65fef70c`, App + Plugin `e5fa947d`, Repo `2ecec9cb`
  - Source `PVTSSF_lAHOAO7dbM4BrMiwzhpgVDY`: Wishlist `523688b0`, Roadmap `d58c920a`
  - Order `PVTF_lAHOAO7dbM4BrMiwzhpgqSI` (number): position on the board
- **Close**: set Status to Done. Draft items have no comments: append findings to the body instead.

Draft items have no labels, so the triage labels in `docs/agents/triage-labels.md` don't apply to them.

## Everything else: GitHub issues in `Raven-Scout/Scout`

Bugs and changes to the shipping version, and all tickets once Scout Next has merged. Use the `gh` CLI.

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Issue numbers here are shared with the former `scout-plugin` repo; see the root `CLAUDE.md` for how to read a
bare `#N` in `apps/macos/`.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

## When a skill says "publish to the issue tracker"

On `de-scout-next` work, create a draft item on the Scout Next board. Otherwise, create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Board ticket: read the draft item via the list query above. GitHub issue: `gh issue view <number> --comments`.
