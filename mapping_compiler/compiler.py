"""映射配置编译器：校验配置、构建依赖图、生成稳定执行顺序。"""

import heapq
from dataclasses import dataclass, field

from .errors import (
    ConfigError,
    CycleError,
    DuplicateTargetError,
    ExecutionError,
    IntegerOverflowError,
    TypeMismatchError,
    UnknownFieldError,
)
from .expressions import (
    INT_RANGES,
    KNOWN_TYPES,
    category,
    evaluate,
    extract_names,
    infer_type,
    is_compatible,
    parse_expression,
)

MISSING = object()


@dataclass
class Rule:
    target: str
    index: int
    source: str = None
    expr_text: str = None
    when_text: str = None
    type: str = None
    default: object = MISSING
    expr: object = None
    when: object = None
    deps: set = field(default_factory=set)


class CompiledMapping:
    """编译产物：按拓扑顺序执行规则，把源记录映射为目标记录。"""

    def __init__(self, source_schema, rules):
        self.source_schema = dict(source_schema)
        self.rules = rules

    @property
    def execution_order(self):
        return [rule.target for rule in self.rules]

    def apply(self, record):
        env = {name: record.get(name) for name in self.source_schema}
        output = {}
        for rule in self.rules:
            value = self._apply_rule(rule, env)
            env[rule.target] = value
            output[rule.target] = value
        return output

    def _apply_rule(self, rule, env):
        if rule.when is not None:
            condition = evaluate(rule.when, env)
            if not condition:
                return rule.default if rule.default is not MISSING else None
        if rule.expr is not None:
            value = evaluate(rule.expr, env)
        elif rule.source is not None:
            value = env[rule.source]
        else:
            value = None
        if value is None and rule.default is not MISSING:
            value = rule.default
        if value is not None and rule.type is not None:
            value = _coerce(rule.target, rule.type, value)
        return value


def _coerce(target, declared_type, value):
    """运行期类型收口：类别校验 + 整数范围检查。"""
    declared_category = category(declared_type)
    if isinstance(value, bool):
        if declared_category != "bool":
            raise ExecutionError(
                f"字段 {target!r} 期望 {declared_type}，实际为 bool"
            )
        return value
    if isinstance(value, int):
        if declared_category == "int":
            low, high = INT_RANGES[declared_type]
            if not low <= value <= high:
                raise IntegerOverflowError(target, value, declared_type)
            return value
        if declared_category == "float":
            return float(value)
        raise ExecutionError(
            f"字段 {target!r} 期望 {declared_type}，实际为 int"
        )
    if isinstance(value, float):
        if declared_category != "float":
            raise ExecutionError(
                f"字段 {target!r} 期望 {declared_type}，实际为 float"
            )
        return value
    if isinstance(value, str):
        if declared_category != "str":
            raise ExecutionError(
                f"字段 {target!r} 期望 {declared_type}，实际为 str"
            )
        return value
    raise ExecutionError(
        f"字段 {target!r} 不支持的值类型: {type(value).__name__}"
    )


def compile_mapping(source_schema, rule_configs):
    """编译映射配置；任何配置错误都在此阶段抛出（发布前拦截）。"""
    if not isinstance(source_schema, dict) or not source_schema:
        raise ConfigError("源 schema 必须是非空的 {字段名: 类型} 字典")
    for name, type_name in source_schema.items():
        if type_name not in KNOWN_TYPES:
            raise ConfigError(
                f"源字段 {name!r} 声明了未知类型 {type_name!r}"
            )

    rules = [_build_rule(index, cfg) for index, cfg in enumerate(rule_configs)]

    seen = {}
    for rule in rules:
        if rule.target in seen:
            raise DuplicateTargetError(rule.target)
        seen[rule.target] = rule

    targets = set(seen)
    for rule in rules:
        for name in rule.deps:
            if name not in targets and name not in source_schema:
                raise UnknownFieldError(rule.target, name)
        rule.deps = {name for name in rule.deps if name in targets}
        if rule.source is not None:
            if rule.source in targets:
                rule.deps.add(rule.source)
            elif rule.source not in source_schema:
                raise UnknownFieldError(rule.target, rule.source)

    ordered = _topo_sort(rules)

    field_types = dict(source_schema)
    for rule in ordered:
        if rule.expr is not None:
            inferred = infer_type(rule.expr, field_types)
            if rule.type is not None and not is_compatible(rule.type, inferred):
                raise TypeMismatchError(rule.target, rule.type, inferred)
            if rule.type is None:
                field_types[rule.target] = (
                    inferred if inferred in KNOWN_TYPES else None
                )
        elif rule.source is not None:
            source_type = field_types.get(rule.source)
            if rule.type is not None and source_type is not None:
                if not is_compatible(rule.type, category(source_type)):
                    raise TypeMismatchError(
                        rule.target, rule.type, category(source_type)
                    )
            if rule.type is None:
                field_types[rule.target] = source_type
        if rule.type is not None:
            field_types[rule.target] = rule.type

    return CompiledMapping(source_schema, ordered)


def _build_rule(index, cfg):
    if not isinstance(cfg, dict):
        raise ConfigError(f"第 {index} 条规则必须是字典")
    target = cfg.get("target")
    if not target or not isinstance(target, str):
        raise ConfigError(f"第 {index} 条规则缺少合法的 target 字段")
    source = cfg.get("source")
    expr_text = cfg.get("expr")
    if source is not None and expr_text is not None:
        raise ConfigError(f"规则 {target!r} 的 source 与 expr 只能二选一")
    type_name = cfg.get("type")
    if type_name is not None and type_name not in KNOWN_TYPES:
        raise ConfigError(f"规则 {target!r} 声明了未知类型 {type_name!r}")
    rule = Rule(
        target=target,
        index=index,
        source=source,
        expr_text=expr_text,
        when_text=cfg.get("when"),
        type=type_name,
        default=cfg["default"] if "default" in cfg else MISSING,
    )
    if expr_text is not None:
        rule.expr = parse_expression(expr_text, target)
        rule.deps |= extract_names(rule.expr)
    if rule.when_text is not None:
        rule.when = parse_expression(rule.when_text, target)
        rule.deps |= extract_names(rule.when)
    return rule


def _topo_sort(rules):
    """Kahn 拓扑排序；同层按配置声明顺序出队，保证顺序稳定。"""
    by_target = {rule.target: rule for rule in rules}
    indegree = {rule.target: 0 for rule in rules}
    dependents = {rule.target: [] for rule in rules}
    for rule in rules:
        for dep in rule.deps:
            indegree[rule.target] += 1
            dependents[dep].append(rule.target)

    ready = [
        (rule.index, rule.target)
        for rule in rules
        if indegree[rule.target] == 0
    ]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, target = heapq.heappop(ready)
        rule = by_target[target]
        ordered.append(rule)
        for dependent in dependents[target]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(
                    ready, (by_target[dependent].index, dependent)
                )
    if len(ordered) != len(rules):
        remaining = [name for name, deg in indegree.items() if deg > 0]
        raise CycleError(remaining)
    return ordered
