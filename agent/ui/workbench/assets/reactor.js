/**
 * 工作台方舟反应炉（Arc Reactor）Canvas 动画 —— 透明底重制版
 *
 * 视觉设计（对标反应炉标志造型，淡蓝科技风）：
 * - 炽热核心：白心 + 多层径向辉光，持续呼吸脉动（说话时幅度加大）
 * - 三角线圈环：10 块梯形线圈段环绕核心，逐段轮流点亮（能量流动感）
 * - 分段发光弧环：多层圆弧正反向旋转，机械仪表感
 * - 刻度环：60 格细刻度，少量高亮刻度点缀
 * - 雷达扫掠：锥形渐变光束缓慢旋转（createConicGradient，不支持时降级跳过）
 * - 轨道粒子：~260 颗，预渲染辉光精灵到离屏 canvas 后 drawImage，规避逐粒子
 *   shadowBlur 的性能问题
 * - 波纹：双层描边圆环向外扩散，说话/触发事件时连发
 *
 * 律动口径：说话不换色，改为加速 + 脉冲幅度增大 + 亮度提升；
 * 速度系数 speed 平滑逼近（AI 说话 2.6 / 聆听 1.6 / 待机 1.0）。
 * 背景透明（clearRect），桌面从窗口透出。
 *
 * 公共 API（供 app.js 事件分发调用，保持兼容）：
 * - new ArcReactor(canvas)
 * - setStatus(status)         standby | listening | speaking | error
 * - setVolume(level)          0~1 音量
 * - setUserSpeaking(bool)
 * - setAiSpeaking(bool)
 * - triggerRipple(count)
 * - speed                     当前动画速度系数
 *
 * 性能口径（用户反馈风扇狂转后优化）：透明窗口走 WS_EX_LAYERED 软件合成，
 * 全屏 60fps 动画是 CPU 大头，故限帧 30fps + dpr 固定 1 + 粒子减到 160，
 * 窗口最小化/不可见时暂停绘制。
 *
 * @author aceFelix
 */

class ArcReactor {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d', { alpha: true });
        // 透明窗口软件合成下高分辨率无收益反增负担，固定 1 倍渲染
        this.dpr = 1;
        // 限帧：30fps（60fps 在分层透明窗口下 CPU 翻倍且肉眼差异小）
        this.frameInterval = 1000 / 30;
        this._lastFrame = 0;

        this.status = 'standby'; // standby | listening | speaking | error
        this.volume = 0.0;
        this.userSpeaking = false;
        this.aiSpeaking = false;
        // 动画速度系数：说话时平滑加速，静止时回落到 1
        this.speed = 1.0;
        // 能量等级：说话时抬升，驱动亮度与脉冲幅度（平滑逼近）
        this.energy = 0.35;

        this.time = 0;
        this.rippleTimer = 0;
        this.particles = [];
        this.ripples = [];

        this.colors = {
            standby: '#5bc8ff',
            listening: '#00f0ff',
            speaking: '#8fe3ff',
            error: '#ff5a5a',
        };
        // 线圈段数量（反应炉标志造型：环绕核心的三角线圈）
        this.coilCount = 10;

        // 支持性探测：锥形渐变用于雷达扫掠（旧内核降级跳过）
        this.hasConic = typeof this.ctx.createConicGradient === 'function';

