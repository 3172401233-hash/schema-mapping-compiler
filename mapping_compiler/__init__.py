"""异构源字段映射编译器。

把「源字段 -> 目标字段」的映射配置（重命名、类型转换、派生表达式、
默认值、条件）编译成带稳定执行顺序的可执行映射，并在发布前拦截
循环依赖、重复目标、未知源字段和不兼容类型。
"""

from .compiler import CompiledMapping, compile_mapping
from .errors import (
    ConfigError,
    CycleError,
    DuplicateTargetError,
    ExecutionError,
    IntegerOverflowError,
    MappingError,
    TypeMismatchError,
    UnknownFieldError,
)

__all__ = [
    "compile_mapping",
    "CompiledMapping",
    "MappingError",
    "ConfigError",
    "CycleError",
    "DuplicateTargetError",
    "UnknownFieldError",
    "TypeMismatchError",
    "ExecutionError",
    "IntegerOverflowError",
]
