"""Per-note privacy classifier and policy (#453, spec §3, §4.2, §5, §12.1–12.2).

Table-driven over the rules: every row is (path, text, expected label fields).
Fixtures use the anonymised stand-ins (Alex, Priya, Sam, example.com).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from scout.kb import privacy
from scout.kb.privacy import Label, Policy, classify, load_policy, normalize_rel, surface_class, visible_to


def _fm(body: str) -> str:
    return f"---\n{body}\n---\n# Note\n\nBody text.\n"


def _overlay(tmp_path: Path, body: str) -> Path:
    (tmp_path / "scout-config.yaml").write_text(body, encoding="utf-8")
    return tmp_path


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(None)


# (id, rel_path, text, expected fields)
_ROWS: list[tuple[str, str, str | None, dict[str, object]]] = [
    (
        "absent-visibility-is-normal-default",
        "knowledge-base/topics/misc/plain.md",
        _fm("name: Plain\ntype: topic"),
        {"visibility": "normal", "visibility_source": "default", "local_only": False, "shared_file": False},
    ),
    (
        "no-frontmatter-is-normal-default",
        "knowledge-base/topics/misc/plain.md",
        "# Plain\n\nNo frontmatter here.\n",
        {"visibility": "normal", "visibility_source": "default", "warnings": ()},
    ),
    (
        "private-trimmed-case-insensitive",
        "knowledge-base/people/sam.md",
        _fm('visibility: "Private "'),
        {"visibility": "private", "visibility_source": "frontmatter", "warnings": ()},
    ),
    (
        "explicit-normal",
        "knowledge-base/people/priya.md",
        _fm("visibility: NORMAL"),
        {"visibility": "normal", "visibility_source": "frontmatter", "warnings": ()},
    ),
    (
        "unknown-value-fails-closed",
        "knowledge-base/topics/misc/draft.md",
        _fm("visibility: PRIVATE while drafting"),
        {"visibility": "private", "visibility_source": "frontmatter", "warnings": ("visibility-value",)},
    ),
    (
        "null-value-fails-closed",
        "knowledge-base/topics/misc/draft.md",
        _fm("visibility:"),
        {"visibility": "private", "visibility_source": "frontmatter", "warnings": ("visibility-value",)},
    ),
    (
        "non-string-value-fails-closed",
        "knowledge-base/topics/misc/draft.md",
        _fm("visibility: true"),
        {"visibility": "private", "visibility_source": "frontmatter", "warnings": ("visibility-value",)},
    ),
    (
        "broken-yaml-is-forced-private",
        "knowledge-base/topics/misc/broken.md",
        _fm("name: [unclosed\nvisibility: normal"),
        {"visibility": "private", "visibility_source": "forced", "warnings": ("frontmatter-unparseable",)},
    ),
    (
        "non-mapping-frontmatter-is-forced-private",
        "knowledge-base/topics/misc/broken.md",
        _fm("- just\n- a list"),
        {"visibility": "private", "visibility_source": "forced", "warnings": ("frontmatter-unparseable",)},
    ),
    (
        "unclosed-fence-is-forced-private",
        "knowledge-base/topics/misc/broken.md",
        "---\nvisibility: normal\nname: Never closed\n",
        {"visibility": "private", "visibility_source": "forced", "warnings": ("frontmatter-unparseable",)},
    ),
    (
        "empty-frontmatter-uses-defaults",
        "knowledge-base/topics/misc/plain.md",
        "---\n---\nBody\n",
        {"visibility": "normal", "visibility_source": "default", "warnings": ()},
    ),
    (
        "bom-before-fence-still-parsed",
        "knowledge-base/people/sam.md",
        "﻿" + _fm("visibility: private"),
        {"visibility": "private", "visibility_source": "frontmatter"},
    ),
    (
        "personal-path-default-private",
        "knowledge-base/personal/x.md",
        _fm("type: task\ndomain: personal"),
        {"visibility": "private", "visibility_source": "path_default"},
    ),
    (
        "personal-explicit-normal-wins",
        "knowledge-base/personal/y.md",
        _fm("visibility: normal"),
        {"visibility": "normal", "visibility_source": "frontmatter"},
    ),
    (
        "inbox-jsonl-private-by-path",
        ".scout-inbox/a.jsonl",
        None,
        {"visibility": "private", "visibility_source": "path_default", "local_only": False},
    ),
    (
        "inbox-md-normal-synced",
        "inbox.md",
        "# Inbox\n",
        {"visibility": "normal", "visibility_source": "default", "local_only": False},
    ),
    (
        "raw-scan-normal-synced",
        "Raw/scan.txt",
        None,
        {"visibility": "normal", "visibility_source": "default", "local_only": False},
    ),
    ("venv-local-only", ".venv/x", None, {"local_only": True, "visibility": "normal"}),
    ("scout-state-lock-local-only", ".scout-state/lock", None, {"local_only": True}),
    ("env-file-local-only", ".env.local", None, {"local_only": True}),
    ("mcp-json-local-only", ".mcp.json", None, {"local_only": True}),
    ("sync-allow-id-map", ".scout-state/id-map.json", None, {"local_only": False}),
    ("sync-allow-schedule", ".scout-state/schedule.yaml", None, {"local_only": False}),
    ("sync-allow-policy", ".scout-state/privacy-policy.json", None, {"local_only": False}),
    (
        "federation-org-declared-sharing-off",
        "knowledge-base/projects/widget/widget.md",
        _fm("name: Widget\nfederation_scope: org"),
        {
            "visibility": "normal",
            "federation_scope": "none",
            "federation_scope_declared": "org",
            "federation_source": "disabled",
            "ceiling": "org",
            "shared_file": True,
            "warnings": (),
        },
    ),
    (
        "federation-org-case-insensitive",
        "knowledge-base/projects/widget/widget.md",
        _fm("federation_scope: ' ORG '"),
        {"federation_scope_declared": "org", "shared_file": True},
    ),
    (
        "federation-explicit-none",
        "knowledge-base/projects/widget/widget.md",
        _fm("federation_scope: none"),
        {"federation_scope_declared": "none", "shared_file": False, "warnings": ()},
    ),
    (
        "federation-person-list",
        "knowledge-base/projects/widget/notes.md",
        _fm("federation_scope: [person:priya@example.com]"),
        {
            "federation_scope": "none",
            "federation_scope_declared": ("person:priya@example.com",),
            "shared_file": True,
            "warnings": (),
        },
    ),
    (
        "federation-person-lowercased-group-kept",
        "knowledge-base/projects/widget/notes.md",
        _fm("federation_scope: [Person:Priya@Example.COM, group:0aF9_x-1, person:priya@example.com]"),
        {"federation_scope_declared": ("person:priya@example.com", "group:0aF9_x-1"), "warnings": ()},
    ),
    (
        "federation-bogus-is-none-with-warning",
        "knowledge-base/projects/widget/notes.md",
        _fm("federation_scope: bogus"),
        {"federation_scope_declared": "none", "shared_file": False, "warnings": ("visibility-value",)},
    ),
    (
        "federation-bad-principal-is-none",
        "knowledge-base/projects/widget/notes.md",
        _fm("federation_scope: [person:priya@example.com, person:not-an-email]"),
        {"federation_scope_declared": "none", "warnings": ("visibility-value",)},
    ),
    (
        "federation-group-too-long-is-none",
        "knowledge-base/projects/widget/notes.md",
        _fm("federation_scope: [group:" + "a" * 65 + "]"),
        {"federation_scope_declared": "none", "warnings": ("visibility-value",)},
    ),
    (
        "federation-scalar-principal-is-none",
        "knowledge-base/projects/widget/notes.md",
        _fm("federation_scope: person:priya@example.com"),
        {"federation_scope_declared": "none", "warnings": ("visibility-value",)},
    ),
    (
        "federation-empty-list-is-none",
        "knowledge-base/projects/widget/notes.md",
        _fm("federation_scope: []"),
        {"federation_scope_declared": "none", "shared_file": False, "warnings": ()},
    ),
    (
        "federation-non-string-entry-is-none",
        "knowledge-base/projects/widget/notes.md",
        _fm("federation_scope: [42]"),
        {"federation_scope_declared": "none", "warnings": ("visibility-value",)},
    ),
    (
        "private-plus-org-contradiction",
        "knowledge-base/people/sam.md",
        _fm("visibility: private\nfederation_scope: org"),
        {
            "visibility": "private",
            "federation_scope": "none",
            "federation_scope_declared": "org",
            "shared_file": True,
            "warnings": ("privacy-contradiction",),
        },
    ),
    (
        "path-default-private-plus-org-contradiction",
        "knowledge-base/personal/z.md",
        _fm("federation_scope: org"),
        {"visibility": "private", "warnings": ("privacy-contradiction",)},
    ),
    (
        "bad-visibility-and-bad-scope-both-warn",
        "knowledge-base/topics/misc/draft.md",
        _fm("visibility: secret\nfederation_scope: everyone"),
        {"visibility": "private", "warnings": ("visibility-value",)},
    ),
    ("drafts-shared-file", "drafts/r.md", _fm("name: Reply"), {"shared_file": True, "visibility": "normal"}),
    (
        "no-inheritance-sibling",
        "knowledge-base/topics/billing/rollout.md",
        _fm("name: Rollout"),
        {"visibility": "normal", "visibility_source": "default"},
    ),
]


@pytest.mark.parametrize(("rel", "text", "expected"), [r[1:] for r in _ROWS], ids=[r[0] for r in _ROWS])
def test_classify_rules(policy: Policy, rel: str, text: str | None, expected: dict[str, object]) -> None:
    label = classify(rel, text, policy)
    assert label.path == rel
    assert label.federation_scope == "none"  # sharing is off in the engine default
    assert label.federation_source == "disabled"
    for field, want in expected.items():
        assert getattr(label, field) == want, field


def test_sibling_of_private_main_file_does_not_inherit(policy: Policy) -> None:
    main = classify("knowledge-base/topics/billing/billing.md", _fm("visibility: private"), policy)
    sibling = classify("knowledge-base/topics/billing/rollout.md", _fm("name: Rollout"), policy)
    assert main.visibility == "private"
    assert sibling.visibility == "normal"


def test_overlay_local_only_add(tmp_path: Path) -> None:
    pol = load_policy(_overlay(tmp_path, 'privacy:\n  local_only_add: ["trading/**"]\n'))
    assert classify("trading/p.md", _fm("name: Positions"), pol).local_only is True
    assert classify("knowledge-base/x.md", None, pol).local_only is False
    assert "trading/**" in pol.local_only
    assert ".venv/**" in pol.local_only_always
    assert pol.warnings == ()


def test_overlay_local_only_add_beats_sync_allow(tmp_path: Path) -> None:
    pol = load_policy(_overlay(tmp_path, 'privacy:\n  local_only_add: [".scout-state/id-map.json"]\n'))
    assert classify(".scout-state/id-map.json", None, pol).local_only is True


def test_overlay_visibility_defaults_go_first(tmp_path: Path) -> None:
    pol = load_policy(
        _overlay(
            tmp_path,
            "privacy:\n  visibility_defaults:\n"
            '    - {glob: "knowledge-base/topics/billing/**", visibility: private}\n'
            '    - {glob: "knowledge-base/personal/shared/**", visibility: normal}\n',
        )
    )
    assert pol.visibility_defaults[0] == ("knowledge-base/topics/billing/**", "private")
    assert pol.visibility_defaults[-2:] == privacy.load_policy(None).visibility_defaults
    rollout = classify("knowledge-base/topics/billing/rollout.md", _fm("name: Rollout"), pol)
    assert (rollout.visibility, rollout.visibility_source) == ("private", "path_default")
    shared = classify("knowledge-base/personal/shared/list.md", None, pol)
    assert shared.visibility == "normal"


def test_overlay_visibility_default_unknown_value_fails_closed(tmp_path: Path) -> None:
    pol = load_policy(
        _overlay(tmp_path, 'privacy:\n  visibility_defaults:\n    - {glob: "notes/**", visibility: secretish}\n')
    )
    assert pol.visibility_defaults[0] == ("notes/**", "private")
    assert "visibility-value" in pol.warnings


def test_overlay_bad_visibility_default_entries_are_skipped(tmp_path: Path) -> None:
    pol = load_policy(
        _overlay(tmp_path, "privacy:\n  visibility_defaults:\n    - notes/**\n    - {visibility: private}\n")
    )
    assert pol.visibility_defaults == load_policy(None).visibility_defaults
    assert "privacy-overlay-invalid" in pol.warnings


def test_overlay_shared_files_add(tmp_path: Path) -> None:
    pol = load_policy(_overlay(tmp_path, 'privacy:\n  shared_files_add: ["outbox/**"]\n'))
    assert pol.shared_files == ("drafts/**", "outbox/**")
    assert classify("outbox/m.md", None, pol).shared_file is True


_WIDGET_SHARE = (
    "privacy:\n  federation:\n    enabled: true\n  federation_defaults:\n"
    '    - {glob: "knowledge-base/projects/widget/**", scope: ["person:Priya@example.com"]}\n'
)


def test_overlay_stage2_keys_are_honoured(tmp_path: Path) -> None:
    pol = load_policy(_overlay(tmp_path, _WIDGET_SHARE))
    assert pol.warnings == ()
    assert pol.federation_enabled is True
    assert pol.federation_defaults == (
        privacy.ScopeDefault("knowledge-base/projects/widget/**", ("person:priya@example.com",)),
    )
    label = classify("knowledge-base/projects/widget/notes.md", _fm("name: Notes"), pol)
    assert (label.federation_scope, label.federation_source, label.ceiling) == (
        ("person:priya@example.com",),
        "default",
        "org",
    )
    assert label.shared_file is True


def test_sharing_is_off_without_the_switch(tmp_path: Path) -> None:
    pol = load_policy(_overlay(tmp_path, _WIDGET_SHARE.replace("enabled: true", "enabled: false")))
    label = classify("knowledge-base/projects/widget/notes.md", _fm("name: Notes"), pol)
    assert (label.federation_scope, label.federation_source) == ("none", "disabled")
    assert label.shared_file is False


@pytest.mark.parametrize(
    "body",
    [
        "privacy:\n  federation: true\n",
        "privacy:\n  federation:\n    enabled: yes-please\n",
        "privacy:\n  federation_defaults: {glob: x}\n",
        "privacy:\n  federation_ceilings: knowledge-base/**\n",
    ],
)
def test_overlay_bad_federation_shapes_share_nothing(tmp_path: Path, body: str) -> None:
    pol = load_policy(_overlay(tmp_path, body))
    assert "privacy-overlay-invalid" in pol.warnings
    assert pol.federation_enabled is False
    assert pol.federation_defaults == ()


def test_overlay_bad_default_scope_fails_closed_in_place(tmp_path: Path) -> None:
    """A bad scope is kept as `none` so a broader entry below it can't match instead."""
    pol = load_policy(
        _overlay(
            tmp_path,
            "privacy:\n  federation: {enabled: true}\n  federation_defaults:\n"
            '    - {glob: "knowledge-base/projects/widget/**", scope: [priya]}\n'
            '    - {glob: "knowledge-base/projects/**", scope: ["person:priya@example.com"]}\n',
        )
    )
    assert "privacy-overlay-invalid" in pol.warnings
    label = classify("knowledge-base/projects/widget/notes.md", _fm("name: Notes"), pol)
    assert label.federation_scope == "none"


