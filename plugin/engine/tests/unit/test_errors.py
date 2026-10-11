"""Unit tests for scout.errors."""

from __future__ import annotations

from scout.errors import (
    ActionItemAmbiguous,
    ActionItemError,
    ActionItemNotFound,
    ConfigError,
    ContractViolation,
    DataDirError,
    ExternalProcessError,
    KBError,
    SchemaVersionMismatch,
    ScoutError,
)


def test_exit_codes_are_stable() -> None:
    assert ScoutError.exit_code == 1
    assert ConfigError.exit_code == 10
    assert DataDirError.exit_code == 11
    assert SchemaVersionMismatch.exit_code == 12
    assert KBError.exit_code == 20
    assert ActionItemError.exit_code == 21
    assert ActionItemNotFound.exit_code == 22
    assert ActionItemAmbiguous.exit_code == 23
    assert ExternalProcessError.exit_code == 30
    assert ContractViolation.exit_code == 40


def test_schema_version_mismatch_message_names_versions() -> None:
    err = SchemaVersionMismatch(have=1, want=2)
    assert "v1" in str(err)
    assert "v2" in str(err)
    assert "scoutctl migrate" in str(err)


def test_all_subclasses_inherit_from_scout_error() -> None:
    for cls in (
        ConfigError,
        DataDirError,
        SchemaVersionMismatch,
        KBError,
        ActionItemError,
        ExternalProcessError,
        ContractViolation,
    ):
        assert issubclass(cls, ScoutError)


def test_selector_failures_are_action_item_errors() -> None:
    """Callers that catch ActionItemError keep catching both; only the
    process exit code tells the app which one happened."""
    assert issubclass(ActionItemNotFound, ActionItemError)
    assert issubclass(ActionItemAmbiguous, ActionItemError)
