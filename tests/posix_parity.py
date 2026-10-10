"""AST fingerprints that prove the alpha.22 POSIX source paths survive alpha.23 unchanged.

alpha.23 adds Windows behaviour only as guarded statements of exactly one shape:

    if sys.platform == "win32":
        ...                      # no else branch

A fingerprint ignores docstrings and exactly those guarded statements, and records everything else of each
function (signature, decorators and every remaining top-level statement of its body), each class body and each
module-level statement. Two fingerprints are equal when the code that runs on a POSIX host is the same.

`tests/generate_posix_parity.py` wrote `tests/fixtures/posix-parity-alpha22.json` from the frozen v1.0.0-alpha.22
tag; `tests/test_posix_parity.py` compares the working tree with it. Standard library only.
"""

import ast
import hashlib

GUARD = "sys.platform == 'win32'"


def is_windows_guard(node):
    """An `if sys.platform == "win32":` statement with no else branch."""
    return isinstance(node, ast.If) and not node.orelse and ast.unparse(node.test) == GUARD


def _strip_docstring(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _h(node):
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode("utf-8")).hexdigest()[:24]


def _function(node):
    body = [s for s in _strip_docstring(node.body) if not is_windows_guard(s)]
    return {"kind": type(node).__name__, "args": _h(node.args), "returns": _h(node.returns) if node.returns else None,
            "decorators": [_h(d) for d in node.decorator_list], "body": [_h(s) for s in body]}


def _key(node):
    if isinstance(node, ast.Assign):
        return "assign " + ", ".join(ast.unparse(t) for t in node.targets)
    if isinstance(node, ast.AnnAssign):
        return "assign " + ast.unparse(node.target)
    return f"{type(node).__name__.lower()} {ast.unparse(node).splitlines()[0][:80]}"


def units(source):
    """{unit name: fingerprint} for one module's source text."""
    tree = ast.parse(source)
    out = {"imports": sorted(ast.unparse(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))}
    for node in _strip_docstring(tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)) or is_windows_guard(node):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[f"def {node.name}"] = _function(node)
        elif isinstance(node, ast.ClassDef):
            members = [s for s in _strip_docstring(node.body) if not is_windows_guard(s)]
            out[f"class {node.name}"] = {"bases": [_h(b) for b in node.bases],
                                         "decorators": [_h(d) for d in node.decorator_list],
                                         "body": [_h(s) for s in members
                                                  if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]}
            for s in members:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"def {node.name}.{s.name}"] = _function(s)
        else:
            key, n = _key(node), 2
            while key in out:
                key, n = f"{_key(node)} #{n}", n + 1
            out[key] = {"stmt": _h(node)}
    return out


def macos_view(text):
    """alpha.26: the C# source a macOS Editor compiles — every `#if UNITY_EDITOR_WIN` branch dropped, its `#else` branch
    kept, every `#if !UNITY_EDITOR_WIN` block kept, and the directive lines removed."""
    out, stack = [], []
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("#if UNITY_EDITOR_WIN"):
            stack.append(["win", False])
            continue
        if s.startswith("#if !UNITY_EDITOR_WIN"):
            stack.append(["notwin", True])
            continue
        if s == "#else" and stack:
            stack[-1][1] = not stack[-1][1]
            continue
        if s == "#endif" and stack:
            stack.pop()
            continue
        if all(keep for _, keep in stack):
            out.append(line)
    if stack:
        raise ValueError("unbalanced #if")
    return "".join(out)
