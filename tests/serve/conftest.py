"""tests/serve 共用夹具 —— 宿主装配件（Scheduler / 感知总线）与配置落盘的隔离。

serve 宿主（ProactiveHub）会在测试里真的启动 Scheduler 与感知总线，两者的
默认持久化路径都在 `~/.jarvis`：不隔离就会把测试任务与感知事件写进用户真实
目录（与 tests/policy/conftest.py 当初要解决的是同一类问题）。

`settings.get/set` 与 `percept.config.set` 还会改用户级 settings.toml，故一并
重定向 `toml_writer.settings_path`（三个配置域共用这一个真源）。

@author aceFelix
"""

from __future__ import annotations

import pytest

import agent.config.toml_writer as toml_writer_mod
import agent.core.daemon.deadline as deadline_mod
import agent.core.daemon.scheduler as scheduler_mod
import agent.core.percept.source_file as source_file_mod
import agent.core.percept.source_mail as source_mail_mod


@pytest.fixture(autouse=True)
def isolate_persistence(tmp_path, monkeypatch):
    """调度/截止日期/感知状态/用户级 settings.toml 全部重定向到 tmp_path。

    与 tests/daemon/test_scheduler.py 同一手法：monkeypatch 模块级路径函数或
    常量，不污染真实 ~/.jarvis。感知源的默认状态路径是模块常量，构造实例时
    才读取，因此替换常量即可影响后续 factory 造出的所有源。
    """
    monkeypatch.setattr(
        scheduler_mod, "_schedule_file", lambda: tmp_path / "schedule.json"
    )
    monkeypatch.setattr(
        deadline_mod, "_deadlines_file", lambda: tmp_path / "deadlines.json"
    )
    percept_dir = tmp_path / "percept"
    monkeypatch.setattr(source_file_mod, "_STATE_FILE", percept_dir / "file_state.json")
    monkeypatch.setattr(source_mail_mod, "_STATE_FILE", percept_dir / "mail_seen.json")
    # 配置落盘真源（desktop_settings / email_settings / percept_settings 都走它）
    monkeypatch.setattr(
        toml_writer_mod, "settings_path", lambda: tmp_path / "settings.toml"
    )
