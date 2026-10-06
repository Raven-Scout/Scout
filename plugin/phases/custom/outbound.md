---
phase: connector
name: custom-outbound
slot: outbound-scan
mode: [consolidation]
---

## {{CONNECTOR_NAME}} Outbound Scan — What {{USER_NAME}} Did There

Check {{CONNECTOR_NAME}} for what {{USER_NAME}} did since the last run. What {{USER_NAME}} sent, closed, or changed is the strongest evidence that an action item is done or in progress.

### Tools

{{CONNECTOR_TOOLS}}

Read only: never call a {{CONNECTOR_NAME}} tool that sends, creates, updates, or deletes anything.

### What Matters Here

{{CONNECTOR_GUIDANCE}}

{{CONNECTOR_NOTES}}

### What to Record

For each thing {{USER_NAME}} did, note who or what it touched, what it was about, and what it implies:

- A reply to a request: that request is likely handled.
- A delivered artifact (file, link, decision): something was completed.
- A new commitment: a new action item for {{USER_NAME}}.
- A hand-off: track the delegation.

If a {{CONNECTOR_NAME}} tool call fails, say so in the sources footer instead of assuming nothing happened.
