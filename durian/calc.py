"""Calculated points: a small, safe expression language over other points. Not eval(): only the nodes listed here run.

    p("boiler-temp") * 1.8 + 32            read another point by name
    max(p("a"), p("b")) - min(p("a"), 0)    abs, min, max, round, sqrt
    p("a") if p("mode") > 0 else 0          comparisons, and/or/not, conditional
Arithmetic is + - * / % only (no powers: they can be made to run for ever). A true or false result is 1 or 0.
"""
import ast
import math

from .core import Quality, Source

MAX_LEN, MAX_REFS = 500, 20
FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "sqrt": math.sqrt}
BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b, ast.Mod: lambda a, b: a % b}
CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
       ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def parse(expr):
    """The checked syntax tree, or ValueError saying what is wrong."""
    if not isinstance(expr, str) or not 0 < len(expr) <= MAX_LEN:
        raise ValueError(f"expression must be text of 1 to {MAX_LEN} characters")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e.msg}") from None
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            ok = isinstance(n.func, ast.Name) and not n.keywords and (
                n.func.id in FUNCS or (n.func.id == "p" and len(n.args) == 1 and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)))
            if not ok:
                raise ValueError('calls allowed: p("point name"), abs, min, max, round, sqrt')
        elif isinstance(n, ast.Constant):
            if isinstance(n.value, bool) or not isinstance(n.value, (int, float, str)):
                raise ValueError("only numbers and point names (inside p(...)) are allowed")
        elif not isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp, ast.Load, ast.Name,
                                ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, *BIN, *CMP)):
            raise ValueError(f"{type(n).__name__} is not allowed in an expression")
        if isinstance(n, ast.Name) and n.id not in FUNCS and n.id != "p":
            raise ValueError(f'unknown name {n.id!r}: read a point with p("name")')
    if len(refs(tree)) > MAX_REFS:
        raise ValueError(f"an expression may read at most {MAX_REFS} points")
    return tree


def refs(tree):
    return sorted({n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "p"})


def evaluate(tree, read):
    """A number from the tree. read(name) returns a number or raises LookupError. Arithmetic, value and lookup errors mean no result."""
    def go(n):
        if isinstance(n, ast.Expression):
            return go(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.BinOp):
            return BIN[type(n.op)](go(n.left), go(n.right))
        if isinstance(n, ast.UnaryOp):
            v = go(n.operand)
            return -v if isinstance(n.op, ast.USub) else v if isinstance(n.op, ast.UAdd) else int(not v)
        if isinstance(n, ast.Compare):
            left = go(n.left)
            for op, right in zip(n.ops, n.comparators):
                r = go(right)
                if not CMP[type(op)](left, r):
                    return 0
                left = r
            return 1
        if isinstance(n, ast.BoolOp):
            vals = [go(v) for v in n.values]  # every branch is read, so a missing input is never hidden by short-circuiting
            return int(all(vals)) if isinstance(n.op, ast.And) else int(any(vals))
        if isinstance(n, ast.IfExp):
            return go(n.body) if go(n.test) else go(n.orelse)
        if n.func.id == "p":
            return read(n.args[0].value)
        return FUNCS[n.func.id](*[go(a) for a in n.args])
    v = go(tree)
    if isinstance(v, str) or not math.isfinite(v):
        raise ValueError("result is not a finite number")
    return float(v)


class CalcSource(Source):
    """Evaluated on every engine tick from the other sources' current values. The engine is set once it is built."""

    def __init__(self, name):
        super().__init__(name)
        self.manages_error = True  # a bad expression is reported on the point and in the error text
        self.error_is_down = True  # ... and a calculation that cannot run is a real fault: the point silently stops updating
        self.engine, self.trees = None, {}

    def add(self, point):
        super().add(point)
        self.trees[point.name] = parse(point.meta["calc"]["expression"])
        return point

    def poll(self, ts):
        if self.engine is None:
            return {}
        everything = {p.name: p for s in self.engine.sources for p in s.points.values()}
        out, failed, worst = {}, [], [Quality.GOOD]

        def read(name):
            p = everything.get(name)
            if p is None or p.last is None or p.quality == Quality.BAD:
                raise LookupError(f"{name} has no good value")
            worst[0] = max(worst[0], p.quality)
            return p.last

        for name, tree in self.trees.items():
            worst[0] = Quality.GOOD
            try:
                out[name] = (evaluate(tree, read), ts, Quality(worst[0]))
            except (ArithmeticError, ValueError, LookupError, TypeError) as e:
                out[name] = (None, ts, Quality.BAD)
                failed.append(f"{name}: {e}")
        self.error = f"{len(failed)} calculation(s) failed: " + "; ".join(failed[:3]) if failed else None
        return out
