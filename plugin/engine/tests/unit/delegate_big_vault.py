"""A generated vault of a few hundred to a few thousand files, for the delegate mirror's scale tests.

:func:`build_big_vault` starts from the anonymised privacy fixture vault (``privacy_vault_helpers``), lists the
two delegates ``tests/unit/test_delegate_mirror.py`` uses (``grok-personal`` sees normal notes only, ``duet``
private ones too), and adds generated notes:

- private people notes (``type: person``, an alias each) and private topic notes (a title each), which
  ``grok-personal`` can't see, so their names, aliases, titles and paths are what its mirror may not name;
- private daily notes, the kind an owner edits every day;
- normal notes of prose, some of which name a private note (by name, alias, title, wikilink or path) and so
  lose lines, a few in their frontmatter;
- a few ``.txt`` and ``.csv`` files, one of which names a private note.

Everything is made up from syllables with a fixed seed, so the same arguments make the same vault.
"""

from __future__ import annotations

import random
from pathlib import Path

from scout.scripts import privacy_migrate

from .privacy_vault_helpers import build_privacy_vault, git

DELEGATES_BLOCK = """\
  delegates:
    grok-personal: {}
    duet:
      include_private: true
"""

_SYLLABLES = (
    "ka ro mi ta zen vo lu pe sa dri mor tal ven qui fa bel nor sto xa gri pla dun "
    "hol mek ras tiv yon zar bro cle fen gor hux jal kip lom nev opa"
).split()


def _word(rng: random.Random, parts: int) -> str:
    return "".join(rng.choice(_SYLLABLES) for _ in range(parts))


def _name(rng: random.Random) -> str:
    return f"{_word(rng, 2).capitalize()} {_word(rng, 3).capitalize()}"


def _prose(rng: random.Random, lines: int) -> list[str]:
    return [" ".join(_word(rng, rng.randint(1, 3)) for _ in range(rng.randint(8, 18))) + "." for _ in range(lines)]


def _write(vault: Path, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_big_vault(tmp: Path, *, people: int, topics: int, daily: int, normal: int, seed: int = 7) -> Path:
    """The privacy fixture vault with generated notes added and committed (module docstring)."""
    rng = random.Random(seed)
    vault = build_privacy_vault(tmp).resolve()
    config = vault / "scout-config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + DELEGATES_BLOCK, encoding="utf-8")
    with (vault / ".gitignore").open("a", encoding="utf-8") as gi:
        gi.write(".scout-logs/\n")

    hidden: list[tuple[str, str]] = []  # (how a visible note names it, the form) for the mentions below
    for n in range(people):
        name, alias = _name(rng), _word(rng, 3).capitalize()
        slug = f"{name.lower().replace(' ', '-')}-{n}"
        rel = f"knowledge-base/people/{slug}.md"
        body = "\n".join(_prose(rng, rng.randint(5, 30)))
        _write(
            vault,
            rel,
            f"---\nname: {name}\ntype: person\nvisibility: private\naliases: [{alias}]\n---\n\n# {name}\n\n{body}\n",
        )
        hidden += [("name", name), ("alias", alias), ("link", f"[[{slug}]]"), ("path", rel[:-3])]
    for n in range(topics):
        title = f"{_word(rng, 2).capitalize()} {_word(rng, 2)} {_word(rng, 3)}"
        rel = f"knowledge-base/topics/private/{_word(rng, 3)}-{n}.md"
        body = "\n".join(_prose(rng, rng.randint(5, 40)))
        _write(vault, rel, f"---\ntitle: {title}\nvisibility: private\n---\n\n# {title}\n\n{body}\n")
        hidden += [("title", title), ("path", rel[:-3])]
    for n in range(daily):
        rel = f"daily/2026-{1 + n // 28:02d}-{1 + n % 28:02d}.md"
        body = "\n".join(_prose(rng, rng.randint(10, 40)))
        _write(vault, rel, f"---\nvisibility: private\n---\n\n# Daily\n\n{body}\n")
    for n in range(normal):
        lines = _prose(rng, rng.randint(10, 60))
        front = f"---\ntitle: {_word(rng, 2).capitalize()} {_word(rng, 3)} {n}\nvisibility: normal\n"
        if hidden and rng.random() < 0.06:
            _how, form = rng.choice(hidden)
            lines.insert(rng.randrange(len(lines)), f"Met {form} about the {_word(rng, 2)} plan.")
        if hidden and rng.random() < 0.02:
            front += f"related:\n  - {_word(rng, 2)}\n  - {rng.choice(hidden)[1]}\n"
        _write(vault, f"knowledge-base/topics/t{n % 40}/{_word(rng, 3)}-{n}.md", front + "---\n\n" + "\n".join(lines))
    _write(vault, "Raw/metrics.csv", "week,signups\n" + "".join(f"W{n},{n * 7 % 97}\n" for n in range(500)))
    if hidden:
        _write(vault, "Raw/call-notes.txt", "\n".join([*_prose(rng, 20), f"Called {hidden[0][1]}.", *_prose(rng, 5)]))
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "generated notes")
    privacy_migrate.accept(vault)
    return vault
