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

Something is text when it is a string (or another constant), an f-string or a concatenation with one, what a text
method gives (``.read()``, ``.read_text()``, ``.split()``, ``.replace()``…), a list, set or dict of text, an item of
text, or a variable only ever set to text (or a loop's item over text). Never a name an ``import`` binds.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

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
_NUMBERS = frozenset({"len", "int", "float", "abs", "round", "bool"})  # builtins that give a plain number
_RE_TESTS = frozenset({"search", "match", "fullmatch", "escape", "findall", "finditer"})
_RE_RESULTS = frozenset({"sub", "subn", "split", "compile"})
# Methods that give text whatever they're called on: a file's or a path's content, a text's pieces.
_GIVES_TEXT = frozenset(
    """read read_text readlines readline splitlines split rsplit replace join strip lstrip rstrip format lower upper
    casefold title decode encode removeprefix removesuffix partition rpartition sub subn findall group""".split()
)
_MAX_DEPTH = 32
# Names that reach a function by name, or run code: a program that uses one has no text literals.
_BY_NAME = frozenset({"getattr", "setattr", "delattr", "globals", "locals", "vars", "eval", "exec", "compile",
                      "__import__", "__builtins__", "builtins", "breakpoint", "memoryview"})  # fmt: skip
# Calls through an import that open, run or reach nothing: a module (or module.function) prefix each.
_SAFE_MODULE_PREFIXES = ("re.", "json.", "os.path.", "os.getcwd", "os.environ.get", "os.sep", "os.close",
                         "os.fsync", "os.write", "sys.stdin.", "sys.argv",
                         "sys.stdout.write", "sys.stderr.write", "sys.exit", "datetime.", "time.", "textwrap.",
                         "string.", "math.", "collections.", "itertools.", "difflib.", "unicodedata.", "calendar.",
                         "pathlib.Path", "pathlib.PurePath", "shlex.quote", "shlex.split", "hashlib.", "random.",
                         "copy.", "uuid.", "decimal.", "fractions.", "statistics.", "operator.itemgetter",
                         "functools.reduce", "dataclasses.", "typing.", "enum.", "html.escape", "html.unescape",
                         "urllib.parse.")  # fmt: skip
# Builtins that open, run or reach nothing.
_SAFE_BUILTINS = frozenset(
    """print len str int float bool repr sorted list dict set frozenset tuple min max sum any all enumerate zip range
    isinstance issubclass type abs round format hash id iter next reversed chr ord hex oct bin divmod pow input slice
    callable""".split()
)
# Methods of text, lists, dicts, sets, files `open()` gave and regex matches that open, run or reach nothing.
_SAFE_METHODS = frozenset(
    """replace split rsplit partition rpartition strip lstrip rstrip removeprefix removesuffix splitlines join format
    format_map encode decode lower upper casefold title capitalize swapcase center ljust rjust zfill expandtabs
    translate maketrans startswith endswith find rfind index rindex count isdigit isalpha isspace isupper islower
    isalnum isnumeric get items keys values pop popitem copy append insert extend remove sort reverse clear add
    discard update setdefault union intersection difference write writelines read readline readlines close flush
    seek tell group groups groupdict span start end fullmatch match search sub subn findall finditer strftime
    isoformat date time timestamp weekday isoweekday""".split()
)
# What the gate's scan judges itself (scout.kb.program_reads): its reads, lists, copies, runs, writes and tests.
_SCAN_JUDGED = frozenset({"open", "Path", "PurePath"})
_SCAN_JUDGED_METHODS = frozenset({"read_text", "read_bytes", "open", "write_text", "write_bytes", "iterdir", "glob",
                                  "rglob", "exists", "is_file", "is_dir", "mkdir", "touch", "stat",
                                  "with_suffix", "with_name", "joinpath", "resolve", "expanduser"})  # fmt: skip


# Calls through an import whose result is text made from their arguments (never an object: `pathlib.Path(p)` is a
# path, and its `.replace` moves the file).
_TEXT_MODULE_CALLS = ("json.dumps", "html.escape", "html.unescape", "shlex.quote", "shlex.split", "textwrap.",
                      "os.path.basename", "os.path.dirname", "os.path.splitext", "os.path.normpath", "os.path.join",
                      "os.path.relpath", "urllib.parse.quote", "urllib.parse.unquote", "unicodedata.normalize",
                      "string.capwords")  # fmt: skip


# Module attributes that are values, not functions.
_NOT_FUNCTIONS = frozenset(
    {
        "sys.stdin",
        "sys.stdout",
        "sys.stderr",
        "sys.argv",
        "sys.path",
        "os.environ",
        "os.sep",
        "os.linesep",
        "os.curdir",
        "os.pardir",
        "os.name",
        "sys.platform",
        "sys.version",
    }
)


def _safe_module_call(name: str) -> bool:
    return name.startswith(_SAFE_MODULE_PREFIXES) or name in {p.rstrip(".") for p in _SAFE_MODULE_PREFIXES}


class PythonText:
    """One program's syntax tree, asked about the literals at given text offsets."""

    def __init__(self, text: str) -> None:
        self.tree = ast.parse(text)  # SyntaxError: the caller treats nothing as text
        self.parent: dict[ast.AST, ast.AST] = {}
        self.loads: dict[str, list[ast.Name]] = {}
        self.stores: dict[str, list[ast.Name]] = {}
        self.imported: set[str] = set()
        self.qualified: dict[str, str] = {}  # a name an import binds -> what it names (`o` -> `os`, `run` -> …)
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
            if isinstance(node, ast.Name):
                (self.loads if isinstance(node.ctx, ast.Load) else self.stores).setdefault(node.id, []).append(node)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    bound = a.asname or a.name.split(".")[0]
                    self.imported.add(bound)
                    self.qualified[bound] = a.name if a.asname else bound
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    self.imported.add(a.asname or a.name)
                    self.qualified[a.asname or a.name] = f"{node.module or ''}.{a.name}"
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
        return node is not None and self.allowed() and self._text(node, set(), 0)

    # -- the whole program --------------------------------------------------------------------------------------

    def qualname(self, expr: ast.AST) -> str | None:
        """The dotted name an expression spells, its import resolved (`o.system` -> `os.system`); None for one that
        isn't a dotted name."""
        parts: list[str] = []
        while isinstance(expr, ast.Attribute):
            parts.append(expr.attr)
            expr = expr.value
        if not isinstance(expr, ast.Name):
            return None
        root = self.qualified.get(expr.id, expr.id)
        return ".".join([root, *reversed(parts)])

    def _module_call(self, func: ast.AST) -> str | None:
        """What a call reaches through an import (`os.system`, `run` from subprocess), or None."""
        root = func
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in self.qualified:
            return self.qualname(func)
        return None

    def allowed(self) -> bool:
        """Whether the program as a whole is one a literal of it may count as text in: no lambda, nothing that
        reaches a function by name or runs code (``getattr``, ``__builtins__``, ``eval``…), and no call through an
        import outside :data:`_SAFE_MODULE_CALLS`."""
        if getattr(self, "_allowed", None) is None:
            self._allowed = self._check_allowed()
        return bool(self._allowed)

    def _check_allowed(self) -> bool:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Lambda):
                return False
            if isinstance(node, ast.Name) and node.id in _BY_NAME:
                return False
            if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
                return False  # `.__call__`, `.__class__`, `.__globals__`
            if isinstance(node, ast.Call):
                reached = self._module_call(node.func)
                if reached is not None and not _safe_module_call(reached):
                    return False
        return True

    def hides_reads(self, judged: Callable[[str], bool] = lambda _name: False) -> bool:
        """Whether the program calls something that may open a file the gate's scan doesn't model, with what it
        can't spell out: a call through an import outside :data:`_SAFE_MODULE_CALLS`, or to an unknown function or
        method, given anything but a constant or text (``io.open_code(x)``, ``__builtins__.open(x)``,
        ``netrc.netrc(x)``, ``ConfigParser().read(x)``), or any call handed a function to call (``xs.sort(key=…)``,
        a lambda, ``map(io.open_code, …)``). The gate records it as a path built at run time (fail closed).
        ``judged`` says which dotted names (``os.open``, ``subprocess.run``) the gate's scan judges itself."""
        if any(isinstance(n, ast.Name) and n.id in _BY_NAME for n in ast.walk(self.tree)):
            return True  # `__builtins__.open(x)`, `getattr(io, "open_code")(x)`: reached by name
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            values = [*node.args, *(k.value for k in node.keywords)]
            if any(self._callable(v) for v in values):
                return True
            func = node.func
            reached = self._module_call(func)
            if reached is not None:
                if _safe_module_call(reached) or judged(reached):
                    continue
                if any(not isinstance(v, ast.Constant) for v in values):
                    return True
                continue
            if isinstance(func, ast.Name) and func.id in _SAFE_BUILTINS:
                continue
            if isinstance(func, ast.Attribute) and func.attr in ("write", "writelines") and self._file(func.value):
                continue  # what a file `open()` gave writes is content, never a path
            if isinstance(func, ast.Attribute) and func.attr in _SAFE_METHODS and self._known_receiver(func.value):
                if all(isinstance(v, ast.Constant) or self._texty(v, set(), 0) for v in values):
                    continue
            if isinstance(func, ast.Name) and func.id in _SCAN_JUDGED:
                continue  # judged by the scan itself
            if isinstance(func, ast.Attribute) and func.attr in _SCAN_JUDGED_METHODS:
                continue
            if any(not isinstance(v, ast.Constant) for v in values):
                return True
        return False

    def _callable(self, value: ast.AST) -> bool:
        """Whether a call's argument is a function for it to call: a lambda, an import's attribute, a builtin that
        opens, runs or reaches by name."""
        if isinstance(value, ast.Lambda):
            return True
        if isinstance(value, ast.Name):
            return value.id in self.qualified or value.id in _BY_NAME or value.id in ("open", "print", "input")
        if isinstance(value, ast.Attribute):
            if value.attr.isupper() or self.qualname(value) in _NOT_FUNCTIONS:
                return False  # a constant (`re.M`, `os.O_RDONLY`) or a stream (`sys.stdin`), not a function
            return self._module_call(value) is not None or value.attr in ("read", "read_text", "open", "system")
        return False

    def _known_receiver(self, expr: ast.AST) -> bool:
        """Whether a method's receiver is text, a container of it, or a file ``open()`` gave: never an object an
        import made (``ConfigParser()``)."""
        if self._texty(expr, set(), 0) or self._file(expr):
            return True
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id in _SAFE_BUILTINS:
            return True
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
            return expr.func.attr in _SAFE_METHODS and self._known_receiver(expr.func.value)
        if isinstance(expr, ast.Name) and expr.id not in self.qualified:
            return all(
                isinstance(p := self.parent.get(st), ast.For | ast.comprehension | ast.Assign | ast.AnnAssign)
                and (
                    isinstance(p, ast.For | ast.comprehension)
                    or (p.value is not None and self._known_receiver(p.value))
                )
                for st in self.stores.get(expr.id, [])
            ) and bool(self.stores.get(expr.id))
        return False

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
            if not self._others_text(call, node):
                return False  # `xs.sort(key=o.system)`: it hands the text to something it calls
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
        """Whether ``call`` takes ``arg`` (an argument, or a keyword) as text: never when another of its arguments
        is anything but a constant or text (a function it may call with it: `re.sub(".+", lambda m: …, s)`)."""
        if not self._others_text(call, arg):
            return False
        func = call.func
        if isinstance(func, ast.Attribute):
            name, recv = func.attr, func.value
            if isinstance(recv, ast.Name) and recv.id == "re" and "re" in self.imported:
                return name in _RE_TESTS or (name in _RE_RESULTS and self._text(call, seen, depth + 1))
            if name in ("write", "writelines"):  # a file's content, not what an archive's write adds
                return bool(call.args) and call.args[0] is arg and self._file(recv)
            if name in ("write_text", "write_bytes"):  # Path(p).write_text(text): the receiver is where
                return bool(call.args) and call.args[0] is arg and self._path(recv)
            reached = self._module_call(func)
            if reached is not None:  # a call through an import: one that gives text passes its value on
                return reached.startswith(_TEXT_MODULE_CALLS) and self._text(call, seen, depth + 1)
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

    def _others_text(self, call: ast.Call, this: ast.AST) -> bool:
        """Whether every argument and keyword of ``call`` other than ``this`` is a constant or text."""
        for value in [*call.args, *call.keywords]:
            if value is this:
                continue
            inner = value.value if isinstance(value, ast.keyword) else value
            if inner is this or isinstance(inner, ast.Constant):
                continue
            if isinstance(inner, ast.Starred) or not self._texty(inner, set(), 0):
                return False
        return True

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
            if self.qualname(func) in ("pathlib.Path", "pathlib.PurePath"):
                return True  # `from pathlib import Path as P`
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
            return True  # text, a number, None: a plain value
        if isinstance(expr, ast.JoinedStr):
            return True
        if isinstance(expr, ast.BinOp):
            return self._texty(expr.left, names, depth + 1) or self._texty(expr.right, names, depth + 1)
        if isinstance(expr, ast.UnaryOp):
            return self._texty(expr.operand, names, depth + 1)  # `-1`, `not s`
        if isinstance(expr, ast.Compare | ast.BoolOp):
            return True  # a bool
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
                return func.attr in _GIVES_TEXT or func.attr in _TEXT_TESTS  # text, or a number or bool made of it
            if isinstance(func, ast.Name) and func.id not in self.imported and func.id in _PASSES | _NUMBERS:
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
