#!/usr/bin/env node
/**
 * J.A.R.V.I.S npm 包 postinstall 脚本。
 *
 * 职责：
 *  - 检测 Python 3.11+ 运行环境
 *  - 检测 uv / pip 安装工具（uv 优先，加速 5-10x）
 *  - 提供交互式功能选装菜单（非 TTY 环境自动装 [all]）
 *  - 调用 pip / uv pip install 安装 jarvis-agent Python 包
 *  - 安装成功后根据所选功能提示对应的系统级依赖
 *
 * @author aceFelix
 */

"use strict";

const { execSync } = require("child_process");
const readline = require("readline");

const PACKAGE = require("./package.json");
const VERSION = PACKAGE.version;

// ─── 功能选装菜单定义 ─────────────────────────────────────────────────────────
// 每项对应菜单里一个可勾选功能；extras 是 pip install jarvis-agent[extras] 的可选依赖名；
// hint 用于安装成功后展示该功能所需的系统级依赖（null 表示无需额外系统依赖提示）。
const FEATURES = [
  {
    name: "语音对话（TTS/STT，需要 PortAudio）",
    extras: ["voice"],
    hint: {
      title: "语音：需要 PortAudio",
      win: "Windows: 通常已内置",
      mac: "macOS: brew install portaudio",
      linux: "Linux: sudo apt install portaudio19-dev",
    },
  },
  {
    name: "系统托盘（常驻后台/主动提醒，需要 psutil）",
    extras: ["daemon"],
    hint: null,
  },
  {
    name: "GUI 视觉操作（鼠标键盘控制/截屏）",
    extras: ["gui"],
    hint: null,
  },
  {
    name: "浏览器自动化（需要 playwright install）",
    extras: ["browser"],
    hint: {
      title: "浏览器：需要下载浏览器二进制",
      extra: "运行: playwright install",
    },
  },
  {
    name: "摄像头/OCR（OpenCV + PaddleOCR）",
    // vision 包含 mediapipe + paddleocr，体积较大；菜单合并展示，安装时同时带上 camera 与 vision
    extras: ["camera", "vision"],
    hint: {
      title: "视觉监控：需要摄像头驱动",
      win: "Windows: 通常已内置",
      mac: "macOS: 需要摄像头权限",
    },
  },
  {
    name: "MCP 生态接入",
    extras: ["mcp"],
    hint: null,
  },
  {
    name: "微信接入",
    extras: ["wechat"],
    hint: null,
  },
  {
    name: "实时聊天窗口（独立语音对话窗口）",
    extras: ["realtime_ui"],
    hint: null,
  },
];

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

