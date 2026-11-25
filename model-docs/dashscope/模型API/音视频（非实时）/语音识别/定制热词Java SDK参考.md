> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# 定制热词Java SDK参考

> 通过Java SDK管理定制热词列表，包括VocabularyService类的方法说明与示例代码。

<meta property="og:title" content="定制热词Java SDK参考" />

<strong>用户指南：</strong>[提升识别准确率](/zh/model-studio/improve-asr-accuracy)。热词列表数量上限等使用限制详见[热词限制与计费](/zh/model-studio/improve-asr-accuracy#hw10_limit_h2)。

<Tip>
  新加坡地域的子业务空间暂不支持热词功能。
</Tip>

## 接口地址 <span id="10fe1be1c35re" />

SDK 默认使用<strong>北京地域</strong>的接口地址。如需切换到其他地域，需在初始化前修改 `Constants.baseHttpApiUrl`。

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

<strong>切换到新加坡地域</strong>：

```java
import com.alibaba.dashscope.utils.Constants;

// 在代码开头设置
Constants.baseHttpApiUrl = "https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1";
```

<strong>注意</strong>：

- 不同地域的 API Key 不同，请确保使用对应地域的 API Key
- 地域配置为全局设置，影响所有 DashScope SDK 的 API 调用

## VocabularyService <span id="vocabularyservice" />

<strong>包路径</strong>：`com.alibaba.dashscope.audio.asr.vocabulary.VocabularyService`

<strong>功能</strong>：管理热词列表的生命周期（创建、查询、更新、删除）

### 构造方法 <span id="cbdefbdc4almq" />

```java
public VocabularyService(String apiKey)
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.3%" }} /><col style={{ width: "16.72%" }} /><col style={{ width: "66.98%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>apiKey</p></td><td><p>String</p></td><td><p>DashScope API Key</p></td></tr></tbody></table>

### createVocabulary() - 创建热词列表 <span id="04c805f2f7bpx" />

<strong>方法签名</strong>：

```java
public Vocabulary createVocabulary(String targetModel,String prefix,JsonArray vocabulary) throws NoApiKeyException, InputRequiredException
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.26%" }} /><col style={{ width: "16.84%" }} /><col style={{ width: "9.8%" }} /><col style={{ width: "57.1%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>targetModel</p></td><td><p>String</p></td><td><p>是</p></td><td><div><p>使用热词列表的语音识别模型，必须与后续调用语音识别接口时使用的模型一致。</p></div></td></tr><tr><td><p>prefix</p></td><td><p>String</p></td><td><p>是</p></td><td><div><p>热词列表自定义前缀，仅允许数字和小写字母，长度不超过10个字符。</p></div></td></tr><tr><td><p>vocabulary</p></td><td><p>JsonArray</p></td><td><p>是</p></td><td><p>热词列表，每个JsonObject包含 text、weight、lang 等字段。</p> <p>详情请参见<a href="/zh/model-studio/vocabulary-java-sdk">热词对象结构</a>。</p></td></tr></tbody></table>

<strong>返回值</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.26%" }} /><col style={{ width: "83.74%" }} /></colgroup><thead><tr><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>Vocabulary</p></td><td><p>热词列表对象，包含 vocabularyId 等信息。</p></td></tr></tbody></table>

<strong>异常</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.35%" }} /><col style={{ width: "83.65%" }} /></colgroup><thead><tr><th><p><strong>异常类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>NoApiKeyException</p></td><td><p>API Key 为空。</p></td></tr><tr><td><p>InputRequiredException</p></td><td><p>必填参数为空。</p></td></tr></tbody></table>

### listVocabulary() - 批量查询热词列表 <span id="52380e9cb48w4" />

<strong>方法签名</strong>：

```java
public Vocabulary[] listVocabulary(String prefix) throws NoApiKeyException, InputRequiredException

public Vocabulary[] listVocabulary(String prefix, int pageIndex, int pageSize) throws NoApiKeyException, InputRequiredException
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.3%" }} /><col style={{ width: "16.86%" }} /><col style={{ width: "9.73%" }} /><col style={{ width: "57.11%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>prefix</p></td><td><p>String</p></td><td><p>否</p></td><td><div><p>热词列表自定义前缀，如果设定则只返回指定前缀的热词列表。</p></div></td></tr><tr><td><p>pageIndex</p></td><td><p>int</p></td><td><p>否</p></td><td><div><p>页码索引，从0开始计数。</p> <p>默认值：0。</p></div></td></tr><tr><td><p>pageSize</p></td><td><p>int</p></td><td><p>否</p></td><td><div><p>每页包含数据条数。</p> <p>默认值：10。</p></div></td></tr></tbody></table>

<strong>返回值</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.16%" }} /><col style={{ width: "83.84%" }} /></colgroup><thead><tr><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>Vocabulary\[]</p></td><td><p>热词列表对象数组。</p></td></tr></tbody></table>

<strong>Vocabulary 对象字段（list返回）</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.26%" }} /><col style={{ width: "17%" }} /><col style={{ width: "66.74%" }} /></colgroup><thead><tr><th><p><strong>字段</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabularyId</p></td><td><p>String</p></td><td><div><p>热词列表ID。</p></div></td></tr><tr><td><p>gmtCreate</p></td><td><p>String</p></td><td><div><p>创建时间。</p></div></td></tr><tr><td><p>gmtModified</p></td><td><p>String</p></td><td><div><p>修改时间。</p></div></td></tr><tr><td><p>status</p></td><td><p>String</p></td><td><div><p>状态：</p> <ul><li><p>OK：可调用</p></li> <li><p>UNDEPLOYED：不可调用。</p></li></ul></div></td></tr></tbody></table>

<strong>异常</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.35%" }} /><col style={{ width: "83.65%" }} /></colgroup><thead><tr><th><p><strong>异常类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>NoApiKeyException</p></td><td><p>API Key 为空。</p></td></tr><tr><td><p>InputRequiredException</p></td><td><p>必填参数为空。</p></td></tr></tbody></table>

### queryVocabulary() - 查询热词列表 <span id="1bfc8cc687iyp" />

<strong>方法签名</strong>：

```java
public Vocabulary queryVocabulary(String vocabularyId) throws NoApiKeyException, InputRequiredException
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.21%" }} /><col style={{ width: "17.09%" }} /><col style={{ width: "9.46%" }} /><col style={{ width: "57.24%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabularyId</p></td><td><p>String</p></td><td><p>是</p></td><td><p>需要查询的热词列表ID。</p></td></tr></tbody></table>

<strong>返回值</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.16%" }} /><col style={{ width: "83.84%" }} /></colgroup><thead><tr><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>Vocabulary</p></td><td><p>热词列表对象，包含详细信息。</p></td></tr></tbody></table>

<strong>Vocabulary 对象字段（query返回）</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.35%" }} /><col style={{ width: "16.95%" }} /><col style={{ width: "66.7%" }} /></colgroup><thead><tr><th><p><strong>字段</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabulary</p></td><td><p>JsonArray</p></td><td><p>热词列表内容</p></td></tr><tr><td><p>targetModel</p></td><td><p>String</p></td><td><div><p>使用热词列表的语音识别模型，必须与后续调用语音识别接口时使用的模型一致。</p></div></td></tr><tr><td><p>gmtCreate</p></td><td><p>String</p></td><td><div><p>创建时间。</p></div></td></tr><tr><td><p>gmtModified</p></td><td><p>String</p></td><td><div><p>修改时间。</p></div></td></tr><tr><td><p>status</p></td><td><p>String</p></td><td><div><p>状态：</p> <ul><li><p>OK：可调用</p></li> <li><p>UNDEPLOYED：不可调用。</p></li></ul></div></td></tr></tbody></table>

<strong>异常</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.35%" }} /><col style={{ width: "83.65%" }} /></colgroup><thead><tr><th><p><strong>异常类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>NoApiKeyException</p></td><td><p>API Key 为空。</p></td></tr><tr><td><p>InputRequiredException</p></td><td><p>必填参数为空。</p></td></tr></tbody></table>

### updateVocabulary() - 更新热词列表 <span id="c0ad86c84f1wy" />

<strong>方法签名</strong>：

```java
public void updateVocabulary(String vocabularyId,JsonArray vocabulary) throws NoApiKeyException, InputRequiredException
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.3%" }} /><col style={{ width: "16.91%" }} /><col style={{ width: "9.78%" }} /><col style={{ width: "57.01%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabularyId</p></td><td><p>String</p></td><td><p>是</p></td><td><div><p>需要更新的热词列表ID。</p></div></td></tr><tr><td><p>vocabulary</p></td><td><p>JsonArray</p></td><td><p>是</p></td><td><div><p>新的热词列表，将完全替换原有内容。</p></div></td></tr></tbody></table>

<strong>返回值</strong>：无

<strong>异常</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.35%" }} /><col style={{ width: "83.65%" }} /></colgroup><thead><tr><th><p><strong>异常类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>NoApiKeyException</p></td><td><p>API Key 为空。</p></td></tr><tr><td><p>InputRequiredException</p></td><td><p>必填参数为空。</p></td></tr></tbody></table>

### deleteVocabulary() - 删除热词列表 <span id="c3a11856fex14" />

<strong>方法签名</strong>：

```java
public void deleteVocabulary(String vocabularyId) throws NoApiKeyException, InputRequiredException
```

<strong>参数</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.35%" }} /><col style={{ width: "16.81%" }} /><col style={{ width: "10.39%" }} /><col style={{ width: "56.45%" }} /></colgroup><thead><tr><th><p><strong>参数</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>vocabularyId</p></td><td><p>String</p></td><td><p>是</p></td><td><div><p>需要删除的热词列表ID。</p></div></td></tr></tbody></table>

<strong>返回值</strong>：无

<strong>异常</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.35%" }} /><col style={{ width: "83.65%" }} /></colgroup><thead><tr><th><p><strong>异常类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>NoApiKeyException</p></td><td><p>API Key 为空。</p></td></tr><tr><td><p>InputRequiredException</p></td><td><p>必填参数为空。</p></td></tr></tbody></table>

## Vocabulary 类 <span id="d5e707c526ky7" />

<strong>包路径</strong>：`com.alibaba.dashscope.audio.asr.vocabulary.Vocabulary`

<strong>功能</strong>：热词列表对象，封装热词列表的元数据和内容

### 主要方法 <span id="eb463784278o7" />

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.58%" }} /><col style={{ width: "16.72%" }} /><col style={{ width: "66.7%" }} /></colgroup><thead><tr><th><p><strong>方法</strong></p></th><th><p><strong>返回类型</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>getVocabularyId()</p></td><td><p>String</p></td><td><p>获取热词列表ID。</p></td></tr><tr><td><p>getTargetModel()</p></td><td><p>String</p></td><td><p>获取目标模型。</p></td></tr><tr><td><p>getVocabulary()</p></td><td><p>JsonArray</p></td><td><p>获取热词列表内容。</p></td></tr><tr><td><p>getStatus()</p></td><td><p>String</p></td><td><p>获取状态。</p></td></tr><tr><td><p>getGmtCreate()</p></td><td><p>String</p></td><td><p>获取创建时间。</p></td></tr><tr><td><p>getGmtModified()</p></td><td><p>String</p></td><td><p>获取修改时间。</p></td></tr><tr><td><p>getData()</p></td><td><p>JsonObject</p></td><td><p>获取完整数据（JSON格式）。</p></td></tr></tbody></table>

## 热词对象结构 <span id="31dbabc25dse7" />

<strong>用于</strong>`vocabulary`<strong>参数的 JsonObject 定义</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%" }}><colgroup><col style={{ width: "16.23%" }} /><col style={{ width: "16.87%" }} /><col style={{ width: "17.52%" }} /><col style={{ width: "49.38%" }} /></colgroup><thead><tr><th><p><strong>字段</strong></p></th><th><p><strong>类型</strong></p></th><th><p><strong>必填</strong></p></th><th><p><strong>说明</strong></p></th></tr></thead><tbody><tr><td><p>text</p></td><td><p>String</p></td><td><p>是</p></td><td><div><p><span>热词文本。</span></p> <p>热词文本的语言必须在所选模型的支持范围内，不同模型支持的语言各不相同。</p> <p>热词用于提升识别的准确率，请使用实际词语而非任意字符组合。</p> <p><span>长度限制：含非 ASCII 字符时不超过 15 个字符；纯 ASCII 时空格分隔片段不超过 7 个</span>。</p></div></td></tr><tr><td><p>weight</p></td><td><p>int</p></td><td><p>是</p></td><td><div><p>热词权重。常用值：4。</p> <p>取值范围：\[1, 5]。</p> <p>如果效果不明显，可以适当增加权重，但权重过大可能产生负面效果，导致其他词语识别不准确。</p></div></td></tr><tr><td><p>lang</p></td><td><p>String</p></td><td><p>否</p></td><td><div><p>待识别音频的语言代码。设置后，系统将对指定语种进行热词识别增强。如果无法提前确定语种，可不设置，模型会自动识别语种。</p> <p>取值范围（因模型而异）：</p> <ul><li><p>Paraformer：</p> <ul><li><p>zh: 中文</p></li> <li><p>en: 英文</p></li> <li><p>ja: 日语</p></li> <li><p>yue: 粤语</p></li> <li><p>ko: 韩语</p></li> <li><p>de：德语</p></li> <li><p>fr：法语</p></li> <li><p>ru：俄语</p></li></ul></li> <li><p>Fun-ASR：</p> <ul><li><p>zh: 中文</p></li> <li><p>en: 英文</p></li> <li><p>ja: 日语</p></li></ul></li></ul></div></td></tr></tbody></table>

## 示例代码 <span id="java-sdk-examples-title" />

### 创建热词列表 <span id="java-example-create-title" />

```java expandable
import com.alibaba.dashscope.audio.asr.vocabulary.Vocabulary;
import com.alibaba.dashscope.audio.asr.vocabulary.VocabularyService;
import com.alibaba.dashscope.exception.InputRequiredException;
import com.alibaba.dashscope.exception.NoApiKeyException;
import com.alibaba.dashscope.utils.Constants;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.util.ArrayList;
import java.util.List;

public class Main {
    // 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
    // 若没有配置环境变量，请用百炼API Key将下行替换为：public static String apiKey = "sk-xxx"
    public static String apiKey = System.getenv("DASHSCOPE_API_KEY");

    public static void main(String[] args) throws NoApiKeyException, InputRequiredException {
        // 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
        Constants.baseHttpApiUrl = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1";
        String targetModel = "fun-asr";

        JsonArray vocabularyJson = new JsonArray();
        List<Hotword> wordList = new ArrayList<>();
        wordList.add(new Hotword("吴贻弓", 4));
        wordList.add(new Hotword("阙里人家", 4));

        for (Hotword word : wordList) {
            JsonObject jsonObject = new JsonObject();
            jsonObject.addProperty("text", word.text);
            jsonObject.addProperty("weight", word.weight);
            vocabularyJson.add(jsonObject);
        }

        VocabularyService service = new VocabularyService(apiKey);
        Vocabulary vocabulary = service.createVocabulary(targetModel, "testpfx", vocabularyJson);
        System.out.println("热词列表ID：" + vocabulary.getVocabularyId());
    }
}

class Hotword {
    String text;
    int weight;

    public Hotword(String text, int weight) {
        this.text = text;
        this.weight = weight;
    }
}
```

### 批量查询热词列表 <span id="java-example-list-title" />

```java
import com.alibaba.dashscope.audio.asr.vocabulary.Vocabulary;
import com.alibaba.dashscope.audio.asr.vocabulary.VocabularyService;
import com.alibaba.dashscope.exception.InputRequiredException;
import com.alibaba.dashscope.exception.NoApiKeyException;
import com.alibaba.dashscope.utils.Constants;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

public class Main {
    // 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
    // 若没有配置环境变量，请用百炼API Key将下行替换为：public static String apiKey = "sk-xxx"
    public static String apiKey = System.getenv("DASHSCOPE_API_KEY");

    public static void main(String[] args) throws NoApiKeyException, InputRequiredException {
        // 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
        Constants.baseHttpApiUrl = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1";

        VocabularyService service = new VocabularyService(apiKey);
        Vocabulary[] vocabularies = service.listVocabulary("testpfx");
        Gson gson = new GsonBuilder()
                .setPrettyPrinting()
                .create();
        System.out.println("热词列表：" + gson.toJson(vocabularies));
    }
}
```

### 查询热词列表 <span id="java-example-query-title" />

```java expandable
import com.alibaba.dashscope.audio.asr.vocabulary.Vocabulary;
import com.alibaba.dashscope.audio.asr.vocabulary.VocabularyService;
import com.alibaba.dashscope.exception.InputRequiredException;
import com.alibaba.dashscope.exception.NoApiKeyException;
import com.alibaba.dashscope.utils.Constants;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

public class Main {
    // 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
    // 若没有配置环境变量，请用百炼API Key将下行替换为：public static String apiKey = "sk-xxx"
    public static String apiKey = System.getenv("DASHSCOPE_API_KEY");

    public static void main(String[] args) throws NoApiKeyException, InputRequiredException {
        // 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
        Constants.baseHttpApiUrl = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1";

        VocabularyService service = new VocabularyService(apiKey);
        // 查询时替换为实际的热词列表ID
        Vocabulary vocabulary = service.queryVocabulary("vocab-testpfx-xxxx");
        Gson gson = new GsonBuilder()
                .setPrettyPrinting()
                .create();
        System.out.println("热词列表：" + gson.toJson(vocabulary.getData()));
    }
}
```

### 更新热词列表 <span id="java-example-update-title" />

```java expandable
import com.alibaba.dashscope.audio.asr.vocabulary.VocabularyService;
import com.alibaba.dashscope.exception.InputRequiredException;
import com.alibaba.dashscope.exception.NoApiKeyException;
import com.alibaba.dashscope.utils.Constants;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.util.ArrayList;
import java.util.List;

public class Main {
    // 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
    // 若没有配置环境变量，请用百炼API Key将下行替换为：public static String apiKey = "sk-xxx"
    public static String apiKey = System.getenv("DASHSCOPE_API_KEY");

    public static void main(String[] args) throws NoApiKeyException, InputRequiredException {
        // 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
        Constants.baseHttpApiUrl = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1";

        JsonArray vocabularyJson = new JsonArray();
        List<Hotword> wordList = new ArrayList<>();
        wordList.add(new Hotword("吴贻弓", 4, "zh"));
        wordList.add(new Hotword("阙里人家", 4, "zh"));

        for (Hotword word : wordList) {
            JsonObject jsonObject = new JsonObject();
            jsonObject.addProperty("text", word.text);
            jsonObject.addProperty("weight", word.weight);
            jsonObject.addProperty("lang", word.lang);
            vocabularyJson.add(jsonObject);
        }

        VocabularyService service = new VocabularyService(apiKey);
        // 替换为实际的热词列表ID
        service.updateVocabulary("vocab-testpfx-xxx", vocabularyJson);
    }
}

class Hotword {
    String text;
    int weight;
    String lang;

    public Hotword(String text, int weight, String lang) {
        this.text = text;
        this.weight = weight;
        this.lang = lang;
    }
}
```

### 删除热词列表 <span id="java-example-delete-title" />

```java
import com.alibaba.dashscope.audio.asr.vocabulary.VocabularyService;
import com.alibaba.dashscope.exception.InputRequiredException;
import com.alibaba.dashscope.exception.NoApiKeyException;
import com.alibaba.dashscope.utils.Constants;

public class Main {
    // 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
    // 若没有配置环境变量，请用百炼API Key将下行替换为：public static String apiKey = "sk-xxx"
    public static String apiKey = System.getenv("DASHSCOPE_API_KEY");

    public static void main(String[] args) throws NoApiKeyException, InputRequiredException {
        // 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
        Constants.baseHttpApiUrl = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1";

        VocabularyService service = new VocabularyService(apiKey);
        // 删除时替换为实际的热词列表ID
        service.deleteVocabulary("vocab-testpfx-xxxx");
    }
}
```
