"""受限表达式引擎：解析、依赖提取、类型推断与空值传播求值。

表达式基于 Python ``ast`` 的白名单子集，只允许：
常量、字段名、算术/比较/布尔运算、条件表达式以及白名单函数。
"""

import ast

from .errors import ConfigError, ExecutionError

INT_TYPES = ("int8", "int16", "int32", "int64")
INT_RANGES = {
    "int8": (-(2 ** 7), 2 ** 7 - 1),
    "int16": (-(2 ** 15), 2 ** 15 - 1),
    "int32": (-(2 ** 31), 2 ** 31 - 1),
    "int64": (-(2 ** 63), 2 ** 63 - 1),
}
KNOWN_TYPES = frozenset(INT_TYPES) | {"float", "str", "bool"}



def _to_int(value):
    if isinstance(value, bool):
        raise ExecutionError("to_int() 不接受布尔值")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value.strip())
    raise ExecutionError(f"to_int() 不支持的类型: {type(value).__name__}")


def _to_float(value):
    if isinstance(value, bool):
        raise ExecutionError("to_float() 不接受布尔值")
    return float(value)


def _to_str(value):
    return str(value)


ALLOWED_FUNCS = {
    "to_int": _to_int,
    "to_float": _to_float,
    "to_str": _to_str,
    "abs": abs,
    "len": len,
    "round": round,
    "upper": lambda s: s.upper(),
    "lower": lambda s: s.lower(),
    "concat": lambda *parts: "".join(str(p) for p in parts),
}

_FUNC_RETURN_TYPES = {
    "to_int": "int",
    "to_float": "float",
    "to_str": "str",
    "abs": "int",
    "len": "int",
    "round": "int",
    "upper": "str",
    "lower": "str",
    "concat": "str",
}

_ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
)


def parse_expression(source_text, target):
    """把表达式文本编译为经过白名单校验的 AST。"""
    try:
        tree = ast.parse(source_text, mode="eval")
    except SyntaxError as exc:
        raise ConfigError(f"规则 {target!r} 的表达式语法错误: {exc}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ConfigError(
                f"规则 {target!r} 的表达式包含不允许的语法: "
                f"{type(node).__name__}"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCS:
                raise ConfigError(
                    f"规则 {target!r} 调用了不允许的函数 "
                    f"{ast.dump(node.func)}"
                )
            if node.keywords:
                raise ConfigError(f"规则 {target!r} 的函数调用不支持关键字参数")
    return tree


def extract_names(tree):
    """提取表达式中引用的所有字段名（不含函数名）。"""
    func_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in func_names:
                names.add(node.id)
    return names


def category(type_name):
    if type_name in INT_TYPES:
        return "int"
    return type_name


def infer_type(tree, field_types):
    """自底向上推断表达式结果类别：int / float / str / bool / unknown。"""
    return _infer(tree.body, field_types)


def _infer(node, field_types):
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None:
            return "unknown"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "str"
        return "unknown"
    if isinstance(node, ast.Name):
        declared = field_types.get(node.id)
        return category(declared) if declared else "unknown"
    if isinstance(node, ast.BinOp):
        left = _infer(node.left, field_types)
        right = _infer(node.right, field_types)
        if "unknown" in (left, right):
            return "unknown"
        if left == "str" or right == "str":
            if left == right == "str" and isinstance(node.op, ast.Add):
                return "str"
            raise ConfigError(
                f"字符串不支持运算符 {type(node.op).__name__} 的混合运算"
            )
        if left == "bool" and right == "bool":
            return "int"
        if "float" in (left, right):
            return "float"
        if left == right == "int":
            if isinstance(node.op, ast.Div):
                return "float"
            return "int"
        return "unknown"
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return "bool"
        return _infer(node.operand, field_types)
    if isinstance(node, (ast.BoolOp, ast.Compare)):
        return "bool"
    if isinstance(node, ast.IfExp):
        body = _infer(node.body, field_types)
        orelse = _infer(node.orelse, field_types)
        if body == orelse:
            return body
        if {body, orelse} <= {"int", "float"}:
            return "float"
        return "unknown"
    if isinstance(node, ast.Call):
        return _FUNC_RETURN_TYPES.get(node.func.id, "unknown")
    return "unknown"


def is_compatible(declared_type, inferred_category):
    """声明类型与推断类别是否兼容（int 可提升为 float）。"""
    if inferred_category in (None, "unknown"):
        return True
    declared_category = category(declared_type)
    if declared_category == inferred_category:
        return True
    return declared_category == "float" and inferred_category == "int"


def evaluate(tree, env):
    """在字段环境 env 上求值，遵循空值传播语义。"""
    return _eval(tree.body, env)


def _eval(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, env)
        right = _eval(node.right, env)
        if left is None or right is None:
            return None
        return _apply_binop(node.op, left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, env)
        if operand is None:
            return None
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand
    if isinstance(node, ast.BoolOp):
        values = [_eval(value, env) for value in node.values]
        if any(value is None for value in values):
            return None
        if isinstance(node.op, ast.And):
            return all(bool(value) for value in values)
        return any(bool(value) for value in values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, env)
            if left is None or right is None:
                return None
            if not _apply_compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        test = _eval(node.test, env)
        if test is None:
            return None
        return _eval(node.body if test else node.orelse, env)
    if isinstance(node, ast.Call):
        args = [_eval(arg, env) for arg in node.args]
        if any(arg is None for arg in args):
            return None
        try:
            return ALLOWED_FUNCS[node.func.id](*args)
        except ExecutionError:
            raise
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise ExecutionError(
                f"函数 {node.func.id} 调用失败: {exc}"
            ) from exc
    raise ExecutionError(f"不支持的表达式节点: {type(node).__name__}")


def _apply_binop(op, left, right):
    try:
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Mod):
            return left % right
        if isinstance(op, ast.Pow):
            return left ** right
    except ArithmeticError as exc:
        raise ExecutionError(f"算术运算失败: {exc}") from exc
    raise ExecutionError(f"不支持的运算符: {type(op).__name__}")


def _apply_compare(op, left, right):
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    raise ExecutionError(f"不支持的比较运算符: {type(op).__name__}")
