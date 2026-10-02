# 终端剪贴板图片改为 Ctrl+V 显式粘贴：下线"回车自动扫剪贴板"

- 日期：2026-10-02
- 仓库：jarvis
- 作者：aceFelix

## 一、现象

终端 jarvis 每次按回车（空行提交或发消息）都会读系统剪贴板，只要剪贴板里
残留过图片就被自动附加：「✅ 检测到剪贴板图片，已添加到待发送列表」反复出现，
普通提问被误带图片。

## 二、排查

自动读剪贴板有两处（均在 REPL 链路）：

1. `main.repl()` 空行提交分支：`_load_image_from_clipboard()` 检测到新图 →
   进 `pending_images`（支持"复制图片 → 直接回车"贴图）；
2. `core/images._auto_attach_clipboard_image`：每次发普通消息前再扫一遍剪贴板，
   有新图自动附加到本条消息。

显式入口已有 `/paste` `/p` `/clipboard`（media_commands），但**没有 Ctrl+V 键绑定**
——用户记忆里的 Ctrl+V 实际是 `/paste` 命令与桌面壳输入框的粘贴事件。

## 三、根因

剪贴板内容是系统级长期残留状态，"每次输入都扫一遍并附带"把残留当成了用户
意图；哈希去重只能挡同一张图重复附加，挡不住"本不想贴图"的场景。

## 四、修复

| # | 改动 | 文件 |
|---|---|---|
| 1 | 删空行提交的剪贴板检测分支（空回车=忽略） | `agent/main.py` |
| 2 | 发消息前不再自动附加，只取显式积累的 `pending_images`（pop） | `agent/main.py` |
| 3 | 删 `_auto_attach_clipboard_image`，新增 `paste_clipboard_image(ctx, ui)`：读剪贴板图加入待发送列表、列表内 MD5 去重、无图警告 | `agent/core/images.py` |
| 4 | prompt_toolkit 新增 `c-v` 键绑定，回调经模块级 `_PASTE_IMAGE_CALLBACK` 注入（`RichCLI.set_paste_image_handler`），UI 层不反向依赖 core 层 | `agent/ui/cli.py` |
| 5 | REPL 建好 ctx 后注入回调 `ui.set_paste_image_handler(lambda: paste_clipboard_image(ctx, ui))` | `agent/main.py` |
| 6 | `/help`、补全描述提示"同 Ctrl+V" | `core_commands.py`、`cli.py` |

行为变化：贴图只有三个显式入口——**Ctrl+V**、`/paste`（`/p` `/clipboard`）、
`/image <路径>`；回车与普通发消息永不碰剪贴板。

## 五、验证

- `uv run pytest tests/core/test_images.py tests/ui -q` → 185 passed
- 全量 `uv run pytest tests -q` → 2285 passed
- 测试改写：`TestAutoAttachClipboardImage`（5 条自动附加语义）→
  `TestPasteClipboardImage`（4 条显式贴图语义：无图警告 / 加入待发 /
  同图去重 / 多图叠加计数）
- 人工走查：重启终端 jarvis → 剪贴板留有图片 → 连续回车/发消息不再出现
  「检测到剪贴板图片」；按 Ctrl+V 提示「✅ 已添加剪贴板图片（待发送 1 张）」

## 六、涉及文件

- `agent/main.py`、`agent/core/images.py`、`agent/ui/cli.py`、
  `agent/commands/handlers/core_commands.py`
- 测试：`tests/core/test_images.py`
- 文档：`README.md`、`README.en.md`、`USER_GUIDE.md`、
  `docs/test/TEST_CHECKLIST.md`、`docs/roadmap/jarvis-upgrade-roadmap.md`

## 七、经验

- **系统级残留状态（剪贴板/最近文件/光标位置）不能当用户意图消费**：
  附加类副作用必须由显式交互（按键/命令）触发；
- UI 层键绑定需要业务回调时用模块级 handler 注入，避免 `ui → core` 反向依赖；
- prompt_toolkit 在 Windows win32 控制台下 Ctrl+字母会映射为 `Keys.ControlX`
  （Ctrl+V → `c-v`），与 ConPTY/常规终端一致，直接 add_binding 即可。
