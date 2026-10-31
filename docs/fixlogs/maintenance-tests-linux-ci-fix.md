# 系统维护层测试在 Linux CI 上整片失败 —— 修复复盘

> 版本：1.0 | 模块：`agent/core/maintenance/*`、`tests/core/test_cleaner_rules.py`、
> `tests/core/test_system_health.py`、`tests/tools/test_maintenance_tools.py`
> 类型：Windows 专属功能的测试缺平台门禁 + 一处校验顺序缺陷
> 发现方式：GitHub Actions `ci.yml`（ubuntu-latest，Python 3.11/3.12/3.13/3.14 矩阵）
> 作者：aceFelix

## 一、问题现象

系统维护层（2.3.2）推送后，开发机（Windows）跑全量是 **4074 passed, 1 skipped**，
CI 上却是：

```
================= 13 failed, 4024 passed, 38 skipped in 38.54s ==================
Error: Process completed with exit code 1.
```

13 条失败，报错分成三类，但根子是同一个：**这套能力是 Windows 专属，测试却没做平台门禁**。

**第一类（9 条，`test_cleaner_rules.py`）** 全是同一个 `ValueError`：

```
ValueError: 展开后不是绝对路径：'%LOCALAPPDATA%\\Temp' -> '%LOCALAPPDATA%\\Temp'
```

连带 `test_load_builtin_rulebook_has_all_rules` 报出内置规则库 **15 条全被拒**。

**第二类（3 条，`test_system_health.py`）** 期望的是参数错误，拿到的是环境错误：

```
assert '全盘' in '当前平台没有 PowerShell，无法调用 Defender'
assert 'path' in '当前平台没有 PowerShell，无法调用 Defender'
assert '未知' in '当前平台没有 PowerShell，无法调用 Defender'
```

**第三类（1 条，`test_maintenance_tools.py`）**：

```
KeyError: 'supported'
```

它是前两类的下游效应——预览要按 id 取规则，而 `user_temp` 在 Linux 上已被拒，取不到。

## 二、排查过程

1. **先确认不是逻辑错**。`expand_path()` 里 `%VAR%` 是自己实现的（注释写明
   「`ntpath.expandvars` 只认 `$`，所以必须自己展开」），走的是 `os.environ.get`。
   Linux 上根本没有 `LOCALAPPDATA` 这个变量，于是函数按设计**保留原样交后续校验拒绝**
   ——这是正确行为，不是缺陷。所以问题不在 `expand_path`。
2. **再确认规则库是不是坏了**。内置 19 条里 15 条是 `paths` 型、路径全是
   `%LOCALAPPDATA%` / `%SYSTEMROOT%` / `%SYSTEMDRIVE%`；4 条 `command` 型没有路径，
   所以在 Linux 上照常加载成功（这也是为什么 `test_cleanmgr_rule_has_no_analyze_cmd`
   没进失败名单——它要的是 command 型规则）。
3. **区分"该 skip"与"该修"**。第一类的 9 条本质是在断言「Windows 路径语义能正确解析」，
   在 POSIX 上无从验证，该 skip；第二类的 3 条断言的是**校验顺序**，与平台无关，该修。
4. **定位顺序缺陷**。读 `defender.start_scan()` 发现顺序是「先查 `powershell_path()`，
   再校验 `kind` / `path`」——于是 Linux 上传 `kind='turbo'` 只会得到「没有 PowerShell」，
   把调用方真正的参数错误藏了起来。这在 Windows 上也有隐患：万一 PowerShell 缺失
   （精简镜像、被策略禁用），所有参数错误都会退化成同一个环境错误。
5. **确认 skip 机制在 CI 上确实生效**。CI 本来就报 `38 skipped`，其中
   `test_expand_all_drives` 用的正是 `pytest.mark.skipif(os.name != "nt", ...)`——
   同一个机制已有先例，搬成模块级常量即可。

## 三、修复

### 1. 平台门禁（9 + 1 条）

两个测试文件各定义一个模块级常量，把散落的条件统一：

```python
# 内置规则库与它承载的路径全是 Windows 语义（%LOCALAPPDATA%、盘符、反斜杠分隔）。
# POSIX 上没有这些环境变量，expand_path 会原样保留占位符继而判为非绝对路径——
# 那是**正确行为**，不是缺陷。所以这类用例打平台门禁，而不是把期望值改成假路径
# 去迁就平台（那样在 Linux 上通过、却不再验证真实的 Windows 场景）。
_WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="规则库与内置规则均为 Windows 语义（%VAR% / 盘符）"
)
```

**刻意没有把 `%LOCALAPPDATA%` 换成 `/tmp` 之类**。换成跨平台路径能让测试变绿，但
那样 CI 通过的东西已经不是你真正要保证的东西了——真正会出事的误删，发生在
Windows 的真实目录上。

### 2. 校验顺序（`defender.start_scan`）

```python
# 参数校验放在平台检查之前：传错参数是调用方的明确错误，与当前平台有没有
# PowerShell 无关。反过来的话，Linux 上传 kind='turbo' 只会得到「没有
# PowerShell」，把真正的调用错误藏了起来。
if kind not in ("quick", "custom", "full"):
    return None, f"未知扫描类型：{kind}"
if kind == "full" and not allow_full:
    return None, "全盘扫描默认关闭（[maintenance].allow_full_scan），请显式开启或改用快速扫描"
if kind == "custom" and not path:
    return None, "指定路径扫描必须给 path"

exe = psrun.powershell_path()
if exe is None:
    return None, "当前平台没有 PowerShell，无法调用 Defender"
```

### 3. 新增两条**不依赖平台**的用例

只改顺序、靠 CI 在 Linux 上偶然验证是不够的——开发机是 Windows，下次谁把顺序改回去，
本地照样全绿。所以把 `powershell_path` 打成 `None`，在**任何平台**重放「没有 PowerShell」
这个场景：

- `test_param_validation_precedes_platform_check`：参数错时必须是参数错误优先；
- `test_platform_check_still_blocks_valid_request`：参数对但平台不支持时，必须如实说
  没 PowerShell，而不是假装开始扫。

## 四、验证

| 环节 | 结果 |
|---|---|
| 本地（Windows）三个受影响文件 | 83 passed（原 81 + 新增 2） |
| 本地全量 | **4076 passed, 1 skipped** |
| 新增 skip 数 | 0（Windows 上 `os.name == "nt"`，全部照常运行） |
| 预期 CI（Linux） | 10 条转 skip，3 条由顺序修复转绿，0 failed |

## 五、教训

1. **Windows 专属能力的测试必须显式标平台，而不是默认"CI 也是 Windows"。**
   CI 是 `ubuntu-latest`，开发机是 Windows——这个错配从项目第一天就存在，
   只是维护层第一次大规模引入 Windows 语义路径才把它暴露出来。
2. **不要把 fail-closed 的行为当成 bug 修掉。** `expand_path` 遇到展不开的占位符就
   保留原样、让后续校验拒绝——这是刻意的安全设计（换成空串会让路径退化成父目录）。
   所以修复方向是「承认这类用例只在 Windows 有意义」，不是「让它在 Linux 上也展开」。
3. **校验顺序是接口契约的一部分。** 「参数错」和「环境不支持」是两种完全不同的失败，
   前者是调用方该改代码，后者是用户该换环境。环境检查排在前面就抹掉了这个区别。
4. **修顺序要配一条跨平台用例。** 否则这条修复只在 CI 的 Linux 上有观察者，
   本机回归时形同不存在。
