"""Whether a string literal in a Python program is only ever text: the KB read gate's judge of a KB name in a
literal that no read call takes (:mod:`scout.kb.program_reads`).

An edit program often names the KB in the text it writes: ``s.replace("[[old]]", "[[knowledge-base/x]]")``, a line
it inserts, a block it appends. Such a literal is no read. But a literal can reach a call that opens it in ways
the gate's scan doesn't model (``from subprocess import run``, ``tarfile``, ``zipfile``, ``import os as o;
o.replace(…)``), so "no judged call takes it" proves nothing. This module proves the other thing instead, on
Python's own syntax tree: every way the literal's value flows ends in a use that treats it as text. Anything it
can't show is not text (fail closed), and so is any program that doesn't parse.

Text uses: an argument to a method of something that is itself text (``s.replace(…)``, ``s.startswith(…)``,
``"\\n".join(…)``, never a module's or another object's method of the same name), a comparison, ``print``/``len``,
``re``'s functions; the content a file ``open()`` gave writes (``.write``, ``.writelines``; an archive's
``write`` adds the file it's named) or ``.write_text`` writes; an item of a list, set or dict of text that is only
used as text; a ``+``, ``%``, f-string, ``.join`` or ``.format`` whose result is only used as text; a variable, loop
variable or subscript whose every use is one of those; a value nothing uses. Not text: an argument to any other
call (``sorted(xs, key=open)`` included), a value a function returns or a lambda holds, an attribute it's stored
in, and anything else.

Something is text when it is a string, an f-string or a concatenation with one, what a text method gives
(``.read()``, ``.read_text()``, ``.split()``, ``.replace()``…), a list, set or dict of text, an item of text, or a
variable only ever set to text (or a loop's item over text). Never a name an ``import`` binds.
"""

from __future__ import annotations

import ast

# Methods whose result is a value made from what they're called on and given: it must be used as text.
_TEXT_RESULTS = frozenset(
    """replace split rsplit partition rpartition strip lstrip rstrip removeprefix removesuffix splitlines join
    format format_map encode decode lower upper casefold title capitalize swapcase center ljust rjust zfill
    expandtabs translate get items keys values pop copy""".split()
)
# Methods whose result is a bool, a number, a position or nothing: the end of the line for a value.
_TEXT_TESTS = frozenset(
    """startswith endswith find rfind index rindex count isdigit isalpha isspace isupper islower sort
    reverse clear""".split()
)
# Methods that put a value into the list, set or dict they're called on: the container's uses must be text.
_PUTS = frozenset({"append", "insert", "extend", "add", "update", "setdefault", "remove", "discard"})
# Calls whose arguments are only shown, measured or tested.
_SHOWN = frozenset({"print", "len", "repr", "bool", "hash", "isinstance", "any", "all", "int", "abs"})
# Calls that pass a value's text on: what they give back must be used as text. Never `map` or `filter`, and none
# with a function to call on it (`sorted(xs, key=open)` opens each one): only the keywords in _PASS_KEYWORDS.
_PASSES = frozenset({"sorted", "list", "tuple", "set", "reversed", "enumerate", "zip", "str", "dict", "min", "max",
                     "next", "sum", "iter"})  # fmt: skip
_PASS_KEYWORDS = frozenset({"reverse", "strict", "start", "default"})
_RE_TESTS = frozenset({"search", "match", "fullmatch", "escape", "findall", "finditer"})
_RE_RESULTS = frozenset({"sub", "subn", "split", "compile"})
# Methods that give text whatever they're called on: a file's or a path's content, a text's pieces.
_GIVES_TEXT = frozenset(
    """read read_text readlines readline splitlines split rsplit replace join strip lstrip rstrip format lower upper
    casefold title decode encode removeprefix removesuffix partition rpartition""".split()
)
_MAX_DEPTH = 32


