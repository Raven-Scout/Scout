---
status: accepted
---

# Brain skills are assembled from connector-based phases

The plugin does not ship SKILL.md, DREAMING.md and RESEARCH.md whole. It ships phases, organised by connector and scan direction (Slack inbound scan, GitHub query), and an upgrade assembles them into the vault's brain skills, with a Run Modes table mapping each slot key to the phase blocks it runs. Connector phases have been the design since the plugin's first day (2026-04-10). In 2026 a long-running vault had grown a mode-based brain skill instead (one linear section per run type), and every upgrade produced the same merge conflict, so the structure was settled deliberately: connector-based won on convergence (the plugin's structure ends the recurring merge conflict by construction), on self-improvement (one focused edit applies to every run type through the table) and on distributability (users enable only the connectors they have). The cost, accepted consciously, is legibility: no single section reads as "what a briefing does". Recorded in [the brain-structure decision](../superpowers/specs/2026-06-22-skill-brain-structure-direction-design.md) (written 2026-06-22, accepted 2026-07-05).

## Consequences

An assembled brain skill is large (SKILL.md runs to thousands of lines), and every run reads the whole of it.
