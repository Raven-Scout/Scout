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
        "federation-org-reserved",
        "knowledge-base/projects/widget/widget.md",
        _fm("name: Widget\nfederation_scope: org"),
        {
            "visibility": "normal",
            "federation_scope": "none",
            "federation_scope_declared": "org",
            "federation_source": "reserved",
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
    assert label.federation_scope == "none"
    assert label.federation_source == "reserved"
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


@pytest.mark.parametrize("key", ["federation_ceilings", "federation_defaults", "federation"])
def test_overlay_stage2_keys_warn_and_are_ignored(tmp_path: Path, key: str) -> None:
    pol = load_policy(_overlay(tmp_path, f"privacy:\n  {key}:\n    enabled: true\n"))
    assert pol.warnings == ("federation-reserved",)
    label = classify("knowledge-base/projects/widget/widget.md", _fm("federation_scope: org"), pol)
    assert label.federation_scope == "none"
    assert dataclasses.replace(pol, warnings=()) == load_policy(None)


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
        "federation_source": "reserved",
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
    pol = load_policy(None)
    labels: set[Label] = set()
    for spelling in spellings:
        rel = normalize_rel(vault, spelling)
        assert rel == want, spelling
        labels.add(classify(rel, note.read_text(), pol))
    assert len(labels) == 1


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