class PythonText:
    """One program's syntax tree, asked about the literals at given text offsets."""

    def __init__(self, text: str) -> None:
        self.tree = ast.parse(text)  # SyntaxError: the caller treats nothing as text
        self.parent: dict[ast.AST, ast.AST] = {}
        self.loads: dict[str, list[ast.Name]] = {}
        self.stores: dict[str, list[ast.Name]] = {}
        self.imported: set[str] = set()
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
            if isinstance(node, ast.Name):
                (self.loads if isinstance(node.ctx, ast.Load) else self.stores).setdefault(node.id, []).append(node)
            elif isinstance(node, ast.Import | ast.ImportFrom):
                self.imported |= {(a.asname or a.name).split(".")[0] for a in node.names}
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                self.imported.add(node.name)  # a name the program defines: not text either
        self._lines = text.splitlines(keepends=True)
        self._starts: list[int] = []
        at = 0
        for line in self._lines:
            self._starts.append(at)
            at += len(line)

    def _offset(self, line: int, col: int) -> int:
        """A node's (line, UTF-8 column) as a character offset in the text."""
        if not 0 < line <= len(self._lines):
            return -1
        return self._starts[line - 1] + len(self._lines[line - 1].encode("utf-8")[:col].decode("utf-8", "ignore"))

    def literal_at(self, at: int) -> ast.expr | None:
        """The innermost string literal or f-string whose text holds offset ``at``."""
        best: ast.expr | None = None
        best_start = -1
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Constant | ast.JoinedStr) or node.end_lineno is None:
                continue
            if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                continue
            a = self._offset(node.lineno, node.col_offset)
            b = self._offset(node.end_lineno, node.end_col_offset or 0)
            if a <= at < b and a >= best_start:
                best, best_start = node, a
        while best is not None and isinstance(self.parent.get(best), ast.JoinedStr):
            joined = self.parent[best]
            assert isinstance(joined, ast.JoinedStr)
            best = joined  # an f-string's parts: the f-string is the value
        return best

    def is_text(self, at: int) -> bool:
        """Whether the literal at offset ``at`` is only ever used as text (see the module's docstring)."""
        node = self.literal_at(at)
        return node is not None and self._text(node, set(), 0)

    # -- one value's uses --------------------------------------------------------------------------------------

    def _text(self, node: ast.AST, seen: set[str], depth: int) -> bool:  # noqa: C901, PLR0911, PLR0912 - one case each
        """Whether the value of ``node`` is only used as text, from where it stands."""
        p = self.parent.get(node)
        if depth > _MAX_DEPTH or p is None:
            return False
        if isinstance(p, ast.keyword):
            call = self.parent.get(p)
            return isinstance(call, ast.Call) and self._call_takes_text(call, p, seen, depth)
        if isinstance(p, ast.Expr | ast.Compare | ast.UnaryOp | ast.Slice):
            return True  # thrown away, compared, negated, a slice's bound
        if isinstance(p, ast.If | ast.While | ast.Assert):
            return node is p.test or (isinstance(p, ast.Assert) and node is p.msg)
        if isinstance(p, ast.BinOp | ast.BoolOp | ast.IfExp | ast.JoinedStr | ast.FormattedValue | ast.Starred):
            return self._text(p, seen, depth + 1)  # a value made from it, or it itself (`a or b`)
        if isinstance(p, ast.List | ast.Tuple | ast.Set | ast.Dict):
            return self._text(p, seen, depth + 1)  # an item of a container: the container's uses
        if isinstance(p, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            return self._text(p, seen, depth + 1)
        if isinstance(p, ast.comprehension):
            return self._target_text(p.target, seen, depth) if node is p.iter else node in p.ifs
        if isinstance(p, ast.Subscript):
            if node is not p.value or not isinstance(p.ctx, ast.Load):
                return True  # a key or an index it looks up; a container being written into
            return self._text(p, seen, depth + 1)
        if isinstance(p, ast.Attribute):  # a method called on it: it's text, so a text method
            call = self.parent.get(p)
            if not (isinstance(call, ast.Call) and call.func is p):
                return False
            if p.attr in _TEXT_RESULTS:
                return self._text(call, seen, depth + 1)
            return p.attr in _TEXT_TESTS or (p.attr in _PUTS and self._puts_into(node, seen, depth))
        if isinstance(p, ast.Call):
            return node is not p.func and self._call_takes_text(p, node, seen, depth)
        if isinstance(p, ast.Assign | ast.AnnAssign | ast.AugAssign):
            if p.value is not node:
                return False
            targets = p.targets if isinstance(p, ast.Assign) else [p.target]
            return all(self._target_text(t, seen, depth) for t in targets)
        if isinstance(p, ast.For):
            return node is p.iter and self._target_text(p.target, seen, depth)
        return False

    def _call_takes_text(self, call: ast.Call, arg: ast.AST, seen: set[str], depth: int) -> bool:  # noqa: PLR0911
        """Whether ``call`` takes ``arg`` (an argument, or a keyword) as text."""
        func = call.func
        if isinstance(func, ast.Attribute):
            name, recv = func.attr, func.value
            if isinstance(recv, ast.Name) and recv.id == "re" and "re" in self.imported:
                return name in _RE_TESTS or (name in _RE_RESULTS and self._text(call, seen, depth + 1))
            if name in ("write", "writelines"):  # a file's content, not what an archive's write adds
                return bool(call.args) and call.args[0] is arg and self._file(recv)
            if name in ("write_text", "write_bytes"):  # Path(p).write_text(text): the receiver is where
                return bool(call.args) and call.args[0] is arg and self._path(recv)
            if not self._texty(recv, set(), 0):
                return False  # `o.replace(…)` of a module, an object's `copy`: not a text method
            if name in _TEXT_RESULTS:
                return self._text(call, seen, depth + 1)
            return name in _TEXT_TESTS or (name in _PUTS and self._puts_into(recv, seen, depth))
        if isinstance(func, ast.Name) and func.id not in self.imported:
            if func.id in _SHOWN:
                return True
            if func.id in _PASSES and all(k.arg in _PASS_KEYWORDS for k in call.keywords):
                return self._text(call, seen, depth + 1)
        return False

    def _file(self, expr: ast.AST) -> bool:
        """Whether ``expr`` is a file ``open()`` gave (``open(p, "w")``, ``with open(p) as f``), or stdout."""
        if isinstance(expr, ast.Call):
            func = expr.func
            if isinstance(func, ast.Name):
                return func.id == "open" and "open" not in self.imported
            return isinstance(func, ast.Attribute) and func.attr == "open" and self._path(func.value)
        if isinstance(expr, ast.Attribute):
            return isinstance(expr.value, ast.Name) and expr.value.id == "sys" and expr.attr in ("stdout", "stderr")
        return self._bound(expr, self._file)

    def _path(self, expr: ast.AST) -> bool:
        """Whether ``expr`` is a pathlib path (``Path(…)``, ``Path(…) / "x"``, a variable set to one)."""
        if isinstance(expr, ast.Call):
            func = expr.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            return name in ("Path", "PurePath", "with_suffix", "with_name", "with_stem", "joinpath")
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            return self._path(expr.left)
        return self._bound(expr, self._path)

    def _bound(self, expr: ast.AST, test: object) -> bool:
        """A variable every value of which passes ``test`` (set by ``=`` or ``with … as``)."""
        if not isinstance(expr, ast.Name) or expr.id in self.imported or not self.stores.get(expr.id):
            return False
        for store in self.stores[expr.id]:
            p = self.parent.get(store)
            if isinstance(p, ast.withitem):
                value: ast.AST = p.context_expr
            elif isinstance(p, ast.Assign) and len(p.targets) == 1 and p.targets[0] is store:
                value = p.value
            else:
                return False
            if not test(value):  # type: ignore[operator]
                return False
        return True

    def _texty(self, expr: ast.AST, names: set[str], depth: int) -> bool:  # noqa: C901, PLR0911
        """Whether ``expr`` is text, or a list, set or dict of text (see the module's docstring)."""
        if depth > _MAX_DEPTH:
            return False
        if isinstance(expr, ast.Constant):
            return isinstance(expr.value, str | bytes)
        if isinstance(expr, ast.JoinedStr):
            return True
        if isinstance(expr, ast.BinOp):
            return self._texty(expr.left, names, depth + 1) or self._texty(expr.right, names, depth + 1)
        if isinstance(expr, ast.IfExp):
            return self._texty(expr.body, names, depth + 1) and self._texty(expr.orelse, names, depth + 1)
        if isinstance(expr, ast.List | ast.Tuple | ast.Set):
            return all(self._texty(e, names, depth + 1) for e in expr.elts)
        if isinstance(expr, ast.Dict):
            return all(self._texty(e, names, depth + 1) for e in [*expr.values, *(k for k in expr.keys if k)])
        if isinstance(expr, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self._texty(expr.elt, names, depth + 1)
        if isinstance(expr, ast.Subscript):
            return self._texty(expr.value, names, depth + 1)
        if isinstance(expr, ast.Call):
            func = expr.func
            if isinstance(func, ast.Attribute):
                return func.attr in _GIVES_TEXT
            if isinstance(func, ast.Name) and func.id not in self.imported and func.id in _PASSES:
                return all(self._texty(a, names, depth + 1) for a in expr.args) and not expr.keywords
            return False
        if isinstance(expr, ast.Name):
            if expr.id == "str" and "str" not in self.stores:
                return True  # `str.replace(…)`
            if expr.id in self.imported or not self.stores.get(expr.id):
                return False
            if expr.id in names:
                return True
            names.add(expr.id)
            for store in self.stores[expr.id]:
                p = self.parent.get(store)
                while isinstance(p, ast.Tuple | ast.List | ast.Starred):
                    p = self.parent.get(p)  # `a, b = …`, `for i, x in …`: an item of what's unpacked
                if isinstance(p, ast.For | ast.comprehension):
                    value: ast.AST | None = p.iter
                elif isinstance(p, ast.Assign | ast.AnnAssign | ast.AugAssign):
                    value = p.value
                else:
                    value = None  # `with … as x`, `except … as x`, a deleted name: not text
                if value is None or not self._texty(value, names, depth + 1):
                    return False
            return True
        return False

    def _puts_into(self, container: ast.AST, seen: set[str], depth: int) -> bool:
        """A value put into ``container`` (``lines.insert(i, s)``, ``x[0] = s``): text when the container is a
        variable of text whose every use is text."""
        if not isinstance(container, ast.Name) or not self._texty(container, set(), 0):
            return False
        return self._var_text(container.id, seen, depth)

    def _target_text(self, target: ast.AST, seen: set[str], depth: int) -> bool:
        if isinstance(target, ast.Name):
            return self._var_text(target.id, seen, depth)
        if isinstance(target, ast.Tuple | ast.List):
            return all(self._target_text(t, seen, depth) for t in target.elts)
        if isinstance(target, ast.Subscript):
            return self._puts_into(target.value, seen, depth)
        return False  # an attribute, a starred name: somewhere the gate can't follow

    def _var_text(self, name: str, seen: set[str], depth: int) -> bool:
        """Whether every use of variable ``name`` is text."""
        if name in seen:
            return True
        if name in self.imported:
            return False
        seen.add(name)
        return all(self._text(use, seen, depth + 1) for use in self.loads.get(name, []))


def text_checker(text: str) -> PythonText | None:
    """A :class:`PythonText` for ``text``, or None when it isn't a Python program that parses."""
    try:
        return PythonText(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
