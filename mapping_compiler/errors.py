"""编译期与运行期错误类型。"""


class MappingError(Exception):
    """所有映射相关错误的基类。"""


class ConfigError(MappingError):
    """发布前（编译期）发现的配置错误。"""


class CycleError(ConfigError):
    """目标字段之间存在循环依赖。"""

    def __init__(self, cycle_fields):
        self.cycle_fields = sorted(cycle_fields)
        super().__init__(
            "检测到循环依赖，涉及目标字段: " + ", ".join(self.cycle_fields)
        )


class DuplicateTargetError(ConfigError):
    """同一目标字段被多条规则写入。"""

    def __init__(self, target):
        self.target = target
        super().__init__(f"目标字段 {target!r} 被重复定义")


class UnknownFieldError(ConfigError):
    """表达式引用了既不在源 schema 也不是任何目标字段的名字。"""

    def __init__(self, target, name):
        self.target = target
        self.name = name
        super().__init__(
            f"规则 {target!r} 引用了未知字段 {name!r}"
            "（既不是源字段，也不是其他目标字段）"
        )


class TypeMismatchError(ConfigError):
    """表达式推断类型与目标声明类型不兼容。"""

    def __init__(self, target, expected, actual):
        self.target = target
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"规则 {target!r} 声明类型为 {expected!r}，"
            f"但表达式推断类型为 {actual!r}，两者不兼容"
        )


class ExecutionError(MappingError):
    """记录执行期错误（除零、运行期类型不符等）。"""


class IntegerOverflowError(ExecutionError):
    """整数值超出目标整数类型的表示范围。"""

    def __init__(self, target, value, int_type):
        self.target = target
        self.value = value
        self.int_type = int_type
        super().__init__(
            f"字段 {target!r} 的值 {value} 超出 {int_type} 的表示范围"
        )
