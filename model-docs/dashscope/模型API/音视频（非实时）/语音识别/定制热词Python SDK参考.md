> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# 定制热词Python SDK参考

> 通过Python SDK管理定制热词列表，包括client.audio.vocabularies资源的方法说明与示例代码。

<meta property="og:title" content="定制热词Python SDK参考" />

<strong>用户指南：</strong>[提升识别准确率](/zh/model-studio/improve-asr-accuracy)。热词列表数量上限等使用限制详见[热词限制与计费](/zh/model-studio/improve-asr-accuracy#hw10_limit_h2)。

<Tip>
  新加坡地域的子业务空间暂不支持热词功能。
</Tip>

## 接口地址 <span id="10fe1be1c35re" />

SDK 默认使用<strong>北京地域</strong>的接口地址。如需切换到其他地域，在构造 `dashscope.Client` 时指定 `region` 参数（如新加坡地域为 `ap-southeast-1`）。

<Tabs>
  <Tab title="华北2（北京）">
    `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1`

    调用时请将`{WorkspaceId}`替换为真实的[Workspace ID](/zh/model-studio/regions#h2_migrate_domain)。
  </Tab>

  <Tab title="新加坡">
    `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1`

    调用时请将`{WorkspaceId}`替换为真实的[业务空间ID](/zh/model-studio/regions#h2_migrate_domain)。
  </Tab>
</Tabs>

<Tip>
  阿里云百炼为华北2（北京）、新加坡地域推出了业务空间专属域名，能够为推理请求提供卓越的性能和更高的稳定性，建议迁移至新域名：

  - 华北2（北京）地域：从 `dashscope.aliyuncs.com` 迁移至 `{WorkspaceId}.cn-beijing.maas.aliyuncs.com`
  - 新加坡地域：从 `dashscope-intl.aliyuncs.com` 迁移至 `{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com`

  `{WorkspaceId}`需要替换为真实的[Workspace ID](/zh/model-studio/regions#h2_migrate_domain)。现有域名仍可正常使用。
</Tip>

<Tip>
  阿里云百炼为华北2（北京）、新加坡地域推出了业务空间专属域名，能够为推理请求提供卓越的性能和更高的稳定性，建议迁移至新域名：

  - 华北2（北京）地域：从 `dashscope.aliyuncs.com` 迁移至 `{WorkspaceId}.cn-beijing.maas.aliyuncs.com`
  - 新加坡地域：从 `dashscope-intl.aliyuncs.com` 迁移至 `{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com`

  `{WorkspaceId}`需要替换为真实的[Workspace ID](/zh/model-studio/regions#h2_migrate_domain)。现有域名仍可正常使用。
</Tip>

<strong>切换到新加坡地域</strong>：

```python
import os
import dashscope

# 构造 Client 时指定新加坡地域
client = dashscope.Client(
    api_key=os.getenv('DASHSCOPE_API_KEY'),
    region='ap-southeast-1',
)
```

<strong>注意</strong>：

- 不同地域的 API Key 不同，请确保使用对应地域的 API Key
- 地域配置为 `dashscope.Client` 实例级设置，仅影响该 client 的 API 调用

## client.audio.vocabularies <span id="vocabularyservice" />

<strong>资源入口</strong>：`dashscope.Client` 实例的 `client.audio.vocabularies` 资源

<strong>功能</strong>：管理热词列表的生命周期（创建、查询、更新、删除）

### 构造方法 <span id="6f78ee5f05mi8" />

```python
import os
import dashscope

client = dashscope.Client(
    api_key=os.getenv('DASHSCOPE_API_KEY'),
    workspace='{WorkspaceId}',
    region='cn-beijing',
)
```

未传 `api_key` 时，SDK 自动读取 `DASHSCOPE_API_KEY` 环境变量的值。

### create() - 创建热词列表 <span id="13e9d780falfk" />

<strong>方法签名</strong>：

```python
def create(
    self,
    *,
    target_model: str,
    prefix: str,
    vocabulary: List[dict]) -> Vocabulary
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.21%" }} /><col style={{ width: "16.95%" }} /><col style={{ width: "9.77%" }} /><col style={{ width: "57.07%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>target\_model</p></td><td><p>str</p></td><td><p>是</p></td><td><div><p>使用热词列表的语音识别模型，必须与后续调用语音识别接口时使用的模型一致。</p></div></td></tr><tr><td><p>prefix</p></td><td><p>str</p></td><td><p>是</p></td><td><div><p>热词列表自定义前缀，仅允许数字和小写字母，长度不超过10个字符。</p></div></td></tr><tr><td><p>vocabulary</p></td><td><p>List\[dict]</p></td><td><p>是</p></td><td><p>热词列表，每个dict包含 text、weight、lang 等字段。</p> <p>详情请参见<a href="/zh/model-studio/vocabulary-python-sdk">热词字典结构</a>。</p></td></tr></tbody></table>

<strong>返回值</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.17%" }} /><col style={{ width: "83.83%" }} /></colgroup><thead><tr><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>Vocabulary</p></td><td><p>热词列表对象，通过 <code>vocabulary\_id</code> 字段获取热词列表ID。</p></td></tr></tbody></table>

### list() - 批量查询热词列表 <span id="652e9dddb48ia" />

对应 HTTP API 的 `action: list_vocabulary`。

<strong>方法签名</strong>：

```python
def list(
    self,
    *,
    prefix: str = None,
    page_index: int = 0,
    page_size: int = 10) -> VocabularyList
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.04%" }} /><col style={{ width: "17.11%" }} /><col style={{ width: "9.93%" }} /><col style={{ width: "56.92%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>prefix</p></td><td><p>str</p></td><td><p>否</p></td><td><div><p>热词列表自定义前缀，如果设定则只返回指定前缀的热词列表。</p></div></td></tr><tr><td><p>page\_index</p></td><td><p>int</p></td><td><p>否</p></td><td><div><p>页码索引，从0开始计数。</p> <p>默认值：0。</p></div></td></tr><tr><td><p>page\_size</p></td><td><p>int</p></td><td><p>否</p></td><td><div><p>每页包含数据条数。</p> <p>默认值：10。</p></div></td></tr></tbody></table>

<strong>返回值</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.07%" }} /><col style={{ width: "83.93%" }} /></colgroup><thead><tr><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>VocabularyList</p></td><td><p>热词列表分页对象，<code>vocabularies</code> 字段为热词列表信息数组，每个dict包含 vocabulary\_id、gmt\_create、gmt\_modified、status。</p></td></tr></tbody></table>

<strong>返回对象字段</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.07%" }} /><col style={{ width: "17.05%" }} /><col style={{ width: "66.88%" }} /></colgroup><thead><tr><th><p><strong>字段</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabulary\_id</p></td><td><p>str</p></td><td><div><p>热词列表ID。</p></div></td></tr><tr><td><p>gmt\_create</p></td><td><p>str</p></td><td><div><p>创建时间。</p></div></td></tr><tr><td><p>gmt\_modified</p></td><td><p>str</p></td><td><div><p>修改时间。</p></div></td></tr><tr><td><p>status</p></td><td><p>str</p></td><td><div><p>状态：</p> <ul><li><p>OK：可调用</p></li> <li><p>UNDEPLOYED：不可调用。</p></li></ul></div></td></tr></tbody></table>

---

### get() - 查询热词列表 <span id="3888358ba66rq" />

对应 1.x SDK 的 `query_vocabulary()`。

<strong>方法签名</strong>：

```python
def get(
    self,
    vocabulary_id: str) -> Vocabulary
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.07%" }} /><col style={{ width: "17%" }} /><col style={{ width: "10.85%" }} /><col style={{ width: "56.08%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabulary\_id</p></td><td><p>str</p></td><td><p>是</p></td><td><p>需要查询的热词列表ID。</p></td></tr></tbody></table>

<strong>返回值</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.07%" }} /><col style={{ width: "83.93%" }} /></colgroup><thead><tr><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>Vocabulary</p></td><td><p>热词列表详细信息对象，包含 vocabulary、target\_model、gmt\_create、gmt\_modified、status。</p></td></tr></tbody></table>

<strong>返回对象字段</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.07%" }} /><col style={{ width: "17.14%" }} /><col style={{ width: "66.79%" }} /></colgroup><thead><tr><th><p><strong>字段</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabulary</p></td><td><p>List\[dict]</p></td><td><p>热词列表内容。</p></td></tr><tr><td><p>target\_model</p></td><td><p>str</p></td><td><div><p>使用热词列表的语音识别模型，必须与后续调用语音识别接口时使用的模型一致。</p></div></td></tr><tr><td><p>gmt\_create</p></td><td><p>str</p></td><td><div><p>创建时间。</p></div></td></tr><tr><td><p>gmt\_modified</p></td><td><p>str</p></td><td><div><p>修改时间。</p></div></td></tr><tr><td><p>status</p></td><td><p>str</p></td><td><div><p>状态：</p> <ul><li><p>OK：可调用</p></li> <li><p>UNDEPLOYED：不可调用。</p></li></ul></div></td></tr></tbody></table>

### update() - 更新热词列表 <span id="bfb9dde2336p2" />

<strong>方法签名</strong>：

```python
def update(
    self,
    vocabulary_id: str,
    *,
    vocabulary: List[dict]) -> JobStatus
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.07%" }} /><col style={{ width: "17.14%" }} /><col style={{ width: "10.39%" }} /><col style={{ width: "56.4%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabulary\_id</p></td><td><p>str</p></td><td><p>是</p></td><td><div><p>需要更新的热词列表ID。</p></div></td></tr><tr><td><p>vocabulary</p></td><td><p>List\[dict]</p></td><td><p>是</p></td><td><div><p>新的热词列表，将完全替换原有内容。</p></div></td></tr></tbody></table>

<strong>返回值</strong>：JobStatus 对象，status 字段为操作状态。

### delete() - 删除热词列表 <span id="284b1ecc29e94" />

<strong>方法签名</strong>：

```python
def delete(
    self,
    vocabulary_id: str) -> JobStatus
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "15.98%" }} /><col style={{ width: "17.23%" }} /><col style={{ width: "10.34%" }} /><col style={{ width: "56.45%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabulary\_id</p></td><td><p>str</p></td><td><p>是</p></td><td><div><p>需要删除的热词列表ID。</p></div></td></tr></tbody></table>

<strong>返回值</strong>：JobStatus 对象，status 字段为操作状态。

## 热词字典结构 <span id="e69652f2feiai" />

<strong>用于</strong>`vocabulary`<strong>参数的字典定义</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.37%" }} /><col style={{ width: "17.01%" }} /><col style={{ width: "16.09%" }} /><col style={{ width: "50.53%" }} /></colgroup><thead><tr><th><p><strong>字段</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>text</p></td><td><p>str</p></td><td><p>是</p></td><td><div><p><span>热词文本。</span></p> <p>热词文本的语言必须在所选模型的支持范围内，不同模型支持的语言各不相同。</p> <p>热词用于提升识别的准确率，请使用实际词语而非任意字符组合。</p> <p><span>长度限制：含非 ASCII 字符时不超过 15 个字符；纯 ASCII 时空格分隔片段不超过 7 个</span>。</p></div></td></tr><tr><td><p>weight</p></td><td><p>int</p></td><td><p>是</p></td><td><div><p>热词权重。常用值：4。</p> <p>取值范围：\[1, 5]。</p> <p>如果效果不明显，可以适当增加权重，但权重过大可能产生负面效果，导致其他词语识别不准确。</p></div></td></tr><tr><td><p>lang</p></td><td><p>str</p></td><td><p>否</p></td><td><div><p>待识别音频的语言代码。设置后，系统将对指定语种进行热词识别增强。如果无法提前确定语种，可不设置，模型会自动识别语种。</p> <p>取值范围（因模型而异）：</p> <ul><li><p>Paraformer：</p> <ul><li><p>zh: 中文</p></li> <li><p>en: 英文</p></li> <li><p>ja: 日语</p></li> <li><p>yue: 粤语</p></li> <li><p>ko: 韩语</p></li> <li><p>de：德语</p></li> <li><p>fr：法语</p></li> <li><p>ru：俄语</p></li></ul></li> <li><p>Fun-ASR：</p> <ul><li><p>zh: 中文</p></li> <li><p>en: 英文</p></li> <li><p>ja: 日语</p></li></ul></li></ul></div></td></tr></tbody></table>

## 示例代码 <span id="python-sdk-examples-title" />

### 创建热词列表 <span id="python-example-create-title" />

```python expandable
import os
import dashscope

# 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
# 未配置环境变量时，将下行替换为：api_key="sk-xxx"
client = dashscope.Client(
    api_key=os.environ.get('DASHSCOPE_API_KEY'),
    # 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
    workspace='{WorkspaceId}',
    region='cn-beijing',
)

prefix = 'testpfx'
target_model = "fun-asr"

my_vocabulary = [
    {"text": "赛德克巴莱", "weight": 4}
]

# 创建热词
vocabulary = client.audio.vocabularies.create(
    prefix=prefix,
    target_model=target_model,
    vocabulary=my_vocabulary)

print(f"热词列表ID为：{vocabulary.vocabulary_id}")
```

### 批量查询热词列表 <span id="python-example-list-title" />

```python
import json
import os
import dashscope

# 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
# 未配置环境变量时，将下行替换为：api_key="sk-xxx"
client = dashscope.Client(
    api_key=os.environ.get('DASHSCOPE_API_KEY'),
    # 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
    workspace='{WorkspaceId}',
    region='cn-beijing',
)

result = client.audio.vocabularies.list()
print(f"热词列表：{json.dumps(result.vocabularies)}")
```

### 查询热词列表 <span id="python-example-query-title" />

```python
import json
import os
import dashscope

# 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
# 未配置环境变量时，将下行替换为：api_key="sk-xxx"
client = dashscope.Client(
    api_key=os.environ.get('DASHSCOPE_API_KEY'),
    # 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
    workspace='{WorkspaceId}',
    region='cn-beijing',
)

# 查询时替换为实际的热词列表ID
vocabulary = client.audio.vocabularies.get("vocab-testpfx-xxx")
print(f"热词列表：{json.dumps(vocabulary.vocabulary)}")
```

### 更新热词列表 <span id="python-example-update-title" />

```python
import os
import dashscope

# 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
# 未配置环境变量时，将下行替换为：api_key="sk-xxx"
client = dashscope.Client(
    api_key=os.environ.get('DASHSCOPE_API_KEY'),
    # 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
    workspace='{WorkspaceId}',
    region='cn-beijing',
)

my_vocabulary = [
    {"text": "赛德克巴莱", "weight": 4, "lang": "zh"}
]
# 替换为实际的热词列表ID
result = client.audio.vocabularies.update("vocab-testpfx-xxx", vocabulary=my_vocabulary)
print(f"更新状态：{result.status}")
```

### 删除热词列表 <span id="python-example-delete-title" />

```python
import os
import dashscope

# 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
# 未配置环境变量时，将下行替换为：api_key="sk-xxx"
client = dashscope.Client(
    api_key=os.environ.get('DASHSCOPE_API_KEY'),
    # 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
    workspace='{WorkspaceId}',
    region='cn-beijing',
)

# 替换为实际的热词表ID
result = client.audio.vocabularies.delete("vocab-testpfx-xxxx")
print(f"删除状态：{result.status}")
```
