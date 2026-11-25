> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# 非实时语音合成Qwen-Audio-TTS Java SDK参考

> 本文介绍非实时语音合成Qwen-Audio-TTS的Java SDK调用方法，支持非流式和流式两种调用模式。

<meta property="og:title" content="非实时语音合成Qwen-Audio-TTS Java SDK参考" />

<Tip>
  本文描述的功能仅在华北2（北京）地域可用。
</Tip>

<Tip>
  阿里云百炼为华北2（北京）地域推出了业务空间专属域名，能够为推理请求提供卓越的性能和更高的稳定性，建议从 `dashscope.aliyuncs.com` 迁移至 `{WorkspaceId}.cn-beijing.maas.aliyuncs.com`。
</Tip>

## 前提条件 <span id="h2-cv1a2b3d" />

- 已[获取与配置 API Key](/zh/model-studio/get-api-key)并将其配置到环境变量
- 已安装符合版本要求的DashScope Java SDK，建议[安装最新版](/zh/model-studio/install-sdk)，SDK版本需≥2.22.15

## HttpSpeechSynthesizer 类 <span id="h2-cv2a3b4d" />

<strong>包路径</strong>：`com.alibaba.dashscope.audio.http_tts.HttpSpeechSynthesizer`

<strong>功能</strong>：基于HTTP的语音合成，支持非流式和流式两种调用方式。

### 构造方法 <span id="h3-cv2a3b5b" />

```java
public HttpSpeechSynthesizer()
```

创建HttpSpeechSynthesizer实例，使用默认配置。SDK会自动从环境变量`DASHSCOPE_API_KEY`或`Constants.apiKey`获取API Key。

### callAndReturnAudio() - 非流式调用（返回音频数据） <span id="h3-cv2a3b6b" />

<strong>方法签名</strong>：

```java
public ByteBuffer callAndReturnAudio(HttpSpeechSynthesisParam param) throws ApiException, NoApiKeyException, InputRequiredException
```

