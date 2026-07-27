# jarvis-agent (npm wrapper)

这是 **J.A.R.V.I.S** 的 npm 安装入口。它本身不包含 Python 代码，
而是在 `postinstall` 阶段自动通过 pip 安装 `jarvis-agent` Python 包，
并把 `jarvis` 命令暴露给 npm/npx 用户。

## 安装要求

- Node.js 14+
- Python 3.11+（Jarvis 核心为 Python 项目）
- pip（随 Python 一起安装）

## 安装

```bash
npm install -g jarvis-agent
```

安装完成后即可运行：

```bash
jarvis
```

## 工作原理

1. `npm install jarvis-agent` 触发 `postinstall` 脚本 `install.js`
2. `install.js` 检测系统中的 Python 和 pip
3. 通过 pip 安装 `jarvis-agent[all]`
4. `run.js` 作为 `jarvis` 命令入口，转发参数给 Python 端的 `jarvis`

## 本地开发

```bash
cd jarvis/npm
node install.js   # 手动触发 pip 安装
node run.js       # 启动 jarvis
```

## 许可证

MIT © aceFelix
