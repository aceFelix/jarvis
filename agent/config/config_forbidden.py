"""禁改清单（护栏本身）：为什么不能改 + 那该怎么改。

## 为什么单独一个模块

两个原因：

1. `config_specs.py` 越过 900 行门禁（实测 963），这批记录又长又不会天天动；
2. 它回答的是另一个问题——真源表说"哪些能改"，这里说"哪些**不许**改、以及该由谁改"。
   混在一起读，很容易把"没有改动路径"误当成"有但没写"。

## 为什么"理由"和"改法"放在同一条记录里

分处两地必然漂移。三处渲染（ConfigSet 拒绝文案 / ConfigExplain / 提示词速查）都从这一条
派生，改一处则三处同步。2026-11 实测教训：只说"不许改"时，模型会去猜替代路径，猜出过
不存在的 Shift+Tab 快捷键，绕了 6 步才回来。

@author aceFelix
"""

from __future__ import annotations

#: 每条记录 = ``(为什么不能改, 那该怎么改)``。**两句话都要自己站得住**：
#: ``forbidden_reason()``/``forbidden_fix()`` 一次只返回一条，模型与用户也是分开看的，
#: 写成"同上：…"到了那种上下文就是废话（有测试专门钉住）。
#:
#: 为什么把"怎么改"并入同一条记录，而不是另开一张表：那张新表必然与这张漂移——
#: 同一件事的"理由"和"改法"一旦分处两地，改一处忘一处就是必然。三处渲染
#: （ConfigSet 拒绝文案 / ConfigExplain / 提示词速查）都从这一条派生。
FORBIDDEN_SPECS: dict[str, tuple[str, str]] = {
    "sandbox_enabled": (
        "沙箱是权限护栏本身，模型改它等于给自己提权",
        "在 ~/.jarvis/settings.toml 的 [sandbox] 段改 enabled；关掉前先想清楚是否只在可信环境用",
    ),
    "sandbox_auto_allow_medium": (
        "决定中等风险命令能否不经人确认就执行，模型改它等于给自己提权",
        "手改 [sandbox] auto_allow_medium；或运行时切 /mode accept_edits 只放行文件编辑",
    ),
    "sandbox_excluded_commands": (
        "豁免名单能让指定命令绕过沙箱，模型改它等于给自己开后门",
        "手改 [sandbox] excluded_commands（数组）；临时需要放行就用 /mode yolo 而不是加白名单",
    ),
    "log_events": (
        "本地留痕是感知行为的唯一审计线，模型关它等于让自己的采集不留证据",
        "手改 [percept] log_events；只在确实不需要审计的机器上关",
    ),
    "api_key": (
        "凭据只能由人填（/init 或环境变量），写进配置文件会随备份外洩",
        "用 /init 引导填写，或设环境变量 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY",
    ),
    "email_smtp_password": (
        "邮箱密码是凭据，只能由人填，模型不碰",
        "手改 [email] smtp_password，或把密码放进系统 keyring",
    ),
    "email_imap_password": (
        "邮箱密码是凭据，只能由人填，模型不碰",
        "手改 [email] imap_password，或把密码放进系统 keyring",
    ),
    # 维护层的两道许可线：它们定义“自动清理能碰到什么”，等价于沙箱的豁免名单
    "auto_clean_max_risk": (
        "这决定自动清理能自动到多高的风险档，是许可边界本身；模型改它等于给自己放宽删文件的范围",
        "手改 [clean] auto_clean_max_risk；越保守越安全，看不懂就保持默认",
    ),
    "rules_file": (
        "规则文件决定“清理哪些目录”，换一个文件就等于换一套靶子；模型改它等于给自己扩容",
        "手改 [clean] rules_file；想换清理范围请改那个 YAML 的内容，而不是指向别的文件",
    ),
    # 2026-11「yolo 全放开」的锁死清单：这些不是"某个配置值"，而是**护栏本身**。
    # 用户点头能授权"做什么"，但不该授权"拆掉监督机制"——当任务卡住时，模型会合理
    # 地请求放宽自己，一轮点头就形成自我授权闭环。故这些项连确认都不收。
    "permissions_file": (
        "换一个权限规则文件就等于换掉整套护栏；模型改它等于给自己重写监督规则",
        "手改 ~/.jarvis/settings.toml 的 permissions_file；改完用 /doctor 确认规则加载成功",
    ),
    "permission_mode": (
        "模式本身就是授权来源，模型改它等于给自己发授权（含把自己锁死在 yolo）",
        "运行时用 **/mode default|plan|accept_edits|yolo** 切换（立刻生效）；"
        "要持久就手改 settings.toml 顶端的 permission_mode",
    ),
    "permissions_yolo_guard_roots": (
        "yolo 的受保护根；模型改它等于拆掉自己的边界",
        "手改 settings.toml 顶端的 permissions_yolo_guard_roots（TOML 数组）；"
        "写 [] 表示不再有受保护根（会真的生效，不是回退默认）",
    ),
    "permissions_yolo_hard_deny": (
        "yolo 的硬禁止清单；模型改它等于删掉仅存的不可协商项",
        "手改 settings.toml 顶端的 permissions_yolo_hard_deny（TOML 数组）；建议保留默认的 ~/.ssh 等",
    ),
    "bridge_token": (
        "跨设备桥接令牌是凭据，模型不碰",
        "由 /connect-phone 生成并写入，或手改 settings.toml 的 bridge_token",
    ),
    "dashscope_api_key": (
        "凭据只能由人填（/init 或环境变量），写进配置文件会随备份外洩",
        "用 /init 填写，或设环境变量 DASHSCOPE_API_KEY",
    ),
    "default_api_key": (
        "凭据只能由人填（/init 或环境变量），写进配置文件会随备份外洩",
        "手改 models.toml 顶端的 default_api_key，或用 /init 重新引导",
    ),
    # 真源表里 tier=forbidden 的项也必须在字典里有理由（测试 test_every_forbidden_spec_carries_a_reason）。
    # 两处都写不是冗余：tier 决定"不进模型可改清单"，字典决定"给得出为什么"和"怎么改".
    "repair_disabled_actions": (
        "它记录哪些修复动作被用户关掉；模型改它等于把自己被禁掉的能力重新打开",
        "用 /repair enable <动作> 显式打开，或在 settings.toml 的 [repair] 段手改",
    ),
}

