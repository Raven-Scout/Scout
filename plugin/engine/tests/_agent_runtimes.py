"""A dummy agent runtime for tests: registered under a test id, never shipped."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from scout.agent_runtimes import registry
from scout.agent_runtimes.base import (
    Capability,
    Failure,
    FailureKind,
    Launch,
    ProcessMarkers,
    RunRequest,
    RuntimeAuth,
)
from scout.errors import ConfigError


class DummyRuntime:
    """A runtime with a settable capability set, provider and model rule (ids look like ``dm-…``)."""

    display_name = "Dummy CLI"

    def __init__(
        self,
        runtime_id: str = "dummy",
        *,
        provider: str = "openai",
        caps: frozenset[Capability] = frozenset(),
        binary: Path | None = None,
        version: str | None = "1.0.0",
        caps_own_spend: bool = False,
    ) -> None:
        self.id = runtime_id
        self.caps_own_spend = caps_own_spend
        self.provider = provider
        self.caps = caps
        self.binary = binary
        self._version = version

    def locate(self, hint: Path | None) -> Path | None:
        return hint or self.binary

    def version(self, binary: Path) -> str | None:
        return self._version

    def auth_status(self, binary: Path) -> RuntimeAuth:
        return RuntimeAuth(signed_in=True, mode="login", detail="dummy")

    def capabilities(self, vault: Path, binary: Path, version: str | None) -> frozenset[Capability]:
        return self.caps

    def validate_model(self, value: object, *, where: str) -> str:
        if isinstance(value, str) and value.strip().lower().startswith("dm-"):
            return value.strip().lower()
        raise ConfigError(f"{where}: model {value!r} is not a dm-… model ID")

    def default_model(self) -> str:
        return "dm-default"

    def known_models(self) -> tuple[str, ...]:
        return ("dm-default",)

    def headless_argv(self, req: RunRequest) -> Launch:
        binary = req.binary or self.binary or Path("dummy-cli")
        argv = [str(binary), "run", "--model", req.model, "--title", req.run_name]
        if req.budget_cap is not None:
            argv += ["--usd", req.budget_cap]
        return Launch(argv=(*argv, req.prompt), env={"DUMMY_MARK": "1"})

    def classify_failure(self, log_tail: str, exit_code: int) -> Failure:
        return Failure(FailureKind.OTHER, None, f"exit {exit_code}")

    def process_markers(self, req: RunRequest | None = None) -> ProcessMarkers:
        return ProcessMarkers(argv_words=("run", "--title"), env={"DUMMY_MARK": "1"})


@contextmanager
def registered(adapter: DummyRuntime) -> Iterator[DummyRuntime]:
    """Register *adapter* for the duration of the block."""
    registry.register(adapter)
    try:
        yield adapter
    finally:
        registry.unregister(adapter.id)