def test_overlay_ceilings_narrow_but_never_widen(tmp_path: Path) -> None:
    pol = load_policy(
        _overlay(
            tmp_path,
            "privacy:\n  federation_ceilings:\n"
            '    - {glob: "knowledge-base/projects/widget/**", ceiling: people}\n'
            '    - {glob: "knowledge-base/people/**", ceiling: org}\n'
            '    - {glob: "knowledge-base/topics/**", ceiling: whatever}\n',
        )
    )
    assert privacy.folder_ceiling(pol, "knowledge-base/projects/widget/a.md", None) == "people"
    assert privacy.folder_ceiling(pol, "knowledge-base/projects/other/a.md", None) == "org"
    assert privacy.folder_ceiling(pol, "knowledge-base/people/sam.md", None) == "none"
    assert privacy.folder_ceiling(pol, "knowledge-base/topics/a.md", None) == "none"
    assert "privacy-overlay-invalid" in pol.warnings


@pytest.mark.parametrize(
    ("rel", "kind", "want"),
    [
        ("knowledge-base/sources/2026-10/m.md", "meeting", "people"),
        ("knowledge-base/sources/2026-10/t.md", "thread", "people"),
        ("knowledge-base/sources/2026-10/e.md", "email", "none"),
        ("knowledge-base/sources/2026-10/x.md", None, "none"),
        ("knowledge-base/people/sam.md", None, "none"),
        ("action-items/action-items-2026-10-31.md", None, "none"),
        ("knowledge-base/research-queue/q.md", None, "org"),
        ("knowledge-base/ontology/entities/e.md", None, "org"),
    ],
)
def test_engine_folder_ceilings(policy: Policy, rel: str, kind: str | None, want: str) -> None:
    assert privacy.folder_ceiling(policy, rel, kind) == want


