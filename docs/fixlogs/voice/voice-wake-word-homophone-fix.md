# 唤醒词写死 + 喊了没反应：配置化与拼音同音容错

> 日期：2026-10
> 触发：「写死的确实有问题，还有就是语音的识别有点不准，有时候喊贾维斯他没反应，
> 这种有啥方法解决吗？比如我说 jarvis、贾维斯在吗、贾维斯或者别的听起来像贾维斯的话，
> 他应该都能识别出来是叫他才对」
> 改动：`agent/voice/wake_words.py`（新建）、`voice_loop.py`、`voice_config.py` 注释、
> `barge_in.py`、`stt/qwen.py`、`stt/__init__.py`、`config/settings.py`、
> `config/settings_domains.py`、`pyproject.toml`
> 测试：`tests/voice/test_wake_words.py`（新建，47 例）、
> `tests/voice/test_stt_qwen_session.py`（+4 例）

---

## 1. 问题拆成两半

用户其实说了两件事，它们正交，必须分开解决：

| 说法 | 真实诉求 | 类别 |
|---|---|---|
| 「写死的确实有问题」 | 想自己决定怎么称呼它，不想改代码 | **可配置性** |
| 「喊贾维斯没反应」 | ASR 听成近音字时也算叫到 | **识别鲁棒性** |

先查清现状：

```
agent/voice/voice_config.py:22   _WAKE_WORDS = ("贾维斯", "贾维思", ...)   ← 模块级常量
agent/voice/voice_loop.py:648    if text and _contains_any(text, _WAKE_WORDS):
```

`_contains_any` 就是 `w.lower() in t.lower()` ——**纯字面子串**。所以：

- 「贾维斯在吗」→ 含「贾维斯」→ ✅ 早就能唤醒（这条用户以为不行，其实行）
- 「加维思」/「贾伟斯」/「假维斯」→ **不含**「贾维斯」→ ❌ 永远不唤醒

而且配置里搜不到任何 wake 相关字段（`settings.py` / `config_specs.py` 全无），
确实是写死的。

---

## 2. 关键决策：拼音归一化，不是编辑距离

「听起来像」有两条技术路线：

| 方案 | 「加维思」 | 「贾伟斯」 | 误报风险 | 依赖 |
|---|---|---|---|---|
| 字面相似度（`difflib.SequenceMatcher`） | 0.667 | 0.667 | **高**（任何「贾X斯」三字词都过线） | 无 |
| **拼音归一化** | `jiaweisi` ✅ | `jiaweisi` ✅ | 低（串够长） | `pypinyin` |

实测（探针跑完删了，结论已固化进单测）：

```
贾维斯 → ['jia','wei','si']
加维思 → ['jia','wei','si']
贾伟斯 → ['jia','wei','si']
```

**同音字归一化后完全相等**，不是「有点像」而是「就是一个串」。13 个近音写法
全部命中；11 条日常语句零误报。

### 2.1 两字词的陷阱（这次最值得记住的一条）

内置词表里有「贾维」这个两字变体。它的拼音串只有 `jiawei`，而
`jiawei` 是这些**正常语句**的子串：

```
甲维集团   → jiaweijituan
加个微信   → jiaweixin
家伟说...  → jiaweishuotayaolai
```

放开就会在用户聊工作时被误唤醒。所以加一道门槛：

```python
_MIN_HAN_FOR_PINYIN = 3     # 拼音层只对 ≥3 个汉字的词生效
```

**短词并没有被禁掉**——它仍走字面匹配那条路（用户真说了「贾维」两个字，
`"贾维" in "贾维"` 为真，照样唤醒）。只是不参与拼音层。

这条边界写成了单测 `test_short_word_is_not_pinyin_matched`：

```python
assert ww.matches("甲维集团", ("贾维",)) is False   # 短词不误报
assert ww.matches("贾维", ("贾维",)) is True       # 字面仍然有效
```

---

## 3. 治本那一层：唤醒词当 ASR 热词

拼音匹配是**兜底**（识别错了才补救）。更治本的是让识别模型一开始就输出对的词。

探 `dashscope` SDK 源码发现 `TranscriptionParams` 有个一直没人用的字段：

```python
# dashscope/audio/qwen_omni/omni_realtime.py
if transcription_params.corpus_text is not None:
    transcription_params.corpus = {"text": transcription_params.corpus_text}
if transcription_params.corpus is not None:
    self.config["input_audio_transcription"].update(
        {"corpus": transcription_params.corpus},
    )
```

