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

    const statusLabels = {
        connecting: '连接中...',
        standby: '待命',
        listening: '聆听中...',
        speaking: '贾维斯说话中...',
        error: '连接异常',
    };

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
            case 'ai_transcript':
                addMessage('ai', payload);
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
                window.pywebview.api.close_session();
            }
        } catch (err) {
            console.error('close_session failed', err);
        }
    });

    // 启动轮询
    pollLoop();
})();
