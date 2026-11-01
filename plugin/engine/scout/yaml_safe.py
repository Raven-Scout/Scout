"""``yaml.safe_load`` that only ever raises ``yaml.YAMLError``, for text a person can get wrong.

PyYAML's constructors let plain exceptions out on text it scanned without
complaint: ``ValueError`` (``date: 2026-13-45``, ``n: 0x_``), ``KeyError``
(``!!bool foo``), ``IndexError`` (``!!int ""``), ``AttributeError``
(``!!timestamp foo``), ``OverflowError`` (``"\\U99999999"``) and
``RecursionError`` (deep nesting). Each just means the text doesn't parse, so
the callers' ``except yaml.YAMLError`` handlers cover them all.

Standing alone so ``scout.config`` can use it: ``scout.kb.privacy`` imports
``scout.config`` through ``scout.kb.lint_config``.
"""

from __future__ import annotations

from typing import Any

import yaml


class YAMLValueError(yaml.YAMLError):
    """YAML that scans but holds a value PyYAML can't build. Names the cause, never the text."""


def load_yaml(stream: Any, *, loader: type[yaml.SafeLoader] = yaml.SafeLoader) -> Any:
    """``yaml.safe_load`` (or ``loader``, a stricter ``SafeLoader``) raising only ``yaml.YAMLError``."""
    try:
        return yaml.load(stream, Loader=loader)  # noqa: S506 - SafeLoader or a subclass
    except yaml.YAMLError:
        raise
    except Exception as e:  # the list above is what PyYAML does today; callers must not depend on it staying complete
        raise YAMLValueError(type(e).__name__) from e


def load_yaml_key(text: str, key: str) -> Any:
    """One top-level key's value, built alone, so a bad value elsewhere in ``text`` doesn't take it down.

    None when ``text`` is empty or has no such key. Raises ``yaml.YAMLError`` when
    ``text`` doesn't scan or isn't a mapping, when that value can't be built, and
    when a merge key (``<<:``) could be supplying it.
    """
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        if root is None:
            return None
        if not isinstance(root, yaml.MappingNode):
            raise YAMLValueError("not a mapping")
        found: yaml.Node | None = None
        for key_node, value_node in root.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise YAMLValueError("merge key")
            if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
                found = value_node  # the last one wins, as in safe_load
        return None if found is None else yaml.SafeLoader("").construct_document(found)
    except yaml.YAMLError:
        raise
    except Exception as e:  # as in load_yaml
        raise YAMLValueError(type(e).__name__) from e


def error_line(text: str) -> int | None:
    """The 1-based line of what keeps ``text`` from loading, or None when that can't be told.

    For a value that can't be built, the line its top-level key's value starts on.
    """
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.MarkedYAMLError as e:
        return e.problem_mark.line + 1 if e.problem_mark else None
    except Exception:  # noqa: BLE001 - a line number is a nicety; the caller has the error itself
        return None
    if not isinstance(root, yaml.MappingNode):
        return None
    for _key, value_node in root.value:
        try:
            yaml.SafeLoader("").construct_document(value_node)
        except Exception:  # noqa: BLE001 - this one is the value that can't be built
            return int(value_node.start_mark.line) + 1
    return None


def yaml_problem(error: yaml.YAMLError, text: str) -> str:
    """``error`` for a message: its cause and line, never the text, which YAML errors can quote."""
    name = str(error) if isinstance(error, YAMLValueError) else type(error).__name__
    line = error_line(text)
    return f"{name} at line {line}" if line else name
