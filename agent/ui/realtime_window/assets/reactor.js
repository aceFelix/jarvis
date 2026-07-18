/**
 * 方舟反应炉（Arc Reactor）Canvas 粒子动画
 *
 * 功能：
 * - 多层同心圆环缓慢旋转
 * - 粒子沿轨道运动，受音量影响抖动幅度
 * - 用户/AI 说话时触发脉冲波纹
 * - 根据说话者身份切换主色调
 *
 * @author aceFelix
 */

class ArcReactor {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.dpr = window.devicePixelRatio || 1;

        this.status = 'connecting'; // connecting | standby | listening | speaking | error
        this.volume = 0.0;
        this.userSpeaking = false;
        this.aiSpeaking = false;

        this.particles = [];
        this.ripples = [];
        this.rings = [];
        this.time = 0;

        this.colors = {
            connecting: { main: '#1a3a5a', glow: '#1a3a5a' },
            standby: { main: '#5bc8ff', glow: '#5bc8ff' },
            listening: { main: '#00f0ff', glow: '#00f0ff' },
            speaking: { main: '#ff6b35', glow: '#ffd700' },
            error: { main: '#ff2a2a', glow: '#ff2a2a' },
        };

        this.resize();
        window.addEventListener('resize', () => this.resize());
        this.initRings();
        this.initParticles();
        this.animate();
    }

    resize() {
        const rect = this.canvas.parentElement ? this.canvas.parentElement.getBoundingClientRect() : { width: window.innerWidth, height: window.innerHeight };
        this.width = rect.width;
        this.height = rect.height;
        this.canvas.width = this.width * this.dpr;
        this.canvas.height = this.height * this.dpr;
        this.canvas.style.width = this.width + 'px';
        this.canvas.style.height = this.height + 'px';
        this.ctx.scale(this.dpr, this.dpr);
        this.cx = this.width / 2;
        this.cy = this.height / 2;
        // 窗口最大化后反应炉仍要足够醒目，这里取 38% 让整体充满画面中央
        this.baseRadius = Math.min(this.width, this.height) * 0.38;
    }

    initRings() {
        // 圆环半径随 baseRadius 变大，保持视觉层次
        this.rings = [
            { radius: 0.45, speed: 0.004, width: 3, alpha: 0.45 },
            { radius: 0.65, speed: -0.006, width: 2.5, alpha: 0.38 },
            { radius: 0.82, speed: 0.008, width: 2, alpha: 0.28 },
            { radius: 1.0, speed: -0.010, width: 1.5, alpha: 0.18 },
        ];
    }

    initParticles() {
        // 粒子更多、分布更广，配合放大后的反应炉
        const count = 200;
        this.particles = [];
        for (let i = 0; i < count; i++) {
            this.particles.push({
                angle: Math.random() * Math.PI * 2,
                orbitRadius: 0.35 + Math.random() * 0.75,
                speed: 0.0015 + Math.random() * 0.005,
                size: 1 + Math.random() * 2.5,
                phase: Math.random() * Math.PI * 2,
            });
        }
    }

    setStatus(status) {
        if (this.status === status) return;
        this.status = status;
        if (status === 'speaking' || status === 'listening') {
            this.triggerRipple(2);
        }
    }

    setVolume(level) {
        // 指数平滑
        this.volume = this.volume * 0.7 + Math.max(0, Math.min(1, level)) * 0.3;
    }

    setUserSpeaking(speaking) {
        if (this.userSpeaking === speaking) return;
        this.userSpeaking = speaking;
        if (speaking) {
            this.setStatus('listening');
            this.triggerRipple(1);
        } else if (!this.aiSpeaking && this.status === 'listening') {
            this.setStatus('standby');
        }
    }

    setAiSpeaking(speaking) {
        if (this.aiSpeaking === speaking) return;
        this.aiSpeaking = speaking;
        if (speaking) {
            this.setStatus('speaking');
            this.triggerRipple(3);
        } else if (!this.userSpeaking && this.status === 'speaking') {
            this.setStatus('standby');
        }
    }

    triggerRipple(count = 1) {
        for (let i = 0; i < count; i++) {
            this.ripples.push({
                radius: this.baseRadius * 0.2,
                alpha: 0.8,
                width: 3,
                delay: i * 8,
            });
        }
    }

    getColor() {
        if (this.status === 'speaking') return this.colors.speaking;
        if (this.status === 'listening') return this.colors.listening;
        return this.colors[this.status] || this.colors.standby;
    }

    drawGlow(cx, cy, radius, color) {
        const gradient = this.ctx.createRadialGradient(cx, cy, radius * 0.2, cx, cy, radius * 1.6);
        gradient.addColorStop(0, color + '33');
        gradient.addColorStop(0.5, color + '11');
        gradient.addColorStop(1, 'transparent');
        this.ctx.fillStyle = gradient;
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, radius * 1.6, 0, Math.PI * 2);
        this.ctx.fill();
    }

    drawCore(cx, cy, radius, color) {
        // 外环
        this.ctx.strokeStyle = color;
        this.ctx.lineWidth = 3;
        this.ctx.shadowBlur = 20;
        this.ctx.shadowColor = color;
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        this.ctx.stroke();
        this.ctx.shadowBlur = 0;

        // 内核发光
        const coreGradient = this.ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 0.85);
        coreGradient.addColorStop(0, color + '66');
        coreGradient.addColorStop(0.6, color + '22');
        coreGradient.addColorStop(1, 'transparent');
        this.ctx.fillStyle = coreGradient;
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, radius * 0.85, 0, Math.PI * 2);
        this.ctx.fill();

        // 中心亮点
        this.ctx.fillStyle = '#ffffff';
        this.ctx.globalAlpha = 0.8;
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, radius * 0.08, 0, Math.PI * 2);
        this.ctx.fill();
        this.ctx.globalAlpha = 1.0;
    }

    drawRings(cx, cy, radius, color) {
        this.rings.forEach((ring, idx) => {
            const r = radius * ring.radius;
            const rotation = this.time * ring.speed * (idx % 2 === 0 ? 1 : -1) + idx;
            this.ctx.save();
            this.ctx.translate(cx, cy);
            this.ctx.rotate(rotation);
            this.ctx.strokeStyle = color;
            this.ctx.globalAlpha = ring.alpha;
            this.ctx.lineWidth = ring.width;
            this.ctx.setLineDash([10, 15]);
            this.ctx.beginPath();
            this.ctx.arc(0, 0, r, 0, Math.PI * 2);
            this.ctx.stroke();
            this.ctx.restore();
        });
        this.ctx.globalAlpha = 1.0;
        this.ctx.setLineDash([]);
    }

    drawParticles(cx, cy, radius, color) {
        const jitter = this.volume * 12;
        const expansion = this.aiSpeaking ? 1.15 : (this.userSpeaking ? 1.05 : 1.0);

        this.particles.forEach(p => {
            p.angle += p.speed * (this.userSpeaking ? 1.5 + this.volume : 1.0);

            const baseR = radius * p.orbitRadius * expansion;
            const shakeX = Math.sin(this.time * 0.2 + p.phase) * jitter;
            const shakeY = Math.cos(this.time * 0.25 + p.phase) * jitter;
            const x = cx + Math.cos(p.angle) * baseR + shakeX;
            const y = cy + Math.sin(p.angle) * baseR * 0.85 + shakeY;

            const alpha = 0.4 + this.volume * 0.5;
            const size = p.size * (0.8 + this.volume * 0.7);

            this.ctx.fillStyle = color;
            this.ctx.globalAlpha = alpha;
            this.ctx.shadowBlur = size * 3;
            this.ctx.shadowColor = color;
            this.ctx.beginPath();
            this.ctx.arc(x, y, size, 0, Math.PI * 2);
            this.ctx.fill();
        });
        this.ctx.globalAlpha = 1.0;
        this.ctx.shadowBlur = 0;
    }

    drawRipples(cx, cy, color) {
        for (let i = this.ripples.length - 1; i >= 0; i--) {
            const ripple = this.ripples[i];
            if (ripple.delay > 0) {
                ripple.delay--;
                continue;
            }
            ripple.radius += this.baseRadius * 0.015;
            ripple.alpha -= 0.015;
            ripple.width -= 0.02;

            if (ripple.alpha <= 0 || ripple.width <= 0) {
                this.ripples.splice(i, 1);
                continue;
            }

            this.ctx.strokeStyle = color;
            this.ctx.globalAlpha = ripple.alpha;
            this.ctx.lineWidth = Math.max(0.5, ripple.width);
            this.ctx.beginPath();
            this.ctx.arc(cx, cy, ripple.radius, 0, Math.PI * 2);
            this.ctx.stroke();
        }
        this.ctx.globalAlpha = 1.0;
    }

    animate() {
        this.time++;
        const ctx = this.ctx;
        const { width, height, cx, cy, baseRadius } = this;
        const color = this.getColor().main;

        // 清屏，带轻微拖尾
        ctx.fillStyle = 'rgba(0, 0, 0, 0.25)';
        ctx.fillRect(0, 0, width, height);

        this.drawGlow(cx, cy, baseRadius, color);
        this.drawCore(cx, cy, baseRadius * 0.35, color);
        this.drawRings(cx, cy, baseRadius, color);
        this.drawParticles(cx, cy, baseRadius, color);
        this.drawRipples(cx, cy, color);

        requestAnimationFrame(() => this.animate());
    }
}

window.ArcReactor = ArcReactor;