@pytest.mark.parametrize(
    ("scope", "ceiling", "want"),
    [
        (("person:a@example.com", "group:g1"), "org", ("person:a@example.com", "group:g1")),
        (("person:a@example.com", "group:g1"), "people", ("person:a@example.com",)),
        (("group:g1",), "people", "none"),
        ("org", "people", "none"),
        ("org", "org", "org"),
        (("person:a@example.com",), "none", "none"),
    ],
)
def test_cap_scope(scope: privacy.FederationScope, ceiling: privacy.CeilingLevel, want: object) -> None:
    assert privacy.cap_scope(scope, ceiling) == want


def test_kind_is_trimmed_and_lowercased() -> None:
    assert privacy.note_facts(_fm("kind: ' Meeting '")).kind == "meeting"
    assert privacy.note_facts(_fm("kind: 3")).kind is None


def test_overlay_surfaces_add_and_move_to_shared(tmp_path: Path) -> None:
    pol = load_policy(
        _overlay(
            tmp_path,
            "privacy:\n  surfaces:\n"
            "    owner_only: [owner_dashboard]\n"
            "    shared: [team_wiki, telegram_private_chat]\n",
        )
    )
    assert surface_class(pol, "owner_dashboard") == "owner_only"
    assert surface_class(pol, "team_wiki") == "shared"
    assert surface_class(pol, "telegram_private_chat") == "shared"
    assert "telegram_private_chat" not in pol.owner_only_surfaces
    assert pol.warnings == ()


