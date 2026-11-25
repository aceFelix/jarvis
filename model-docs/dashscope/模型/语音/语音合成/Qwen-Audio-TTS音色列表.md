> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Qwen-Audio-TTS音色列表

> 查看 Qwen-Audio-TTS 各模型的系统音色，试听音色效果，并下载基础音色列表和试听音频。

<meta property="og:title" content="Qwen-Audio-TTS音色列表" />

<Note>
  - 所选音色（`voice`）必须属于当前模型（`model`）支持的音色列表，不能跨模型混用。音色不匹配时，服务会返回 `InvalidParameter` 错误，例如 `[cosyvoice:]Engine error [411]: TTS speak operation failed`。
  - 待合成文本（`text`）应使用所选音色支持的语言，否则可能出现发音错误或语音不自然。

  如需专属音色，请参见[音色复刻](/zh/model-studio/voice-cloning-user-guide)或[音色设计](/zh/model-studio/voice-design-user-guide)。
</Note>

## 系统音色 <span id="system-voices" />

对于支持方言指令的音色，可通过指令控制字段 `instruction` 指定方言，例如 `请讲上海话。`。

### qwen-audio-3.1-tts-plus <span id="qwen-tts31-plus-voices" />

<Tabs>
  <Tab title="旗舰音色">
    <table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
      <colgroup>
        <col style={{ width: "30%" }} />

        <col style={{ width: "10%" }} />

        <col style={{ width: "25%" }} />

        <col style={{ width: "35%" }} />
      </colgroup>

      <thead>
        <tr>
          <th style={{ verticalAlign: "top" }}><strong>名称</strong></th>
          <th style={{ verticalAlign: "top" }}><strong>性别</strong></th>
          <th style={{ verticalAlign: "top" }}><strong>特性支持</strong></th>
          <th style={{ verticalAlign: "top" }}><strong>试听</strong></th>
        </tr>
      </thead>

      <tbody>
        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安灵心\_v3.1

            **voice 参数**: <code>longanlingxin\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <Tabs>
              <Tab title="试听1">
                ```text
                你好呀，我是龙安灵心。在这个快节奏的世界里，我想做你耳畔最温柔的守候。无论是深夜的失眠还是清晨的微光，只要你需要，我都会在这里，用声音拥抱你的每一个情绪瞬间。
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f9279.wav" type="audio/wav" />
                </audio>
              </Tab>

              <Tab title="试听2">
                ```text
                今天过得怎么样？有没有遇到什么开心的小事，或者让你眉头紧锁的烦恼？别担心，慢慢说给我听。我会安静地陪伴着你，帮你梳理思绪，让心情像雨后的天空一样，重新变得清澈明亮。
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f9278.wav" type="audio/wav" />
                </audio>
              </Tab>

              <Tab title="试听3">
                ```text
                月亮已经爬上了树梢，星星在眨着眼睛。闭上眼睛，想象自己躺在柔软的云朵上，随着微风轻轻飘荡。所有的疲惫都随风散去，现在，请安心入睡，愿你在甜美的梦乡中，遇见最美的风景。
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f927f.wav" type="audio/wav" />
                </audio>
              </Tab>
            </Tabs>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 许南川

            **voice 参数**: <code>xunanchuan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <Tabs>
              <Tab title="试听1">
                ```text
                遇见你之前，我的世界是黑白单调的；遇见你之后，色彩斑斓如雨后彩虹。我想牵着你的手，走过春夏秋冬，看遍世间繁华。无论风雨如何变幻，只要你在身边，便是人间最美风景。
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f927e.wav" type="audio/wav" />
                </audio>
              </Tab>

              <Tab title="试听2">
                ```text
                我是许南川，一个喜欢用声音讲述故事的人。我的声音或许不够华丽，但足够真诚。希望能通过这磁性的语调，带你走进不同的世界，感受文字背后的温度与力量，期待与你相遇。
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f927d.wav" type="audio/wav" />
                </audio>
              </Tab>

              <Tab title="试听3">
                ```text
                在这个喧嚣的时代，保持清醒是一种奢侈。我喜欢在安静的午后，泡一杯茶，读一本好书。那种心灵的宁静，比任何物质的享受都来得珍贵。你呢？是否也渴望这样片刻的安宁？
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f927c.wav" type="audio/wav" />
                </audio>
              </Tab>
            </Tabs>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安欢\_v3.1

            **voice 参数**: <code>longanhuan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <Tabs>
              <Tab title="试听1">
                ```text
                嗨，大家好！我是龙安欢。我喜欢笑，喜欢热闹，更喜欢看到大家快乐的样子。有什么烦心事尽管找我，保证让你笑着走出阴霾。让我们一起快乐起来吧！
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f9273.wav" type="audio/wav" />
                </audio>
              </Tab>

              <Tab title="试听2">
                ```text
                买衣服不要只看款式，更要看面料和剪裁。这件大衣版型正，羊毛含量高，穿在身上既保暖又有型。性价比超高，闭眼入都不会错。信我的准没错，赶紧入手一件过冬吧！
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f9272.wav" type="audio/wav" />
                </audio>
              </Tab>

              <Tab title="试听3">
                ```text
                天哪，今天的天气简直太糟糕了！出门忘带伞，结果淋成落汤鸡。不过没关系，回家喝杯热姜茶，洗个热水澡，又是一条好汉！你们今天过得怎么样？有没有什么趣事跟我分享呀？
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f9271.wav" type="audio/wav" />
                </audio>
              </Tab>
            </Tabs>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安风悦\_v3.1

            **voice 参数**: <code>longanfengyue\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <Tabs>
              <Tab title="试听1">
                ```text
                明天白天多云转晴，气温适宜，最高二十度，最低十五度。风力不大，非常适合户外活动。不过早晚温差较大，建议大家适当增减衣物，预防感冒。出行前记得查看实时路况哦。
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f9270.wav" type="audio/wav" />
                </audio>
              </Tab>

              <Tab title="试听2">
                ```text
                最近我在看一部老电影，里面的配乐特别经典，听得我忍不住单曲循环。你平时喜欢听什么类型的音乐？是轻快的流行乐，还是安静的纯音乐？推荐几首给我呗，我想换换口味。
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f9277.wav" type="audio/wav" />
                </audio>
              </Tab>

              <Tab title="试听3">
                ```text
                外婆坐在藤椅上，手里织着那条没完工的围巾，针脚细密而整齐。夕阳透过窗棂洒在她银白的发丝上，泛着柔和的光晕。她抬起头，笑着对我说：‘天凉了，快戴上吧。’那一刻，时光仿佛静止。
                ```

                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
                  <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/audios/6a4b3c2d1e0f9276.wav" type="audio/wav" />
                </audio>
              </Tab>
            </Tabs>
          </td>
        </tr>
      </tbody>
    </table>
  </Tab>
</Tabs>

### qwen-audio-3.1-tts-flash <span id="qwen-tts31-voices" />

<Note>
  多语种与方言音色、精品英文音色、小语种音色支持北京和新加坡地域，其他类型音色仅支持北京地域。
</Note>

<Tabs>
  <Tab title="多语种与方言音色">
    <span id="qwen-tts31-multilingual" />

    以下四个音色均支持下列全部方言和语言。

    - 方言：上海话、广东话、东北话、重庆话、陕西话、云南话、宁波话、甘肃话。
    - 语言：日语、韩语、法语、德语、葡萄牙语、意大利语、越南语、印尼语。

    <span id="qwen-tts31-language-hints" />

    使用下表中的音色合成方言或部分小语种时，可通过 `language_hints` 指定目标语种或方言。

    <Accordion title="点击查看语种和方言的设置方法">
      将语种或方言代号作为数组传给 `language_hints`，每次只传一个代号。待合成文本应与指定的语种或方言一致。合成普通话或英语时，无需设置此参数。

      <Note>
        如果同时使用 `instruction`，可用它控制语气或情感。不要在 `instruction` 中重复指定语种或方言，也不要指定不同的语种或方言。
      </Note>

      **示例**

      ```json
      {
        "text": "侬好，今朝天气蛮好。",
        "voice": "longanlingxin_v3.1",
        "language_hints": ["zh-sh"]
      }
      ```

      **语种和方言取值范围**

      <Note>
        未列出的语种或方言可传 `["other"]`，但不保证所选音色支持。
      </Note>

      <Tabs>
        <Tab title="语种">
          - 日语：`ja`
          - 韩语：`ko`
          - 法语：`fr`
          - 德语：`de`
          - 葡萄牙语：`pt`
          - 意大利语：`it`
          - 越南语：`vi`
          - 印度尼西亚语：`id`
          - 阿拉伯语：`ar`
          - 西班牙语：`es`
          - 马来语：`ms`
          - 俄语：`ru`
          - 泰语：`th`
          - 菲律宾语：`tl`
        </Tab>

        <Tab title="方言">
          - 广东话：`zh-yue`
          - 东北话：`zh-ne`
          - 甘肃话：`zh-gs`
          - 贵州话：`zh-gz`
          - 河南话：`zh-ha`
          - 河北话：`zh-he`
          - 湖北话：`zh-hb`
          - 湖南话：`zh-hn`
          - 宁夏话：`zh-nx`
          - 陕西话：`zh-sx`
          - 山西话：`zh-shan`
          - 山东话：`zh-sd`
          - 青岛话：`zh-qd`
          - 四川话：`zh-sc`
          - 重庆话：`zh-cq`
          - 云南话：`zh-yn`
          - 江西话：`zh-jx`
          - 浙江话：`zh-zj`
          - 宁波话：`zh-nb`
          - 上海话：`zh-sh`
        </Tab>
      </Tabs>
    </Accordion>

    <table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
      <colgroup>
        <col style={{ width: "30%" }} />

        <col style={{ width: "10%" }} />

        <col style={{ width: "25%" }} />

        <col style={{ width: "35%" }} />
      </colgroup>

      <thead>
        <tr>
          <th style={{ verticalAlign: "top" }}>
            **音色信息**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **性别**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **特性支持**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **试听**
          </th>
        </tr>
      </thead>

      <tbody>
        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安欢\_v3.1

            **voice 参数**: <code>longanhuan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <Tabs>
              <Tab title="重庆话">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9ced.wav" />
              </Tab>

              <Tab title="宁波话">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cec.wav" />
              </Tab>

              <Tab title="韩语">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9ce3.wav" />
              </Tab>

              <Tab title="印尼语">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9ce2.wav" />
              </Tab>
            </Tabs>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安灵心\_v3.1

            **voice 参数**: <code>longanlingxin\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <Tabs>
              <Tab title="云南话">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9ce1.wav" />
              </Tab>

              <Tab title="陕西话">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9ce0.wav" />
              </Tab>

              <Tab title="上海话">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9ce7.wav" />
              </Tab>

              <Tab title="法语">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9ce6.wav" />
              </Tab>

              <Tab title="意大利语">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9ce5.wav" />
              </Tab>
            </Tabs>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安风悦\_v3.1

            **voice 参数**: <code>longanfengyue\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <Tabs>
              <Tab title="东北话">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9ce4.wav" />
              </Tab>

              <Tab title="越南语">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cfb.wav" />
              </Tab>

              <Tab title="日语">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cfa.wav" />
              </Tab>
            </Tabs>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 许南川

            **voice 参数**: <code>xunanchuan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <Tabs>
              <Tab title="甘肃话">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cf9.wav" />
              </Tab>

              <Tab title="东北话">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cf8.wav" />
              </Tab>

              <Tab title="法语">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cff.wav" />
              </Tab>

              <Tab title="葡萄牙语">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cfe.wav" />
              </Tab>
            </Tabs>
          </td>
        </tr>
      </tbody>
    </table>
  </Tab>

  <Tab title="精品中文音色">
    <span id="qwen-tts31-chinese" />

    以下音色仅支持中文普通话。

    <Note>
      Bella\_v3.1 同时支持中文普通话和英文。
    </Note>

    <table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
      <colgroup>
        <col style={{ width: "23%" }} />

        <col style={{ width: "7%" }} />

        <col style={{ width: "12%" }} />

        <col style={{ width: "13%" }} />

        <col style={{ width: "20%" }} />

        <col style={{ width: "25%" }} />
      </colgroup>

      <thead>
        <tr>
          <th style={{ verticalAlign: "top" }}>
            **音色信息**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **性别**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **声线特质**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **适用场景**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **特性支持**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **试听**
          </th>
        </tr>
      </thead>

      <tbody>
        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 于小云

            **voice 参数**: <code>yuxiaoyun\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            元气、亲切、自然
          </td>

          <td style={{ verticalAlign: "top" }}>
            广告营销、广播、客服助手、旁白
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cfd.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 乔小娇

            **voice 参数**: <code>qiaoxiaojiao\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            俏丽、可爱
          </td>

          <td style={{ verticalAlign: "top" }}>
            广告营销、客服助手、有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cfc.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 夏小晨

            **voice 参数**: <code>xiaxiaochen\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            元气、明亮
          </td>

          <td style={{ verticalAlign: "top" }}>
            广告营销、有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cf2.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 安明远

            **voice 参数**: <code>anmingyuan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            清亮、自然
          </td>

          <td style={{ verticalAlign: "top" }}>
            广告营销、有声书、旁白
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cf1.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 温怀清

            **voice 参数**: <code>wenhuaiqing\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            清亮、柔和
          </td>

          <td style={{ verticalAlign: "top" }}>
            儿童故事、客服助手、广告营销、新闻播报
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cf0.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 安小岚

            **voice 参数**: <code>anxiaolan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            清甜、纯净
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书、客服助手、旁白、新闻播报、广告营销
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cf7.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 谢舒柔

            **voice 参数**: <code>xieshurou\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            柔和、自然、知性
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书、客服助手、旁白
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9cf5.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 白清岚

            **voice 参数**: <code>baiqinglan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            明亮、清纯
          </td>

          <td style={{ verticalAlign: "top" }}>
            语音助手、客服助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c0b.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 许玉远

            **voice 参数**: <code>xuyuyuan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            知性、成熟、质感
          </td>

          <td style={{ verticalAlign: "top" }}>
            广告营销、新闻播报、旁白、客服助手、有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c0a.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 安若柔

            **voice 参数**: <code>anruorou\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            气声、知性
          </td>

          <td style={{ verticalAlign: "top" }}>
            旁白、语音助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c09.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 闻怀之

            **voice 参数**: <code>wenhuaizhi\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            稳重、成熟
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书、新闻播报、广告营销、客服助手、旁白
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c08.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 萧行之

            **voice 参数**: <code>xiaoxingzhi\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            端庄、贵气
          </td>

          <td style={{ verticalAlign: "top" }}>
            新闻播报、有声书、旁白、客服助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c0f.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 顾云舒

            **voice 参数**: <code>guyunshu\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            成熟、稳重
          </td>

          <td style={{ verticalAlign: "top" }}>
            音乐电台、客服助手、有声书、旁白
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c0e.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 霍拙石

            **voice 参数**: <code>huozhuoshi\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            清亮
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书、广告营销、旁白
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c0d.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 叶清禾

            **voice 参数**: <code>yeqinghe\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            亲切、温柔
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书、广告营销、旁白、客服助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c0c.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 云欢欢

            **voice 参数**: <code>yunhuanhuan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            高亢、热情
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书、旁白、客服助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c03.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 徐小俏

            **voice 参数**: <code>xuxiaoqiao\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            自然、俏皮
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书、旁白、客服助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c02.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 白安然

            **voice 参数**: <code>baianran\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            低沉、浑厚、气声
          </td>

          <td style={{ verticalAlign: "top" }}>
            配音讲解、有声书、旁白
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c01.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 许言初

            **voice 参数**: <code>xuyanchu\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            沉稳、磁性
          </td>

          <td style={{ verticalAlign: "top" }}>
            新闻播报、有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c00.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 叶知晴

            **voice 参数**: <code>yezhiqing\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            轻快、自然
          </td>

          <td style={{ verticalAlign: "top" }}>
            儿童故事、客服助手、语音助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c05.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 安迪

            **voice 参数**: <code>andi\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            ABC口音
          </td>

          <td style={{ verticalAlign: "top" }}>
            语音助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c1b.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 安语晴

            **voice 参数**: <code>anyuqing\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            甜妹
          </td>

          <td style={{ verticalAlign: "top" }}>
            语音助手、旁白、新闻播报
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c1a.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 林小灵

            **voice 参数**: <code>linxiaoling\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            甜妹
          </td>

          <td style={{ verticalAlign: "top" }}>
            语音助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93c1.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙菲菲\_v3.1

            **voice 参数**: <code>longfeifei\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            甜妹
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93c0.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙婧\_v3.1

            **voice 参数**: <code>longjing\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            质感、沉稳
          </td>

          <td style={{ verticalAlign: "top" }}>
            新闻播报
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93c7.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙嫱\_v3.1

            **voice 参数**: <code>longqiang\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            浪漫、风情
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93c6.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙颜\_v3.1

            **voice 参数**: <code>longyan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            温暖、自然、亲切
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93c5.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙小白\_v3.1

            **voice 参数**: <code>longxiaobai\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            磁性、质感
          </td>

          <td style={{ verticalAlign: "top" }}>
            新闻播报
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93c4.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙悦\_v3.1

            **voice 参数**: <code>longyue\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            温暖、磁性
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93db.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙橙\_v3.1

            **voice 参数**: <code>longcheng\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            智慧、磁性
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93da.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙老铁\_v3.1

            **voice 参数**: <code>longlaotie\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            东北、直率
          </td>

          <td style={{ verticalAlign: "top" }}>
            方言
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93d9.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙邵\_v3.1

            **voice 参数**: <code>longshao\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            积极、元气
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93d8.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙书\_v3.1

            **voice 参数**: <code>longshu\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            沉稳、磁性
          </td>

          <td style={{ verticalAlign: "top" }}>
            新闻播报
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93df.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙泽\_v3.1

            **voice 参数**: <code>longze\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            温暖、元气、自然
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93de.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙浩\_v3.1

            **voice 参数**: <code>longhao\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            多情、忧郁
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93dd.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙津\_v3.1

            **voice 参数**: <code>longjin\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            优雅、温润
          </td>

          <td style={{ verticalAlign: "top" }}>
            诗词朗诵
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93dc.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙楠\_v3.1

            **voice 参数**: <code>longnan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            睿智
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93d3.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙硕\_v3.1

            **voice 参数**: <code>longshuo\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            博才、干练
          </td>

          <td style={{ verticalAlign: "top" }}>
            新闻播报
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93d2.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙天\_v3.1

            **voice 参数**: <code>longtian\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            磁性、理智
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93d1.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙小诚\_v3.1

            **voice 参数**: <code>longxiaocheng\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            磁性、低音
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93d0.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙可\_v3.1

            **voice 参数**: <code>longke\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            儿童
          </td>

          <td style={{ verticalAlign: "top" }}>
            懵懂
          </td>

          <td style={{ verticalAlign: "top" }}>
            童声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93d7.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Bella\_v3.1

            **voice 参数**: <code>loongbella\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            干练、磁性、知性
          </td>

          <td style={{ verticalAlign: "top" }}>
            英文、新闻播报
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <Tabs>
              <Tab title="中文试听">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93d6.wav" />
              </Tab>

              <Tab title="英文试听">
                <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93d5.wav" />
              </Tab>
            </Tabs>
          </td>
        </tr>
      </tbody>
    </table>
  </Tab>

  <Tab title="精品英文音色">
    <span id="qwen-tts31-english" />

    以下音色仅支持英文。

    <table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
      <colgroup>
        <col style={{ width: "27%" }} />

        <col style={{ width: "8%" }} />

        <col style={{ width: "12%" }} />

        <col style={{ width: "23%" }} />

        <col style={{ width: "30%" }} />
      </colgroup>

      <thead>
        <tr>
          <th style={{ verticalAlign: "top" }}>
            **音色信息**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **性别**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **口音**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **特性支持**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **试听**
          </th>
        </tr>
      </thead>

      <tbody>
        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Emily

            **voice 参数**: <code>Emily\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            英式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c19.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Luna

            **voice 参数**: <code>Luna\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            英式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c18.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Eric

            **voice 参数**: <code>Eric\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            英式男声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c1f.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Luca

            **voice 参数**: <code>Luca\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            英式男声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c1e.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Abby

            **voice 参数**: <code>Abby\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c1d.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Annie

            **voice 参数**: <code>Annie\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c1c.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Ava

            **voice 参数**: <code>Ava\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c13.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Beth

            **voice 参数**: <code>Beth\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c12.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Betty

            **voice 参数**: <code>Betty\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c11.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Cally

            **voice 参数**: <code>Cally\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c10.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Cindy

            **voice 参数**: <code>Cindy\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c17.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Donna

            **voice 参数**: <code>Donna\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式女声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c16.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Andy

            **voice 参数**: <code>Andy\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式男声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c15.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: Brian

            **voice 参数**: <code>Brian\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式男声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c14.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: David

            **voice 参数**: <code>David\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            美式男声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c2b.wav" />
          </td>
        </tr>
      </tbody>
    </table>
  </Tab>

  <Tab title="其他系统音色">
    <span id="qwen-tts31-additional" />

    <table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
      <colgroup>
        <col style={{ width: "23%" }} />

        <col style={{ width: "7%" }} />

        <col style={{ width: "12%" }} />

        <col style={{ width: "13%" }} />

        <col style={{ width: "20%" }} />

        <col style={{ width: "25%" }} />
      </colgroup>

      <thead>
        <tr>
          <th style={{ verticalAlign: "top" }}>
            **音色信息**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **性别**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **声线特质**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **适用场景**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **特性支持**
          </th>

          <th style={{ verticalAlign: "top" }}>
            **试听**
          </th>
        </tr>
      </thead>

      <tbody>
        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安元妃\_v3.1

            **voice 参数**: <code>longanyuanfei\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            高傲妃子音
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c2a.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙杰力豆\_v3.1

            **voice 参数**: <code>longjielidou\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            天真男童音
          </td>

          <td style={{ verticalAlign: "top" }}>
            儿童陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c29.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安灵希\_v3.1

            **voice 参数**: <code>longanlingxi\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            可爱甜美音
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴（精品中文）
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c2f.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙火火\_v3.1

            **voice 参数**: <code>longhuohuo\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            顽皮少年音
          </td>

          <td style={{ verticalAlign: "top" }}>
            角色音
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c28.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙应桃\_v3.1

            **voice 参数**: <code>longyingtao\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            温柔淡定女
          </td>

          <td style={{ verticalAlign: "top" }}>
            客服
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c2e.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安雅\_v3.1

            **voice 参数**: <code>longanya\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            高雅气质女
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c2d.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙婉\_v3.1

            **voice 参数**: <code>longwan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            细腻柔声女
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c2c.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙星\_v3.1

            **voice 参数**: <code>longxing\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            温婉邻家女
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c23.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙华\_v3.1

            **voice 参数**: <code>longhua\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            元气甜美女
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c22.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙寒\_v3.1

            **voice 参数**: <code>longhan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            温暖痴情男
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c21.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安智\_v3.1

            **voice 参数**: <code>longanzhi\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            睿智轻熟男
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c20.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙哲\_v3.1

            **voice 参数**: <code>longzhe\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            呆板大暖男
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c27.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安洋\_v3.1

            **voice 参数**: <code>longanyang\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            阳光大男孩
          </td>

          <td style={{ verticalAlign: "top" }}>
            社交陪伴（标杆音色）
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c26.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 李白\_v3.1

            **voice 参数**: <code>libai\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            古代诗仙男
          </td>

          <td style={{ verticalAlign: "top" }}>
            诗词朗诵
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c25.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙铃\_v3.1

            **voice 参数**: <code>longling\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            稚气呆板女
          </td>

          <td style={{ verticalAlign: "top" }}>
            童声
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c24.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙牛牛\_v3.1

            **voice 参数**: <code>longniuniu\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            阳光男童声
          </td>

          <td style={{ verticalAlign: "top" }}>
            消费电子-儿童有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c3b.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙闪闪\_v3.1

            **voice 参数**: <code>longshanshan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            戏剧化童声
          </td>

          <td style={{ verticalAlign: "top" }}>
            消费电子-儿童有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c3a.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙泡泡\_v3.1

            **voice 参数**: <code>longpaopao\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            飞天泡泡音
          </td>

          <td style={{ verticalAlign: "top" }}>
            消费电子-儿童陪伴
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c39.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: loongstella\_v3.1

            **voice 参数**: <code>loongstella\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            飒爽利落女
          </td>

          <td style={{ verticalAlign: "top" }}>
            新闻播报
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c38.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙媛\_v3.1

            **voice 参数**: <code>longyuan\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            温暖治愈女
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c3f.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙妙\_v3.1

            **voice 参数**: <code>longmiao\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            抑扬顿挫女
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c3e.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙三叔\_v3.1

            **voice 参数**: <code>longsanshu\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            沉稳质感男
          </td>

          <td style={{ verticalAlign: "top" }}>
            有声书
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c3d.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安莉\_v3.1

            **voice 参数**: <code>longanli\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            利落从容女
          </td>

          <td style={{ verticalAlign: "top" }}>
            语音助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c3c.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安温\_v3.1

            **voice 参数**: <code>longanwen\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            优雅知性女
          </td>

          <td style={{ verticalAlign: "top" }}>
            语音助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c33.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安朗\_v3.1

            **voice 参数**: <code>longanlang\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            清爽利落男
          </td>

          <td style={{ verticalAlign: "top" }}>
            语音助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c32.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙小夏\_v3.1

            **voice 参数**: <code>longxiaoxia\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            沉稳权威女
          </td>

          <td style={{ verticalAlign: "top" }}>
            语音助手
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c31.wav" />
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **名称**: 龙安冲\_v3.1

            **voice 参数**: <code>longanchong\_v3.1</code>
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            激情推销男
          </td>

          <td style={{ verticalAlign: "top" }}>
            直播带货
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }} src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9c30.wav" />
          </td>
        </tr>
      </tbody>
    </table>
  </Tab>

  <Tab title="小语种音色">
    <table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
      <colgroup>
        <col style={{ width: "28%" }} />

        <col style={{ width: "13%" }} />

        <col style={{ width: "7%" }} />

        <col style={{ width: "22%" }} />

        <col style={{ width: "30%" }} />
      </colgroup>

      <thead>
        <tr>
          <th style={{ verticalAlign: "top" }}><strong>音色信息</strong></th>
          <th style={{ verticalAlign: "top" }}><strong>语种</strong></th>
          <th style={{ verticalAlign: "top" }}><strong>性别</strong></th>
          <th style={{ verticalAlign: "top" }}><strong>特性支持</strong></th>
          <th style={{ verticalAlign: "top" }}><strong>试听</strong></th>
        </tr>
      </thead>

      <tbody>
        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Zora

            **本地名称**: Зора

            **voice 参数**: `qwen-audio-3.1-tts-flash-zora_bg`
          </td>

          <td style={{ verticalAlign: "top" }}>
            保加利亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93fd.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Linde

            **本地名称**: Linde

            **voice 参数**: `qwen-audio-3.1-tts-flash-linde_nl`
          </td>

          <td style={{ verticalAlign: "top" }}>
            荷兰语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93fe.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Hajnal

            **本地名称**: Hajnal

            **voice 参数**: `qwen-audio-3.1-tts-flash-hajnal_hu`
          </td>

          <td style={{ verticalAlign: "top" }}>
            匈牙利语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93ff.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Senin

            **本地名称**: Senin

            **voice 参数**: `qwen-audio-3.1-tts-flash-senin_ro`
          </td>

          <td style={{ verticalAlign: "top" }}>
            罗马尼亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93fc.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Birk

            **本地名称**: Birk

            **voice 参数**: `qwen-audio-3.1-tts-flash-birk_da`
          </td>

          <td style={{ verticalAlign: "top" }}>
            丹麦语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93f3.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Lipa

            **本地名称**: Lipa

            **voice 参数**: `qwen-audio-3.1-tts-flash-lipa_hr`
          </td>

          <td style={{ verticalAlign: "top" }}>
            克罗地亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93f2.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Avgi

            **本地名称**: Αυγή

            **voice 参数**: `qwen-audio-3.1-tts-flash-avgi_el`
          </td>

          <td style={{ verticalAlign: "top" }}>
            希腊语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93f1.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Jitrenka

            **本地名称**: Jitřenka

            **voice 参数**: `qwen-audio-3.1-tts-flash-jitrenka_cs`
          </td>

          <td style={{ verticalAlign: "top" }}>
            捷克语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93f0.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Solglimt

            **本地名称**: Solglimt

            **voice 参数**: `qwen-audio-3.1-tts-flash-solglimt_nn`
          </td>

          <td style={{ verticalAlign: "top" }}>
            挪威语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93f7.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Glimma

            **本地名称**: Glimma

            **voice 参数**: `qwen-audio-3.1-tts-flash-glimma_sv`
          </td>

          <td style={{ verticalAlign: "top" }}>
            瑞典语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93f6.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Svar

            **本地名称**: स्वर

            **voice 参数**: `qwen-audio-3.1-tts-flash-svar_hi`
          </td>

          <td style={{ verticalAlign: "top" }}>
            印地语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93f5.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Iskra

            **本地名称**: Iskra

            **voice 参数**: `qwen-audio-3.1-tts-flash-iskra_sk`
          </td>

          <td style={{ verticalAlign: "top" }}>
            斯洛伐克语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f93f4.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Aava

            **本地名称**: Aava

            **voice 参数**: `qwen-audio-3.1-tts-flash-aava_fi`
          </td>

          <td style={{ verticalAlign: "top" }}>
            芬兰语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f930b.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Sahar

            **本地名称**: سحر

            **voice 参数**: `qwen-audio-3.1-tts-flash-sahar_ur`
          </td>

          <td style={{ verticalAlign: "top" }}>
            乌尔都语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f930a.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Jasna

            **本地名称**: Јасна

            **voice 参数**: `qwen-audio-3.1-tts-flash-jasna_sr`
          </td>

          <td style={{ verticalAlign: "top" }}>
            塞尔维亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9309.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Brisa

            **本地名称**: Brisa

            **voice 参数**: `qwen-audio-3.1-tts-flash-brisa_es_mx`
          </td>

          <td style={{ verticalAlign: "top" }}>
            墨西哥西班牙语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9308.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Ausra

            **本地名称**: Aušra

            **voice 参数**: `qwen-audio-3.1-tts-flash-ausra_lt`
          </td>

          <td style={{ verticalAlign: "top" }}>
            立陶宛语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f930f.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Rasa

            **本地名称**: Rasa

            **voice 参数**: `qwen-audio-3.1-tts-flash-rasa_lv`
          </td>

          <td style={{ verticalAlign: "top" }}>
            拉脱维亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f930e.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Deniz

            **本地名称**: Deniz

            **voice 参数**: `qwen-audio-3.1-tts-flash-deniz_tr`
          </td>

          <td style={{ verticalAlign: "top" }}>
            土耳其语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f930d.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Ava

            **本地名称**: آوا

            **voice 参数**: `qwen-audio-3.1-tts-flash-ava_fa`
          </td>

          <td style={{ verticalAlign: "top" }}>
            波斯语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f930c.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Or

            **本地名称**: אור

            **voice 参数**: `qwen-audio-3.1-tts-flash-or_he`
          </td>

          <td style={{ verticalAlign: "top" }}>
            希伯来语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9303.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Ayalguu

            **本地名称**: Аялгуу

            **voice 参数**: `qwen-audio-3.1-tts-flash-ayalguu_mn`
          </td>

          <td style={{ verticalAlign: "top" }}>
            蒙古语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9302.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Zarja

            **本地名称**: Zarja

            **voice 参数**: `qwen-audio-3.1-tts-flash-zarja_sl`
          </td>

          <td style={{ verticalAlign: "top" }}>
            斯洛文尼亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9301.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Ishiq

            **本地名称**: İşıq

            **voice 参数**: `qwen-audio-3.1-tts-flash-ishiq_az`
          </td>

          <td style={{ verticalAlign: "top" }}>
            阿塞拜疆语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9300.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Zorza

            **本地名称**: Zorza

            **voice 参数**: `qwen-audio-3.1-tts-flash-zorza_pl`
          </td>

          <td style={{ verticalAlign: "top" }}>
            波兰语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9307.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Alba

            **本地名称**: Alba

            **voice 参数**: `qwen-audio-3.1-tts-flash-alba_ca`
          </td>

          <td style={{ verticalAlign: "top" }}>
            加泰罗尼亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9306.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Romduol

            **本地名称**: រំដួល

            **voice 参数**: `qwen-audio-3.1-tts-flash-romduol_km`
          </td>

          <td style={{ verticalAlign: "top" }}>
            高棉语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9305.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Aurora

            **本地名称**: Aurora

            **voice 参数**: `qwen-audio-3.1-tts-flash-aurora_pt_br`
          </td>

          <td style={{ verticalAlign: "top" }}>
            巴西葡萄牙语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9304.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Dao

            **本地名称**: ດາວ

            **voice 参数**: `qwen-audio-3.1-tts-flash-dao_lo`
          </td>

          <td style={{ verticalAlign: "top" }}>
            老挝语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f931b.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Tsiskari

            **本地名称**: ცისკარი

            **voice 参数**: `qwen-audio-3.1-tts-flash-tsiskari_ka`
          </td>

          <td style={{ verticalAlign: "top" }}>
            格鲁吉亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f931a.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Lente

            **本地名称**: Lente

            **voice 参数**: `qwen-audio-3.1-tts-flash-lente_af`
          </td>

          <td style={{ verticalAlign: "top" }}>
            南非荷兰语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9319.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Kiran

            **本地名称**: किरण

            **voice 参数**: `qwen-audio-3.1-tts-flash-kiran_ne`
          </td>

          <td style={{ verticalAlign: "top" }}>
            尼泊尔语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9318.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Zorka

            **本地名称**: Зорка

            **voice 参数**: `qwen-audio-3.1-tts-flash-zorka_be`
          </td>

          <td style={{ verticalAlign: "top" }}>
            白俄罗斯语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f931f.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Nilaav

            **本地名称**: നിലാവ്

            **voice 参数**: `qwen-audio-3.1-tts-flash-nilaav_ml`
          </td>

          <td style={{ verticalAlign: "top" }}>
            马拉雅拉姆语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f931e.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Staer

            **本地名称**: Stär

            **voice 参数**: `qwen-audio-3.1-tts-flash-staer_lb`
          </td>

          <td style={{ verticalAlign: "top" }}>
            卢森堡语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f931d.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Drita

            **本地名称**: Drita

            **voice 参数**: `qwen-audio-3.1-tts-flash-drita_sq`
          </td>

          <td style={{ verticalAlign: "top" }}>
            阿尔巴尼亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f931c.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Nila

            **本地名称**: நிலா

            **voice 参数**: `qwen-audio-3.1-tts-flash-nila_ta`
          </td>

          <td style={{ verticalAlign: "top" }}>
            泰米尔语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9313.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Aruna

            **本地名称**: අරුණ

            **voice 参数**: `qwen-audio-3.1-tts-flash-aruna_si`
          </td>

          <td style={{ verticalAlign: "top" }}>
            僧伽罗语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9312.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Vennela

            **本地名称**: వెన్నెల

            **voice 参数**: `qwen-audio-3.1-tts-flash-vennela_te`
          </td>

          <td style={{ verticalAlign: "top" }}>
            泰卢固语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9311.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Sade

            **本地名称**: Säde

            **voice 参数**: `qwen-audio-3.1-tts-flash-sade_et`
          </td>

          <td style={{ verticalAlign: "top" }}>
            爱沙尼亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9310.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Bituon

            **本地名称**: Bituon

            **voice 参数**: `qwen-audio-3.1-tts-flash-bituon_ceb`
          </td>

          <td style={{ verticalAlign: "top" }}>
            宿务语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9317.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Vedrina

            **本地名称**: Vedrina

            **voice 参数**: `qwen-audio-3.1-tts-flash-vedrina_bs`
          </td>

          <td style={{ verticalAlign: "top" }}>
            波斯尼亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9316.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: LaYaung

            **本地名称**: လရောင်

            **voice 参数**: `qwen-audio-3.1-tts-flash-layaung_my`
          </td>

          <td style={{ verticalAlign: "top" }}>
            缅甸语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9315.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Luys

            **本地名称**: Լույս

            **voice 参数**: `qwen-audio-3.1-tts-flash-luys_hy`
          </td>

          <td style={{ verticalAlign: "top" }}>
            亚美尼亚语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9314.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Lumen

            **本地名称**: Lumen

            **voice 参数**: `qwen-audio-3.1-tts-flash-lumen_la`
          </td>

          <td style={{ verticalAlign: "top" }}>
            拉丁语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f932b.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Birta

            **本地名称**: Birta

            **voice 参数**: `qwen-audio-3.1-tts-flash-birta_is`
          </td>

          <td style={{ verticalAlign: "top" }}>
            冰岛语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f932a.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Zerniq

            **本地名称**: Żerniq

            **voice 参数**: `qwen-audio-3.1-tts-flash-zerniq_mt`
          </td>

          <td style={{ verticalAlign: "top" }}>
            马耳他语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9329.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Nuru

            **本地名称**: Nuru

            **voice 参数**: `qwen-audio-3.1-tts-flash-nuru_sw`
          </td>

          <td style={{ verticalAlign: "top" }}>
            斯瓦希里语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9328.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Arai

            **本地名称**: Арай

            **voice 参数**: `qwen-audio-3.1-tts-flash-arai_kk`
          </td>

          <td style={{ verticalAlign: "top" }}>
            哈萨克语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f932f.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Zetwal

            **本地名称**: Zetwal

            **voice 参数**: `qwen-audio-3.1-tts-flash-zetwal_crs`
          </td>

          <td style={{ verticalAlign: "top" }}>
            塞舌尔克里奥尔语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f932e.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Zoria

            **本地名称**: Зоря

            **voice 参数**: `qwen-audio-3.1-tts-flash-zoria_uk`
          </td>

          <td style={{ verticalAlign: "top" }}>
            乌克兰语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f932d.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Nagi

            **本地名称**: 凪

            **voice 参数**: `qwen-audio-3.1-tts-flash-nagi_ja`
          </td>

          <td style={{ verticalAlign: "top" }}>
            日语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9062.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Haneul

            **本地名称**: 하늘

            **voice 参数**: `qwen-audio-3.1-tts-flash-haneul_ko`
          </td>

          <td style={{ verticalAlign: "top" }}>
            韩语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9061.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Aube

            **本地名称**: Aube

            **voice 参数**: `qwen-audio-3.1-tts-flash-aube_fr`
          </td>

          <td style={{ verticalAlign: "top" }}>
            法语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9060.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Lerche

            **本地名称**: Lerche

            **voice 参数**: `qwen-audio-3.1-tts-flash-lerche_de`
          </td>

          <td style={{ verticalAlign: "top" }}>
            德语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9067.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Veter

            **本地名称**: Ветер

            **voice 参数**: `qwen-audio-3.1-tts-flash-veter_ru`
          </td>

          <td style={{ verticalAlign: "top" }}>
            俄语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f907b.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Rugiada

            **本地名称**: Rugiada

            **voice 参数**: `qwen-audio-3.1-tts-flash-rugiada_it`
          </td>

          <td style={{ verticalAlign: "top" }}>
            意大利语
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9066.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Albor

            **本地名称**: Albor

            **voice 参数**: `qwen-audio-3.1-tts-flash-albor_es`
          </td>

          <td style={{ verticalAlign: "top" }}>
            西班牙语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9065.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Farol

            **本地名称**: Farol

            **voice 参数**: `qwen-audio-3.1-tts-flash-farol_pt`
          </td>

          <td style={{ verticalAlign: "top" }}>
            葡萄牙语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9064.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Naseem

            **本地名称**: نسيم

            **voice 参数**: `qwen-audio-3.1-tts-flash-naseem_ar`
          </td>

          <td style={{ verticalAlign: "top" }}>
            阿拉伯语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9079.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Embun

            **本地名称**: Embun

            **voice 参数**: `qwen-audio-3.1-tts-flash-embun_id`
          </td>

          <td style={{ verticalAlign: "top" }}>
            印尼语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f907f.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: May

            **本地名称**: Mây

            **voice 参数**: `qwen-audio-3.1-tts-flash-may_vi`
          </td>

          <td style={{ verticalAlign: "top" }}>
            越南语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f907a.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Bayu

            **本地名称**: Bayu

            **voice 参数**: `qwen-audio-3.1-tts-flash-bayu_ms`
          </td>

          <td style={{ verticalAlign: "top" }}>
            马来语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f9078.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Sailom

            **本地名称**: สายลม

            **voice 参数**: `qwen-audio-3.1-tts-flash-sailom_th`
          </td>

          <td style={{ verticalAlign: "top" }}>
            泰语
          </td>

          <td style={{ verticalAlign: "top" }}>
            男
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f907e.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>

        <tr>
          <td style={{ verticalAlign: "top" }}>
            **音色名称**: Hiraya

            **本地名称**: Hiraya

            **voice 参数**: `qwen-audio-3.1-tts-flash-hiraya_tl`
          </td>

          <td style={{ verticalAlign: "top" }}>
            菲律宾语（他加禄语）
          </td>

          <td style={{ verticalAlign: "top" }}>
            女
          </td>

          <td style={{ verticalAlign: "top" }}>
            SSML: 支持

            指令控制: 支持

            时间戳: 支持
          </td>

          <td style={{ verticalAlign: "top" }}>
            <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
              <source src="https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/audios/6a4b3c2d1e0f907c.wav" type="audio/wav" />
            </audio>
          </td>
        </tr>
      </tbody>
    </table>
  </Tab>
</Tabs>

### qwen-audio-3.0-tts-plus <span id="f399ce27bcc8" />

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "15%" }} />

    <col style={{ width: "30%" }} />

    <col style={{ width: "20%" }} />

    <col style={{ width: "35%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        **适用场景**
      </th>

      <th style={{ verticalAlign: "top" }}>
        **音色信息**
      </th>

      <th style={{ verticalAlign: "top" }}>
        **特性支持**
      </th>

      <th style={{ verticalAlign: "top" }}>
        **音频试听（右键保存音频）**
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td rowSpan={2} style={{ verticalAlign: "top" }}>
        社交陪伴（旗舰音色）
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安灵心

        <strong>voice参数</strong>：longanlingxin

        <strong>特质</strong>：知心温暖音

        <strong>年龄</strong>：25岁

        <strong>性别</strong>：女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/deqsie/01_longanlingxin.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安鲁风

        <strong>voice参数</strong>：longanlufeng

        <strong>特质</strong>：明亮开朗音

        <strong>年龄</strong>：25岁

        <strong>性别</strong>：男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/tpgsdv/02_longanlufeng.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>
  </tbody>
</table>

### qwen-audio-3.0-tts-flash <span id="f399ce27bcc8flash" />

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "15%" }} />

    <col style={{ width: "30%" }} />

    <col style={{ width: "20%" }} />

    <col style={{ width: "35%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        **适用场景**
      </th>

      <th style={{ verticalAlign: "top" }}>
        **音色信息**
      </th>

      <th style={{ verticalAlign: "top" }}>
        **特性支持**
      </th>

      <th style={{ verticalAlign: "top" }}>
        **音频试听（右键保存音频）**
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td rowSpan={5} style={{ verticalAlign: "top" }}>
        社交陪伴（精品中文）
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安风悦

        <strong>voice参数</strong>：longanfengyue

        <strong>特质</strong>：自然亲切音

        <strong>年龄</strong>：30岁

        <strong>性别</strong>：女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/djxajf/03_longanfengyue.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安元妃

        <strong>voice参数</strong>：longanyuanfei

        <strong>特质</strong>：高傲妃子音

        <strong>年龄</strong>：30岁

        <strong>性别</strong>：女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/lnyaxv/04_longanyuanfei.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安灵希

        <strong>voice参数</strong>：longanlingxi

        <strong>特质</strong>：可爱甜美音

        <strong>年龄</strong>：25岁

        <strong>性别</strong>：女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/pfkypj/05_longanlingxi.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安小昕

        <strong>voice参数</strong>：longanxiaoxin

        <strong>特质</strong>：亲切活泼音

        <strong>年龄</strong>：22岁

        <strong>性别</strong>：女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/msjxqk/07_longanxiaoxin.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安欢

        <strong>voice参数</strong>：longanhuan\_v3.6

        <strong>年龄</strong>：25岁

        <strong>性别</strong>：女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/glwaer/09_longanhuan.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td rowSpan={2} style={{ verticalAlign: "top" }}>
        儿童陪伴/智能玩具（精品儿童）
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙杰力豆

        <strong>voice参数</strong>：longjielidou\_v3.6

        <strong>特质</strong>：天真男童

        <strong>年龄</strong>：5岁

        <strong>性别</strong>：男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/wirytn/08_longjielidou.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙泡泡

        <strong>voice参数</strong>：longpaopao\_v3.6

        <strong>特质</strong>：软糯可爱音

        <strong>年龄</strong>：5岁

        <strong>性别</strong>：女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/nrgkjo/13_longpaopao.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td rowSpan={2} style={{ verticalAlign: "top" }}>
        角色音/游戏（精品中文）
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙火火

        <strong>voice参数</strong>：longhuohuo\_v3.6

        <strong>特质</strong>：顽皮少年音

        <strong>年龄</strong>：8岁

        <strong>性别</strong>：男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/efdgqh/11_longhuohuo.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙川叔

        <strong>voice参数</strong>：longchuanshu\_v3.6

        <strong>特质</strong>：川普大叔音

        <strong>年龄</strong>：40岁

        <strong>性别</strong>：男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 支持

        指令控制: 支持

        时间戳: 支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/makxfd/12_longchuanshu.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td rowSpan={3} style={{ verticalAlign: "top" }}>
        社交陪伴/语音助手（精品英文）
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongmary

        <strong>voice参数</strong>：loongmary

        <strong>特质</strong>：温暖英音

        <strong>年龄</strong>：20岁

        <strong>性别</strong>：女

        <strong>语言</strong>：英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 不支持

        指令控制: 不支持

        时间戳: 不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/wpitzn/01_loongmary.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongeva

        <strong>voice参数</strong>：loongeva\_v3.6

        <strong>特质</strong>：高智美音

        <strong>年龄</strong>：28岁

        <strong>性别</strong>：女

        <strong>语言</strong>：英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 不支持

        指令控制: 不支持

        时间戳: 不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/xajwqw/02_loongeva.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongJohn

        <strong>voice参数</strong>：loongjohn

        <strong>特质</strong>：沉稳亲切美音

        <strong>年龄</strong>：28岁

        <strong>性别</strong>：男

        <strong>语言</strong>：英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML: 不支持

        指令控制: 不支持

        时间戳: 不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260706/pwxpto/03_loongjohn_1.wav" type="audio/x-wav" />
        </audio>
      </td>
    </tr>
  </tbody>
</table>

## 基础音色 <span id="ext-voices-title" />

<Note>
  建议优先使用系统音色，以获得更稳定的语音合成效果。如需定制音色，可通过[音色复刻](/zh/model-studio/voice-cloning-user-guide)或[音色设计](/zh/model-studio/voice-design-user-guide)创建专属音色。基础音色提供更多选择，使用前建议试听并评估其是否符合业务需求。
</Note>

除上述系统音色外，qwen-audio-3.0-tts-plus和qwen-audio-3.0-tts-flash各自还提供500余个通过音色复刻生成的基础音色，调用方式与系统音色一致。

<span id="base-voice-usage" />

基础音色命名格式为`qwen-audio-3.0-tts-{plus|flash}-{音色后缀}`，两个模型同一后缀对应同一套试听音频。

<span id="base-voice-downloads" />

- qwen-audio-3.0-tts-plus基础音色列表（Excel）：[qwen-audio-3.0-tts-plus基础音色.xlsx](https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260723/ydwqqz/qwen-audio-3.0-tts-plus%E5%9F%BA%E7%A1%80%E9%9F%B3%E8%89%B2.xlsx)
- qwen-audio-3.0-tts-flash基础音色列表（Excel）：[qwen-audio-3.0-tts-flash基础音色.xlsx](https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260723/thosjr/qwen-audio-3.0-tts-flash%E5%9F%BA%E7%A1%80%E9%9F%B3%E8%89%B2.xlsx)
- 基础音色试听音频包（plus和flash共用）：[基础音色试听音频包.zip](https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/zh/files/6a4b3c2d1e0f9016.zip)

**试听步骤**

<span id="base-voice-preview" />

1. 下载Excel和试听音频包，将音频包解压到本地。
2. 在Excel中找到“预览音频文件名”列，获取音频文件名。
3. 在解压目录中找到对应文件，使用播放器打开试听。
