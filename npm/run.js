#!/usr/bin/env node

"use strict";

const { execSync, spawnSync } = require("child_process");
const path = require("path");

const PACKAGE = require("./package.json");
const VERSION = PACKAGE.version;

// ─── 检测 jarvis 是否已安装 ────────────────────────────────────────────────────

function findJarvisCommand() {
  // Windows: jarvis.exe 在 Scripts 目录，通常已在 PATH
  // macOS/Linux: jarvis 在 bin 目录
  const candidates =
    process.platform === "win32"
      ? ["jarvis.exe", "jarvis"]
      : ["jarvis"];

  for (const cmd of candidates) {
    try {
      const result = spawnSync(cmd, ["--version"], {
        encoding: "utf8",
        timeout: 10000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      if (result.status === 0 || (result.stdout && result.stdout.length > 0)) {
        return cmd;
      }
    } catch {
      // 继续
    }
  }

  // fallback: 尝试 python -m agent.main --version
  const pythonCmds =
    process.platform === "win32"
      ? ["python", "python3", "py"]
      : ["python3", "python"];

  for (const py of pythonCmds) {
    try {
      const result = spawnSync(py, ["-m", "agent.main", "--version"], {
        encoding: "utf8",
        timeout: 10000,
        stdio: ["pipe", "pipe", "pipe"],
      });
      if (result.status === 0) {
        return `${py} -m agent.main`;
      }
    } catch {
      // 继续
    }
  }

  return null;
}

function needsInstall() {
  const cmd = findJarvisCommand();
  if (!cmd) return true;

  // 检查版本是否匹配
  try {
    const result = spawnSync(cmd.split(" ")[0], cmd.includes("-m") ? ["-m", "agent.main", "--version"] : ["--version"], {
      encoding: "utf8",
      timeout: 10000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const output = (result.stdout || "") + (result.stderr || "");
    if (output.includes(VERSION)) return false;
    // 版本不匹配但不强制重装（用户可能手动装了更新版本）
    return false;
  } catch {
    return false;
  }
}

// ─── 主流程 ────────────────────────────────────────────────────────────────────

// 如果 jarvis 未安装，先触发安装
if (needsInstall()) {
  console.log(`[jarvis] 未检测到 jarvis-agent，正在安装 v${VERSION}...\n`);
  try {
    execSync(`node ${JSON.stringify(path.join(__dirname, "install.js"))}`, {
      stdio: "inherit",
      cwd: __dirname,
    });
  } catch {
    console.error(
      "[jarvis] 自动安装失败。请手动安装:\n" +
        '  pip install "jarvis-agent[all]"\n'
    );
    process.exit(1);
  }
}

// 找到 jarvis 命令并转发所有参数
const jarvisCmd = findJarvisCommand();
if (!jarvisCmd) {
  console.error(
    "[jarvis] ❌ 找不到 jarvis 命令。\n" +
      "   请确认 Python 已安装且 jarvis-agent 在 PATH 中。\n" +
      '   手动安装: pip install "jarvis-agent[all]"\n'
  );
  process.exit(1);
}

// 转发参数给真正的 jarvis
const args = process.argv.slice(2);
const parts = jarvisCmd.split(" ");
const bin = parts[0];
const binArgs = parts.slice(1).concat(args);

const result = spawnSync(bin, binArgs, {
  stdio: "inherit",
  // Windows 上 jarvis 是 .exe，需要 shell 来找到它
  shell: process.platform === "win32",
});

process.exit(result.status !== null ? result.status : 1);
