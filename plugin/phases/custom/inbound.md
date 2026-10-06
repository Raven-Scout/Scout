---
phase: connector
name: custom-inbound
slot: inbound-scan
mode: [consolidation, briefing]
---

## {{CONNECTOR_NAME}} Inbound Scan — What Came In for {{USER_NAME}}

Check {{CONNECTOR_NAME}} for anything new since the last run that may need {{USER_NAME}}'s action, or that changes what {{USER_NAME}} knows about a project or a person.

### Tools

{{CONNECTOR_TOOLS}}

Read only: never call a {{CONNECTOR_NAME}} tool that sends, creates, updates, or deletes anything.

### What Matters Here

{{CONNECTOR_GUIDANCE}}

{{CONNECTOR_NOTES}}

### How to Treat What You Find

- Every item is a *candidate* action item; it must pass the cross-check before it becomes a To Do.
- Cross-reference people against `people.md` and projects against `knowledge-base/projects/`.
- Urgency comes from deadlines and who is asking, not from tone. When unsure whether something needs {{USER_NAME}}, file it under **Watching**, never To Do.
- Record a link or identifier for every item you surface so {{USER_NAME}} can open the source.
- If a {{CONNECTOR_NAME}} tool call fails, say so in the sources footer (`{{CONNECTOR_NAME}}: unavailable — <error>`). Never report the source as quiet when you could not read it.