        this.resize();
        window.addEventListener('resize', () => this.resize());
        this._buildSprites();
        this.initParticles();
        this.animate();
    }

    // ================= 尺寸与几何 =================

    resize() {
        this.width = window.innerWidth;
        this.height = window.innerHeight;
        this.canvas.width = this.width * this.dpr;
        this.canvas.height = this.height * this.dpr;
        this.canvas.style.width = this.width + 'px';
        this.canvas.style.height = this.height + 'px';
        this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
        this.cx = this.width / 2;
        this.cy = this.height / 2;
        // 三栏布局下反应炉居中铺底，半径取短边 30%
        this.baseRadius = Math.min(this.width, this.height) * 0.30;
        // 辉光精灵尺寸依赖半径，窗口变化后重建
        if (this.sprites) this._buildSprites();
    }

    /** 预渲染三档尺寸的辉光粒子精灵到离屏 canvas（一次绘制，逐帧 drawImage） */
    _buildSprites() {
        const base = Math.max(10, this.baseRadius * 0.06);
        this.sprites = [12, 20, 30].map(px => {
            const size = base * (px / 15);
            const c = document.createElement('canvas');
            c.width = size * 2;
            c.height = size * 2;
            const g = c.getContext('2d');
            const grad = g.createRadialGradient(size, size, 0, size, size, size);
            grad.addColorStop(0, 'rgba(255,255,255,0.95)');
            grad.addColorStop(0.25, 'rgba(143,227,255,0.55)');
            grad.addColorStop(0.6, 'rgba(91,200,255,0.18)');
            grad.addColorStop(1, 'rgba(91,200,255,0)');
            g.fillStyle = grad;
            g.fillRect(0, 0, size * 2, size * 2);
            return c;
        });
    }

    // ================= 初始化元素 =================

    /** 轨道粒子：限帧后用 160 颗兼顾观感与 CPU（原 260 在软件合成下发热明显） */
    initParticles() {
        const count = 160;
        this.particles = [];
        for (let i = 0; i < count; i++) {
            this.particles.push({
                angle: Math.random() * Math.PI * 2,
                // 轨道半径：核心外围到最外环之外，分布略偏外圈
                orbit: 0.42 + Math.pow(Math.random(), 0.8) * 0.95,
                speed: (0.0012 + Math.random() * 0.004) * (Math.random() < 0.2 ? -1 : 1),
                sprite: (Math.random() * 3) | 0,
                phase: Math.random() * Math.PI * 2,
                alpha: 0.25 + Math.random() * 0.55,
            });
        }
    }

    // ================= 公共控制 API =================

    setStatus(status) {
        if (this.status === status) return;
        this.status = status;
        if (status === 'speaking' || status === 'listening') {
            this.triggerRipple(2);
        }
    }

    setVolume(level) {
        // 指数平滑，避免波纹抖动突跳
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

    /** 触发向外扩散的波纹（事件驱动 + 待机呼吸共用） */
    triggerRipple(count = 1) {
        for (let i = 0; i < count; i++) {
            this.ripples.push({
                radius: this.baseRadius * 0.22,
                alpha: 0.55,
                delay: i * 9,
            });
        }
    }

    getColor() {
        return this.colors[this.status] || this.colors.standby;
    }

    /** 当前帧的脉冲系数：呼吸正弦 + 音量抬升 + 说话能量加成 */
    _pulse() {
        const breath = 0.5 + 0.5 * Math.sin(this.time * 0.035 * this.speed);
        return 0.85 + breath * 0.1 + this.volume * 0.25 + this.energy * 0.15;
    }

    // ================= 分层绘制 =================

    /** 底层大辉光：淡蓝径向光晕，随脉冲微微涨缩 */
    drawAmbient(color) {
        const { ctx, cx, cy } = this;
        const r = this.baseRadius * (1.55 + this.energy * 0.25);
        const grad = ctx.createRadialGradient(cx, cy, this.baseRadius * 0.05, cx, cy, r);
        grad.addColorStop(0, color + '30');
        grad.addColorStop(0.45, color + '12');
        grad.addColorStop(1, color + '00');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
    }

    /** 雷达扫掠：锥形渐变光束缓慢旋转（不支持 conic 渐变时跳过） */
    drawSweep(color) {
        if (!this.hasConic) return;
        const { ctx, cx, cy, baseRadius } = this;
        const angle = this.time * 0.006 * this.speed;
        const grad = ctx.createConicGradient(angle, cx, cy);
        grad.addColorStop(0, color + '26');
        grad.addColorStop(0.08, color + '00');
        grad.addColorStop(1, color + '00');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(cx, cy, baseRadius * 1.28, 0, Math.PI * 2);
        ctx.fill();
    }

    /** 炽热核心：白心 + 内环描边 + 辉光，呼吸脉动 */
    drawCore(color) {
        const { ctx, cx, cy } = this;
        const pulse = this._pulse();
        const r = this.baseRadius * 0.24 * pulse;

        // 核心外圈辉光
        const glow = ctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r * 2.4);
        glow.addColorStop(0, color + '55');
        glow.addColorStop(0.5, color + '1a');
        glow.addColorStop(1, color + '00');
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(cx, cy, r * 2.4, 0, Math.PI * 2);
        ctx.fill();

        // 核心盘面：中心炽白向边缘过渡到淡蓝
        const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        core.addColorStop(0, 'rgba(255,255,255,0.95)');
        core.addColorStop(0.35, color + 'cc');
        core.addColorStop(0.8, color + '40');
        core.addColorStop(1, color + '00');
        ctx.fillStyle = core;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();

        // 核心内环描边（清晰轮廓）
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.8;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cx, cy, r * 0.82, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
    }

    /** 三角线圈环：10 块梯形线圈段，逐段轮流点亮形成能量流动 */
    drawCoils(color) {
        const { ctx, cx, cy, baseRadius, coilCount } = this;
        const inner = baseRadius * 0.34;
        const outer = baseRadius * 0.48;
        const seg = (Math.PI * 2) / coilCount;
        const gap = seg * 0.22; // 段间缝隙
        // 点亮波峰沿线圈环转动（速度系数生效）
        const wave = this.time * 0.02 * this.speed;

        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(this.time * 0.0012 * this.speed);
        for (let i = 0; i < coilCount; i++) {
            const a0 = i * seg + gap / 2;
            const a1 = (i + 1) * seg - gap / 2;
            // 与波峰的角度差决定该段当前亮度（余弦衰减）
            const diff = Math.cos(i * seg - wave);
            const lit = Math.max(0, diff) ** 2;
            const alpha = 0.18 + lit * (0.5 + this.energy * 0.3);

            // 梯形线圈：内窄外宽
            const ix0 = Math.cos(a0) * inner, iy0 = Math.sin(a0) * inner;
            const ix1 = Math.cos(a1) * inner, iy1 = Math.sin(a1) * inner;
            const ox1 = Math.cos(a1) * outer, oy1 = Math.sin(a1) * outer;
            const ox0 = Math.cos(a0) * outer, oy0 = Math.sin(a0) * outer;

            ctx.beginPath();
            ctx.moveTo(ix0, iy0);
            ctx.lineTo(ix1, iy1);
            ctx.lineTo(ox1, oy1);
            ctx.lineTo(ox0, oy0);
            ctx.closePath();

            ctx.fillStyle = color;
            ctx.globalAlpha = alpha * 0.55;
            ctx.fill();
            ctx.globalAlpha = alpha;
            ctx.lineWidth = 1;
            ctx.strokeStyle = color;
            ctx.stroke();
        }
        ctx.restore();
        ctx.globalAlpha = 1;
    }

    /** 分段发光弧环：多层虚线弧正反向旋转 */
    drawArcs(color) {
        const { ctx, cx, cy, baseRadius } = this;
        const rings = [
            { r: 0.58, w: 2.5, dash: [baseRadius * 0.22, baseRadius * 0.10], v: 0.006, a: 0.55 },
            { r: 0.70, w: 2, dash: [baseRadius * 0.05, baseRadius * 0.045], v: -0.009, a: 0.42 },
            { r: 0.85, w: 3, dash: [baseRadius * 0.30, baseRadius * 0.16], v: 0.004, a: 0.35 },
            { r: 1.0, w: 1.5, dash: [baseRadius * 0.08, baseRadius * 0.12], v: -0.005, a: 0.28 },
        ];
        ctx.save();
        ctx.translate(cx, cy);
        rings.forEach((ring, idx) => {
            ctx.save();
            ctx.rotate(this.time * ring.v * this.speed + idx * 1.3);
            ctx.strokeStyle = color;
            // 说话时弧环更亮
            ctx.globalAlpha = ring.a * (0.8 + this.energy * 0.5);
            ctx.lineWidth = ring.w;
            ctx.lineCap = 'round';
            ctx.setLineDash(ring.dash);
            ctx.beginPath();
            ctx.arc(0, 0, baseRadius * ring.r, 0, Math.PI * 2);
            ctx.stroke();
            ctx.restore();
        });
        ctx.restore();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
    }

    /** 刻度环：60 格细刻度，每 5 格加长提亮，缓慢反向旋转 */
    drawTicks(color) {
        const { ctx, cx, cy, baseRadius } = this;
        const r = baseRadius * 1.12;
        const n = 60;
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(-this.time * 0.0018 * this.speed);
        for (let i = 0; i < n; i++) {
            const major = i % 5 === 0;
            const len = major ? baseRadius * 0.05 : baseRadius * 0.022;
            const a = (i / n) * Math.PI * 2;
            const c = Math.cos(a), s = Math.sin(a);
            ctx.strokeStyle = color;
            ctx.globalAlpha = major ? 0.5 : 0.22;
            ctx.lineWidth = major ? 1.5 : 1;
            ctx.beginPath();
            ctx.moveTo(c * r, s * r);
            ctx.lineTo(c * (r + len), s * (r + len));
            ctx.stroke();
        }
        ctx.restore();
        ctx.globalAlpha = 1;
    }

    /** 轨道粒子：预渲染辉光精灵 + drawImage（说话时轨道扩张、提速） */
    drawParticles(color) {
        const { ctx, cx, cy, baseRadius } = this;
        const expansion = this.aiSpeaking ? 1.1 : (this.userSpeaking ? 1.04 : 1.0);
        const boost = this.speed * (this.userSpeaking ? 1.4 + this.volume : 1.0);

        for (const p of this.particles) {
            p.angle += p.speed * boost;
            // 说话时轨道随音量轻微鼓胀
            const orbit = baseRadius * p.orbit * expansion + Math.sin(this.time * 0.02 + p.phase) * this.volume * 10;
            const x = cx + Math.cos(p.angle) * orbit;
            const y = cy + Math.sin(p.angle) * orbit * 0.92;
            const sprite = this.sprites[p.sprite];
            // 闪烁：正弦相位叠加能量亮度
            const twinkle = 0.6 + 0.4 * Math.sin(this.time * 0.05 + p.phase);
            ctx.globalAlpha = Math.min(1, p.alpha * twinkle * (0.7 + this.energy));
            ctx.drawImage(sprite, x - sprite.width / 2, y - sprite.height / 2);
        }
        ctx.globalAlpha = 1;
    }

    /** 波纹：双层描边圆环向外扩散并淡出 */
    drawRipples(color) {
        const { ctx, cx, cy } = this;
        for (let i = this.ripples.length - 1; i >= 0; i--) {
            const rp = this.ripples[i];
            if (rp.delay > 0) {
                rp.delay--;
                continue;
            }
            rp.radius += this.baseRadius * 0.014 * this.speed;
            rp.alpha -= 0.009 * this.speed;
            if (rp.alpha <= 0 || rp.radius > this.baseRadius * 1.6) {
                this.ripples.splice(i, 1);
                continue;
            }
            // 主环
            ctx.strokeStyle = color;
            ctx.globalAlpha = rp.alpha;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(cx, cy, rp.radius, 0, Math.PI * 2);
            ctx.stroke();
            // 伴生细环（滞后一点，层次感）
            ctx.globalAlpha = rp.alpha * 0.45;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.arc(cx, cy, rp.radius * 0.93, 0, Math.PI * 2);
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }

    // ================= 主循环 =================

    animate(now) {
        requestAnimationFrame((t) => this.animate(t));
        // 窗口不可见（最小化/遮挡）时浏览器自动暂停 rAF，CPU 归零；
        // 可见时再限帧到 30fps，避免透明窗口软件合成下满帧发热
        if (document.hidden) return;
        if (now - this._lastFrame < this.frameInterval) return;
        this._lastFrame = now;

        this.time++;
        const ctx = this.ctx;
        const color = this.getColor();

        // 速度系数平滑逼近目标：说话 2.6 倍速，待机 1 倍速
        const targetSpeed = this.aiSpeaking ? 2.6 : (this.userSpeaking ? 1.6 : 1.0);
        this.speed += (targetSpeed - this.speed) * 0.06;
        // 能量等级平滑逼近：说话时更亮更饱满
        const targetEnergy = this.aiSpeaking ? 0.85 : (this.userSpeaking ? 0.55 : 0.35);
        this.energy += (targetEnergy - this.energy) * 0.05;

        // 待机呼吸波纹：每 ~130 帧（随速度缩短）自动扩散一圈
        this.rippleTimer += this.speed;
        if (this.rippleTimer > 130) {
            this.rippleTimer = 0;
            this.triggerRipple(1);
        }

        // 透明底：直接清屏（不设背景色，桌面从窗口透出）
        ctx.clearRect(0, 0, this.width, this.height);

        this.drawAmbient(color);
        this.drawSweep(color);
        this.drawCore(color);
        this.drawCoils(color);
        this.drawArcs(color);
        this.drawTicks(color);
        this.drawParticles(color);
        this.drawRipples(color);
    }
}

window.ArcReactor = ArcReactor;
