"""Turn a vault's edit to a managed file into a patch against the plugin's template.

``scoutctl bootstrap drift --patch`` uses this so a fix that lives only in one
vault can become a plugin PR instead of being carried by hand forever. It is
the template counterpart of ``phase_backport`` (which does the same for the
assembled brain files) and reuses its conservative re-templatizing.

Rendering substitutes single-line values, so line *i* of a template is line *i*
of its render. The vault file is diffed against the render; unchanged lines map
back to the raw template lines, and added lines have their safe template
variables reversed (a vault path back to ``{{SCOUT_DIR}}``). The patched
template therefore renders back to exactly the vault's file.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from scout.scripts.phase_assembly import render_template
from scout.scripts.phase_backport import retemplatize

# Template variables whose values say nothing about the user or the machine.
_IMPERSONAL_VARS = frozenset({"PLATFORM", "AUTO_UPDATE_ENABLED", "TODAY_DATE"})


def _instance_values(lines: list[str], vars_: dict[str, str]) -> list[str]:
    """What in ``lines`` belongs to this vault: variable names whose value
    appears, and the home directory. Matching is on values, so after
    ``retemplatize`` only what it chose not to reverse is left to find."""
    text = "\n".join(lines)
    found = [k for k, v in vars_.items() if v and k not in _IMPERSONAL_VARS and v in text]
    if str(Path.home()) in text:
        found.append("home directory")
    return found


@dataclass(frozen=True)
class FilePatch:
    vault_rel: str
    plugin_rel: str
    patch: str  # unified diff against plugin_rel; "" when there is nothing to back-port
    template_after: str | None  # the patched template, or None when it can't be mapped back
    warnings: list[str] = field(default_factory=list)


def _unified(before: str, after: str, path: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{path}", f"b/{path}"
    )
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def backport_patch(
    *, vault_rel: str, plugin_rel: str, template: str, rendered: bool, live: str, vars_: dict[str, str]
) -> FilePatch:
    """The patch that makes ``template`` (at ``plugin_rel``) produce ``live``.

    ``rendered`` says the file is a template (variables substituted); a verbatim
    file is diffed as is. Warnings name template-variable values left in the
    added lines: the plugin repo is public, so they must be made generic first.
    """
    render = render_template(template, vars_) if rendered else template
    t_lines, r_lines, l_lines = template.split("\n"), render.split("\n"), live.split("\n")
    if len(t_lines) != len(r_lines):
        return FilePatch(
            vault_rel,
            plugin_rel,
            "",
            None,
            [f"{vault_rel}: a template variable renders to several lines, so the edit can't be mapped back"],
        )

    out: list[str] = []
    flagged: list[str] = []
    matcher = difflib.SequenceMatcher(a=r_lines, b=l_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out += t_lines[i1:i2]
        elif tag in ("insert", "replace"):
            added = l_lines[j1:j2]
            if rendered:
                added, _ = retemplatize(added, vars_)
            flagged += [k for k in _instance_values(added, vars_) if k not in flagged]
            out += added
    after = "\n".join(out)
    warnings = [
        f"{vault_rel}: added lines contain this vault's "
        + (f"value of {{{{{name}}}}} ({vars_[name]!r})" if name in vars_ else name)
        + " — make them generic before opening a PR; the plugin repo is public"
        for name in flagged
    ]
    patch = _unified(template, after, plugin_rel) if after != template else ""
    return FilePatch(vault_rel, plugin_rel, patch, after, warnings)
