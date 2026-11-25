> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Qwen-Audio-ASR-Filetrans非实时语音识别Java SDK

> 本文介绍Qwen-Audio-ASR-Filetrans非实时语音识别Java SDK的参数和接口细节。

<meta property="og:title" content="Qwen-Audio-ASR-Filetrans非实时语音识别Java SDK" />

关于支持的音频格式、文件大小限制、时长限制等输入要求，请参见[音频规格](/zh/model-studio/asr-model#asr_audio_spec02)。

## 前提条件 <span id="8c73014707u4b" />

- 已开通服务并[获取与配置 API Key](/zh/model-studio/get-api-key)。请配置API Key到环境变量，而非硬编码在代码中，防范因代码泄露导致的安全风险。

- [安装最新版DashScope SDK](/zh/model-studio/install-sdk)。

## 快速开始 <span id="e4705e474eiyi" />

[核心类（Transcription）](/zh/model-studio/qwen-audio-asr-filetrans-java-sdk#adcb5e9bddbyq)提供了异步提交任务、同步等待任务结束和异步查询任务执行结果的接口。可通过如下两种调用方式进行非实时语音识别：

- 异步提交任务+同步等待任务结束：提交任务后，阻塞当前线程直到任务结束并获取识别结果。
- 异步提交任务+异步查询任务执行结果：提交任务后，在需要的时候通过调用查询任务接口获取任务的执行结果。

<Tabs>
  <Tab title="异步提交任务+同步等待任务结束">
    <span id="3f0e8beb6ccng" />

    <Image src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/images/6a4b3c2d1e0f93bd.png" alt="提交任务并同步等待" width={1000} />

    1. 配置请求参数。
    2. 实例化核心类（Transcription）。
    3. 调用核心类（Transcription）的`asyncCall`方法异步提交任务。

       <Note>
         - 文件转写服务对通过API提交的任务采取尽力服务原则进行处理。任务提交后将进入排队（`PENDING`）状态，排队时间取决于队列长度和文件时长，无法明确给出，通常在数分钟内。任务开始处理后，语音识别将以数百倍加速完成。
         - 每一个任务完成后，识别结果和URL下载链接有效期为24小时，超时后无法查询任务或通过先前查询结果中的URL下载结果。
       </Note>
    4. 调用核心类（Transcription）的`wait`方法同步等待任务结束。

       任务的状态包括`PENDING`、`RUNNING`、`SUCCEEDED`和`FAILED`。当任务处于`PENDING`或`RUNNING`状态时，`wait`接口将被阻塞。当任务处于`SUCCEEDED`或`FAILED`状态时，`wait`接口不再阻塞并返回任务的执行结果。

       `wait`返回任务执行结果（TranscriptionResult）。

    ```java expandable
    import com.alibaba.dashscope.audio.asr.transcription.*;
    import com.alibaba.dashscope.utils.Constants;
    import com.google.gson.*;

    import java.util.Arrays;

    public class Main {
        public static void main(String[] args) {
            // 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
            Constants.baseHttpApiUrl = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1";
            // 创建转写请求参数
            TranscriptionParam param =
                    TranscriptionParam.builder()
                            // 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
                            // 若没有配置环境变量，请用百炼API Key将下行替换为：.apiKey("sk-xxx")
                            //.apiKey("apikey")
                            .model("qwen-audio-3.1-asr-flash-filetrans") // 此处以qwen-audio-3.1-asr-flash-filetrans为例，可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/models
                            .fileUrls(
                                    Arrays.asList(
                                            "{YOUR_AUDIO_URL}"))
                            .build();
            try {
                Transcription transcription = new Transcription();
                // 提交转写请求
                TranscriptionResult result = transcription.asyncCall(param);
                System.out.println("RequestId: " + result.getRequestId());
                // 阻塞等待任务完成并获取结果
                result = transcription.wait(
                        TranscriptionQueryParam.FromTranscriptionParam(param, result.getTaskId()));
                // 打印结果
                System.out.println(new GsonBuilder().setPrettyPrinting().create().toJson(result.getOutput()));
            } catch (Exception e) {
                System.out.println("error: " + e);
            }
            System.exit(0);
        }
    }
    ```
  </Tab>

  <Tab title="异步提交任务+异步查询任务执行结果">
    <span id="909738567dq8d" />

    <Image src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/images/6a4b3c2d1e0f93bc.png" alt="提交任务并主动轮询" width={900} />

    1. 配置请求参数。
    2. 实例化核心类（Transcription）。
    3. 调用核心类（Transcription）的`asyncCall`方法异步提交任务。

       <Note>
         - 文件转写服务对通过API提交的任务采取尽力服务原则进行处理。任务提交后将进入排队（`PENDING`）状态，排队时间取决于队列长度和文件时长，无法明确给出，通常在数分钟内。任务开始处理后，语音识别将以数百倍加速完成。
         - 每一个任务完成后，识别结果和URL下载链接有效期为24小时，超时后无法查询任务或通过先前查询结果中的URL下载结果。
       </Note>
    4. 循环调用核心类（Transcription）的`fetch`方法直到获取最终的任务结果。

       当任务状态为`SUCCEEDED`或`FAILED`时，停止轮询并处理结果。

       `fetch`返回任务执行结果（TranscriptionResult）。

    ```java expandable
    import com.alibaba.dashscope.audio.asr.transcription.*;
    import com.alibaba.dashscope.common.TaskStatus;
    import com.alibaba.dashscope.utils.Constants;
    import com.google.gson.*;

    import java.util.Arrays;

    public class Main {
        public static void main(String[] args) {
            // 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
            Constants.baseHttpApiUrl = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1";
            // 创建转写请求参数
            TranscriptionParam param =
                    TranscriptionParam.builder()
                            // 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
                            // 若没有配置环境变量，请用百炼API Key将下行替换为：.apiKey("sk-xxx")
                            //.apiKey("apikey")
                            .model("qwen-audio-3.1-asr-flash-filetrans") // 此处以qwen-audio-3.1-asr-flash-filetrans为例，可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/models
                            .fileUrls(
                                    Arrays.asList(
                                            "{YOUR_AUDIO_URL}"))
                            .build();
            try {
                Transcription transcription = new Transcription();
                // 提交转写请求
                TranscriptionResult result = transcription.asyncCall(param);
                System.out.println("RequestId: " + result.getRequestId());
                // 循环获取任务执行结果，直到任务结束
                while (true) {
                    result = transcription.fetch(TranscriptionQueryParam.FromTranscriptionParam(param, result.getTaskId()));
                    if (result.getTaskStatus() == TaskStatus.SUCCEEDED || result.getTaskStatus() == TaskStatus.FAILED) {
                        break;
                    }
                    Thread.sleep(1000);
                }
                // 打印结果
                System.out.println(new GsonBuilder().setPrettyPrinting().create().toJson(result.getOutput()));
            } catch (Exception e) {
                System.out.println("error: " + e);
            }
            System.exit(0);
        }
    }
    ```
  </Tab>
</Tabs>

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
</Tip>

<strong>注意</strong>：

- 不同地域的 API Key 不同，请确保使用对应地域的 API Key
- 地域配置为全局设置，影响所有 DashScope SDK 的 API 调用

## 请求参数 <span id="48ea212b1d08r" />

请求参数通过`TranscriptionParam`的链式方法进行配置。

```java
TranscriptionParam param = TranscriptionParam.builder()
  .model("qwen-audio-3.1-asr-flash-filetrans")
  .fileUrls(
          Arrays.asList(
                  "{YOUR_AUDIO_URL}"))
  .build();
```

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "14.89%" }} />

    <col style={{ width: "11.93%" }} />

    <col style={{ width: "9.4%" }} />

    <col style={{ width: "63.78%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>是否必须</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `model`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        模型名称。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `fileUrls`
      </td>

      <td style={{ verticalAlign: "top" }}>
        List\<String>
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        音视频文件转写的URL列表，支持HTTP / HTTPS协议，单次请求仅支持1个URL。关于支持的音频格式、文件大小限制、时长限制等输入要求，请参见[音频规格](/zh/model-studio/asr-model#asr_audio_spec02)。

        若录音文件存储在阿里云OSS，使用RESTful API方式支持使用以`oss://`为前缀的临时 URL，使用SDK方式不支持使用以 oss\://为前缀的临时 URL。

        <Tip>
          - 临时 URL 有效期48小时，过期后无法使用，<strong>请勿用于生产环境。</strong>
          - 文件上传凭证接口限流为 100 QPS 且不支持扩容，<strong>请勿用于生产环境、高并发及压测场景。</strong>
          - 生产环境建议使用[阿里云OSS](https://help.aliyun.com/zh/oss/user-guide/what-is-oss) 等稳定存储，确保文件长期可用并规避限流问题。
          - 录音文件URL设置成OSS临时公网访问不通该如何处理？请求头中将`X-DashScope-OssResourceResolve`设为`enable`（不推荐该方式）。

            SDK不支持对请求头进行配置。
        </Tip>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `keep_dialect`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `boolean`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        仅 `qwen-audio-3.1-asr-flash-filetrans` 支持。默认 `false`，将方言转写为普通话；设为 `true` 时保留方言表达。通过 `.parameter("keep_dialect", value)` 设置。完整参数说明请参见[API 参考](/zh/model-studio/qwen-audio-asr-filetrans-http-api)。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `vocabularyId`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        预编译热词列表 ID。

        需预先调用创建热词列表接口生成，识别时传入该 ID 即可使用列表中的热词。

        适用于词汇已知且相对稳定、需要跨请求复用同一词表的场景。

        使用方法请参见[预编译热词](/zh/model-studio/improve-asr-accuracy#hw_precompiled_h3)。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `vocabulary`
      </td>

      <td style={{ verticalAlign: "top" }}>
        Map\<String, Integer>
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        即时热词。

        以键值对形式传入，键为热词文本（`string`），值为热词权重（`integer`），无需预先创建热词列表。权重取值范围为 \[1, 5] 或 50：取 \[1, 5] 时值越大模型越倾向输出该词；取 50 时为超级热词，召回率大幅提升，但超级热词数量最多不超过 50 个。

        适用于临时性、会话级别的热词优化。

        与预编译热词同时配置时，系统会合并两类热词；合并后超过 2000 个时，随机选择 2000 个使用。使用方法请参见[即时热词](/zh/model-studio/improve-asr-accuracy#hw_instant_h3)。

        `vocabulary`需要通过`TranscriptionParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          Map<String, Integer> vocab = new HashMap<>();
          vocab.put("张三", 5);
          vocab.put("李四", 5);

          TranscriptionParam param = TranscriptionParam.builder()
            .model("qwen-audio-3.1-asr-flash-filetrans")
            .parameter("vocabulary", vocab)
            .build();
          ```

          ```java 通过parameters设置
          Map<String, Integer> vocab = new HashMap<>();
          vocab.put("张三", 5);
          vocab.put("李四", 5);

          TranscriptionParam param = TranscriptionParam.builder()
            .model("qwen-audio-3.1-asr-flash-filetrans")
            .parameters(Collections.singletonMap("vocabulary", vocab))
            .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `channelId`
      </td>

      <td style={{ verticalAlign: "top" }}>
        List\<Integer>
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        指定在多音轨音频文件中需要识别的音轨索引，索引从 0 开始。例如，\[0] 表示识别第一个音轨，\[0, 1] 表示同时识别第一和第二个音轨。如果省略此参数，则默认处理第一个音轨。

        <Tip>
          指定的每一个音轨都将独立计费。例如，为单个文件请求 \[0, 1] 会产生两笔独立的费用。
        </Tip>

        默认值：\[0]。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `specialWordFilter`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        指定在语音识别过程中需要处理的敏感词，并支持对不同敏感词设置不同的处理方式。详情请参见[敏感词过滤](/zh/model-studio/non-realtime-speech-recognition-user-guide#nrt03_sensitive_h3)。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `diarizationEnabled`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `Boolean`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        是否启用说话人分离，默认关闭。

        仅适用于单声道音频，多声道音频不支持说话人分离。

        启用该功能后，识别结果中将显示`speaker_id`字段，用于区分不同说话人。

        <Note>
          如果启用说话人分离功能，建议音频时长不超过2小时，否则可能导致识别失败或超时。
        </Note>

        默认值：false。

        有关`speaker_id`的示例，请参见识别结果说明。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `speakerCount`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `Integer`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        <Tip>
          仅在开启说话人分离功能（`diarization_enabled`设置为`true`）时生效。
        </Tip>

        说话人数量参考值。取值范围为2至100的整数（包含2和100）。

        默认自动判断说话人数量，如果配置此项，只能辅助算法尽量输出指定人数，无法保证一定会输出此人数。

        无默认值。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `language_hints`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String[]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        设置待识别语言代码。如果无法提前确定语种，可不设置，模型会自动识别语种。

        最多支持设置 4 个值，超出时仅前 4 个生效。

        <Accordion title="点击查看支持的语言代码">
          - zh: 中文
          - en: 英文
          - ja: 日语
          - ko：韩语
          - vi：越南语
          - th：泰语
          - id：印尼语
          - ms：马来语
          - tl：菲律宾语
          - hi：印地语
          - ar：阿拉伯语
          - fr：法语
          - de：德语
          - es：西班牙语
          - pt：葡萄牙语
          - ru：俄语
          - it：意大利语
          - nl：荷兰语
          - sv：瑞典语
          - da：丹麦语
          - fi：芬兰语
          - no：挪威语
          - el：希腊语
          - pl：波兰语
          - cs：捷克语
          - hu：匈牙利语
          - ro：罗马尼亚语
          - bg：保加利亚语
          - hr：克罗地亚语
          - sk：斯洛伐克语
        </Accordion>

        `language_hints`需要通过`TranscriptionParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          TranscriptionParam param = TranscriptionParam.builder()
            .model("qwen-audio-3.1-asr-flash-filetrans")
            .parameter("language_hints", new String[]{"zh"})
            .build();
          ```

          ```java 通过parameters设置
          TranscriptionParam param = TranscriptionParam.builder()
            .model("qwen-audio-3.1-asr-flash-filetrans")
            .parameters(Collections.singletonMap("language_hints", new String[]{"zh"}))
            .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `apiKey`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        用户API Key。如已将API Key配置到环境变量，则无须在代码中设置。否则一定要在代码中进行设置。
      </td>
    </tr>
  </tbody>
</table>

## 响应结果 <span id="deebdc73cawgt" />

### 任务执行结果（`TranscriptionResult`） <span id="11a082e1d9ijq" />

`TranscriptionResult`封装了当前任务执行结果。

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "30.66%" }} />

    <col style={{ width: "18.37%" }} />

    <col style={{ width: "16.07%" }} />

    <col style={{ width: "34.9%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>接口/方法</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>返回值</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>描述</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public String getRequestId()
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        无
      </td>

      <td style={{ verticalAlign: "top" }}>
        requestId
      </td>

      <td style={{ verticalAlign: "top" }}>
        获取requestId。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public String getTaskId()
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        无
      </td>

      <td style={{ verticalAlign: "top" }}>
        taskId
      </td>

      <td style={{ verticalAlign: "top" }}>
        获取taskId。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public TaskStatus getTaskStatus()
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        无
      </td>

      <td style={{ verticalAlign: "top" }}>
        `TaskStatus`，任务状态
      </td>

      <td style={{ verticalAlign: "top" }}>
        获取任务状态。

        `TaskStatus`为枚举类，只需关注`PENDING`、`RUNNING`、`SUCCEEDED`和`FAILED`这四个状态即可。

        <Note>
          当任务包含多个子任务时，只要存在任一子任务成功，整个任务状态将标记为`SUCCEEDED`，需通过`subtask_status`字段判断具体子任务结果。
        </Note>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public List<TranscriptionTaskResult> getResults()
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        无
      </td>

      <td style={{ verticalAlign: "top" }}>
        子任务执行结果（TranscriptionTaskResult）
      </td>

      <td style={{ verticalAlign: "top" }}>
        获取子任务执行结果（TranscriptionTaskResult）。

        每个任务对一个或多个音频文件进行识别，不同音频文件在不同的子任务中处理，因此每个任务对应一到多个子任务。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public JsonObject getOutput()
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        无
      </td>

      <td style={{ verticalAlign: "top" }}>
        任务执行结果，为JSON格式的数据
      </td>

      <td style={{ verticalAlign: "top" }}>
        获取任务执行结果。

        该结果是一个JSON格式的数据，如果您想通过`getOutput`接口获取任务执行结果，请您在获取结果后自行解析。

        <Accordion title="点击查看JSON示例">
          <strong>正常示例</strong>

          ```json expandable
          {
              "task_id":"0795ff8c-b666-4e91-bb8b-xxx",
              "task_status":"SUCCEEDED",
              "submit_time":"2025-02-13 16:12:09.109",
              "scheduled_time":"2025-02-13 16:12:09.128",
              "end_time":"2025-02-13 16:12:10.189",
              "results":[
                  {
                      "file_url":"{YOUR_AUDIO_URL}",
                      "transcription_url":"https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/prod/paraformer-v2/20250213/16%3A12/3baafe5f-d09d-46c6-8b01-724927670edb-1.json?Expires=1739520730&OSSAccessKeyId=YOUR_ACCESS_KEY_ID&Signature=YOUR_SIGNATURE",
                      "subtask_status":"SUCCEEDED"
                  }
              ],
              "task_metrics":{
                  "TOTAL":1,
                  "SUCCEEDED":1,
                  "FAILED":0
              }
          }
          ```

          <strong>异常示例</strong>

          `code`为错误码，`message`为错误信息，只有异常情况才有这两个字段，您可以通过这两个字段，对照错误码排查问题。

          ```json expandable
          {
              "task_id": "7bac899c-06ec-4a79-8875-xxxxxxxxxxxx",
              "task_status": "SUCCEEDED",
              "submit_time": "2024-12-16 16:30:59.170",
              "scheduled_time": "2024-12-16 16:30:59.204",
              "end_time": "2024-12-16 16:31:02.375",
              "results": [
                  {
                      "file_url": "{YOUR_AUDIO_URL}",
                      "code": "FILE_DOWNLOAD_FAILED",
                      "message": "The audio file cannot be downloaded.",
                      "subtask_status": "FAILED"
                  }
              ],
              "task_metrics": {
                  "TOTAL": 1,
                  "SUCCEEDED": 0,
                  "FAILED": 1
              }
          }
          ```
        </Accordion>
      </td>
    </tr>
  </tbody>
</table>

### 子任务执行结果（`TranscriptionTaskResult`） <span id="86b568aa3asuj" />

`TranscriptionTaskResult`封装了子任务执行结果。子任务对单个音频文件进行识别。

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "30.66%" }} />

    <col style={{ width: "18.37%" }} />

    <col style={{ width: "16.07%" }} />

    <col style={{ width: "34.9%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>接口/方法</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>返回值</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>描述</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public String getFileUrl()
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        无
      </td>

      <td style={{ verticalAlign: "top" }}>
        被识别的音频文件的链接
      </td>

      <td style={{ verticalAlign: "top" }}>
        获取被识别音频文件的链接。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public String getTranscriptionUrl()
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        无
      </td>

      <td style={{ verticalAlign: "top" }}>
        识别结果对应的链接
      </td>

      <td style={{ verticalAlign: "top" }}>
        获取识别结果对应的链接。该链接有效期为24小时，超时后无法查询任务或通过先前查询结果中的URL下载结果。

        识别结果保存为JSON文件，您可以通过上述链接下载该文件或直接通过HTTP请求读取该文件中的内容。

        JSON数据中各字段含义请参见识别结果说明。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public TaskStatus getSubTaskStatus()
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        无
      </td>

      <td style={{ verticalAlign: "top" }}>
        `TaskStatus`，子任务状态
      </td>

      <td style={{ verticalAlign: "top" }}>
        获取子任务状态。

        `TaskStatus`为枚举类，只需关注`PENDING`、`RUNNING`、`SUCCEEDED`和`FAILED`这四个状态即可。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public String getMessage()
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        无
      </td>

      <td style={{ verticalAlign: "top" }}>
        任务执行过程中关键信息，可能为空
      </td>

      <td style={{ verticalAlign: "top" }}>
        获取任务执行过程中的关键信息。

        当任务失败时，可查看该内容分析原因。
      </td>
    </tr>
  </tbody>
</table>

### 识别结果说明 <span id="a9021178ccl7s" />

识别结果保存为JSON文件。

```json expandable
{
    "file_url":"{YOUR_AUDIO_URL}",
    "properties":{
        "audio_format":"pcm_s16le",
        "channels":[
            0
        ],
        "original_sampling_rate":16000,
        "original_duration_in_milliseconds":3834
    },
    "transcripts":[
        {
            "channel_id":0,
            "content_duration_in_milliseconds":3720,
            "text":"Hello world, 这里是阿里巴巴语音实验室。",
            "sentences":[
                {
                    "begin_time":100,
                    "end_time":3820,
                    "text":"Hello world, 这里是阿里巴巴语音实验室。",
                    "sentence_id":1,
                    "speaker_id":0, //当开启自动说话人分离功能时才会显示该字段
                    "words":[
                        {
                            "begin_time":100,
                            "end_time":596,
                            "text":"Hello ",
                            "punctuation":""
                        },
                        {
                            "begin_time":596,
                            "end_time":844,
                            "text":"world",
                            "punctuation":", "
                        }
                        // 这里省略其它内容
                    ]
                }
            ]
        }
    ]
}
```

需要关注的参数如下：

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "12.81%" }} />

    <col style={{ width: "11.11%" }} />

    <col style={{ width: "76.08%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `audio_format`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        源文件中音频的格式。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `channels`
      </td>

      <td style={{ verticalAlign: "top" }}>
        array\[integer]
      </td>

      <td style={{ verticalAlign: "top" }}>
        源文件中音频的音轨索引信息，对单轨音频返回\[0]，对双轨音频返回\[0, 1]，以此类推。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `original_sampling_rate`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `integer`
      </td>

      <td style={{ verticalAlign: "top" }}>
        源文件中音频的采样率（Hz）。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `original_duration_in_milliseconds`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `integer`
      </td>

      <td style={{ verticalAlign: "top" }}>
        源文件中的原始音频时长（ms）。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `channel_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `integer`
      </td>

      <td style={{ verticalAlign: "top" }}>
        转写结果的音轨索引，以0为起始。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `content_duration`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `integer`
      </td>

      <td style={{ verticalAlign: "top" }}>
        音轨中被判定为语音内容的时长（ms）。

        <Note>
          以下计费说明适用于 `qwen-audio-3.0-asr-flash-filetrans`。`qwen-audio-3.1-asr-flash-filetrans` 按 Token 计费。
        </Note>

        语音识别模型服务仅对音轨中被判定为语音内容的时长进行语音转写，并据此进行计量计费，非语音内容不计量、不计费。通常情况下语音内容时长会短于原始音频时长。由于对是否存在语音内容的判定是由AI模型给出的，可能与实际情况存在一定误差。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `transcript`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        段落级别的语音转写结果。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `sentences`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `array`
      </td>

      <td style={{ verticalAlign: "top" }}>
        句子级别的语音转写结果。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `words`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `array`
      </td>

      <td style={{ verticalAlign: "top" }}>
        词级别的语音转写结果。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `begin_time`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `integer`
      </td>

      <td style={{ verticalAlign: "top" }}>
        开始时间戳（ms）。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `end_time`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `integer`
      </td>

      <td style={{ verticalAlign: "top" }}>
        结束时间戳（ms）。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `text`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        语音转写结果。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `speaker_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `integer`
      </td>

      <td style={{ verticalAlign: "top" }}>
        当前说话人的索引，以0为起始，用于区分不同的说话人。

        仅在启用说话人分离功能时，该字段才会显示于识别结果中。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `punctuation`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        预测出的词之后的标点符号（如有）。
      </td>
    </tr>
  </tbody>
</table>

## 关键接口 <span id="60280ef1ec77f" />

### 任务查询参数配置类（`TranscriptionQueryParam`） <span id="3639e1cb40mxi" />

`TranscriptionQueryParam`在等待任务完成（调用`Transcription`的`wait`方法）或查询任务执行结果（调用`Transcription`的`fetch`方法）时用到。

通过静态方法`FromTranscriptionParam`创建`TranscriptionQueryParam`实例。

```java expandable
// 创建转写请求参数
TranscriptionParam param =
        TranscriptionParam.builder()
                // 若没有将API Key配置到环境变量中，需将apiKey替换为自己的API Key
                //.apiKey("apikey")
                .model("qwen-audio-3.0-asr-flash-filetrans")
                .fileUrls(
                        Arrays.asList(
                                "{YOUR_AUDIO_URL}"))
                .build();
try {
    Transcription transcription = new Transcription();
    // 提交转写请求
    TranscriptionResult result = transcription.asyncCall(param);
    System.out.println("RequestId: " + result.getRequestId());
    TranscriptionQueryParam queryParam = TranscriptionQueryParam.FromTranscriptionParam(param, result.getTaskId());

} catch (Exception e) {
    System.out.println("error: " + e);
}
```

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "30.78%" }} />

    <col style={{ width: "18.37%" }} />

    <col style={{ width: "15.95%" }} />

    <col style={{ width: "34.9%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>接口/方法</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>返回值</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>描述</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public static TranscriptionQueryParam FromTranscriptionParam(TranscriptionParam param, String taskId)
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        - `param`：`TranscriptionParam`实例
        - `taskId`：任务ID
      </td>

      <td style={{ verticalAlign: "top" }}>
        `TranscriptionQueryParam`实例
      </td>

      <td style={{ verticalAlign: "top" }}>
        创建`TranscriptionQueryParam`实例。
      </td>
    </tr>
  </tbody>
</table>

### 核心类（`Transcription`） <span id="adcb5e9bddbyq" />

`Transcription`可以通过`import com.alibaba.dashscope.audio.asr.transcription.*;`方式引入。它的关键接口如下：

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "30.78%" }} />

    <col style={{ width: "18.25%" }} />

    <col style={{ width: "15.95%" }} />

    <col style={{ width: "35.02%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>接口/方法</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>返回值</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>描述</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public TranscriptionResult asyncCall(TranscriptionParam param)
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        `param`：语音识别相关参数，`TranscriptionParam`实例
      </td>

      <td style={{ verticalAlign: "top" }}>
        任务执行结果（TranscriptionResult）
      </td>

      <td style={{ verticalAlign: "top" }}>
        异步提交语音识别任务。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public TranscriptionResult wait(TranscriptionQueryParam queryParam)
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        `queryParam`：`TranscriptionQueryParam`实例
      </td>

      <td style={{ verticalAlign: "top" }}>
        任务执行结果（TranscriptionResult）
      </td>

      <td style={{ verticalAlign: "top" }}>
        阻塞当前线程直到异步任务结束（任务状态为`SUCCEEDED`或`FAILED`）。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        ```java
        public TranscriptionResult fetch(TranscriptionQueryParam queryParam)
        ```
      </td>

      <td style={{ verticalAlign: "top" }}>
        `queryParam`：`TranscriptionQueryParam`实例
      </td>

      <td style={{ verticalAlign: "top" }}>
        任务执行结果（TranscriptionResult）
      </td>

      <td style={{ verticalAlign: "top" }}>
        异步查询当前任务执行结果。
      </td>
    </tr>
  </tbody>
</table>

## 其他接口：批量查询任务状态/取消任务 <span id="afbd594d9a7hx" />

详情请参见[管理异步任务](/zh/model-studio/manage-asynchronous-tasks)：支持批量查询24小时内提交的非实时语音识别任务，同时支持取消`PENDING`（排队）状态的任务。

## 错误码 <span id="a370386972oa4" />

如遇报错问题，请参见[错误码](/zh/model-studio/error-code)进行排查。

当任务包含多个子任务时，只要存在任一子任务成功，整个任务状态将标记为`SUCCEEDED`，需通过`subtask_status`字段判断具体子任务结果。

错误返回示例：

```json expandable
{
    "task_id": "7bac899c-06ec-4a79-8875-xxxxxxxxxxxx",
    "task_status": "SUCCEEDED",
    "submit_time": "2024-12-16 16:30:59.170",
    "scheduled_time": "2024-12-16 16:30:59.204",
    "end_time": "2024-12-16 16:31:02.375",
    "results": [
        {
            "file_url": "{YOUR_AUDIO_URL}",
            "code": "FILE_DOWNLOAD_FAILED",
            "message": "The audio file cannot be downloaded.",
            "subtask_status": "FAILED"
        }
    ],
    "task_metrics": {
        "TOTAL": 1,
        "SUCCEEDED": 0,
        "FAILED": 1
    }
}
```

## 常见问题 <span id="7640d5d455ayp" />

### 功能特性 <span id="ef653d0bb7ol9" />

#### Q：是否支持Base64编码方式的音频？ <span id="326a5e31a2p0m" />

不支持Base64编码方式的音频。仅支持可通过公网访问的 URL 所指向的音频的识别，不支持识别二进制流，也不支持直接识别本地文件。

#### Q：如何将音频文件以公网可访问的URL形式提供？ <span id="01d5f797c8ksz" />

通常遵循以下几个步骤（这里为您提供一种思路，具体情况因不同存储产品而异，推荐将音频[上传至阿里云OSS](https://help.aliyun.com/zh/oss/user-guide/simple-upload#a632b50f190j8)）：

<Accordion title="1、选择存储和托管方式">
  如以下这几种：

  - 对象存储服务（推荐）：

    - 使用云服务商的对象存储服务（如[阿里云OSS](https://help.aliyun.com/zh/oss/user-guide/simple-upload#a632b50f190j8)），将音频文件上传到存储桶中，并设置为公开访问。
    - 优点：高可用性、支持 CDN 加速、易于管理。
  - Web 服务器：

    - 将音频文件放置在支持 HTTP/HTTPS 访问的 Web 服务器上（如 Nginx、Apache）。
    - 优点：适合小型项目或本地测试。
  - 内容分发网络（CDN）：

    - 将音频文件托管在 CDN 上，通过 CDN 提供的 URL 访问。
    - 优点：加速文件传输，适合高并发场景。
</Accordion>

<Accordion title="2、上传音频文件">
  根据选择的存储/托管方式，将音频上传，如：

  - 对象存储服务：

    - 登录云服务商的控制台，创建存储桶。
    - 上传音频文件，并设置文件权限为“公共读”或生成临时访问链接。
  - Web 服务器：

    - 将音频文件放置在服务器指定目录下（如 `/var/www/html/audio/`）。
    - 确保文件可以通过 HTTP/HTTPS 访问。
</Accordion>

<Accordion title="3、生成公网可访问的URL">
  例如：

  - 对象存储服务：

    - 文件上传后，系统会自动生成一个公网访问 URL（通常格式为 `https://<bucket-name>.<region>.aliyuncs.com/<file-name>`）。
    - 如果需要更友好的域名，可以绑定自定义域名并开启 HTTPS。
  - Web 服务器：

    - 文件的访问 URL 通常是服务器地址加上文件路径（如 `https://your-domain.com/audio/file.mp3`）。
  - CDN：

    - 配置 CDN 加速后，使用 CDN 提供的 URL（如 `https://cdn.your-domain.com/audio/file.mp3`）。
</Accordion>

<Accordion title="4、验证URL的可用性">
  公网环境下，确保生成的 URL 可以正常访问，例如：

  - 在浏览器中打开 URL，检查是否能播放音频文件。
  - 使用工具（如 `curl` 或 Postman）验证 URL 是否返回正确的 HTTP 响应（状态码 200）。
</Accordion>

使用SDK时，若录音文件存储在[阿里云OSS](https://help.aliyun.com/zh/oss/user-guide/simple-upload#a632b50f190j8)，不支持使用以 `oss://`为前缀的临时 URL。

使用RESTful API时，若录音文件存储在[阿里云OSS](https://help.aliyun.com/zh/oss/user-guide/simple-upload#a632b50f190j8)，支持使用以 `oss://`为前缀的临时 URL：

<Tip>
  - 临时 URL 有效期48小时，过期后无法使用，<strong>请勿用于生产环境。</strong>
  - 文件上传凭证接口限流为 100 QPS 且不支持扩容，<strong>请勿用于生产环境、高并发及压测场景。</strong>
  - 生产环境建议使用[阿里云OSS](https://help.aliyun.com/zh/oss/user-guide/what-is-oss) 等稳定存储，确保文件长期可用并规避限流问题。
</Tip>

#### Q：多久能获取识别结果？ <span id="33c69cf322xkf" />

任务提交后将进入排队（PENDING）状态，排队时间取决于队列长度和文件时长，无法明确给出，通常在数分钟内，请耐心等待。并且音频时长越长，所需时间越久。

### 故障排查 <span id="abe43b2c2a8sq" />

如遇代码报错问题，请根据错误码中的信息进行排查。

#### Q：一直轮询不到结果？ <span id="ed3d52b907dbl" />

可能是限流原因，请耐心等待。

#### Q：无法识别语音（无识别结果）是什么原因？ <span id="88d8b9a4b6xv7" />

请检查音频格式和采样率是否正确且符合参数约束。

可以使用[ffprobe](https://ffmpeg.org/ffprobe.html)工具获取音频的容器、编码、采样率、声道等信息：

```bash
ffprobe -v error -show_entries format=format_name -show_entries stream=codec_name,sample_rate,channels -of default=noprint_wrappers=1 input.xxx
```
