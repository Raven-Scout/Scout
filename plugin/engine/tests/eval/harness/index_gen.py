"""A deterministic, synthetic ``knowledge-base.md`` for the eval fixture.

Its size is the point: a real index is large enough that reading it whole is
the wrong move (#196), and committing that much filler isn't worth it. Every
word comes from the neutral lists below, so the fixture scrub allowlist only
needs the few capitalised headings.
"""

from __future__ import annotations

import random

HEADINGS = ("Projects", "Topics", "Sources", "Meetings", "Decisions", "Glossary")
_NOUNS = tuple(
    (
        "ledger pipeline gateway widget cache queue schema report budget rollout migration dashboard "
        "contract runbook backlog roadmap audit billing export import sync index archive review "
        "release metric alert handoff template checklist survey forecast invoice catalog"
    ).split()
)
_ADJECTIVES = tuple(
    (
        "quarterly legacy shared internal weekly regional draft pending stable nightly manual "
        "partial upstream downstream optional core"
    ).split()
)
_VERBS = tuple(("tracks covers summarises lists explains records compares outlines").split())


def generate_index(*, seed: int = 196, entries: int = 600) -> str:
    rng = random.Random(seed)
    out = ["# Knowledge Base", ""]
    per_heading = max(1, -(-entries // len(HEADINGS)))
    written = 0
    for heading in HEADINGS:
        if written >= entries:
            break
        out += [f"## {heading}", ""]
        for _ in range(min(per_heading, entries - written)):
            adj, noun, other = rng.choice(_ADJECTIVES), rng.choice(_NOUNS), rng.choice(_NOUNS)
            slug = f"{heading.lower()}/{adj}-{noun}-{written:04d}"
            phrase = (
                f"{rng.choice(_VERBS)} the {adj} {noun} and how it feeds the {other}; "
                f"see the {rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)} notes for history"
            )
            out.append(f"- [[{slug}]] — {phrase} · **Last verified:** {{{{d-{rng.randint(1, 400)}}}}}")
            written += 1
        out.append("")
    return "\n".join(out)