即最终报文是 `session.update.input_audio_transcription.corpus.text`。

### 3.1 先实测它认不认，再动手改

官方文档我只查到「上下文增强」有这回事、`corpus.text` 是热词文本，但没找到
`qwen3-asr-flash-realtime` 明确支持该字段的段落。**格式猜错会把整个 STT 弄挂**，
所以先建连实测（不录音，只看服务端有没有回 `error` 事件）：

```
===== A. 不带热词（现状） =====
update_session 调用未抛异常
input_audio_transcription = {"language": "zh"}
服务端事件数 = 2 | error = None

===== B. 带热词 corpus_text（拟改造） =====
update_session 调用未抛异常
input_audio_transcription = {"language": "zh", "corpus": {"text": "贾维斯、加维思、贾伟斯、jarvis"}}
服务端事件数 = 2 | error = None
```

**两组都是 2 个事件、零 error** → 服务端接受该字段。这才敢接进去。

### 3.2 只送第一个词

`voice_loop` 里传的是 `build_asr_hotwords(wake_words[:1])`，不是整个词表：

> 把 7 个谐音变体全塞给模型，反而会让它以为这些写法都对，削弱治本效果。
> 谐音鲁棒性交给拼音层。

热词为空则**不下发该字段**，报文与改造前完全一致（单测
`test_no_hotwords_keeps_payload_unchanged` 锁住这条）。

---

## 4. 改了哪些文件

### 新建 `agent/voice/wake_words.py`（188 行）

单一真源，四个出口：

| 函数 | 职责 |
|---|---|
| `matches(text, words)` | 先字面再拼音的判定入口 |
| `resolve_wake_words(settings)` | 读 `[voice] wake_words`，空则回退内置默认 |
| `resolve_exit_words` / `resolve_interrupt_words` | 同理，退下词/打断词 |
| `build_asr_hotwords(words)` | 生成 `corpus.text` 语料（去重保序） |

`_pinyin()` 挂了 `lru_cache(maxsize=512)`：待机每几秒一轮，ASR 文本会反复出现，
缓存直接省掉重复转换。

### `voice_config.py`

**没删任何常量**——它们从「唯一定义」降级为「内置默认值」，`wake_words.py`
从那里 import。老配置、老测试零影响。

### `stt/qwen.py` + `stt/__init__.py`

`QwenASR.__init__` 加 `hotwords: str = ""`；`listen()` 里把原来内联的
`TranscriptionParams(...)` 提出来，非空才赋 `corpus_text`。
`create_stt()` 同步加 `hotwords` 透传。

### `voice_loop.py`

```python
wake_words = resolve_wake_words(settings)        # 整会话解析一次
asr_hotwords = build_asr_hotwords(wake_words[:1])
wake_hint = wake_words[0] if wake_words else "贾维斯"
...
if text and matches(text, wake_words):           # 原来是 _contains_any + 常量
```

顺带把两处写死的「贾维斯」改成 `wake_hint`：启动横幅
（`说「X」唤醒`）和待机提示（`💤 待机中，说「X」唤醒我`）。用户换了称呼，
界面得跟着变，否则自相矛盾。

### `config/settings.py` + `settings_domains.py`

```python
voice_wake_words: tuple[str, ...] = ()
voice_exit_words: tuple[str, ...] = ()
voice_interrupt_words: tuple[str, ...] = ()
```

```python
("voice", (
    ("barge_in", "voice_barge_in"),
    ("barge_in_key", "voice_barge_in_key"),
    ("wake_words", "voice_wake_words"),
    ("exit_words", "voice_exit_words"),
    ("interrupt_words", "voice_interrupt_words"),
)),
```

留空 = 回退内置默认，**老配置文件一行都不用改**。

### `pyproject.toml`

`pypinyin>=0.53.0` 进 `[voice]` 可选依赖（纯 Python，无 C 扩展）。

---

## 5. 降级路径

`pypinyin` 是可选依赖，**不能因为它缺失就把语音弄挂**：

```python
try:
    from pypinyin import Style, lazy_pinyin
except Exception:
    return ""        # 拼音层整体停用
```

`matches()` 拿到空串就跳过拼音层 → 退化为「只做字面匹配」。覆盖变窄，但绝不报错。
单测 `TestPinyinUnavailable` 用 `sys.modules["pypinyin"] = None` 把这条路走了一遍。

---

## 6. 踩的坑

### 6.1 测试自己造了个死循环（折腾了半小时）

