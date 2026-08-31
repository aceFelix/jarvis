/**
 * 工作台前端主逻辑（三栏）
 *
 * - 轮询 pywebview.api.poll_events() 消费引擎事件
 * - 中栏：气泡渲染（流式文本/思考块/工具卡片）+ 文本输入发送 + ask_user 条
 * - 左栏：模式切换（文本/实时）、双面板切换（历史会话 ⇄ 模型音色）
 * - 右栏：CPU/内存/磁盘指标渲染
 * - 标题栏：自绘窗口控制按钮（最小化/全屏/关闭，无边框窗口）
 * - 背景：反应炉波纹动画（说话时律动加速）
 *
 * @author aceFelix
 */

(function () {
    const reactor = new window.ArcReactor(document.getElementById('reactor-canvas'));

    // ---- DOM ----
    const chatHistory = document.getElementById('chat-history');
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const talkBtn = document.getElementById('talk-btn');
    const statusDot = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');
    const askBar = document.getElementById('ask-user-bar');
    const askText = document.getElementById('ask-user-text');
    const askInput = document.getElementById('ask-user-input');
    const askSend = document.getElementById('ask-user-send');

    // 当前流式中的 AI 气泡正文元素与思考块元素
    let streamingBody = null;
    let thinkingBlock = null;
    // 当前模式：text（文本对话）| talk（实时语音）
    let mode = 'text';
    let talkActive = false;

    // ================= 中栏：气泡渲染 =================

    function scrollBottom() {
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function makeMessage(cls, labelText) {
        const div = document.createElement('div');
        div.className = 'message ' + cls;
        if (labelText) {
            const label = document.createElement('div');
            label.className = 'message-label';
            label.textContent = labelText;
            div.appendChild(label);
        }
        chatHistory.appendChild(div);
        return div;
    }

    function addUserBubble(text) {
        const div = makeMessage('user', '你');
        const body = document.createElement('div');
        body.textContent = text;
        div.appendChild(body);
        scrollBottom();
    }

    function startAiBubble() {
        const div = makeMessage('ai', '贾维斯');
        const body = document.createElement('div');
        div.appendChild(body);
        scrollBottom();
        return body;
    }

    function appendAssistantText(text) {
        // 流式增量：追加到当前 AI 气泡（不存在则新建）
        if (!streamingBody) streamingBody = startAiBubble();
        streamingBody.textContent += text;
        scrollBottom();
    }

    function appendThinking(text) {
        // 思考链增量：浅色斜体块，位于当前气泡顶部
        if (!streamingBody) streamingBody = startAiBubble();
        const parent = streamingBody.parentElement;
        if (!thinkingBlock) {
            thinkingBlock = document.createElement('div');
            thinkingBlock.className = 'thinking-block';
            parent.insertBefore(thinkingBlock, streamingBody);
        }
        thinkingBlock.textContent += text;
        scrollBottom();
    }

    function finishAssistant() {
        streamingBody = null;
        thinkingBlock = null;
    }

    function addSystemMessage(text, isError) {
        const div = makeMessage('system' + (isError ? ' error-msg' : ''), '');
        div.textContent = text;
        scrollBottom();
    }

    function addToolCard(name, toolUseId, inputJson) {
        // 可折叠工具卡片：details/summary 原生组件
        const card = document.createElement('details');
        card.className = 'tool-card';
        card.dataset.toolId = toolUseId;
        const summary = document.createElement('summary');
        summary.textContent = name;
        const body = document.createElement('div');
        body.className = 'tool-body';
        body.textContent = inputJson;
        card.appendChild(summary);
        card.appendChild(body);
        chatHistory.appendChild(card);
        scrollBottom();
    }

    function fillToolResult(toolUseId, name, content, isError) {
        let card = chatHistory.querySelector(`.tool-card[data-tool-id="${CSS.escape(toolUseId)}"]`);
        if (!card) {
            addToolCard(name, toolUseId, '');
            card = chatHistory.querySelector(`.tool-card[data-tool-id="${CSS.escape(toolUseId)}"]`);
        }
        if (isError) card.classList.add('error');
        const body = card.querySelector('.tool-body');
        body.textContent = content || '(无输出)';
    }

    function showAskUser(prompt) {
        askText.textContent = prompt;
        askInput.value = '';
        askBar.classList.remove('hidden');
        askInput.focus();
    }

    function hideAskUser() {
        askBar.classList.add('hidden');
    }

    function sendAnswer() {
        const text = askInput.value;
        hideAskUser();
        callApi(api => api.answer_user(text));
    }

    // ================= 左栏：面板与列表 =================

    function callApi(fn) {
        try {
            if (window.pywebview && window.pywebview.api) {
                return fn(window.pywebview.api);
            }
        } catch (err) {
            console.error('api call failed', err);
        }
        return null;
    }

    /** 构造列表项（安全 DOM 构建，不用 innerHTML：会话标题/模型名均为数据）。 */
    function makeListItem(name, subText, current) {
        const item = document.createElement('div');
        item.className = 'list-item' + (current ? ' current' : '');
        const title = document.createElement('span');
        title.textContent = name;
        const sub = document.createElement('span');
        sub.className = 'sub';
        sub.textContent = subText;
        item.appendChild(title);
        item.appendChild(sub);
        return item;
    }

    async function refreshSessionList() {
        const list = document.getElementById('session-list');
        const sessions = await callApi(api => api.list_sessions()) || [];
        list.innerHTML = '';
        sessions.slice(0, 60).forEach(s => {
            const time = new Date(s.updated_at * 1000).toLocaleString();
            const item = makeListItem(s.name, `${time} · ${s.message_count} 条消息`, false);
            item.addEventListener('click', () => {
                callApi(api => api.load_session(s.name));
            });
            list.appendChild(item);
        });
    }

    async function refreshModelList() {
        const list = document.getElementById('model-list');
        const models = await callApi(api => api.list_models()) || [];
        list.innerHTML = '';
        models.forEach(m => {
            // 内置模型带描述（[llm.models]）时优先展示描述，其次厂商名，最后“当前”标记
            const sub = [m.desc || m.vendor || '', m.current ? '· 当前' : ''].filter(Boolean).join(' ');
            const item = makeListItem(m.name, sub, m.current);
            if (!m.current) {
                item.addEventListener('click', async () => {
                    await callApi(api => api.set_model(m.name));
                    addSystemMessage(`模型已切换为 ${m.name}（下次对话生效）`);
                    refreshModelList();
                });
            }
            list.appendChild(item);
        });
    }

    async function refreshVoiceList() {
        const list = document.getElementById('voice-list');
        const voices = await callApi(api => api.list_voices()) || [];
        list.innerHTML = '';
        voices.forEach(v => {
            const item = makeListItem(v.name, `${v.description || ''}${v.current ? ' · 当前' : ''}`, v.current);
            if (!v.current) {
                item.addEventListener('click', async () => {
                    await callApi(api => api.set_voice(v.name));
                    addSystemMessage(`音色已切换为 ${v.name}（下次语音生效）`);
                    refreshVoiceList();
                });
            }
            list.appendChild(item);
        });
    }

    // 面板切换：历史会话 ⇄ 模型 ⇄ 音色（三选一，各自独占全高可滚动）
    const panels = {
        'panel-history': { el: 'history-panel', refresh: refreshSessionList },
        'panel-model': { el: 'model-panel', refresh: refreshModelList },
        'panel-voice': { el: 'voice-panel', refresh: refreshVoiceList },
    };

    function switchPanel(activeBtnId) {
        Object.entries(panels).forEach(([btnId, cfg]) => {
            const active = btnId === activeBtnId;
            document.getElementById(cfg.el).classList.toggle('hidden', !active);
            document.getElementById(btnId).classList.toggle('active', active);
        });
        panels[activeBtnId].refresh();
    }

    Object.keys(panels).forEach(btnId => {
        document.getElementById(btnId).addEventListener('click', () => switchPanel(btnId));
    });

    // ================= 模式切换（文本 / 实时） =================

    function setMode(next) {
        if (next === mode) return;
        mode = next;
        document.getElementById('mode-text').classList.toggle('active', mode === 'text');
        document.getElementById('mode-talk').classList.toggle('active', mode === 'talk');
        if (mode === 'talk' && !talkActive) startTalk();
        if (mode === 'text' && talkActive) stopTalk();
    }

    function startTalk() {
        callApi(api => api.start_talk());
    }

    function stopTalk() {
        callApi(api => api.stop_talk());
    }

    document.getElementById('mode-text').addEventListener('click', () => setMode('text'));
    document.getElementById('mode-talk').addEventListener('click', () => setMode('talk'));
    talkBtn.addEventListener('click', () => setMode(talkActive ? 'text' : 'talk'));

    // ================= 发送消息 =================

    function sendMessage() {
        const text = chatInput.value.trim();
        if (!text) return;
        chatInput.value = '';
        chatInput.style.height = 'auto';
        addUserBubble(text);
        statusDot.className = 'status-dot busy';
        statusText.textContent = '思考中...';
        callApi(api => api.send_message(text));
    }

    sendBtn.addEventListener('click', sendMessage);
    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    // 输入框自适应高度
    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
    });
    document.getElementById('new-session-btn').addEventListener('click', () => {
        callApi(api => api.new_session());
    });
    askSend.addEventListener('click', sendAnswer);
    askInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') sendAnswer();
    });

    // ================= 右栏指标 =================

    function renderMetrics(payload) {
        const cpu = payload.cpu || 0;
        document.getElementById('cpu-value').textContent = cpu + '%';
        const cpuBar = document.getElementById('cpu-bar');
        cpuBar.style.width = cpu + '%';
        cpuBar.classList.toggle('hot', cpu > 85);

        const mem = payload.memory || {};
        document.getElementById('mem-value').textContent = (mem.percent || 0) + '%';
        document.getElementById('mem-bar').style.width = (mem.percent || 0) + '%';
        if (mem.total_gb) {
            document.getElementById('mem-detail').textContent =
                `${mem.used_gb} / ${mem.total_gb} GB`;
        }

        const disk = payload.disk || {};
        document.getElementById('disk-value').textContent = (disk.percent || 0) + '%';
        document.getElementById('disk-bar').style.width = (disk.percent || 0) + '%';
        if (disk.total_gb) {
            document.getElementById('disk-detail').textContent =
                `${disk.used_gb} / ${disk.total_gb} GB`;
        }
    }

    // ================= 事件分发 =================

    const talkStatusLabels = {
        connecting: '实时连接中...',
        standby: '实时待命',
        listening: '聆听中...',
        speaking: '贾维斯说话中...',
        error: '实时连接异常',
    };

    function dispatchEvent(event) {
        const { type, payload } = event;
        switch (type) {
            // ---- 初始化 ----
            case 'init':
                refreshSessionList();
                refreshModelList();
                refreshVoiceList();
                break;
            // ---- 文本对话 ----
            case 'user_message':
                // 引擎回显（本地已渲染过则跳过，避免双气泡）
                break;
            case 'assistant_text':
                appendAssistantText(payload);
                break;
            case 'assistant_thinking':
                appendThinking(payload);
                break;
            case 'assistant_done':
                finishAssistant();
                statusDot.className = 'status-dot idle';
                statusText.textContent = '就绪';
                refreshSessionList();
                break;
            case 'tool_use':
                addToolCard(payload.name, payload.id, JSON.stringify(payload.input, null, 2));
                break;
            case 'tool_result':
                fillToolResult(payload.id, payload.name, payload.content, payload.is_error);
                break;
            case 'info':
                addSystemMessage(payload);
                break;
            case 'warn':
                addSystemMessage('⚠ ' + payload);
                break;
            case 'error':
                statusDot.className = 'status-dot err';
                statusText.textContent = '出错';
                addSystemMessage('✗ ' + payload, true);
                break;
            case 'ask_user':
                showAskUser(payload);
                break;
            // ---- 会话管理 ----
            case 'session_ready':
            case 'session_new':
                chatHistory.innerHTML = '';
                streamingBody = null;
                thinkingBlock = null;
                if (type === 'session_new') addSystemMessage('已开启新会话');
                refreshSessionList();
                break;
            case 'session_loaded':
                // 恢复历史会话：清空并回放历史消息
                chatHistory.innerHTML = '';
                streamingBody = null;
                thinkingBlock = null;
                (payload.messages || []).forEach(m => {
                    if (m.role === 'assistant') {
                        // 无文本的纯工具轮次不生成空气泡，只留工具提示行
                        if (m.text) {
                            const div = makeMessage('ai', '贾维斯');
                            const body = document.createElement('div');
                            body.textContent = m.text;
                            div.appendChild(body);
                        }
                        if (m.tool_count) addToolCard(`历史工具调用 ×${m.tool_count}`, '', '(历史会话)');
                    } else if (m.text) {
                        addUserBubble(m.text);
                    }
                });
                scrollBottom();
                addSystemMessage(`已恢复会话「${payload.name}」`);
                refreshSessionList();
                break;
            case 'status':
                if (typeof payload === 'string' && talkStatusLabels[payload]) {
                    // 实时模式状态
                    reactor.setStatus(payload);
                    statusText.textContent = talkStatusLabels[payload];
                    statusDot.className = 'status-dot talk';
                } else if (typeof payload === 'string') {
                    statusText.textContent = payload;
                }
                break;
            // ---- 实时语音 ----
            case 'talk_started':
                talkActive = true;
                talkBtn.classList.add('danger');
                talkBtn.textContent = '⏹';
                break;
            case 'talk_stopped':
                talkActive = false;
                talkBtn.classList.remove('danger');
                talkBtn.textContent = '🎙️';
                statusDot.className = 'status-dot idle';
                statusText.textContent = '就绪';
                break;
            case 'volume':
                reactor.setVolume(payload);
                break;
            case 'user_speaking':
                reactor.setUserSpeaking(payload);
                break;
            case 'ai_speaking':
                reactor.setAiSpeaking(payload);
                break;
            case 'user_transcript':
                addUserBubble(payload);
                break;
            case 'ai_transcript_delta':
                appendAssistantText(payload);
                break;
            case 'ai_transcript':
                if (streamingBody) {
                    streamingBody.textContent = payload;
                    finishAssistant();
                } else {
                    const body = startAiBubble();
                    body.textContent = payload;
                }
                scrollBottom();
                break;
            // ---- 系统指标 ----
            case 'metrics':
                renderMetrics(payload);
                break;
            default:
                console.log('unknown event', event);
        }
    }

    // ================= 窗口控制（自绘标题栏按钮） =================

    // 无边框窗口没有系统按钮，控制按钮经 pywebview.api 转给主进程；
    // 无全屏按钮：启动即铺满工作区（不盖任务栏），真全屏会盖任务栏故不提供
    document.getElementById('btn-min').addEventListener('click', () => {
        callApi(api => api.window_minimize());
    });
    document.getElementById('btn-close').addEventListener('click', () => {
        callApi(api => api.window_close());
    });

    async function pollLoop() {
        try {
            const events = await callApi(api => api.poll_events());
            if (Array.isArray(events)) events.forEach(dispatchEvent);
        } catch (err) {
            // pywebview 未就绪时静默重试
        }
        setTimeout(pollLoop, 50);
    }

    // 启动
    pollLoop();
    refreshSessionList();
})();
