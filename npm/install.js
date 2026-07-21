#!/usr/bin/env node

"use strict";

const { execSync } = require("child_process");
const path = require("path");

const PACKAGE = require("./package.json");
const VERSION = PACKAGE.version;
const PIP_PACKAGE = `jarvis-agent[all]==${VERSION}`;

// ─── Python 检测 ───────────────────────────────────────────────────────────────

const PYTHON_CANDIDATES =
  process.platform === "win32"
    ? ["python", "python3", "py"]
    : ["python3", "python"];

function findPython() {
  for (const cmd of PYTHON_CANDIDATES) {
    try {
      const out = execSync(`${cmd} --version`, {
        encoding: "utf8",
        timeout: 10000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      // "Python 3.13.0" or "Python 3.11.2"
      const match = out.match(/Python\s+(\d+)\.(\d+)\.(\d+)/i);
      if (match) {
        const major = parseInt(match[1], 10);
        const minor = parseInt(match[2], 10);
        if (major === 3 && minor >= 11) {
          return { cmd, version: `${major}.${minor}.${match[3]}` };
        }
      }
    } catch {
      // 该候选不可用，继续
    }
  }
  return null;
}

// ─── pip 检测 ──────────────────────────────────────────────────────────────────

function findPip(pythonCmd) {
  // 优先用 python -m pip（最可靠）
  try {
    execSync(`${pythonCmd} -m pip --version`, {
      encoding: "utf8",
      timeout: 10000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return `${pythonCmd} -m pip`;
  } catch {
    // fallback
  }

  const pipCandidates = process.platform === "win32" ? ["pip", "pip3"] : ["pip3", "pip"];
  for (const cmd of pipCandidates) {
    try {
      execSync(`${cmd} --version`, {
        encoding: "utf8",
        timeout: 10000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      return cmd;
    } catch {
      // 继续
    }
  }
  return null;
}

// ─── 已安装版本检测 ────────────────────────────────────────────────────────────

function getInstalledVersion(pythonCmd) {
  try {
    const out = execSync(`${pythonCmd} -m pip show jarvis-agent`, {
      encoding: "utf8",
      timeout: 15000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const match = out.match(/^Version:\s*(.+)$/m);
    return match ? match[1].trim() : null;
  } catch {
    return null;
  }
}

// ─── 主流程 ────────────────────────────────────────────────────────────────────

function main() {
  console.log(`\n[jarvis] Installing J.A.R.V.I.S v${VERSION} via pip...\n`);

  // 1. 检测 Python
  const python = findPython();
  if (!python) {
    console.error(
      "[jarvis] ❌ 未找到 Python 3.11+。\n" +
        "   J.A.R.V.I.S 是 Python 项目，需要先安装 Python。\n\n" +
        "   下载地址: https://www.python.org/downloads/\n" +
        "   安装时请勾选 \"Add Python to PATH\"。\n\n" +
        "   或使用 uv（更快的 Python 管理器）:\n" +
        "     https://docs.astral.sh/uv/\n"
    );
    process.exit(1);
  }
  console.log(`[jarvis] ✓ Python ${python.version} (${python.cmd})`);

  // 2. 检测 pip
  const pip = findPip(python.cmd);
  if (!pip) {
    console.error(
      "[jarvis] ❌ 未找到 pip。\n" +
        `   请运行: ${python.cmd} -m ensurepip --upgrade\n`
    );
    process.exit(1);
  }
  console.log(`[jarvis] ✓ pip 可用 (${pip})`);

  // 3. 检查是否已安装正确版本
  const installed = getInstalledVersion(python.cmd);
  if (installed === VERSION) {
    console.log(`[jarvis] ✓ jarvis-agent v${VERSION} 已安装，跳过。`);
    return;
  }
  if (installed) {
    console.log(`[jarvis] 检测到已安装 v${installed}，升级到 v${VERSION}...`);
  }

  // 4. 执行安装
  const installCmd = `${pip} install "${PIP_PACKAGE}"`;
  console.log(`[jarvis] 执行: ${installCmd}\n`);

  try {
    execSync(installCmd, {
      stdio: "inherit",
      timeout: 300000, // 5 分钟超时（依赖较多）
    });
  } catch (err) {
    console.error(
      `\n[jarvis] ❌ pip install 失败。\n` +
        `   你可以手动安装:\n` +
        `     ${pip} install "jarvis-agent[all]"\n\n` +
        `   仅安装核心功能（不含语音/GUI/浏览器）:\n` +
        `     ${pip} install jarvis-agent\n`
    );
    process.exit(1);
  }

  console.log(`\n[jarvis] ✅ 安装成功！运行 "jarvis" 启动贾维斯。\n`);
}

main();