def test_overlay_cannot_move_default_shared_surface_to_owner_only(tmp_path: Path) -> None:
    pol = load_policy(_overlay(tmp_path, "privacy:\n  surfaces:\n    owner_only: [slack]\n"))
    assert surface_class(pol, "slack") == "shared"
    assert "slack" not in pol.owner_only_surfaces
    assert pol.warnings == ("surface-widen-ignored",)


def test_overlay_surface_in_both_classes_is_shared(tmp_path: Path) -> None:
    pol = load_policy(_overlay(tmp_path, "privacy:\n  surfaces:\n    owner_only: [wiki]\n    shared: [wiki]\n"))
    assert surface_class(pol, "wiki") == "shared"


@pytest.mark.parametrize(
    "body",
    [
        "privacy: just-a-string\n",
        "privacy:\n  local_only_add: trading/**\n",
        "privacy:\n  surfaces: [slack]\n",
        "privacy:\n  surfaces:\n    shared: slack\n",
        "privacy:\n  shared_files_add: {a: b}\n",
        "privacy:\n  visibility_defaults: notes/**\n",
    ],
)
def test_overlay_wrong_shapes_are_ignored_with_warning(tmp_path: Path, body: str) -> None:
    pol = load_policy(_overlay(tmp_path, body))
    assert "privacy-overlay-invalid" in pol.warnings
    assert dataclasses.replace(pol, warnings=()) == load_policy(None)