集成用例这么写：

```python
stop = threading.Event()
stt = _ScriptedSTT(["退下吧"], stop)      # STT 持有一个 stop
events = _run_voice_loop(monkeypatch, settings, stt)

def _run_voice_loop(...):
    stop = threading.Event()               # ← 循环用的是**另一个** stop
    asyncio.run(vl.voice_loop(..., stop_event=stop))
```

两个不同的 `Event` 对象。STT 脚本跑完后置位自己那个，`voice_loop` 那个永远不置位
→ 待机轮询无休止地录音 → `asyncio.to_thread` 不断产生工作线程 → pytest 挂死。

定位过程也踩了坑：`pytest -q | Select-Object -Last N` 会**缓冲到进程结束**才输出，
所以什么都看不到。改用 `Start-Process -RedirectStandardOutput` 写文件 +
`-o faulthandler_timeout=20` 才拿到主线程栈（`asyncio run_forever` 空转）。

修法：`_run_voice_loop` 自己建 `stop`、自己建 STT，让两者共用同一个对象，
调用方只传脚本列表：

```python
def _run_voice_loop(monkeypatch, settings, script: list[str]) -> CollectingVoiceEvents:
    stop = threading.Event()
    stt = _ScriptedSTT(script, stop)       # 同一个 stop
```

### 6.2 「新棋舞」不是「星期五」的同音

我拍脑袋写了个近音样本，结果 `xin qi wu` ≠ `xing qi wu`。换成 `星其舞`
（`xing qi wu`）才对。**拼音是精确的，不能靠感觉**——这类断言必须先验证
再固化。

### 6.3 断言写太宽

```python
assert not any("唤醒" in str(p) for _, p in events.records)   # ❌
```

启动横幅里本来就有「说「贾维斯」唤醒 · 打断/退出由宿主控制」，这条断言永远失败。
改成只匹配真正的唤醒事件文案「回到对话模式」。

### 6.4 新文件未入 git 会撞文档链检查

`tests/test_md_link_checker.py::test_repo_docs_links_are_all_git_tracked`
要求文档里 `file://` 链接的目标必须已被 git 跟踪。新加的
`agent/voice/wake_words.py` 在文档里带了链接 → 未 `git add` 就红。
这不是 bug，是仓库自己立的门禁（防 CI 上链接指向不存在文件）。

---

## 7. 验证

| 项 | 结果 |
|---|---|
| `pytest tests/voice` | **322 passed** |
| `pytest tests`（全量） | **3786 passed**（起点 3733，本轮 +53） |
| 近音字命中 | 13/13 |
| 日常语句误报 | 0/11 |
| 服务端接受 `corpus.text` | 实测零 `error` 事件 |
| 单文件行数 | `wake_words.py` 188、`voice_loop.py` 719、`test_wake_words.py` 356，均 < 800 |
| 探针清理 | `.git/_probe*` 空 |

---

## 8. 教训

1. **「听起来像」要用客观标尺。** 编辑距离在短中文串上区分度太差（「贾伟斯」和
   「甲维集团」一样像），拼音归一化能把同音字压成**完全相等**。选对表示法，
   问题从「调阈值」变成「判等」。
2. **模糊匹配必须配一道长度门槛。** 越短的同音串越是正常语句的子串。
   门槛要按「会不会误伤正常说话」定，不是按「想覆盖多少写法」定。
3. **厂商字段先实测再接。** `corpus.text` 我只查到「有此功能」，没查到该模型
   明确支持。建连发一次 `session.update`、看服务端有没有 `error`，成本两分钟，
   比上线后 STT 全挂强得多。
4. **兜底和治本要分开做。** 热词让模型别听错（治本），拼音匹配在听错之后
   还能认（兜底）。只做前者，模型偶尔仍会错；只做后者，错得更多但总能救回来。
5. **配置化不能顺手改默认行为。** 三张词表全用「空 = 内置默认」，老配置文件
   一行不用动。默认值从「唯一定义」降级为「回退值」，是最小侵入的改法。
6. **测试替身的共享状态要显式。** 两个 `threading.Event` 长得一样、名字一样、
   行为一样——但它们是两个对象。凡是「A 置位、B 轮询」的协作，测试里必须
   确保传的是**同一个**。
7. **输出被管道缓冲时换重定向。** `pytest | Select-Object` 看不到中间输出，
   挂死就没法定位。写文件 + `faulthandler_timeout` 才能拿到栈。
