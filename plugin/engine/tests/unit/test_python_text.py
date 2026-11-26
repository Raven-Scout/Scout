"""scout.kb.python_text: whether a literal in a Python program is only ever text. Each program names the KB once;
the literal judged is the one holding the first ``knowledge-base``."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.kb import python_text, trusted_scripts
from scout.kb.python_text import text_checker
from scout.kb.trusted_scripts import trusted_script

L = '"knowledge-base/a.md"'

TEXT = [
    # where a value ends
    f"{L}",  # thrown away (a docstring, a bare string)
    f"x = 'a' in {L}",
    f"x = not {L}",
    f"s = 'abc'\nprint(s[:len({L})])",
    f"if {L}:\n    pass",
    f"while {L}:\n    break",
    f"assert True, {L}",
    f"print({L} or 'x')",
    f"print({L} if True else 'x')",
    f"print(f'{{{L}}}')",
    f"print(*[{L}])",
    f"print(({L}, 'x'))",
    f"print({{{L}}})",
    f"print({{'k': {L}}})",
    f"print({{{L}: 1}})",
    f"print([x for x in [{L}]])",
    f"print([1 for x in 'ab' if x == {L}])",
    f"print({{x: 1 for x in [{L}]}})",
    f"d = {{'a': 1}}\nprint(d.get({L}))",
    f"d = {{}}\nd[{L}] = 1\nprint(d)",
    f"print({L}.startswith('x'))",
    f"print({L}.upper())",
    f"xs = ['a']\nxs.append({L})\nprint(xs)",
    f"xs = ['a']\nxs[0] = {L}\nprint(xs)",
    f"import re\nprint(re.search({L}, 'x'))",
    f"import re\nprint(re.sub('x', {L}, 'y'))",
    f"open('docs/a.md', 'w').write({L})",
    f"with open('docs/a.md', 'w') as f:\n    f.write({L})",
    f"f = open('docs/a.md', 'w')\nf.writelines([{L}])",
    f"import sys\nsys.stdout.write({L})",
    f"from pathlib import Path\nPath('docs/a.md').write_text({L})",
    f"from pathlib import Path\n(Path('docs') / 'a.md').write_text({L})",
    f"from pathlib import Path\np = Path('docs/a.md')\np.open('w').write({L})",
    f"s = open('docs/a.md').read()\nprint(s.replace('x', {L}))",
    f"print('-'.join([{L}]))",
    f"print(str.replace('a', 'b', {L}))",
    f"print(len({L}), {L})",
    f"print(sorted([{L}], reverse=True))",
    f"print('x', sep={L})",
    f"x: str = {L}\nprint(x)",
    f"x = 'a'\nx += {L}\nprint(x)",
    f"a, b = {L}, 'x'\nprint(a, b)",
    f"for x in [{L}]:\n    print(x)",
    f"s = b'x' + b'y'\nprint(s.find({L}))",
    f"s = 'a' if True else 'b'\nprint(s.find({L}))",
    f"s = ('a', 'b')\nprint(s.index({L}))",
    f"s = {{'a': 'b'}}\nprint(s.get({L}))",
    f"s = [x for x in 'ab']\nprint(s.count({L}))",
    f"s = ['a'][0]\nprint(s.find({L}))",
    f"s = sorted(['a'])\nprint(s.index({L}))",
    f"for i, s in enumerate(['a']):\n    print(s.find({L}))",
    f"s = 'a'\ns = s\nprint(s.find({L}))",
    f"print([{L}][0])",
    f"s = f'a'\nprint(s.find({L}))",
    f"d = {{'a': 1}}\nprint(d.get({L}))",
]
NOT_TEXT = [
    f"print(open({L}).read())",
    f"x = {L}\ndef f():\n    return x\n",
    f"f = lambda: {L}",
    f"import shutil\nshutil.copy({L}, 'x')",
    f"from shutil import copy\ncopy({L}, 'x')",
    f"class C:\n    pass\nC({L})",
    f"x = {L}\nprint(open(x).read())",
    f"print(sorted([{L}], key=len))",
    f"import os as o\no.replace({L}, 'x')",
    f"z = object()\nz.write({L})",
    f"z = object()\nz.write_text({L})",
    f"t = object()\nt.add({L})",
    f"with open('a') as f:\n    s = f\nprint(s.find({L}))",
    f"s = object()\nprint(s.find({L}))",
    f"print(q.find({L}))",
    f"import re\nprint(x.sub({L}, 'y'))",
    f"o = object()\no.p = {L}",
    f"[*a] = [{L}]",
    f"f = open('a') if True else None\nf.write({L})",
    f"f, g = open('a'), open('b')\nf.write({L})",
    f"print(open(f'{{{L}}}').read())",
    f"x = {L}.replace('a', 'b')\nprint(open(x).read())",
    f"xs = list(map(str, 'a'))\nxs.append({L})",
    f"print({L}.encode().hex())",
    f"print(open(next(iter([{L}]))).read())",
    f"print({L}.__class__)",
    f"[1].append({L})",
    f"from m import y\ny = {L}",
]


@pytest.mark.parametrize("program", TEXT)
def test_a_literal_only_used_as_text_is_text(program: str) -> None:
    checker = text_checker(program)
    assert checker is not None, program
    assert checker.is_text(program.index("knowledge-base")), program


@pytest.mark.parametrize("program", NOT_TEXT)
def test_a_literal_that_may_be_opened_is_not_text(program: str) -> None:
    checker = text_checker(program)
    assert checker is not None, program
    assert not checker.is_text(program.index("knowledge-base")), program


def test_an_f_strings_text_part_is_the_f_string() -> None:
    program = "print(f'see knowledge-base/{x}')"
    checker = text_checker(program)
    assert checker is not None
    node = checker.literal_at(program.index("knowledge-base"))
    assert node is not None and type(node).__name__ == "JoinedStr"
    assert checker._offset(99, 0) == -1  # a line the text doesn't have


def test_a_program_that_doesnt_parse_has_no_checker() -> None:
    assert text_checker("print(") is None
    assert text_checker("x = 1") is not None


def test_a_chain_too_deep_to_follow_is_not_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(python_text, "_MAX_DEPTH", 1)
    program = f"print((({L} + 'a') + 'b') + 'c')"
    checker = text_checker(program)
    assert checker is not None and not checker.is_text(program.index("knowledge-base"))
    program = "s = 'a' + ('b' + ('c' + 'd'))\nprint(s.find('knowledge-base'))"
    checker = text_checker(program)
    assert checker is not None and not checker.is_text(program.index("knowledge-base"))


# ----- trusted_scripts' edges ---------------------------------------------------------------------------------


def test_an_unreadable_or_huge_vault_copy_is_not_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout

    vault = tmp_path / "Vault"
    rel = "scripts/kb-shard-log.py"
    copy = vault / rel
    copy.parent.mkdir(parents=True)
    copy.write_bytes((Path(scout.__file__).resolve().parents[2] / "templates" / rel).read_bytes())
    assert trusted_script(copy, vault, {})
    monkeypatch.setattr(trusted_scripts, "_MAX_BYTES", 1)
    assert not trusted_script(copy, vault, {})  # too big to compare: not Scout's own
    monkeypatch.undo()

    def boom(self: Path) -> bytes:
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_bytes", boom)
    assert not trusted_script(copy, vault, {})


def test_a_path_that_wont_resolve_is_not_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "elsewhere.py"
    outside.write_text("print(1)\n")
    vault = tmp_path / "Vault"
    vault.mkdir()
    assert not trusted_script(outside, vault, {})  # a file, but neither the plugin's nor in the vault
    real_resolve = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        if self.name in ("loop.py", "Vault"):
            raise RuntimeError("symlink loop")
        return real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert not trusted_script(tmp_path / "loop.py", vault, {})
    assert not trusted_script(outside, vault, {})
