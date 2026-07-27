/**
 * 实时聊天窗口前端逻辑
 *
 * - 初始化方舟反应炉动画
 * - 通过 pywebview.api.poll_events() 从 Python 拉取事件
 * - 更新状态栏、音量条、聊天气泡
 * - 关闭按钮通知 Python 停止会话
 *
 * @author aceFelix
 */

(function () {
    const canvas = document.getElementById('reactor-canvas');
    const statusText = document.getElementById('status-text');
    const statusIndicator = document.getElementById('status-indicator');
    const volumeFill = document.getElementById('volume-fill');
    const chatHistory = document.getElementById('chat-history');
    const closeBtn = document.getElementById('close-btn');

    const reactor = new window.ArcReactor(canvas);

    // 会话状态：true 表示正在运行，false 表示已暂停
    let sessionActive = true;
    // 当前正在流式输出的 AI 消息体元素（null 表示无）
    let streamingAiBody = null;

    const statusLabels = {
        connecting: '连接中...',
        standby: '待命',
        listening: '聆听中...',
        speaking: '贾维斯说话中...',
        error: '连接异常',
        paused: '已暂停',
    };

    function setButtonPaused(paused) {
        if (!closeBtn) return;
        sessionActive = !paused;
        if (paused) {
            closeBtn.textContent = '恢复对话';
            closeBtn.classList.add('resume');
            closeBtn.classList.remove('close');
        } else {
            closeBtn.textContent = '结束';
            closeBtn.classList.add('close');
            closeBtn.classList.remove('resume');
        }
    }

    function setStatus(status) {
        reactor.setStatus(status);
        statusIndicator.className = status;
        statusText.textContent = statusLabels[status] || status;
    }

    function setVolume(level) {
        reactor.setVolume(level);
        volumeFill.style.width = Math.round(level * 100) + '%';
    }

    function addMessage(role, text) {
        const div = document.createElement('div');
        div.className = 'message ' + role;

        const label = document.createElement('div');
        label.className = 'message-label';
        label.textContent = role === 'user' ? '你' : '贾维斯';

        const body = document.createElement('div');
        body.textContent = text;

        div.appendChild(label);
        div.appendChild(body);
        chatHistory.appendChild(div);
        chatHistory.scrollTop = chatHistory.scrollHeight;
        return body;
    }

    function startStreamingAi() {
        // 创建一条空的 AI 消息，返回 body 元素供后续追加文字
        const div = document.createElement('div');
        div.className = 'message ai';

        const label = document.createElement('div');
        label.className = 'message-label';
        label.textContent = '贾维斯';

        const body = document.createElement('div');
        body.textContent = '';

        div.appendChild(label);
        div.appendChild(body);
        chatHistory.appendChild(div);
        chatHistory.scrollTop = chatHistory.scrollHeight;
        return body;
    }

    function dispatchEvent(event) {
        const { type, payload } = event;
        switch (type) {
            case 'status':
                setStatus(payload);
                break;
            case 'volume':
                setVolume(payload);
                break;
            case 'user_speaking':
                reactor.setUserSpeaking(payload);
                if (payload) setStatus('listening');
                break;
            case 'ai_speaking':
                reactor.setAiSpeaking(payload);
                if (payload) setStatus('speaking');
                break;
            case 'user_transcript':
                addMessage('user', payload);
                break;
            case 'ai_transcript_delta':
                // 流式转写：逐字追加到当前 AI 消息气泡
                if (!streamingAiBody) {
                    streamingAiBody = startStreamingAi();
                }
                streamingAiBody.textContent += payload;
                chatHistory.scrollTop = chatHistory.scrollHeight;
                break;
            case 'ai_transcript':
                // 完整转写：如果有流式气泡则替换为最终文本，否则新建
                if (streamingAiBody) {
                    streamingAiBody.textContent = payload;
                    streamingAiBody = null;
                } else {
                    addMessage('ai', payload);
                }
                break;
            case 'clear_chat':
                chatHistory.innerHTML = '';
                streamingAiBody = null;
                break;
            case 'session_started':
                setButtonPaused(false);
                addMessage('ai', '会话已恢复，请说吧');
                break;
            case 'session_ended':
                setButtonPaused(true);
                addMessage('ai', '会话已暂停，点击“恢复对话”继续');
                break;
            case 'error':
                setStatus('error');
                addMessage('ai', '⚠ ' + payload);
                break;
            default:
                console.log('unknown event', event);
        }
    }

    async function pollLoop() {
        try {
            if (window.pywebview && window.pywebview.api) {
                const events = await window.pywebview.api.poll_events();
                if (Array.isArray(events)) {
                    events.forEach(dispatchEvent);
                }
            }
        } catch (err) {
            // pywebview 未就绪或出错时静默重试
        }
        setTimeout(pollLoop, 50);
    }

    closeBtn.addEventListener('click', () => {
        try {
            if (window.pywebview && window.pywebview.api) {
                if (sessionActive) {
                    // 立即切换 UI，避免 backend 卡顿时无反馈
                    setButtonPaused(true);
                    window.pywebview.api.close_session();
                } else {
                    setButtonPaused(false);
                    window.pywebview.api.resume_session();
                }
            }
        } catch (err) {
            console.error('toggle_session failed', err);
        }
    });

    // ESC 键暂停对话
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            try {
                if (window.pywebview && window.pywebview.api && sessionActive) {
                    setButtonPaused(true);
                    window.pywebview.api.close_session();
                }
            } catch (err) {
                console.error('ESC close_session failed', err);
            }
        }
    });

    // 启动轮询
    pollLoop();
})();
