- **Review what your agents proposed, in Settings ▸ Agent copies (#550 V4b3).**
  - **Each agent with an inbox gets its inbox in a line:** how many proposals wait for your review, or why the
    last pull was refused. When the engine offers it, a **Review** button opens a sheet with each proposal.
  - **How the text is shown:** each item is headed `FROM AGENT delegate-<id>`. The agent's text is plain
    monospaced text (never Markdown, no links), with hidden characters shown as `\u{…}` and look-alike words
    flagged.
  - **Accepting** opens a confirmation that repeats the agent's id and the full text, with an "Accept from
    delegate-<id>" button. Reject is one click.
  - **Accepted observations** can be promoted or made private again. Each first shows a preview: the exact text
    and exactly which agents will see it, or stop seeing it. The button names those agents ("Promote to
    delegate-a, delegate-b", "Make private again: hide from …"). The app passes the preview's hash back and
    promotes to exactly the agents it named. A refusal shows in the engine's own words.
  - **What the app may run:** every `scoutctl inbox` command goes through `InboxReviewService`, which allows only
    review, accept, reject, promote and unpromote. A source-scan test fails on any other use. Pull, apply,
    revert and sync stay with the schedule and Terminal.
