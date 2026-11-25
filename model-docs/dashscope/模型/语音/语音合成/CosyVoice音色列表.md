> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# CosyVoice音色列表

> CosyVoice 系统音色列表与试听。

<meta property="og:title" content="CosyVoice音色列表" />

<Note>
  所选音色（`voice`）须在当前模型（`model`）的音色列表中。待合成文本（`text`）须使用该音色支持的语言，否则可能出现发音错误或语音不自然。
</Note>

## cosyvoice-v3-flash <span id="39a40bbc5dxp3" />

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "12%" }} />

    <col style={{ width: "28%" }} />

    <col style={{ width: "24%" }} />

    <col style={{ width: "23%" }} />

    <col style={{ width: "13%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>适用场景</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>音色信息</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>特性支持</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>试听</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>支持地域</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td rowSpan={3} style={{ verticalAlign: "top" }}>
        社交陪伴（标杆音色）
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安洋

        <strong>voice参数</strong>：<code>longanyang</code>

        <strong>特质</strong>：阳光大男孩

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：支持

        时间戳：支持

        <Accordion title="点击查看Instruct设置">
          <strong>Instruct文本必须使用中文</strong>，须严格按以下格式填写，包括标点符号，不可遗漏结尾句号。支持的情感值：`neutral`、`fearful`、`angry`、`sad`、`surprised`、`happy`、`disgusted`。

          1. 设置情感

             - 格式：“`你说话的情感是<情感值>。`”
             - 示例：“`你说话的情感是neutral。`”
          2. 设置场景+情感

             - 格式：“`你正在进行<场景>，你说话的情感是<情感值>。`”
             - 示例：“`你正在进行闲聊互动，你说话的情感是neutral。`”
             - 支持的场景：`闲聊互动`、`新闻播报`、`广告促销`、`比赛解说`、`一些儿童内容解说`、`语音导航`、`脱口秀表演`。
          3. 设置角色+情感

             - 格式：“`你现在说话的角色是<角色>，你说话的情感是<情感值>。`”
             - 示例：“`你现在说话的角色是一个旁白，你说话的情感是neutral。`”
             - 支持的角色：`一个旁白`。
          4. 设置身份+情感

             - 格式：“`你正在以一个<身份>的身份说话，你说话的情感是<情感值>。`”
             - 示例：“`你正在以一个故事机的身份说话，你说话的情感是neutral。`”
             - 支持的身份：`故事机`。
        </Accordion>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/khzqvb/%E9%BE%99%E5%AE%89%E6%B4%8B.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安欢（V3）

        <strong>voice参数</strong>：<code>longanhuan\_v3</code>

        <strong>特质</strong>：欢脱元气女

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话、广东话、东北话、河南话、湖南话、陕西话、山东话、四川话、安徽话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：支持

        时间戳：支持

        <Accordion title="点击查看Instruct设置">
          <strong>Instruct文本必须使用中文</strong>，须严格按以下格式填写，包括标点符号，不可遗漏结尾句号。

          设置方言：

          - 格式：“`请用<方言>表达。`”
          - 示例：“`请用山东话表达。`”
          - 支持的方言：`普通话`、`广东话`、`东北话`、`河南话`、`湖南话`、`陕西话`、`山东话`、`四川话`、`安徽话`
        </Accordion>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/lolnxw/%E9%BE%99%E5%AE%89%E6%AC%A2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安欢

        <strong>voice参数</strong>：<code>longanhuan</code>

        <strong>特质</strong>：欢脱元气女

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：支持

        时间戳：支持

        <Accordion title="点击查看Instruct设置">
          <strong>Instruct文本必须使用中文</strong>，须严格按以下格式填写，包括标点符号，不可遗漏结尾句号。支持的情感值：`neutral`、`fearful`、`angry`、`sad`、`surprised`、`happy`、`disgusted`。

          1. 设置情感

             - 格式：“`你说话的情感是<情感值>。`”
             - 示例：“`你说话的情感是neutral。`”
          2. 设置场景+情感

             - 格式：“`你正在进行<场景>，你说话的情感是<情感值>。`”
             - 示例：“`你正在进行闲聊对话，你说话的情感是neutral。`”
             - 支持的场景：`闲聊对话`、`比赛解说`、`深夜电台广播`、`剧情解说`、`诗歌朗诵`、`科普知识推广`、`产品推广`、`脱口秀表演`。
          3. 设置角色+情感

             - 格式：“`你说话的角色是<角色>，你说话的情感是<情感值>。`”
             - 示例：“`你说话的角色是温和客服，你说话的情感是neutral。`”
             - 支持的角色：`温和客服`。
        </Accordion>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/lolnxw/%E9%BE%99%E5%AE%89%E6%AC%A2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        童声（标杆音色）
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙呼呼

        <strong>voice参数</strong>：<code>longhuhu\_v3</code>

        <strong>特质</strong>：天真烂漫女童

        <strong>年龄</strong>：6\~10岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：支持

        时间戳：支持

        <Accordion title="点击查看Instruct设置">
          <strong>Instruct文本必须使用中文</strong>，须严格按以下格式填写，包括标点符号，不可遗漏结尾句号。支持的情感值：`neutral`、`fearful`、`angry`、`sad`、`surprised`、`happy`、`disgusted`。

          1. 设置情感

             - 格式：“`你说话的情感是<情感值>。`”
             - 示例：“`你说话的情感是neutral。`”
          2. 设置场景+情感

             - 格式：“`你正在进行<场景>，你说话的情感是<情感值>。`”
             - 示例：“`你正在进行自由对话，你说话的情感是neutral。`”
             - 支持的场景：`自由对话`、`广告促销`。
          3. 设置角色+情感

             - 格式：“`你说话的角色是<角色>，你说话的情感是<情感值>。`”
             - 示例：“`你说话的角色是傲娇公主，你说话的情感是neutral。`”
             - 支持的角色：`傲娇公主`、`元气少女`、`可爱孩童`、`机器人`、`小猪佩奇`。
          4. 设置身份+情感

             - 格式：“`你正在以一个<身份>的身份说话，你说话的情感是<情感值>。`”
             - 示例：“`你正在以一个故事机的身份说话，你说话的情感是neutral。`”
             - 支持的身份：`故事机`、`儿童玩具`。
        </Accordion>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/qxsnvh/%E9%BE%99%E5%91%BC%E5%91%BC.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={4} style={{ verticalAlign: "top" }}>
        智能玩具/儿童故事机
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙泡泡

        <strong>voice参数</strong>：<code>longpaopao\_v3</code>

        <strong>特质</strong>：飞天泡泡音

        <strong>年龄</strong>：6\~15岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/hcjrnj/%E9%BE%99%E6%B3%A1%E6%B3%A1.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙杰力豆

        <strong>voice参数</strong>：<code>longjielidou\_v3</code>

        <strong>特质</strong>：阳光顽皮男

        <strong>年龄</strong>：10岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/lsdtoy/%E9%BE%99%E6%9D%B0%E5%8A%9B%E8%B1%86.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙仙

        <strong>voice参数</strong>：<code>longxian\_v3</code>

        <strong>特质</strong>：豪放可爱女

        <strong>年龄</strong>：12岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/iexyvo/%E9%BE%99%E4%BB%99.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙铃

        <strong>voice参数</strong>：<code>longling\_v3</code>

        <strong>特质</strong>：稚气呆板女

        <strong>年龄</strong>：10岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/rkdnzm/%E9%BE%99%E9%93%83.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={2} style={{ verticalAlign: "top" }}>
        消费电子-儿童有声书
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙闪闪

        <strong>voice参数</strong>：<code>longshanshan\_v3</code>

        <strong>特质</strong>：戏剧化童声

        <strong>年龄</strong>：6\~15岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/wgyxdc/%E9%BE%99%E9%97%AA%E9%97%AA.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙牛牛

        <strong>voice参数</strong>：<code>longniuniu\_v3</code>

        <strong>特质</strong>：阳光男童声

        <strong>年龄</strong>：6\~15岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/eozaog/%E9%BE%99%E7%89%9B%E7%89%9B.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={6} style={{ verticalAlign: "top" }}>
        方言
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙嘉欣

        <strong>voice参数</strong>：<code>longjiaxin\_v3</code>

        <strong>特质</strong>：优雅粤语女

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：中文（粤语）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/jakmyo/%E9%BE%99%E5%98%89%E6%AC%A3.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙嘉怡

        <strong>voice参数</strong>：<code>longjiayi\_v3</code>

        <strong>特质</strong>：知性粤语女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（粤语）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/dsuxvc/%E9%BE%99%E5%98%89%E6%80%A1.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安粤

        <strong>voice参数</strong>：<code>longanyue\_v3</code>

        <strong>特质</strong>：欢脱粤语男

        <strong>年龄</strong>：25\~35岁

        <strong>语言</strong>：中文（粤语）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/emleqe/%E9%BE%99%E5%AE%89%E7%B2%A4.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙老铁

        <strong>voice参数</strong>：<code>longlaotie\_v3</code>

        <strong>特质</strong>：东北直率男

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（东北话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/yuxzau/%E9%BE%99%E8%80%81%E9%93%81.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙陕哥

        <strong>voice参数</strong>：<code>longshange\_v3</code>

        <strong>特质</strong>：原味陕北男

        <strong>年龄</strong>：25\~35岁

        <strong>语言</strong>：中文（陕西话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/oltkqv/%E9%BE%99%E9%99%95%E5%93%A5.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安闽

        <strong>voice参数</strong>：<code>longanmin\_v3</code>

        <strong>特质</strong>：清纯萝莉女

        <strong>年龄</strong>：18\~25岁

        <strong>语言</strong>：中文（闽南话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/biokep/%E9%BE%99%E5%AE%89%E9%97%BD.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={22} style={{ verticalAlign: "top" }}>
        出海营销
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongkyong

        <strong>voice参数</strong>：<code>loongkyong\_v3</code>

        <strong>特质</strong>：韩语女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：韩语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/eezgsn/loongkyong.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：Riko

        <strong>voice参数</strong>：<code>loongriko\_v3</code>

        <strong>特质</strong>：二次元霓虹女

        <strong>年龄</strong>：18\~25岁

        <strong>语言</strong>：日语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/vbcvdf/Riko.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongtomoka

        <strong>voice参数</strong>：<code>loongtomoka\_v3</code>

        <strong>特质</strong>：日语女

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：日语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/nsevrv/loongtomoka.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongabby

        <strong>voice参数</strong>：<code>loongabby\_v3</code>

        <strong>特质</strong>：美式英文女

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/zanqxn/loongabby.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongandy

        <strong>voice参数</strong>：<code>loongandy\_v3</code>

        <strong>特质</strong>：美式英文男

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/anhpgt/loongandy.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongannie

        <strong>voice参数</strong>：<code>loongannie\_v3</code>

        <strong>特质</strong>：美式英文女

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/sgraii/loongannie.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongava

        <strong>voice参数</strong>：<code>loongava\_v3</code>

        <strong>特质</strong>：美式英文女

        <strong>年龄</strong>：35\~40岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/wpthth/loongava.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongbeth

        <strong>voice参数</strong>：<code>loongbeth\_v3</code>

        <strong>特质</strong>：美式英文女

        <strong>年龄</strong>：35\~40岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/uqenag/loongbeth.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongbetty

        <strong>voice参数</strong>：<code>loongbetty\_v3</code>

        <strong>特质</strong>：美式英文女

        <strong>年龄</strong>：35\~40岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/nprwoh/loongbetty.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongcally

        <strong>voice参数</strong>：<code>loongcally\_v3</code>

        <strong>特质</strong>：美式英文女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/ssxlxo/loongcally.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongcindy

        <strong>voice参数</strong>：<code>loongcindy\_v3</code>

        <strong>特质</strong>：美式英文女

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/exqerq/loongcindy.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongdavid

        <strong>voice参数</strong>：<code>loongdavid\_v3</code>

        <strong>特质</strong>：美式英文男

        <strong>年龄</strong>：35\~40岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/chnojy/loongdavid.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongdonna

        <strong>voice参数</strong>：<code>loongdonna\_v3</code>

        <strong>特质</strong>：美式英文女

        <strong>年龄</strong>：35\~40岁

        <strong>语言</strong>：美式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/pfyloh/loongdonna.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongemily

        <strong>voice参数</strong>：<code>loongemily\_v3</code>

        <strong>特质</strong>：英式英文女

        <strong>年龄</strong>：35\~40岁

        <strong>语言</strong>：英式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/obxqhd/loongemily.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongeric

        <strong>voice参数</strong>：<code>loongeric\_v3</code>

        <strong>特质</strong>：英式英文男

        <strong>年龄</strong>：35\~40岁

        <strong>语言</strong>：英式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/cbfkgb/loongeric.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongluna

        <strong>voice参数</strong>：<code>loongluna\_v3</code>

        <strong>特质</strong>：英式英文女

        <strong>年龄</strong>：35\~40岁

        <strong>语言</strong>：英式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/ellvhy/loongluna.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongluca

        <strong>voice参数</strong>：<code>loongluca\_v3</code>

        <strong>特质</strong>：英式英文男

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：英式英语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/fpfaas/loongluca.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongtomoya

        <strong>voice参数</strong>：<code>loongtomoya\_v3</code>

        <strong>特质</strong>：日语男

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：日语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/mzdrqv/loongtomoya.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：Yuuna

        <strong>voice参数</strong>：<code>loongyuuna\_v3</code>

        <strong>特质</strong>：日语女

        <strong>年龄</strong>：18\~25岁

        <strong>语言</strong>：日语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/jqwyne/loongyuuna.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：Yuuma

        <strong>voice参数</strong>：<code>loongyuuma\_v3</code>

        <strong>特质</strong>：日语男

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：日语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/fyhuhb/loongyuuma.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：Jihun

        <strong>voice参数</strong>：<code>loongjihun\_v3</code>

        <strong>特质</strong>：韩语男

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：韩语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/trddky/loongjihun.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongindah

        <strong>voice参数</strong>：<code>loongindah\_v3</code>

        <strong>特质</strong>：印尼女

        <strong>年龄</strong>：22\~27岁

        <strong>语言</strong>：印尼语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260410/kucmxk/loongindah.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        诗词朗诵
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙飞

        <strong>voice参数</strong>：<code>longfei\_v3</code>

        <strong>特质</strong>：热血磁性男

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/noojqr/%E9%BE%99%E9%A3%9E.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        电话销售
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应笑

        <strong>voice参数</strong>：<code>longyingxiao\_v3</code>

        <strong>特质</strong>：清甜推销女

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/istqlq/%E9%BE%99%E5%BA%94%E7%AC%91.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={4} style={{ verticalAlign: "top" }}>
        客服
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应询

        <strong>voice参数</strong>：<code>longyingxun\_v3</code>

        <strong>特质</strong>：年轻青涩男

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/lzhjmw/%E9%BE%99%E5%BA%94%E8%AF%A2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应静

        <strong>voice参数</strong>：<code>longyingjing\_v3</code>

        <strong>特质</strong>：低调冷静女

        <strong>年龄</strong>：25\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251210/wnnnvw/%E9%BE%99%E5%BA%94%E9%9D%99.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应聆

        <strong>voice参数</strong>：<code>longyingling\_v3</code>

        <strong>特质</strong>：温和共情女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251210/dqrorx/%E9%BE%99%E5%BA%94%E8%81%86.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应桃

        <strong>voice参数</strong>：<code>longyingtao\_v3</code>

        <strong>特质</strong>：温柔淡定女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/hmtohx/%E9%BE%99%E5%BA%94%E6%A1%83.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={8} style={{ verticalAlign: "top" }}>
        语音助手
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙小淳

        <strong>voice参数</strong>：<code>longxiaochun\_v3</code>

        <strong>特质</strong>：知性积极女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/flmrpr/%E9%BE%99%E5%B0%8F%E6%B7%B3.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙小夏

        <strong>voice参数</strong>：<code>longxiaoxia\_v3</code>

        <strong>特质</strong>：沉稳权威女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/bncrea/%E9%BE%99%E5%B0%8F%E5%A4%8F.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：YUMI

        <strong>voice参数</strong>：<code>longyumi\_v3</code>

        <strong>特质</strong>：正经青年女

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/lbchpn/YUMI.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安昀

        <strong>voice参数</strong>：<code>longanyun\_v3</code>

        <strong>特质</strong>：居家暖男

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/jsbqod/%E9%BE%99%E5%AE%89%E6%98%80.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安温

        <strong>voice参数</strong>：<code>longanwen\_v3</code>

        <strong>特质</strong>：优雅知性女

        <strong>年龄</strong>：25\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/mwznrm/%E9%BE%99%E5%AE%89%E6%B8%A9.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安莉

        <strong>voice参数</strong>：<code>longanli\_v3</code>

        <strong>特质</strong>：利落从容女

        <strong>年龄</strong>：25\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/juyxgb/%E9%BE%99%E5%AE%89%E8%8E%89.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安朗

        <strong>voice参数</strong>：<code>longanlang\_v3</code>

        <strong>特质</strong>：清爽利落男

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/spager/%E9%BE%99%E5%AE%89%E6%9C%97.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应沐

        <strong>voice参数</strong>：<code>longyingmu\_v3</code>

        <strong>特质</strong>：优雅知性女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/qvbteh/%E9%BE%99%E5%BA%94%E6%B2%90.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={18} style={{ verticalAlign: "top" }}>
        社交陪伴
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安台

        <strong>voice参数</strong>：<code>longantai\_v3</code>

        <strong>特质</strong>：嗲甜台湾女

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/jlfjdr/%E9%BE%99%E5%AE%89%E5%8F%B0.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙华

        <strong>voice参数</strong>：<code>longhua\_v3</code>

        <strong>特质</strong>：元气甜美女

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/bmmeqr/%E9%BE%99%E5%8D%8E.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙橙

        <strong>voice参数</strong>：<code>longcheng\_v3</code>

        <strong>特质</strong>：智慧青年男

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/qagbzd/%E9%BE%99%E6%A9%99.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙泽

        <strong>voice参数</strong>：<code>longze\_v3</code>

        <strong>特质</strong>：温暖元气男

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/tkdnwq/%E9%BE%99%E6%B3%BD.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙哲

        <strong>voice参数</strong>：<code>longzhe\_v3</code>

        <strong>特质</strong>：呆板大暖男

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/vtyigc/%E9%BE%99%E5%93%B2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙颜

        <strong>voice参数</strong>：<code>longyan\_v3</code>

        <strong>特质</strong>：温暖春风女

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/qljxmn/%E9%BE%99%E9%A2%9C.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙星

        <strong>voice参数</strong>：<code>longxing\_v3</code>

        <strong>特质</strong>：温婉邻家女

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/tpsroh/%E9%BE%99%E6%98%9F.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙天

        <strong>voice参数</strong>：<code>longtian\_v3</code>

        <strong>特质</strong>：磁性理智男

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/mbkkoa/%E9%BE%99%E5%A4%A9.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙婉

        <strong>voice参数</strong>：<code>longwan\_v3</code>

        <strong>特质</strong>：细腻柔声女

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/zqjmxw/%E9%BE%99%E5%A9%89.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙嫱

        <strong>voice参数</strong>：<code>longqiang\_v3</code>

        <strong>特质</strong>：浪漫风情女

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/ecdnsw/%E9%BE%99%E5%AB%B1.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙菲菲

        <strong>voice参数</strong>：<code>longfeifei\_v3</code>

        <strong>特质</strong>：甜美娇气女

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/ncxhvr/%E9%BE%99%E8%8F%B2%E8%8F%B2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙浩

        <strong>voice参数</strong>：<code>longhao\_v3</code>

        <strong>特质</strong>：多情忧郁男

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/bdluvk/%E9%BE%99%E6%B5%A9.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安柔

        <strong>voice参数</strong>：<code>longanrou\_v3</code>

        <strong>特质</strong>：温柔闺蜜女

        <strong>年龄</strong>：20\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251210/xucyrv/%E9%BE%99%E5%AE%89%E6%9F%94.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙寒

        <strong>voice参数</strong>：<code>longhan\_v3</code>

        <strong>特质</strong>：温暖痴情男

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251210/dhjimm/%E9%BE%99%E5%AF%92.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安智

        <strong>voice参数</strong>：<code>longanzhi\_v3</code>

        <strong>特质</strong>：睿智轻熟男

        <strong>年龄</strong>：25\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/whzljl/%E9%BE%99%E5%AE%89%E6%99%BA.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安灵

        <strong>voice参数</strong>：<code>longanling\_v3</code>

        <strong>特质</strong>：思维灵动女

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/kmgknr/%E9%BE%99%E5%AE%89%E7%81%B5.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安雅

        <strong>voice参数</strong>：<code>longanya\_v3</code>

        <strong>特质</strong>：高雅气质女

        <strong>年龄</strong>：25\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/tccnbg/%E9%BE%99%E5%AE%89%E9%9B%85.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安亲

        <strong>voice参数</strong>：<code>longanqin\_v3</code>

        <strong>特质</strong>：亲和活泼女

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/dpiusw/%E9%BE%99%E5%AE%89%E4%BA%B2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={10} style={{ verticalAlign: "top" }}>
        有声书
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙妙

        <strong>voice参数</strong>：<code>longmiao\_v3</code>

        <strong>特质</strong>：抑扬顿挫女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/jhevhe/%E9%BE%99%E5%A6%99.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙三叔

        <strong>voice参数</strong>：<code>longsanshu\_v3</code>

        <strong>特质</strong>：沉稳质感男

        <strong>年龄</strong>：25\~45岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/jspsfm/%E9%BE%99%E4%B8%89%E5%8F%94.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙媛

        <strong>voice参数</strong>：<code>longyuan\_v3</code>

        <strong>特质</strong>：温暖治愈女

        <strong>年龄</strong>：35\~40岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/pnwdrz/%E9%BE%99%E5%AA%9B.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙悦

        <strong>voice参数</strong>：<code>longyue\_v3</code>

        <strong>特质</strong>：温暖磁性女

        <strong>年龄</strong>：30\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/rtaxvh/%E9%BE%99%E6%82%A6.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙修

        <strong>voice参数</strong>：<code>longxiu\_v3</code>

        <strong>特质</strong>：博才说书男

        <strong>年龄</strong>：25\~35岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/bpsefy/%E9%BE%99%E4%BF%AE.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙楠

        <strong>voice参数</strong>：<code>longnan\_v3</code>

        <strong>特质</strong>：睿智青年男

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/nvpzlm/%E9%BE%99%E6%A5%A0.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙婉君

        <strong>voice参数</strong>：<code>longwanjun\_v3</code>

        <strong>特质</strong>：细腻柔声女

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/bmrohp/%E9%BE%99%E5%A9%89%E5%90%9B.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙逸尘

        <strong>voice参数</strong>：<code>longyichen\_v3</code>

        <strong>特质</strong>：洒脱活力男

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/rgsfye/%E9%BE%99%E9%80%B8%E5%B0%98.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙老伯

        <strong>voice参数</strong>：<code>longlaobo\_v3</code>

        <strong>特质</strong>：沧桑岁月爷

        <strong>年龄</strong>：60岁以上

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/mlmetv/%E9%BE%99%E8%80%81%E4%BC%AF.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙老姨

        <strong>voice参数</strong>：<code>longlaoyi\_v3</code>

        <strong>特质</strong>：烟火从容阿姨

        <strong>年龄</strong>：60岁以上

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/gltsou/%E9%BE%99%E8%80%81%E5%A7%A8.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={3} style={{ verticalAlign: "top" }}>
        短视频配音
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙机器

        <strong>voice参数</strong>：<code>longjiqi\_v3</code>

        <strong>特质</strong>：呆萌机器人

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/upftsm/%E9%BE%99%E6%9C%BA%E5%99%A8.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙猴哥

        <strong>voice参数</strong>：<code>longhouge\_v3</code>

        <strong>特质</strong>：经典猴哥

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/tpwkox/%E9%BE%99%E7%8C%B4%E5%93%A5.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙黛玉

        <strong>voice参数</strong>：<code>longdaiyu\_v3</code>

        <strong>特质</strong>：娇率才女音

        <strong>年龄</strong>：15\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/vzioqa/%E9%BE%99%E9%BB%9B%E7%8E%89.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={2} style={{ verticalAlign: "top" }}>
        直播带货
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安燃

        <strong>voice参数</strong>：<code>longanran\_v3</code>

        <strong>特质</strong>：活泼质感女

        <strong>年龄</strong>：30\~40岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/nzdwwg/%E9%BE%99%E5%AE%89%E7%87%83.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安宣

        <strong>voice参数</strong>：<code>longanxuan\_v3</code>

        <strong>特质</strong>：经典直播女

        <strong>年龄</strong>：30\~40岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/agbkwa/%E9%BE%99%E5%AE%89%E5%AE%A3.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td rowSpan={3} style={{ verticalAlign: "top" }}>
        新闻播报
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙硕

        <strong>voice参数</strong>：<code>longshuo\_v3</code>

        <strong>特质</strong>：博才干练男

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260128/zqsbkd/%E9%BE%99%E7%A1%95.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙书

        <strong>voice参数</strong>：<code>longshu\_v3</code>

        <strong>特质</strong>：沉稳青年男

        <strong>年龄</strong>：20\~25岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260105/tsviqq/%E9%BE%99%E4%B9%A6.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：Bella3.0

        <strong>voice参数</strong>：<code>loongbella\_v3</code>

        <strong>特质</strong>：精准干练女

        <strong>年龄</strong>：25\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/bvvefg/Bella3.0.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>
  </tbody>
</table>

## cosyvoice-v3-plus <span id="76869542dc9qa" />

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "12%" }} />

    <col style={{ width: "28%" }} />

    <col style={{ width: "24%" }} />

    <col style={{ width: "23%" }} />

    <col style={{ width: "13%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>适用场景</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>音色信息</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>特性支持</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>试听</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>支持地域</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td rowSpan={2} style={{ verticalAlign: "top" }}>
        社交陪伴（标杆音色）
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安洋

        <strong>voice参数</strong>：<code>longanyang</code>

        <strong>特质</strong>：阳光大男孩

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：支持

        时间戳：支持

        <Accordion title="点击查看Instruct设置">
          <strong>Instruct文本必须使用中文</strong>，须严格按以下格式填写，包括标点符号，不可遗漏结尾句号。支持的情感值：`neutral`、`fearful`、`angry`、`sad`、`surprised`、`happy`、`disgusted`。

          1. 设置情感

             - 格式：“`你说话的情感是<情感值>。`”
             - 示例：“`你说话的情感是neutral。`”
          2. 设置场景+情感

             - 格式：“`你正在进行<场景>，你说话的情感是<情感值>。`”
             - 示例：“`你正在进行闲聊互动，你说话的情感是neutral。`”
             - 支持的场景：`闲聊互动`、`新闻播报`、`广告促销`、`比赛解说`、`一些儿童内容解说`、`语音导航`、`脱口秀表演`。
          3. 设置角色+情感

             - 格式：“`你现在说话的角色是<角色>，你说话的情感是<情感值>。`”
             - 示例：“`你现在说话的角色是一个旁白，你说话的情感是neutral。`”
             - 支持的角色：`一个旁白`。
          4. 设置身份+情感

             - 格式：“`你正在以一个<身份>的身份说话，你说话的情感是<情感值>。`”
             - 示例：“`你正在以一个故事机的身份说话，你说话的情感是neutral。`”
             - 支持的身份：`故事机`。
        </Accordion>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/khzqvb/%E9%BE%99%E5%AE%89%E6%B4%8B.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安欢

        <strong>voice参数</strong>：<code>longanhuan</code>

        <strong>特质</strong>：欢脱元气女

        <strong>年龄</strong>：20\~30岁

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：支持

        时间戳：支持

        <Accordion title="点击查看Instruct设置">
          <strong>Instruct文本必须使用中文</strong>，须严格按以下格式填写，包括标点符号，不可遗漏结尾句号。支持的情感值：`neutral`、`fearful`、`angry`、`sad`、`surprised`、`happy`、`disgusted`。

          1. 设置情感

             - 格式：“`你说话的情感是<情感值>。`”
             - 示例：“`你说话的情感是neutral。`”
          2. 设置场景+情感

             - 格式：“`你正在进行<场景>，你说话的情感是<情感值>。`”
             - 示例：“`你正在进行闲聊互动，你说话的情感是neutral。`”
             - 支持的场景：`闲聊对话`、`比赛解说`、`深夜电台广播`、`诗歌朗诵`、`科普知识推广`、`产品推广`、`脱口秀表演`。
          3. 设置角色+情感

             - 格式：“`你说话的角色是<角色>，你说话的情感是<情感值>。`”
             - 示例：“`你说话的角色是温和客服，你说话的情感是neutral。`”
             - 支持的角色：`温和客服`。
        </Accordion>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251117/lolnxw/%E9%BE%99%E5%AE%89%E6%AC%A2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）、新加坡
      </td>
    </tr>
  </tbody>
</table>

## cosyvoice-v2 <span id="29ae27111excp" />

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "12%" }} />

    <col style={{ width: "28%" }} />

    <col style={{ width: "24%" }} />

    <col style={{ width: "23%" }} />

    <col style={{ width: "13%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>适用场景</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>音色信息</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>特性支持</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>试听</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>支持地域</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        电话销售
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应笑

        <strong>voice参数</strong>：<code>longyingxiao</code>

        <strong>特质</strong>：清甜推销女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/hvbyrx/%E9%BE%99%E5%BA%94%E7%AC%91.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={8} style={{ verticalAlign: "top" }}>
        短视频配音
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙机器

        <strong>voice参数</strong>：<code>longjiqi</code>

        <strong>特质</strong>：呆萌机器人

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/vqyqta/%E9%BE%99%E6%9C%BA%E5%99%A8.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙猴哥

        <strong>voice参数</strong>：<code>longhouge</code>

        <strong>特质</strong>：经典猴哥

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/slzkaa/%E9%BE%99%E7%8C%B4%E5%93%A5.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙机心

        <strong>voice参数</strong>：<code>longjixin</code>

        <strong>特质</strong>：毒舌心机女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/ovssxn/%E9%BE%99%E6%9C%BA%E5%BF%83.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安粤

        <strong>voice参数</strong>：<code>longanyue</code>

        <strong>特质</strong>：欢脱粤语男

        <strong>语言</strong>：中文（粤语）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/sluhct/%E9%BE%99%E5%AE%89%E7%B2%A4.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙陕哥

        <strong>voice参数</strong>：<code>longshange</code>

        <strong>特质</strong>：原味陕北男

        <strong>语言</strong>：中文（陕西话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/vqomyc/%E9%BE%99%E9%99%95%E5%93%A5.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安敏

        <strong>voice参数</strong>：<code>longanmin</code>

        <strong>特质</strong>：甜美闽南女

        <strong>语言</strong>：中文（闽南话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/mcmxgh/%E9%BE%99%E5%AE%89%E6%95%8F.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙黛玉

        <strong>voice参数</strong>：<code>longdaiyu</code>

        <strong>特质</strong>：娇率才女音

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/wywrbk/%E9%BE%99%E9%BB%9B%E7%8E%89.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙高僧

        <strong>voice参数</strong>：<code>longgaoseng</code>

        <strong>特质</strong>：得道高僧音

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/zvvknx/%E9%BE%99%E9%AB%98%E5%83%A7.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={7} style={{ verticalAlign: "top" }}>
        语音助手
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安莉

        <strong>voice参数</strong>：<code>longanli</code>

        <strong>特质</strong>：利落从容女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/qeupsn/%E9%BE%99%E5%AE%89%E8%8E%89.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安朗

        <strong>voice参数</strong>：<code>longanlang</code>

        <strong>特质</strong>：清爽利落男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/tsrnwi/%E9%BE%99%E5%AE%89%E6%9C%97.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安温

        <strong>voice参数</strong>：<code>longanwen</code>

        <strong>特质</strong>：优雅知性女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/yxzzsu/%E9%BE%99%E5%AE%89%E6%B8%A9.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安昀

        <strong>voice参数</strong>：<code>longanyun</code>

        <strong>特质</strong>：居家暖男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/qxvhdx/%E9%BE%99%E5%AE%89%E6%98%80.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：YUMI

        <strong>voice参数</strong>：<code>longyumi\_v2</code>

        <strong>特质</strong>：正经青年女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/rbfsft/YUMI.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙小淳

        <strong>voice参数</strong>：<code>longxiaochun\_v2</code>

        <strong>特质</strong>：知性积极女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/lzchvc/%E9%BE%99%E5%B0%8F%E6%B7%B3.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙小夏

        <strong>voice参数</strong>：<code>longxiaoxia\_v2</code>

        <strong>特质</strong>：沉稳权威女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/hefbjz/%E9%BE%99%E5%B0%8F%E5%A4%8F.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={11} style={{ verticalAlign: "top" }}>
        有声书
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙逸尘

        <strong>voice参数</strong>：<code>longyichen</code>

        <strong>特质</strong>：洒脱活力男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/pperyq/%E9%BE%99%E9%80%B8%E5%B0%98.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙婉君

        <strong>voice参数</strong>：<code>longwanjun</code>

        <strong>特质</strong>：细腻柔声女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/tacvta/%E9%BE%99%E5%A9%89%E5%90%9B.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙老伯

        <strong>voice参数</strong>：<code>longlaobo</code>

        <strong>特质</strong>：沧桑岁月爷

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/kudwut/%E9%BE%99%E8%80%81%E4%BC%AF.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙老姨

        <strong>voice参数</strong>：<code>longlaoyi</code>

        <strong>特质</strong>：烟火从容阿姨

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/zspyhi/%E9%BE%99%E8%80%81%E5%A7%A8.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙白芷

        <strong>voice参数</strong>：<code>longbaizhi</code>

        <strong>特质</strong>：睿气旁白女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/ypyjeg/%E9%BE%99%E7%99%BD%E8%8A%B7.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙三叔

        <strong>voice参数</strong>：<code>longsanshu</code>

        <strong>特质</strong>：沉稳质感男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/otqtwd/%E9%BE%99%E4%B8%89%E5%8F%94.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙修

        <strong>voice参数</strong>：<code>longxiu\_v2</code>

        <strong>特质</strong>：博才说书男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/isfcla/%E9%BE%99%E4%BF%AE.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙妙

        <strong>voice参数</strong>：<code>longmiao\_v2</code>

        <strong>特质</strong>：抑扬顿挫女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/ynecbl/%E9%BE%99%E5%A6%99.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙悦

        <strong>voice参数</strong>：<code>longyue\_v2</code>

        <strong>特质</strong>：温暖磁性女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/xladsm/%E9%BE%99%E6%82%A6.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙楠

        <strong>voice参数</strong>：<code>longnan\_v2</code>

        <strong>特质</strong>：睿智青年男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/tojjob/%E9%BE%99%E6%A5%A0.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙媛

        <strong>voice参数</strong>：<code>longyuan\_v2</code>

        <strong>特质</strong>：温暖治愈女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/nqjskd/%E9%BE%99%E5%AA%9B.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={21} style={{ verticalAlign: "top" }}>
        社交陪伴
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安亲

        <strong>voice参数</strong>：<code>longanqin</code>

        <strong>特质</strong>：亲和活泼女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/hiywdd/%E9%BE%99%E5%AE%89%E4%BA%B2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安雅

        <strong>voice参数</strong>：<code>longanya</code>

        <strong>特质</strong>：高雅气质女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/ynwlzs/%E9%BE%99%E5%AE%89%E9%9B%85.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安朔

        <strong>voice参数</strong>：<code>longanshuo</code>

        <strong>特质</strong>：干净清爽男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/nybufr/%E9%BE%99%E5%AE%89%E6%9C%94.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安灵

        <strong>voice参数</strong>：<code>longanling</code>

        <strong>特质</strong>：思维灵动女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/lwdcfc/%E9%BE%99%E5%AE%89%E7%81%B5.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安智

        <strong>voice参数</strong>：<code>longanzhi</code>

        <strong>特质</strong>：睿智轻熟男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/sovode/%E9%BE%99%E5%AE%89%E6%99%BA.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安柔

        <strong>voice参数</strong>：<code>longanrou</code>

        <strong>特质</strong>：温柔闺蜜女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/koxfwm/%E9%BE%99%E5%AE%89%E6%9F%94.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙嫱

        <strong>voice参数</strong>：<code>longqiang\_v2</code>

        <strong>特质</strong>：浪漫风情女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/ycatrj/%E9%BE%99%E5%AB%B1.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙寒

        <strong>voice参数</strong>：<code>longhan\_v2</code>

        <strong>特质</strong>：温暖痴情男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/npcvfg/%E9%BE%99%E5%AF%92.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙星

        <strong>voice参数</strong>：<code>longxing\_v2</code>

        <strong>特质</strong>：温婉邻家女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/gqlaod/%E9%BE%99%E6%98%9F.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙华

        <strong>voice参数</strong>：<code>longhua\_v2</code>

        <strong>特质</strong>：元气甜美女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/cvtzjh/%E9%BE%99%E5%8D%8E.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙婉

        <strong>voice参数</strong>：<code>longwan\_v2</code>

        <strong>特质</strong>：积极知性女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/ybizun/%E9%BE%99%E5%A9%89.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙橙

        <strong>voice参数</strong>：<code>longcheng\_v2</code>

        <strong>特质</strong>：智慧青年男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/yxlcev/%E9%BE%99%E6%A9%99.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙菲菲

        <strong>voice参数</strong>：<code>longfeifei\_v2</code>

        <strong>特质</strong>：甜美娇气女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/afkidl/%E9%BE%99%E8%8F%B2%E8%8F%B2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙小诚

        <strong>voice参数</strong>：<code>longxiaocheng\_v2</code>

        <strong>特质</strong>：磁性低音男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/sjhntj/%E9%BE%99%E5%B0%8F%E8%AF%9A.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙哲

        <strong>voice参数</strong>：<code>longzhe\_v2</code>

        <strong>特质</strong>：呆板大暖男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/wogvqz/%E9%BE%99%E5%93%B2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙颜

        <strong>voice参数</strong>：<code>longyan\_v2</code>

        <strong>特质</strong>：温暖春风女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/xtfsbk/%E9%BE%99%E9%A2%9C.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙天

        <strong>voice参数</strong>：<code>longtian\_v2</code>

        <strong>特质</strong>：磁性理智男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/vkbkhz/%E9%BE%99%E5%A4%A9.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙泽

        <strong>voice参数</strong>：<code>longze\_v2</code>

        <strong>特质</strong>：温暖元气男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/mdolqm/%E9%BE%99%E6%B3%BD.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙邵

        <strong>voice参数</strong>：<code>longshao\_v2</code>

        <strong>特质</strong>：积极向上男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/krbwty/%E9%BE%99%E9%82%B5.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙浩

        <strong>voice参数</strong>：<code>longhao\_v2</code>

        <strong>特质</strong>：多情忧郁男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/lbljnc/%E9%BE%99%E6%B5%A9.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙深

        <strong>voice参数</strong>：<code>kabuleshen\_v2</code>

        <strong>特质</strong>：实力歌手男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250624/uqtctc/%E9%BE%99%E6%B7%B1.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        童声（标杆音色）
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙呼呼

        <strong>voice参数</strong>：<code>longhuhu</code>

        <strong>特质</strong>：天真烂漫女童

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/zfvpjw/%E9%BE%99%E5%91%BC%E5%91%BC.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        消费电子-教育培训
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安培

        <strong>voice参数</strong>：<code>longanpei</code>

        <strong>特质</strong>：青少年教师女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/qpyqha/%E9%BE%99%E5%AE%89%E5%9F%B9.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={2} style={{ verticalAlign: "top" }}>
        消费电子-儿童陪伴
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙汪汪

        <strong>voice参数</strong>：<code>longwangwang</code>

        <strong>特质</strong>：台湾少年音

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/goftfb/%E9%BE%99%E6%B1%AA%E6%B1%AA.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙泡泡

        <strong>voice参数</strong>：<code>longpaopao</code>

        <strong>特质</strong>：飞天泡泡音

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/usrfom/%E9%BE%99%E6%B3%A1%E6%B3%A1.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={2} style={{ verticalAlign: "top" }}>
        消费电子-儿童有声书
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙闪闪

        <strong>voice参数</strong>：<code>longshanshan</code>

        <strong>特质</strong>：戏剧化童声

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/nnygwq/%E9%BE%99%E9%97%AA%E9%97%AA.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙牛牛

        <strong>voice参数</strong>：<code>longniuniu</code>

        <strong>特质</strong>：阳光男童声

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/yeljel/%E9%BE%99%E7%89%9B%E7%89%9B.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={10} style={{ verticalAlign: "top" }}>
        客服
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应沐

        <strong>voice参数</strong>：<code>longyingmu</code>

        <strong>特质</strong>：优雅知性女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/daptml/%E9%BE%99%E5%BA%94%E6%B2%90.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应询

        <strong>voice参数</strong>：<code>longyingxun</code>

        <strong>特质</strong>：年轻青涩男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/lezfco/%E9%BE%99%E5%BA%94%E8%AF%A2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应催

        <strong>voice参数</strong>：<code>longyingcui</code>

        <strong>特质</strong>：严肃催收男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/yyoson/%E9%BE%99%E5%BA%94%E5%82%AC.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应答

        <strong>voice参数</strong>：<code>longyingda</code>

        <strong>特质</strong>：开朗高音女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/eoxghk/%E9%BE%99%E5%BA%94%E7%AD%94.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应静

        <strong>voice参数</strong>：<code>longyingjing</code>

        <strong>特质</strong>：低调冷静女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/nbabuh/%E9%BE%99%E5%BA%94%E9%9D%99.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应严

        <strong>voice参数</strong>：<code>longyingyan</code>

        <strong>特质</strong>：义正严辞女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/vufxoj/%E9%BE%99%E5%BA%94%E4%B8%A5.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应甜

        <strong>voice参数</strong>：<code>longyingtian</code>

        <strong>特质</strong>：温柔甜美女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/fiiebi/%E9%BE%99%E5%BA%94%E7%94%9C.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应冰

        <strong>voice参数</strong>：<code>longyingbing</code>

        <strong>特质</strong>：尖锐强势女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/ucqara/%E9%BE%99%E5%BA%94%E5%86%B0.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应桃

        <strong>voice参数</strong>：<code>longyingtao</code>

        <strong>特质</strong>：温柔淡定女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/ovwghc/%E9%BE%99%E5%BA%94%E6%A1%83.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙应聆

        <strong>voice参数</strong>：<code>longyingling</code>

        <strong>特质</strong>：温和共情女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/ldntih/%E9%BE%99%E5%BA%94%E8%81%86.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={4} style={{ verticalAlign: "top" }}>
        直播带货
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安燃

        <strong>voice参数</strong>：<code>longanran</code>

        <strong>特质</strong>：活泼质感女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/smffyb/%E9%BE%99%E5%AE%89%E7%87%83.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安宣

        <strong>voice参数</strong>：<code>longanxuan</code>

        <strong>特质</strong>：经典直播女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/kcnyee/%E9%BE%99%E5%AE%89%E5%AE%A3.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安冲

        <strong>voice参数</strong>：<code>longanchong</code>

        <strong>特质</strong>：激情推销男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/vovswj/%E9%BE%99%E5%AE%89%E5%86%B2.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙安萍

        <strong>voice参数</strong>：<code>longanping</code>

        <strong>特质</strong>：高亢直播女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250813/otftuf/%E9%BE%99%E5%AE%89%E8%90%8D.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={4} style={{ verticalAlign: "top" }}>
        童声
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙杰力豆

        <strong>voice参数</strong>：<code>longjielidou\_v2</code>

        <strong>特质</strong>：阳光顽皮男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/xqrzon/%E9%BE%99%E6%9D%B0%E5%8A%9B%E8%B1%86.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙铃

        <strong>voice参数</strong>：<code>longling\_v2</code>

        <strong>特质</strong>：稚气呆板女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/qunivx/%E9%BE%99%E9%93%83.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙可

        <strong>voice参数</strong>：<code>longke\_v2</code>

        <strong>特质</strong>：懵懂乖乖女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/fpavdo/%E9%BE%99%E5%8F%AF.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙仙

        <strong>voice参数</strong>：<code>longxian\_v2</code>

        <strong>特质</strong>：豪放可爱女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/hethqh/%E9%BE%99%E4%BB%99.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={3} style={{ verticalAlign: "top" }}>
        方言
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙老铁

        <strong>voice参数</strong>：<code>longlaotie\_v2</code>

        <strong>特质</strong>：东北直率男

        <strong>语言</strong>：中文（东北话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/vomcqn/%E9%BE%99%E8%80%81%E9%93%81.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙嘉怡

        <strong>voice参数</strong>：<code>longjiayi\_v2</code>

        <strong>特质</strong>：知性粤语女

        <strong>语言</strong>：中文（粤语）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/gzugcz/%E9%BE%99%E5%98%89%E6%80%A1.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙桃

        <strong>voice参数</strong>：<code>longtao\_v2</code>

        <strong>特质</strong>：积极粤语女

        <strong>语言</strong>：中文（粤语）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/fqkjrp/%E9%BE%99%E6%A1%83.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={3} style={{ verticalAlign: "top" }}>
        诗词朗诵
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙飞

        <strong>voice参数</strong>：<code>longfei\_v2</code>

        <strong>特质</strong>：热血磁性男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/liqtfn/%E9%BE%99%E9%A3%9E.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：李白

        <strong>voice参数</strong>：<code>libai\_v2</code>

        <strong>特质</strong>：古代诗仙男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/jkrnti/%E6%9D%8E%E7%99%BD.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙津

        <strong>voice参数</strong>：<code>longjin\_v2</code>

        <strong>特质</strong>：优雅温润男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/yfsrer/%E9%BE%99%E6%B4%A5.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={6} style={{ verticalAlign: "top" }}>
        新闻播报
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙书

        <strong>voice参数</strong>：<code>longshu\_v2</code>

        <strong>特质</strong>：沉稳青年男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/zihuud/%E9%BE%99%E4%B9%A6.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：Bella2.0

        <strong>voice参数</strong>：<code>loongbella\_v2</code>

        <strong>特质</strong>：精准干练女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/jbxouw/Bella2.0.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙硕

        <strong>voice参数</strong>：<code>longshuo\_v2</code>

        <strong>特质</strong>：博才干练男

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/mhfnjs/%E9%BE%99%E7%A1%95.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙小白

        <strong>voice参数</strong>：<code>longxiaobai\_v2</code>

        <strong>特质</strong>：沉稳播报女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/ruqhkp/%E9%BE%99%E5%B0%8F%E7%99%BD.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：龙婧

        <strong>voice参数</strong>：<code>longjing\_v2</code>

        <strong>特质</strong>：典型播音女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/yryekx/%E9%BE%99%E5%A9%A7.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongstella

        <strong>voice参数</strong>：<code>loongstella\_v2</code>

        <strong>特质</strong>：飒爽利落女

        <strong>语言</strong>：中文（普通话）、英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250617/jfnolr/loongstella.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td rowSpan={22} style={{ verticalAlign: "top" }}>
        出海营销
      </td>

      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongyuuna

        <strong>voice参数</strong>：<code>loongyuuna\_v2</code>

        <strong>特质</strong>：元气霓虹女

        <strong>语言</strong>：日语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/udwmjr/loongyuuna.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongyuuma

        <strong>voice参数</strong>：<code>loongyuuma\_v2</code>

        <strong>特质</strong>：干练霓虹男

        <strong>语言</strong>：日语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/rtivvj/loongyuuma.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongjihun

        <strong>voice参数</strong>：<code>loongjihun\_v2</code>

        <strong>特质</strong>：阳光韩国男

        <strong>语言</strong>：韩语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251020/eukhgt/loongjihun.mp3" type="audio/mpeg" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongeva

        <strong>voice参数</strong>：<code>loongeva\_v2</code>

        <strong>特质</strong>：知性英文女

        <strong>语言</strong>：英式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/xyivxq/loongeva.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongbrian

        <strong>voice参数</strong>：<code>loongbrian\_v2</code>

        <strong>特质</strong>：沉稳英文男

        <strong>语言</strong>：英式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/evasww/loongbrian.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongluna

        <strong>voice参数</strong>：<code>loongluna\_v2</code>

        <strong>特质</strong>：英式英文女

        <strong>语言</strong>：英式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/otfvbi/loongluna.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongluca

        <strong>voice参数</strong>：<code>loongluca\_v2</code>

        <strong>特质</strong>：英式英文男

        <strong>语言</strong>：英式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/qlvogk/loongluca.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongemily

        <strong>voice参数</strong>：<code>loongemily\_v2</code>

        <strong>特质</strong>：英式英文女

        <strong>语言</strong>：英式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/mprrsl/loongemily.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongeric

        <strong>voice参数</strong>：<code>loongeric\_v2</code>

        <strong>特质</strong>：英式英文男

        <strong>语言</strong>：英式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/nonkfg/loongeric.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongabby

        <strong>voice参数</strong>：<code>loongabby\_v2</code>

        <strong>特质</strong>：美式英文女

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/rldxbm/loongabby.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongannie

        <strong>voice参数</strong>：<code>loongannie\_v2</code>

        <strong>特质</strong>：美式英文女

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/clmmzb/loongannie.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongandy

        <strong>voice参数</strong>：<code>loongandy\_v2</code>

        <strong>特质</strong>：美式英文男

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/mqqzei/loongandy.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongava

        <strong>voice参数</strong>：<code>loongava\_v2</code>

        <strong>特质</strong>：美式英文女

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/vtqwat/loongava.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongbeth

        <strong>voice参数</strong>：<code>loongbeth\_v2</code>

        <strong>特质</strong>：美式英文女

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/rcpeak/loongbeth.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongbetty

        <strong>voice参数</strong>：<code>loongbetty\_v2</code>

        <strong>特质</strong>：美式英文女

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/zmyqjd/loongbetty.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongcindy

        <strong>voice参数</strong>：<code>loongcindy\_v2</code>

        <strong>特质</strong>：美式英文女

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/umqsuu/loongcindy.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongcally

        <strong>voice参数</strong>：<code>loongcally\_v2</code>

        <strong>特质</strong>：美式英文女

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/ebfujn/loongcally.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongdavid

        <strong>voice参数</strong>：<code>loongdavid\_v2</code>

        <strong>特质</strong>：美式英文男

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/kbhqis/loongdavid.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongdonna

        <strong>voice参数</strong>：<code>loongdonna\_v2</code>

        <strong>特质</strong>：美式英文女

        <strong>语言</strong>：美式英文
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/yjpiqa/loongdonna.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongkyong

        <strong>voice参数</strong>：<code>loongkyong\_v2</code>

        <strong>特质</strong>：韩语女

        <strong>语言</strong>：韩语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/cwjeat/loongkyong.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongtomoka

        <strong>voice参数</strong>：<code>loongtomoka\_v2</code>

        <strong>特质</strong>：日语女

        <strong>语言</strong>：日语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/psiupe/loongtomoka.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <strong>名称</strong>：loongtomoya

        <strong>voice参数</strong>：<code>loongtomoya\_v2</code>

        <strong>特质</strong>：日语男

        <strong>语言</strong>：日语
      </td>

      <td style={{ verticalAlign: "top" }}>
        SSML：不支持

        Instruct：不支持

        时间戳：不支持
      </td>

      <td style={{ verticalAlign: "top" }}>
        <audio controls style={{ width: "100%", maxWidth: "100%", minWidth: 0 }}>
          <source src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250618/mjjhps/loongtomoya.wav" type="audio/x-wav" />
        </audio>
      </td>

      <td style={{ verticalAlign: "top" }}>
        华北2（北京）
      </td>
    </tr>
  </tbody>
</table>

## 使用说明 <span id="voice-usage" />

各音色支持的功能以表格中的“特性支持”列为准：

- **SSML**：在 `text` 中填写 SSML 内容，标签用法请参见[SSML 标记语言介绍](/zh/model-studio/ssml-latex-user-guide#sl01_tagref_h3)。
- **指令控制（Instruct）**：通过 `instruction` 设置指令。音色有指定格式时，须按该音色的折叠说明填写；未指定格式时，可使用自然语言指令。详情请参见[实时语音合成](/zh/model-studio/realtime-tts-user-guide#12884a10929p9)和[非实时语音合成](/zh/model-studio/non-realtime-tts-user-guide#nrt_instruct_h3)的指令控制说明。
- **时间戳**：通过 `word_timestamp_enabled` 开启；Java SDK 对应参数为 `enableWordTimestamp`。

如需专属音色，请参见[音色复刻](/zh/model-studio/voice-cloning-user-guide)。