<strong>参数说明</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "20%" }} />

    <col style={{ width: "25%" }} />

    <col style={{ width: "55%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <span><strong>参数</strong></span>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <span><strong>类型</strong></span>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <span><strong>说明</strong></span>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>param</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span><Link href="#h2-cv3a4b5c">HttpSpeechSynthesisParam</Link></span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>语音合成参数对象，包含模型、文本、音色等配置。</span>
      </td>
    </tr>
  </tbody>
</table>

<strong>返回值</strong>：`ByteBuffer`，包含完整的音频数据。可通过`remaining()`获取音频大小（字节）。

### call() - 非流式调用（返回音频URL） <span id="h3-cv2a3b7b" />

<strong>方法签名</strong>：

```java
public HttpSpeechSynthesisResult call(HttpSpeechSynthesisParam param) throws ApiException, NoApiKeyException, InputRequiredException
```

<strong>参数说明</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "20%" }} />

    <col style={{ width: "25%" }} />

    <col style={{ width: "55%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <span><strong>参数</strong></span>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <span><strong>类型</strong></span>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <span><strong>说明</strong></span>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>param</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span><code>HttpSpeechSynthesisParam</code></span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>语音合成参数对象。</span>
      </td>
    </tr>
  </tbody>
</table>

<strong>返回值</strong>：`HttpSpeechSynthesisResult`对象，通过`getAudioInfo().getUrl()`获取音频下载URL，URL有效期有限，可通过`getAudioInfo().getExpiresAt()`获取过期时间。

### streamCall() - 流式调用 <span id="h3-cv2a3b8b" />

<strong>方法签名</strong>：

```java
public void streamCall(HttpSpeechSynthesisParam param, ResultCallback<HttpSpeechSynthesisResult> callback) throws ApiException, NoApiKeyException, InputRequiredException
```

<strong>参数说明</strong>：

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "15%" }} />

    <col style={{ width: "25%" }} />

    <col style={{ width: "60%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <span><strong>参数</strong></span>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <span><strong>类型</strong></span>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <span><strong>说明</strong></span>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>param</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span><code>HttpSpeechSynthesisParam</code></span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>语音合成参数对象。</span>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>callback</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>ResultCallback\<HttpSpeechSynthesisResult></code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>回调对象，需实现<code>onEvent</code>（接收音频分片）、<code>onComplete</code>（合成完成）、<code>onError</code>（错误处理）三个方法。</span>
      </td>
    </tr>
  </tbody>
</table>

该方法为异步调用，音频数据通过回调函数分片返回，适用于对首包延迟有要求的场景。

<strong>ResultCallback 回调方法</strong>：

`com.alibaba.dashscope.common.ResultCallback`是DashScope SDK提供的通用回调接口，需实现以下三个方法：

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "25%" }} />

    <col style={{ width: "35%" }} />

    <col style={{ width: "40%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <span><strong>方法</strong></span>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <span><strong>参数</strong></span>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <span><strong>说明</strong></span>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>onEvent</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>HttpSpeechSynthesisResult result</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>每接收到一个音频分片时触发。通过<code>result.hasAudioData()</code>判断是否包含音频数据，通过<code>result.getAudioDataSize()</code>获取分片大小。</span>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>onComplete</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>无</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>语音合成完成时触发，表示所有音频分片已接收完毕。</span>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>onError</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>Exception e</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>合成过程中发生错误时触发，可通过<code>e.getMessage()</code>获取错误信息。</span>
      </td>
    </tr>
  </tbody>
</table>

## HttpSpeechSynthesisParam 类 <span id="h2-cv3a4b5c" />

<strong>包路径</strong>：`com.alibaba.dashscope.audio.http_tts.HttpSpeechSynthesisParam`

通过Builder模式构建参数对象。

部分参数没有专用的Builder方法，需要通过继承自父类的`parameter(String key, Object value)`方法或`parameters(Map<String, Object>)`方法进行设置，详见下表中的说明。

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "18%" }} />

    <col style={{ width: "12%" }} />

    <col style={{ width: "10%" }} />

    <col style={{ width: "60%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>方法</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>必填</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        <span>model(String)</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>String</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>语音合成模型。</span>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <span>text(String)</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>String</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>待合成文本。</span>

        支持 SSML 和 LaTeX 格式输入。将待合成文本替换为对应格式即可。

        - 使用 SSML 时，需同时将 `enable_ssml` 设置为 `true`。支持的 SSML 标签及用法，请参见[SSML 与 LaTeX](/zh/model-studio/ssml-latex-user-guide)。
        - 使用 LaTeX 时，将待合成文本替换为 LaTeX 格式即可，无需额外配置。支持的 LaTeX 语法及用法，请参见[LaTeX 公式转语音](/zh/model-studio/latex-capability-support-description)。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <span>voice(String)</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>String</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音色。</span>

        取值范围：

        - 系统音色：参见[Qwen-Audio-TTS音色列表](/zh/model-studio/qwen-audio-tts-voice-list)
        - 声音复刻音色：如何创建音色请参见<Link href="/zh/model-studio/voice-clone-design-http-api">声音复刻HTTP API参考</Link>
        - 声音设计音色：如何创建音色请参见<Link href="/zh/model-studio/voice-design-api-references">声音设计API参考</Link>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <span>format(String)</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>String</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音频编码格式。</span>

        默认值：mp3。

        取值范围：

        - mp3
        - pcm
        - wav
        - opus
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <span>sampleRate(int)</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>int</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音频采样率（Hz）。</span>

        取值范围：8000, 16000, 22050（默认）, 24000, 44100, 48000。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <span>volume(int)</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>int</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音量。</span>

        默认值：50。

        取值范围：\[0, 100]。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <span>rate(float)</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>float</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>语速。</span>

        默认值：1.0。

        取值范围：\[0.5, 2.0]。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <span>pitch(float)</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>float</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音调。</span>

        默认值：1.0。

        取值范围：\[0.5, 2.0]。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>enable\_ssml</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>boolean</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是否开启SSML功能。设置为<code>true</code>时，<code>text</code>参数需传入SSML格式文本。支持的SSML标签及用法，请参见<Link href="/zh/model-studio/ssml-latex-user-guide">SSML 与 LaTeX</Link>。SSML 的使用限制（支持的模型、音色和接口），请参见<Link href="/zh/model-studio/ssml-latex-user-guide#sl01_constraint_h3">使用限制</Link>。</span>

        默认值：false。

        `enable_ssml`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("<speak>你好</speak>")
              .voice("longanhuan_v3.6")
              .parameter("enable_ssml", true)
              .build();
          ```

          ```java 通过parameters设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("<speak>你好</speak>")
              .voice("longanhuan_v3.6")
              .parameters(Collections.singletonMap("enable_ssml", true))
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>word\_timestamp\_enabled</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>boolean</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是否开启字级别时间戳。</span>

        默认值：false。

        仅在流式输出模式下可用。支持复刻音色；支持的系统音色请参见[Qwen-Audio-TTS音色列表](/zh/model-studio/qwen-audio-tts-voice-list)。

        `word_timestamp_enabled`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameter("word_timestamp_enabled", true)
              .build();
          ```

          ```java 通过parameters设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameters(Collections.singletonMap("word_timestamp_enabled", true))
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>seed</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>int</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>生成时使用的随机数种子，使合成的效果产生变化。在模型版本、文本、音色及其他参数均相同的前提下，使用相同的seed可复现相同的合成结果。</span>

        默认值0。

        取值范围：\[0, 65535]。

        `seed`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameter("seed", 1234)
              .build();
          ```

          ```java 通过parameters设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameters(Collections.singletonMap("seed", 1234))
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>language\_hints</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>List</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <Tip>
          - 此参数为数组，但当前版本仅处理第一个元素，因此建议只传入一个值。
          - 此参数用于指定语音合成的目标语言，该设置与声音复刻时的样本音频的语种无关。如需设置复刻任务的源语言，请参见<Link href="/zh/model-studio/voice-clone-design-http-api#h2-create">声音复刻API参考</Link>。
        </Tip>

        指定语音合成的目标语言，提升合成效果。

        当数字、缩写、符号等朗读方式或者小语种合成效果不符合预期时使用，例如：

        - 数字朗读方式不符合预期，“hello, this is 110”读成“hello, this is one one zero”而非“hello, this is 幺幺零”
        - 符号朗读不准确，“@”读成“艾特”而非“at”
        - 小语种合成效果差，合成不自然

        <Accordion title="取值范围">
          * zh：中文
          * en：英语
          * fr：法语
          * de：德语
          * ja：日语
          * ko：韩语
          * ru：俄语
          * pt：葡萄牙语
          * th：泰语
          * id：印尼语
          * vi：越南语
          * es：西班牙语
          * it：意大利语
          * ms：马来西亚语
          * fil：菲律宾语
          * ar：阿拉伯语
          * other：其他语种（适用于 `qwen-audio-3.1-tts-flash`）

            保加利亚语、荷兰语、匈牙利语、罗马尼亚语、丹麦语、克罗地亚语、希腊语、捷克语、挪威语、瑞典语、印地语、斯洛伐克语、芬兰语、乌尔都语、塞尔维亚语、墨西哥西班牙语、立陶宛语、拉脱维亚语、土耳其语、波斯语、希伯来语、蒙古语、斯洛文尼亚语、阿塞拜疆语、波兰语、加泰罗尼亚语、高棉语、巴西葡萄牙语、老挝语、格鲁吉亚语、南非荷兰语、尼泊尔语、白俄罗斯语、马拉雅拉姆语、卢森堡语、阿尔巴尼亚语、泰米尔语、僧伽罗语、泰卢固语、爱沙尼亚语、宿务语、波斯尼亚语、缅甸语、亚美尼亚语、拉丁语、冰岛语、马耳他语、斯瓦希里语、哈萨克语、塞舌尔克里奥尔语、乌克兰语。
        </Accordion>

        `language_hints`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameter("language_hints", Arrays.asList("zh"))
              .build();
          ```

          ```java 通过parameters设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameters(Collections.singletonMap("language_hints", Arrays.asList("zh")))
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>instruction</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>String</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>设置指令，用于控制方言、情感或角色等合成效果。</span>

        具体用法请参见[非实时语音合成](/zh/model-studio/non-realtime-tts-user-guide)。

        `instruction`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameter("instruction", "请用非常开心的语气说话。")
              .build();
          ```

          ```java 通过parameters设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameters(Collections.singletonMap("instruction", "请用非常开心的语气说话。"))
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>bit\_rate</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>int</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音频码率（单位：kbps）。</span>

        默认值：32。

        取值范围：\[6, 510]。

        <Tip>
          仅在`format`为`opus`时支持使用该参数。
        </Tip>

        `bit_rate`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .format("opus")
              .parameter("bit_rate", 32)
              .build();
          ```

          ```java 通过parameters设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .format("opus")
              .parameters(Collections.singletonMap("bit_rate", 32))
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>enable\_aigc\_tag</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>boolean</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是否在生成的音频中添加AIGC隐性标识。设置为true时，会将隐性标识嵌入到支持格式（wav/mp3/opus）的音频中。</span>

        默认值：false。

        `enable_aigc_tag`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameter("enable_aigc_tag", true)
              .build();
          ```

          ```java 通过parameters设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameters(Collections.singletonMap("enable_aigc_tag", true))
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>aigc\_propagator</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>String</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>设置AIGC隐性标识中的 <code>ContentPropagator</code> 字段，用于标识内容的传播者。仅在 <code>enable\_aigc\_tag</code> 为 <code>true</code> 时生效。</span>

        默认值：阿里云UID。

        `aigc_propagator`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameter("enable_aigc_tag", true)
              .parameter("aigc_propagator", "xxxx")
              .build();
          ```

          ```java 通过parameters设置 expandable
          Map<String, Object> map = new HashMap<>();
          map.put("enable_aigc_tag", true);
          map.put("aigc_propagator", "xxxx");

          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameters(map)
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>aigc\_propagate\_id</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>String</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>设置AIGC隐性标识中的 <code>PropagateID</code> 字段，用于唯一标识一次具体的传播行为。仅在 <code>enable\_aigc\_tag</code> 为 <code>true</code> 时生效。</span>

        默认值：本次语音合成请求Request ID。

        `aigc_propagate_id`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameter("enable_aigc_tag", true)
              .parameter("aigc_propagate_id", "xxxx")
              .build();
          ```

          ```java 通过parameters设置 expandable
          Map<String, Object> map = new HashMap<>();
          map.put("enable_aigc_tag", true);
          map.put("aigc_propagate_id", "xxxx");

          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("我家的后面有一个很大的花园。")
              .voice("longanhuan_v3.6")
              .parameters(map)
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>hot\_fix</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>Map</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>文本热修复配置，用于自定义指定词语的发音或对待合成文本进行替换。</span>

        参数介绍：

        - pronunciation：自定义发音。指定词语的拼音标注，用于纠正默认发音不准确的情况。
        - replace：文本替换。在语音合成前将指定词语替换为目标文本，替换后的文本将作为实际合成内容。

        示例：

        ```json
        "hot_fix": {
          "pronunciation": [
            {"天气": "tian1 qi4"}
          ],
          "replace": [
            {"今天": "金天"}
          ]
        }
        ```

        `hot_fix`需要通过`HttpSpeechSynthesisParam`实例的`parameter`方法或者`parameters`方法进行设置：

        <CodeGroup>
          ```java 通过parameter设置 expandable
          Map<String, Object> hotFix = new HashMap<>();

          List<Map<String, String>> pronunciation = new ArrayList<>();
          Map<String, String> pronItem = new HashMap<>();
          pronItem.put("天气", "tian1 qi4");
          pronunciation.add(pronItem);
          hotFix.put("pronunciation", pronunciation);

          List<Map<String, String>> replace = new ArrayList<>();
          Map<String, String> replaceItem = new HashMap<>();
          replaceItem.put("今天", "金天");
          replace.add(replaceItem);
          hotFix.put("replace", replace);

          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("今天天气真好。")
              .voice("longanhuan_v3.6")
              .parameter("hot_fix", hotFix)
              .build();
          ```

          ```java 通过parameters设置
          // 构建hotFix对象同上
          HttpSpeechSynthesisParam param = HttpSpeechSynthesisParam.builder()
              .model("qwen-audio-3.0-tts-flash")
              .text("今天天气真好。")
              .voice("longanhuan_v3.6")
              .parameters(Collections.singletonMap("hot_fix", hotFix))
              .build();
          ```
        </CodeGroup>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `enable_markdown_filter`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `boolean`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        是否启用 Markdown 过滤。启用后，服务会在合成前将 Markdown 转换成适合朗读的文本。

        默认值：`false`。设为 `true` 启用。

        通过 `parameter("enable_markdown_filter", true)` 启用。
      </td>
    </tr>
  </tbody>
</table>

## 示例代码 <span id="h2-cv4a5b6b" />

以下示例展示Qwen-Audio-TTS语音合成的非流式和流式调用方式。运行前请确保已设置环境变量`DASHSCOPE_API_KEY`。

<Tip>
  不同模型需使用匹配的音色。更换模型时，请同步更换音色，并确认音色支持目标语言。具体对应关系请参见[Qwen-Audio-TTS音色列表](/zh/model-studio/qwen-audio-tts-voice-list)。
</Tip>

<Tabs>
  <Tab title="非流式调用">
    <span id="h3-cv4a5b7b" />

    非流式调用会等待服务端合成完成后一次性返回结果。根据返回类型的不同，提供以下两种方式：

    - `callAndReturnAudio()`：返回音频二进制数据（ByteBuffer），适用于直接保存或处理音频的场景。
    - `call()`：返回音频URL，适用于需要通过URL下载音频的场景。

    ```java expandable
    import com.alibaba.dashscope.audio.http_tts.AudioInfo;
    import com.alibaba.dashscope.audio.http_tts.HttpSpeechSynthesisParam;
    import com.alibaba.dashscope.audio.http_tts.HttpSpeechSynthesisResult;
    import com.alibaba.dashscope.audio.http_tts.HttpSpeechSynthesizer;
    import com.alibaba.dashscope.exception.ApiException;
    import com.alibaba.dashscope.exception.InputRequiredException;
    import com.alibaba.dashscope.exception.NoApiKeyException;
    import com.alibaba.dashscope.utils.Constants;

    import java.io.FileOutputStream;
    import java.io.IOException;
    import java.nio.ByteBuffer;

    public class QwenAudioTtsSyncExample {
        static {
            // 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
            Constants.baseHttpApiUrl = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1";
        }

        /**
         * 非流式调用示例一：返回音频数据（ByteBuffer）
         * 调用callAndReturnAudio方法，阻塞等待合成完成后返回完整音频数据。
         */
        public static void syncCallReturnAudio() {
            HttpSpeechSynthesizer synthesizer = new HttpSpeechSynthesizer();

            HttpSpeechSynthesisParam param =
                HttpSpeechSynthesisParam.builder()
                    .model("qwen-audio-3.0-tts-flash")  // 更换模型时，需同步更换为对应版本的音色
                    .text("我家的后面有一个很大的花园。")
                    .voice("longanhuan_v3.6")
                    .format("wav")
                    .sampleRate(24000)
                    // 未配置环境变量时，将下行替换为：apiKey("sk-xxx")，即替换为实际的API Key
                    .apiKey(System.getenv("DASHSCOPE_API_KEY"))
                    // 通过parameter方法设置额外参数
                    // .parameter("seed", 1234)
                    // .parameter("enable_ssml", true)
                    .build();

            try {
                ByteBuffer audioData = synthesizer.callAndReturnAudio(param);
                if (audioData != null && audioData.hasRemaining()) {
                    byte[] bytes = new byte[audioData.remaining()];
                    audioData.get(bytes);

                    try (FileOutputStream fos = new FileOutputStream("sync_output.wav")) {
                        fos.write(bytes);
                        System.out.println("Audio saved to sync_output.wav, size: " + bytes.length + " bytes");
                    } catch (IOException e) {
                        System.err.println("Failed to save audio: " + e.getMessage());
                    }
                }
            } catch (ApiException | NoApiKeyException | InputRequiredException e) {
                System.err.println("Synthesis failed: " + e.getMessage());
            }
            System.exit(0);
        }

        /**
         * 非流式调用示例二：返回音频URL
         * 调用call方法，返回包含音频URL的结果对象，可通过URL下载音频文件。
         */
        public static void syncCallReturnUrl() {
            HttpSpeechSynthesizer synthesizer = new HttpSpeechSynthesizer();

            HttpSpeechSynthesisParam param =
                HttpSpeechSynthesisParam.builder()
                    .model("qwen-audio-3.0-tts-flash")  // 更换模型时，需同步更换为对应版本的音色
                    .text("我家的后面有一个很大的花园。")
                    .voice("longanhuan_v3.6")
                    .format("wav")
                    .sampleRate(24000)
                    // 未配置环境变量时，将下行替换为：apiKey("sk-xxx")，即替换为实际的API Key
                    .apiKey(System.getenv("DASHSCOPE_API_KEY"))
                    .build();

            try {
                HttpSpeechSynthesisResult result = synthesizer.call(param);
                System.out.println("Request ID: " + result.getRequestId());

                if (result.hasAudioUrl()) {
                    AudioInfo audioInfo = result.getAudioInfo();
                    System.out.println("Audio URL: " + audioInfo.getUrl());
                    System.out.println("Expires At: " + audioInfo.getExpiresAt());
                    System.out.println("Remaining Time: " + audioInfo.getRemainingSeconds() + " seconds");
                }
            } catch (ApiException | NoApiKeyException | InputRequiredException e) {
                System.err.println("Synthesis failed: " + e.getMessage());
            }
        }

        public static void main(String[] args) {
            // 非流式调用示例一：返回音频数据（ByteBuffer）
            // syncCallReturnAudio();
            // 非流式调用示例二：返回音频URL
            syncCallReturnUrl();
        }
    }
    ```
  </Tab>

  <Tab title="流式调用">
    <span id="h3-cv4a5b8b" />

    流式调用通过回调函数分片返回音频数据，无需等待合成完成即可开始处理，适用于对首包延迟有要求的实时播放场景。

    ```java expandable
    import com.alibaba.dashscope.audio.http_tts.HttpSpeechSynthesisParam;
    import com.alibaba.dashscope.audio.http_tts.HttpSpeechSynthesisResult;
    import com.alibaba.dashscope.audio.http_tts.HttpSpeechSynthesizer;
    import com.alibaba.dashscope.common.ResultCallback;
    import com.alibaba.dashscope.exception.ApiException;
    import com.alibaba.dashscope.exception.InputRequiredException;
    import com.alibaba.dashscope.exception.NoApiKeyException;
    import com.alibaba.dashscope.utils.Constants;

    import java.io.FileOutputStream;
    import java.io.IOException;
    import java.util.concurrent.CountDownLatch;

    public class QwenAudioTtsStreamExample {
        static {
            // 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
            Constants.baseHttpApiUrl = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1";
        }

        public static void streamCallWithCallback() {
            HttpSpeechSynthesizer synthesizer = new HttpSpeechSynthesizer();

            HttpSpeechSynthesisParam param =
                    HttpSpeechSynthesisParam.builder()
                            .model("qwen-audio-3.0-tts-flash")  // 更换模型时，需同步更换为对应版本的音色
                            .text("今天天气真好，适合出去玩。")
                            .voice("longanhuan_v3.6")
                            .format("wav")
                            .sampleRate(24000)
                            // 未配置环境变量时，将下行替换为：apiKey("sk-xxx")，即替换为实际的API Key
                            .apiKey(System.getenv("DASHSCOPE_API_KEY"))
                            .build();

            CountDownLatch latch = new CountDownLatch(1);

            // 新增：创建输出文件流
            try (FileOutputStream fos = new FileOutputStream("output.wav")) {

                synthesizer.streamCall(param,
                        new ResultCallback<HttpSpeechSynthesisResult>() {
                            private int chunkCount = 0;

                            @Override
                            public void onEvent(HttpSpeechSynthesisResult result) {
                                chunkCount++;
                                if (result.hasAudioData()) {
                                    System.out.println("Received chunk #" + chunkCount
                                            + ", size: " + result.getAudioDataSize() + " bytes");
                                    try {
                                        fos.write(result.getAudioData());
                                    } catch (IOException e) {
                                        System.err.println("Failed to write audio data: " + e.getMessage());
                                    }
                                }
                                if (result.getRequestId() != null) {
                                    System.out.println("Request ID: " + result.getRequestId());
                                }
                            }

                            @Override
                            public void onComplete() {
                                System.out.println("Synthesis completed, total chunks: " + chunkCount);
                                System.out.println("Audio saved to output.wav");
                                latch.countDown();
                            }

                            @Override
                            public void onError(Exception e) {
                                System.err.println("Error during synthesis: " + e.getMessage());
                                latch.countDown();
                            }
                        });

                latch.await();

            } catch (ApiException | NoApiKeyException | InputRequiredException | InterruptedException e) {
                System.err.println("Failed: " + e.getMessage());
            } catch (IOException e) {
                System.err.println("Failed to create output file: " + e.getMessage());
            }
        }

        public static void main(String[] args) {
            streamCallWithCallback();
            System.exit(0);
        }
    }
    ```
  </Tab>
</Tabs>