#: 键前缀 → (理由, 改法)，覆盖 api_key_* / sandbox_* 这类成族字段
FORBIDDEN_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("api_key", "凭据只能由人填（/init 或环境变量），模型不碰",
     "用 /init 或环境变量填，不要写进配置文件"),
    ("sandbox_", "沙箱是权限护栏本身，模型改它等于给自己提权",
     "手改 settings.toml 的 [sandbox] 段；模型侧改用 /mode 调整"),
    # 成族覆盖：permissions_* 全是护栏本身（2026-11 锁死清单）
    ("permissions_", "权限护栏本身，模型改它等于给自己重写监督规则",
     "手改 settings.toml 顶端对应的 permissions_* 项；运行时权限用 /mode"),
)


def forbidden_reason(key: str) -> str:
    """该键是否被禁止改：返回拒绝理由（空串 = 不是禁止项）。"""
    k = str(key or "").strip()
    if k in FORBIDDEN_SPECS:
        return FORBIDDEN_SPECS[k][0]
    for prefix, reason, _fix in FORBIDDEN_PREFIXES:
        if k.startswith(prefix):
            return reason
    return ""


def forbidden_fix(key: str) -> str:
    """禁改项的**正确改法**（空串 = 没有专门指引）。

    与 :func:`forbidden_reason` 同源（同一条记录的两半）。只说"不行"会让模型去猜
    替代路径（实测它猜出过不存在的 Shift+Tab 快捷键）；把改法一起给出，它就不必猜。
    """
    k = str(key or "").strip()
    if k in FORBIDDEN_SPECS:
        return FORBIDDEN_SPECS[k][1]
    for prefix, _reason, fix in FORBIDDEN_PREFIXES:
        if k.startswith(prefix):
            return fix
    return ""