def test_missing_config_or_key_means_defaults(tmp_path: Path) -> None:
    assert load_policy(tmp_path) == load_policy(None)
    assert load_policy(_overlay(tmp_path, "user:\n  name: Alex\n")) == load_policy(None)
    assert load_policy(_overlay(tmp_path, "")) == load_policy(None)


@pytest.mark.parametrize(
    "body",
    [
        'privacy:\n  local_only_add: ["trading/**"\n',  # missing ]
        "- a\n- list\n",
        b"\xff\xfeprivacy: {}\n",
    ],
)
def test_unreadable_overlay_warns_and_uses_defaults(tmp_path: Path, body: str | bytes) -> None:
    config = tmp_path / "scout-config.yaml"
    if isinstance(body, bytes):
        config.write_bytes(body)
    else:
        config.write_text(body, encoding="utf-8")
    pol = load_policy(tmp_path)
    assert pol.warnings == ("privacy-overlay-unreadable",)
    assert dataclasses.replace(pol, warnings=()) == load_policy(None)
    assert classify("trading/p.md", None, pol).local_only is False


def test_engine_default_policy_matches_spec(policy: Policy) -> None:
    assert policy.local_only_always == (
        ".scout-logs/**",
        ".scout-cache/**",
        ".claude/**",
        ".venv/**",
        ".obsidian/**",
        ".pytest_cache/**",
        ".mcp.json",
        ".env*",
        ".scout-state/**",
    )
    assert policy.local_only == ()
    assert policy.sync_allow == (
        ".scout-state/schedule.yaml",
        ".scout-state/id-map.json",
        ".scout-state/privacy-policy.json",
    )
    assert policy.visibility_defaults == (
        ("knowledge-base/personal/**", "private"),
        (".scout-inbox/**", "private"),
    )
    assert policy.owner_only_surfaces == frozenset(
        {"vault", "app", "telegram_private_chat", "agnes_owner_conversation", "agnes_personal_memory", "vault_sync"}
    )
    assert policy.shared_surfaces == frozenset(
        {
            "slack",
            "connector_write",
            "connector_query",
            "web_query",
            "agnes_shared",
            "agnes_memory_promotion",
            "peer_agent",
            "public_repo",
            "telegram_group_chat",
        }
    )
    assert policy.shared_files == ("drafts/**",)
    assert policy.warnings == ()