// ─── uv 检测 ──────────────────────────────────────────────────────────────────
// 检测系统是否安装 uv；若可用则用 uv pip install 加速安装（快 5-10x）。
function findUv() {
  try {
    execSync("uv --version", {
      encoding: "utf8",
      timeout: 10000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    return true;
  } catch {
    return false;
  }
}

// ─── 交互式选装菜单 ─────────────────────────────────────────────────────────────
// 返回选中的 extras 数组；特殊值 "all" 表示安装 [all]；空数组表示仅装核心。
// 非 TTY 环境（CI / 管道）跳过交互，直接返回 ["all"]。
function promptFeatures() {
  if (!process.stdin.isTTY) {
    console.log("[jarvis] 非 TTY 环境，跳过选装菜单，安装全部功能 [all]。");
    return Promise.resolve(["all"]);
  }

  console.log("\nJ.A.R.V.I.S 功能选装菜单：\n");
  console.log("  [x] 核心功能（对话/工具/模型切换）—— 必装");
  FEATURES.forEach((f, i) => {
    console.log(`  [ ] ${i + 1}. ${f.name}`);
  });
  console.log(
    "\n  回车 = 全装[all] | 输入序号用逗号分隔（如 1,3,5）| 0 = 仅核心\n"
  );

  return new Promise((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    rl.question("请选择: ", (answer) => {
      rl.close();
      const trimmed = (answer || "").trim();

      // 回车 → 全装 [all]
      if (trimmed === "") {
        resolve(["all"]);
        return;
      }
      // 0 → 仅核心（无 extras）
      if (trimmed === "0") {
        resolve([]);
        return;
      }
      // 解析逗号/空格分隔的序号，仅保留合法范围内的整数
      const nums = trimmed
        .split(/[,\s]+/)
        .map((s) => s.trim())
        .filter(Boolean)
        .map((s) => parseInt(s, 10))
        .filter((n) => Number.isInteger(n) && n >= 1 && n <= FEATURES.length);

      // 去重后收集对应 extras
      const uniqueNums = [...new Set(nums)];
      const extrasSet = new Set();
      uniqueNums.forEach((idx) => {
        FEATURES[idx - 1].extras.forEach((e) => extrasSet.add(e));
      });

      // 没有有效输入时退回 [all]，避免漏装
      if (extrasSet.size === 0) {
        console.log("[jarvis] 未识别到有效序号，默认安装 [all]。");
        resolve(["all"]);
        return;
      }
      resolve([...extrasSet]);
    });
  });
}

// ─── 系统级依赖提示 ───────────────────────────────────────────────────────────
// 根据用户选装的功能，打印对应的系统级依赖提示；未选装的功能不提示。
function showSystemDeps(selectedFeatureObjs) {
  const hints = selectedFeatureObjs.filter((f) => f && f.hint).map((f) => f.hint);
  if (hints.length === 0) {
    return;
  }
  console.log("\n📋 系统级依赖检查：");
  hints.forEach((h) => {
    console.log(`  ✓ ${h.title}`);
    if (h.win) console.log(`    - ${h.win}`);
    if (h.mac) console.log(`    - ${h.mac}`);
    if (h.linux) console.log(`    - ${h.linux}`);
    if (h.extra) console.log(`    - ${h.extra}`);
  });
}

// ─── 主流程 ────────────────────────────────────────────────────────────────────

async function main() {
  console.log(`\n[jarvis] Installing J.A.R.V.I.S v${VERSION} via pip...\n`);

  // 1. 检测 Python 3.11+
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

  // 2. 已安装版本检查（提前退出，避免无谓交互）
  const installed = getInstalledVersion(python.cmd);
  if (installed === VERSION) {
    console.log(`[jarvis] ✓ jarvis-agent v${VERSION} 已安装，跳过。`);
    return;
  }
  if (installed) {
    console.log(`[jarvis] 检测到已安装 v${installed}，升级到 v${VERSION}...`);
  }

  // 3. 交互式选装（改进 A）
  const selectedExtras = await promptFeatures();

  // 4. 根据 extras 构造 pip install 目标 & 记录已选 feature 对象（用于系统依赖提示）
  let target;
  let selectedFeatureObjs;
  if (selectedExtras.length === 0) {
    // 0 = 仅核心
    target = `jarvis-agent==${VERSION}`;
    selectedFeatureObjs = [];
  } else if (selectedExtras.includes("all")) {
    // 回车 = 全装 [all]
    target = `jarvis-agent[all]==${VERSION}`;
    selectedFeatureObjs = FEATURES;
  } else {
    target = `jarvis-agent[${selectedExtras.join(",")}]==${VERSION}`;
    // 通过 extras 反查用户实际选中的 feature 对象
    selectedFeatureObjs = FEATURES.filter((f) =>
      f.extras.some((e) => selectedExtras.includes(e))
    );
  }

  // 5. 检测 uv（改进 C）→ 不可用再退回 pip
  let installPrefix;
  const hasUv = findUv();
  if (hasUv) {
    console.log("[jarvis] ✓ 检测到 uv，使用 uv 加速安装（快 5-10x）");
    installPrefix = "uv pip install";
  } else {
    const pip = findPip(python.cmd);
    if (!pip) {
      console.error(
        "[jarvis] ❌ 未找到 pip。\n" +
          `   请运行: ${python.cmd} -m ensurepip --upgrade\n` +
          "   或安装 uv（推荐）: https://docs.astral.sh/uv/\n"
      );
      process.exit(1);
    }
    console.log(`[jarvis] ✓ pip 可用 (${pip})`);
    installPrefix = `${pip} install`;
  }

  // 6. 执行安装
  const installCmd = `${installPrefix} "${target}"`;
  console.log(`[jarvis] 执行: ${installCmd}\n`);

  try {
    execSync(installCmd, {
      stdio: "inherit",
      timeout: 300000, // 5 分钟超时（依赖较多）
    });
  } catch (err) {
    console.error(
      `\n[jarvis] ❌ 安装失败。\n` +
        `   你可以手动安装:\n` +
        `     ${installPrefix} "${target}"\n\n` +
        `   仅安装核心功能（不含语音/GUI/浏览器）:\n` +
        `     ${installPrefix} "jarvis-agent==${VERSION}"\n`
    );
    process.exit(1);
  }

  // 7. 成功提示 + 系统级依赖提示（改进 D）
  console.log(`\n✅ Python 依赖安装成功！`);
  showSystemDeps(selectedFeatureObjs);
  console.log(`\n[jarvis] 运行 "jarvis" 启动贾维斯。\n`);
}

main();
