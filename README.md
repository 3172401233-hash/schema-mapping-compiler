# schema-mapping-compiler

Python 异构源字段映射编译器：把多来源字段映射到统一仓表，支持重命名、
类型转换、依赖其他目标字段的派生表达式、默认值和条件。配置在发布前编译，
自动建立表达式依赖图并生成稳定执行顺序，前向依赖不再读到空值。

## 快速开始

```python
from mapping_compiler import compile_mapping

source_schema = {"price": "float", "qty": "int32", "name": "str"}

rules = [
    # 配置顺序可以随意：c_total 引用了后面才声明的 b_taxed
    {"target": "c_total", "expr": "b_taxed * qty", "type": "float"},
    {"target": "b_taxed", "expr": "a_net * 1.13", "type": "float"},
    {"target": "a_net",   "expr": "price * 0.9",  "type": "float"},
    # 重命名 + 条件 + 默认值
    {"target": "title", "source": "name", "type": "str",
     "when": "name is not None", "default": "unknown"},
]

mapping = compile_mapping(source_schema, rules)   # 发布前：全部校验在此完成
mapping.execution_order                            # ['a_net', 'b_taxed', 'c_total', 'title']
mapping.apply({"price": 100.0, "qty": 2, "name": "apple"})
```

## 规则字段

| 字段 | 说明 |
| --- | --- |
| `target` | 目标字段名（必填，全局唯一） |
| `source` | 源字段重命名；与 `expr` 二选一 |
| `expr` | 派生表达式，可引用源字段和其他目标字段 |
| `type` | 目标类型：`int8/int16/int32/int64/float/str/bool` |
| `default` | 结果为 `None` 或条件不命中时的显式默认值 |
| `when` | 条件表达式，结果为假或 `None` 时跳过计算 |

## 发布前校验（compile_mapping 阶段报错）

- **循环依赖**：目标字段相互（或自我）引用，报 `CycleError` 并列出涉及字段
- **重复目标**：多条规则写同一目标，报 `DuplicateTargetError`
- **未知源字段**：表达式/条件/rename 引用了 schema 和目标之外的名称，报 `UnknownFieldError`
- **不兼容类型**：表达式推断类型与声明类型冲突（如 `str -> int32`、
  `float -> int32` 未显式 `to_int`），报 `TypeMismatchError`

执行顺序用 Kahn 拓扑排序生成，同层规则按配置声明顺序出队，因此同一配置
永远得到同一顺序；无依赖关系的规则保持配置书写顺序。

## 运行时语义

- **空值传播**：任一操作数为 `None`，算术/比较/布尔/函数调用结果均为
  `None`；源记录缺键按 `None` 处理
- **显式默认值**：表达式结果为 `None` 或 `when` 不命中时，使用 `default`
- **整数溢出**：写入 `int8/16/32/64` 目标时做范围检查，越界抛
  `IntegerOverflowError`（只在目标赋值点检查，中间结果按 Python 整数计算）
- **类型收口**：运行时值类别与声明类型不符抛 `ExecutionError`；
  `int -> float` 自动提升，其余转换必须显式调用转换函数
- 除零抛 `ExecutionError`

## 表达式能力边界

表达式是 Python AST 的白名单子集，**不是**通用 Python：

- 支持：常量、字段引用、算术（`+ - * / // % **`）、一元正负、比较
  （含链式、`is`/`is not`）、`and/or/not`、条件表达式 `a if cond else b`
- 支持函数（仅这些，且不支持关键字参数）：
  `to_int / to_float / to_str / abs / len / round / upper / lower / concat`
- 不支持：属性访问、下标、切片、列表/字典/集合字面量、推导式、lambda、
  f-string、赋值、任意函数调用（如 `__import__` 会在编译期被拒绝）
- 类型推断是静态且保守的：`int / float` 混合得 `float`，`int / int`
  得 `float`，字符串仅支持 `+` 拼接；推断不出的按 `unknown` 放行，
  留待运行时收口
- 布尔运算在任一操作数为 `None` 时整体为 `None`（三值逻辑），
  `when` 拿到 `None` 视为不命中

## 测试

```bash
python -m unittest discover -s tests
```