def test_label_to_json_shape_matches_spec(policy: Policy) -> None:
    label = classify("knowledge-base/personal/task-renew-permit.md", _fm("type: task"), policy)
    assert label.to_json() == {
        "path": "knowledge-base/personal/task-renew-permit.md",
        "visibility": "private",
        "visibility_source": "path_default",
        "federation_scope": "none",
        "federation_scope_declared": "none",
        "federation_source": "disabled",
        "ceiling": "none",
        "local_only": False,
        "shared_file": False,
        "warnings": [],
    }
    listed = classify("n.md", _fm("federation_scope: [person:priya@example.com]"), policy).to_json()
    assert listed["federation_scope_declared"] == ["person:priya@example.com"]


def test_policy_to_json_round_trips_yaml_shape(policy: Policy) -> None:
    data = policy.to_json()
    assert data["local_only"]["always"][0] == ".scout-logs/**"
    assert data["local_only"]["default"] == []
    assert data["visibility_defaults"][0] == {"glob": "knowledge-base/personal/**", "visibility": "private"}
    assert data["surfaces"]["shared"] == sorted(policy.shared_surfaces)
    assert data["shared_files"] == ["drafts/**"]
    assert data["warnings"] == []


@pytest.mark.parametrize(
    ("reader", "expected"),
    [
        ("owner", True),
        ("autonomous", True),
        ("agent:person:priya@example.com", False),
        ("agent:org", False),
        ("indexer", False),
        ("", False),
    ],
)
def test_visible_to(policy: Policy, reader: str, expected: bool) -> None:
    for label in (
        classify("knowledge-base/people/sam.md", _fm("visibility: private"), policy),
        classify("knowledge-base/people/priya.md", _fm("visibility: normal"), policy),
    ):
        assert visible_to(label, reader) is expected


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("vault", "owner_only"),
        ("telegram_private_chat", "owner_only"),
        ("vault_sync", "owner_only"),
        ("slack", "shared"),
        ("connector_query", "shared"),
        ("telegram_group_chat", "shared"),
        ("something-new", "shared"),
    ],
)
def test_surface_class(policy: Policy, surface: str, expected: str) -> None:
    assert surface_class(policy, surface) == expected


