---
status: accepted
---

# Dreaming edits brain skills directly; proposals are the exception

Dreaming used to be allowed only to propose changes to SKILL.md, which then waited for the user's approval. We retired that gate in favour of transparency and reversibility: dreaming applies additive and feedback-aligned edits directly, each as a git commit the user can revert. Large, structural, behaviour-removing or uncertain changes become opt-out dreaming proposals, applied after their date unless rejected; only governance and safety changes wait for an explicit Approved. When the harness blocks a direct edit, dreaming falls back to writing a proposal. The trade-off is review for autonomy: improvements reach the next run without the user in the loop, and a bad edit is caught after the fact rather than before. The rationale beyond "transparency and reversibility" is reconstructed from the diff, since no spec records it. Decided in [#93](https://github.com/Raven-Scout/Scout/pull/93) (2026-06-02); the rules live in `plugin/phases/modes/feedback-processing.md`.
