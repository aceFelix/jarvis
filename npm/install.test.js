/**
 * npm 安装器回归测试：锁住 uv 必须绑定已校验 Python 解释器这一不变量。
 *
 * 背景：PATH 中坏的 Chocolatey python3.14.exe 会诱导 uv 选中不存在的解释器，
 * 即使 install.js 已找到可用 Python 3.13。
 *
 * @author aceFelix
 * @since 2026-11-27
 */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  resolvePythonExecutable,
  buildUvInstallPrefix,
} = require("./install.js");

test("resolvePythonExecutable 查询已验证解释器的 sys.executable", () => {
  const fakeRun = (command) => {
    assert.match(command, /^python -c "import sys; print\(sys\.executable\)"$/);
    return "C:\\Program Files\\Python313\\python.exe\n";
  };

  assert.equal(
    resolvePythonExecutable("python", fakeRun),
    "C:\\Program Files\\Python313\\python.exe"
  );
});

test("resolvePythonExecutable 查询失败时退回原命令", () => {
  const fakeRun = () => {
    throw new Error("interpreter query failed");
  };

  assert.equal(resolvePythonExecutable("python", fakeRun), "python");
});

test("buildUvInstallPrefix 显式绑定解释器并给空格路径加引号", () => {
  assert.equal(
    buildUvInstallPrefix("C:\\Program Files\\Python313\\python.exe"),
    'uv pip install --python "C:\\Program Files\\Python313\\python.exe"'
  );
});