def _case_insensitive(directory: Path) -> bool:
    probe = directory / "Case-Probe"
    probe.write_text("x", encoding="utf-8")
    try:
        return (directory / "case-probe").exists()
    finally:
        probe.unlink()


def test_normalize_rel_symlink_spellings(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "knowledge-base" / "personal").mkdir(parents=True)
    (vault / "knowledge_base").symlink_to("knowledge-base")
    note = vault / "knowledge-base" / "personal" / "task-renew-permit.md"
    note.write_text(_fm("type: task"), encoding="utf-8")
    want = "knowledge-base/personal/task-renew-permit.md"
    spellings: list[str | Path] = [
        want,
        "knowledge_base/personal/task-renew-permit.md",
        "./knowledge-base/personal/task-renew-permit.md",
        "knowledge-base/people/../personal/task-renew-permit.md",
        "./knowledge_base/./personal/../personal/task-renew-permit.md",
        note,
        vault / "knowledge_base" / "personal" / "task-renew-permit.md",
        Path("knowledge_base") / "personal" / "task-renew-permit.md",
    ]
    if _case_insensitive(vault):
        spellings += [
            "Knowledge-Base/Personal/task-renew-permit.md",
            "knowledge-base/PERSONAL/task-renew-permit.md",
            "KNOWLEDGE_BASE/personal/Task-Renew-Permit.MD",
            vault / "Knowledge-Base" / "personal" / "TASK-RENEW-PERMIT.md",
        ]
    pol = load_policy(None)
    labels: set[Label] = set()
    for spelling in spellings:
        rel = normalize_rel(vault, spelling)
        assert rel == want, spelling
        labels.add(classify(rel, note.read_text(), pol))
    assert len(labels) == 1


def test_normalize_rel_missing_components_stay_as_typed(tmp_path: Path) -> None:
    (tmp_path / "knowledge-base").mkdir()
    (tmp_path / "knowledge-base" / "a.md").write_text("x", encoding="utf-8")
    assert normalize_rel(tmp_path, "knowledge-base/New/Thing.md") == "knowledge-base/New/Thing.md"
    # A component under a regular file can't be listed: kept as typed.
    assert normalize_rel(tmp_path, "knowledge-base/a.md/Sub") == "knowledge-base/a.md/Sub"


def test_on_disk_case_ambiguous_match_stays_as_typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Entry:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Listing:
        def __enter__(self) -> list[_Entry]:
            return [_Entry("Notes"), _Entry("NOTES")]

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(privacy.os, "scandir", lambda _p: _Listing())
    assert privacy._on_disk_case(tmp_path, ("notes", "a.md")) == ("notes", "a.md")
    assert privacy._on_disk_case(tmp_path, ("NOTES", "a.md")) == ("NOTES", "a.md")


def test_normalize_rel_without_symlink_still_maps_spelling(tmp_path: Path) -> None:
    (tmp_path / "knowledge-base").mkdir()
    assert normalize_rel(tmp_path, "knowledge_base/personal/a.md") == "knowledge-base/personal/a.md"
    assert normalize_rel(tmp_path, "knowledge_base") == "knowledge-base"


