# Excel Pytest 参数化规范

## 目录

- 推荐页签结构
- 固定枚举
- 字段映射
- 加载与校验
- pytest 参数化骨架
- 适用范围

## 推荐页签结构

保留人工页签，例如 `新增`；新增机器页签，例如 `新增_自动化`。

| 列 | 必填 | 示例 | 说明 |
| --- | --- | --- | --- |
| `case_id` | 是 | `PROJECT-ADD-001` | 全局唯一且稳定 |
| `enabled` | 是 | `Y` | 仅 `Y` 进入参数集 |
| `module_key` | 是 | `project` | 对应模块配置键 |
| `case_title` | 是 | `金额不允许负数` | 人类可读标题 |
| `field_key` | 是 | `amount` | 对应字段配置键 |
| `operation` | 是 | `fill` | 固定操作枚举 |
| `input_value` | 视操作 | `-1` | 实际值或确定性特殊值 |
| `submit_action` | 是 | `save` | 操作后是否保存/提交 |
| `expected_type` | 是 | `field_error` | 固定断言类型 |
| `expected_value` | 是 | `金额不能小于0` | 唯一可观察结果 |
| `priority` | 是 | `P1` | `P0/P1/P2/P3` |
| `remark` | 否 | `前端失焦校验` | 不参与核心执行 |

不要把执行结果、测试人员、日期混入机器参数；这些属于测试报告结果，不是输入配置。

## 固定枚举

`operation`：

```text
fill
clear
select
check
uncheck
upload
```

`submit_action`：

```text
none
save
submit
```

`expected_type`：

```text
field_error
toast
value
visible
hidden
save_success
submit_success
```

特殊输入值：

```text
__EMPTY__             清空字段
__SPACE__             一个半角空格
__NULL__              JSON null，仅接口测试
__MISSING__           不传字段，仅接口测试
__REPEAT__:a:51       生成 51 个 a
```

禁止通过 Excel 单元格执行 `eval()`、Python 表达式或任意脚本。

## 字段映射

Excel 中只保存稳定语义键：

```python
MODULE_CONFIGS = {
    "project": {
        "url": "/project/list",
        "fields": {
            "name": {"test_id": "project-name", "label": "项目名称"},
            "amount": {"test_id": "amount", "label": "金额"},
        },
        "valid_data": {"name": "AUTO_项目", "amount": "100"},
    }
}
```

`field_key` 不需要与数据库或接口字段同名；它必须与配置键一致。不要把动态 `el-id-*`、DOM 序号或完整 XPath 当作 `field_key`。

## 加载与校验

读取工作簿后先验证配置，再收集参数。推荐把验证错误作为收集错误报告，不要伪装成产品断言失败。

```python
ALLOWED_OPERATIONS = {"fill", "clear", "select", "check", "uncheck", "upload"}
ALLOWED_SUBMIT_ACTIONS = {"none", "save", "submit"}
ALLOWED_EXPECTED_TYPES = {
    "field_error", "toast", "value", "visible", "hidden",
    "save_success", "submit_success",
}


def parse_input_value(value):
    if value == "__EMPTY__":
        return ""
    if value == "__SPACE__":
        return " "
    if isinstance(value, str) and value.startswith("__REPEAT__:"):
        _, char, length = value.split(":", 2)
        return char * int(length)
    return value


def validate_case(case, module_configs):
    if case["module_key"] not in module_configs:
        raise ValueError(f"未知 module_key: {case['module_key']}")
    fields = module_configs[case["module_key"]]["fields"]
    if case["field_key"] not in fields:
        raise ValueError(f"未知 field_key: {case['field_key']}")
    if case["operation"] not in ALLOWED_OPERATIONS:
        raise ValueError(f"非法 operation: {case['operation']}")
    if case["submit_action"] not in ALLOWED_SUBMIT_ACTIONS:
        raise ValueError(f"非法 submit_action: {case['submit_action']}")
    if case["expected_type"] not in ALLOWED_EXPECTED_TYPES:
        raise ValueError(f"非法 expected_type: {case['expected_type']}")
```

还要检查 `case_id` 重复、启用行缺少断言、非法特殊值，以及文本标识被 Excel 转为数字或日期。

## pytest 参数化骨架

公共测试只写一套，每个模块提供轻量配置和合法基准数据：

pytest 在 fixture 和浏览器页面创建之前完成参数收集。若字段只能从真实 DOM 获得，先执行发现阶段并保存字段清单 JSON，再用清单与 Excel 规则生成下一次运行的参数；不要在测试函数或 fixture 内修改 `parametrize`。

```python
@pytest.mark.parametrize(
    "case",
    CASES,
    ids=lambda case: f"{case['module_key']}-{case['case_id']}",
)
def test_field_validation(page, login, case):
    config = MODULE_CONFIGS[case["module_key"]]
    open_add_form(page, config)
    fill_valid_baseline(page, config)

    field = locate_field(page, config["fields"][case["field_key"]])
    apply_operation(field, case["operation"], parse_input_value(case["input_value"]))
    apply_submit_action(page, config, case["submit_action"])
    assert_expected(page, config, case)
```

不要为每个模块复制完整参数化测试。只有 URL、定位器和值不同就复用公共测试；步骤、状态流转或断言机制不同则写独立测试。

## 适用范围

优先参数化：

- 必填、空值和空格。
- 文本长度边界。
- 数值范围、金额和精度。
- 手机号、邮箱、密码、证件格式。
- 固定枚举和相同交互模式的错误提示。

默认独立编写 pytest：

- 多级联动和动态显隐。
- 富文本及复杂上传下载。
- 防重复保存/提交。
- 动态明细、排序和跨页面流程。
- 需要多个步骤或多个状态断言的业务场景。
