"""Ordinary words a vault names notes with (#576).

A private note's slug, ``name:`` or ``title:`` that is one of these words on its
own is not a private term (:func:`scout.kb.outbound.note_from_text`): ``plan.md``
or ``billing.md`` must not refuse every sentence that says "plan" or "billing".
Such a note still matches by a link, its path, a longer name and its aliases.

Words for kinds of documents and the topics, areas and projects a knowledge base
files notes under, case-folded. Keep it to those: a word added here stops a
one-word private name from being matched, so a project's code name or a
person's name never belongs here (person notes keep their names anyway).
"""

from __future__ import annotations

ORDINARY_WORDS: frozenset[str] = frozenset(
    """
    about account accounts accounting active admin agenda analytics annual archive archived areas
    attachments backlog backup backups benefits billing birthday blocked bookmarks books brainstorm
    broken budget budgets calendar candidates changelog checklist client clients company
    compensation compliance conference conferences config contacts content contract contracts costs
    courses culture current customer customers daily dashboard data deals decision decisions design
    development diary docs document documents done downloads draft drafts education engineering
    events expenses experiment experiments family faq feedback files finance financial fitness food
    friends garden general gifts glossary goal goals growth guide guides health hiring history holiday
    holidays home house howto idea ideas images inbox index infra infrastructure insurance integration
    interview interviews invoice invoices journal kids later launch leads learning legal links log
    logs maintenance marketing media medical meeting meetings metrics migration minutes misc mission
    monthly moving music new note notes onboarding operations ops org other overview partner partners
    payment payments payroll people performance permits personal photos pipeline plan planning plans
    playbook pricing priorities privacy process processes product products project projects proposal
    proposals prototype quarterly questions readme reading recipes recruiting reference references
    release releases renovation repairs report reporting reports research resource resources retro
    retrospective revenue review reviews roadmap runbook salary sales sandbox scan scans schedule
    school scratch security settings setup shopping someday status strategy summaries summary
    support task tasks tax taxes team teams template templates temp test tests timeline today todo todos
    tooling tools topics travel trips tutorial update updates uploads vacation values vendor vendors
    vision waiting week weekly wishlist work workflow year yearly
    """.split()
)