def test_normalize_rel_through_symlinked_vault(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "knowledge-base").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert normalize_rel(link, real / "knowledge-base" / "a.md") == "knowledge-base/a.md"
    assert normalize_rel(real, link / "knowledge-base" / "a.md") == "knowledge-base/a.md"


@pytest.mark.parametrize("path", ["../outside.md", "knowledge-base/../../outside.md", "/etc/passwd", "", "."])
def test_normalize_rel_rejects_outside_or_root(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError):
        normalize_rel(tmp_path, path)


def test_normalize_rel_rejects_symlink_escaping_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "elsewhere.md").write_text("x", encoding="utf-8")
    (vault / "escape.md").symlink_to(tmp_path / "elsewhere.md")
    with pytest.raises(ValueError):
        normalize_rel(vault, "escape.md")


@pytest.mark.parametrize("raw", ["{a: b}", "42"])
def test_federation_scope_other_types_are_none(policy: Policy, raw: str) -> None:
    label = classify("n.md", _fm(f"federation_scope: {raw}"), policy)
    assert (label.federation_scope_declared, label.warnings) == ("none", ("visibility-value",))


def test_engine_yaml_guards() -> None:
    import yaml

    raw = yaml.safe_load((Path(privacy.__file__).parents[1] / "defaults" / "privacy.yaml").read_text())
    assert privacy._parse_engine_yaml(raw) == load_policy(None)
    with pytest.raises(ValueError, match="schema_version"):
        privacy._parse_engine_yaml({**raw, "schema_version": 2})
    with pytest.raises(ValueError, match="bad visibility"):
        privacy._parse_engine_yaml({**raw, "visibility_defaults": [{"glob": "x/**", "visibility": "secret"}]})


def test_label_is_frozen(policy: Policy) -> None:
    label: Label = classify("a.md", None, policy)
    with pytest.raises(dataclasses.FrozenInstanceError):
        label.visibility = "private"  # type: ignore[misc]


def test_classify_unreadable_is_private_forced(policy: Policy) -> None:
    label = classify("knowledge-base/people/sam.md", None, policy, unreadable=True)
    assert (label.visibility, label.visibility_source, label.warnings) == ("private", "forced", ("file-unreadable",))
    assert classify("knowledge-base/people/sam.md", None, policy).visibility == "normal"


# ----- reading files: read_head / label_file (one reader for every caller) -------


def test_read_head_non_markdown_and_missing_use_path_defaults(tmp_path: Path) -> None:
    (tmp_path / "scan.txt").write_text("---\nvisibility: private\n---\n", encoding="utf-8")
    assert privacy.read_head(tmp_path / "scan.txt") == (None, False)
    assert privacy.read_head(tmp_path / "gone.md") == (None, False)


def test_read_head_unreadable_markdown(tmp_path: Path) -> None:
    (tmp_path / "dir.md").mkdir()
    assert privacy.read_head(tmp_path / "dir.md") == (None, True)


def test_read_head_decodes_with_replacement_and_caps_the_window(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_bytes(b"---\nvisibility: private\ntitle: \xff\n---\n" + b"x" * (privacy.HEAD_BYTES * 2))
    text, unreadable = privacy.read_head(p)
    assert not unreadable and text is not None
    assert "�" in text
    assert text.endswith("x") and text.count("x") == privacy.HEAD_BYTES - len(
        "---\nvisibility: private\ntitle: \xff\n---\n"
    )
    assert privacy.decode_head(p.read_bytes()) == text


def test_label_file_matches_classify(tmp_path: Path, policy: Policy) -> None:
    note = tmp_path / "knowledge-base" / "personal" / "t.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\nname: t\ntype: task\n---\n", encoding="utf-8")
    rel = "knowledge-base/personal/t.md"
    assert privacy.label_file(tmp_path, rel, policy) == classify(rel, note.read_text(encoding="utf-8"), policy)
    assert privacy.label_file(tmp_path, "knowledge-base/personal/gone.md", policy).visibility_source == "path_default"
    (tmp_path / "x.md").mkdir()
    assert privacy.label_file(tmp_path, "x.md", policy).warnings == ("file-unreadable",)
