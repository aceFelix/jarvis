"""Skill 技能管理模块单元测试。

覆盖 agent/core/extensions/skills.py:
- _parse_frontmatter: YAML frontmatter 极简解析
- _load_one_skill / _scan_skills_dir: 单技能加载与目录扫描
- load_skills: 用户级 + 项目级合并（项目级覆盖同名）
- skills_to_prompt / skills_section: prompt 拼接
- list_skill_files: 来源目录查询

说明:
- 通过 monkeypatch Path.home() 将用户级技能目录重定向到临时目录，
  避免污染真实 ~/.jarvis/skills。
- 不修改被测源码。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

from agent.core.extensions import skills as sk

SKILL_MD = """---
name: git-helper
description: Git 版本控制专家
when_to_use: 当用户需要 git 操作时
trigger_words: git, 提交, 分支
---
# Git 助手
你现在是 Git 专家，遵循以下规范。
"""


class TestFrontmatter:
    """frontmatter 解析测试。"""

    def test_parse_normal(self):
        """标准 frontmatter 解析出字段与正文。"""
        fm, body = sk._parse_frontmatter(SKILL_MD)
        assert fm["name"] == "git-helper"
        assert fm["description"] == "Git 版本控制专家"
        assert fm["when_to_use"] == "当用户需要 git 操作时"
        assert fm["trigger_words"] == "git, 提交, 分支"
        assert "# Git 助手" in body
        assert "你现在是 Git 专家" in body

    def test_parse_no_frontmatter(self):
        """无 frontmatter 时返回空字典与原文。"""
        fm, body = sk._parse_frontmatter("plain text")
        assert fm == {}
        assert body == "plain text"

    def test_parse_skips_comments_and_non_kv(self):
        """跳过注释行与无冒号行。"""
        text = (
            "---\n"
            "# 这是注释\n"
            "name: x\n"
            "\n"
            "no-colon-line\n"
            "key: value\n"
            "---\n"
            "body content"
        )
        fm, body = sk._parse_frontmatter(text)
        assert fm == {"name": "x", "key": "value"}
        assert body == "body content"

    def test_parse_crlf(self):
        """兼容 CRLF 换行。"""
        text = "---\r\nname: a\r\ndescription: b\r\n---\r\n正文"
        fm, body = sk._parse_frontmatter(text)
        assert fm["name"] == "a"
        assert "正文" in body


class TestLoadOne:
    """单技能加载测试。"""

    def test_load_normal(self, tmp_path):
        """正常加载：字段解析 + source/path 记录。"""
        d = tmp_path / "git-helper"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        skill = sk._load_one_skill(d, "user")
        assert skill is not None
        assert skill.name == "git-helper"
        assert skill.trigger_words == ["git", "提交", "分支"]
        assert skill.content.startswith("# Git 助手")
        assert skill.source == "user"
        assert skill.path == str(d / "SKILL.md")

    def test_load_missing_skill_md(self, tmp_path):
        """目录下没有 SKILL.md → None。"""
        d = tmp_path / "empty"
        d.mkdir()
        assert sk._load_one_skill(d, "user") is None

    def test_load_read_error(self, tmp_path):
        """SKILL.md 是目录（读取异常）→ None。"""
        d = tmp_path / "weird"
        (d / "SKILL.md").mkdir(parents=True)
        assert sk._load_one_skill(d, "user") is None

    def test_load_default_name_from_dir(self, tmp_path):
        """frontmatter 无 name 时回退用目录名。"""
        d = tmp_path / "unnamed"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# 只有正文", encoding="utf-8")
        skill = sk._load_one_skill(d, "project")
        assert skill.name == "unnamed"
        assert skill.source == "project"

    def test_scan_skills_dir(self, tmp_path):
        """目录扫描：跳过非目录与无 SKILL.md 的目录。"""
        base = tmp_path / "base"
        (base / "a").mkdir(parents=True)
        (base / "a" / "SKILL.md").write_text("---\nname: a\n---\nA", encoding="utf-8")
        (base / "b").mkdir()          # 无 SKILL.md
        (base / "file.txt").write_text("x", encoding="utf-8")  # 非目录
        skills = sk._scan_skills_dir(base, "user")
        assert len(skills) == 1
        assert skills[0].name == "a"

    def test_scan_missing_dir(self, tmp_path):
        """目录不存在返回空列表。"""
        assert sk._scan_skills_dir(tmp_path / "missing", "user") == []


class TestLoadSkills:
    """load_skills 合并逻辑测试。"""

    def test_project_overrides_user(self, tmp_path, monkeypatch):
        """项目级同名技能覆盖用户级。"""
        home = tmp_path
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        user_dir = home / ".jarvis" / "skills"
        proj_dir = tmp_path / "proj" / ".jarvis" / "skills"
        # 用户级
        (user_dir / "common").mkdir(parents=True)
        (user_dir / "common" / "SKILL.md").write_text(
            "---\nname: common\ndescription: 用户版\n---\nU", encoding="utf-8")
        (user_dir / "only-user").mkdir(parents=True)
        (user_dir / "only-user" / "SKILL.md").write_text(
            "---\nname: only-user\n---\nU", encoding="utf-8")
        # 项目级（同名 common）
        (proj_dir / "common").mkdir(parents=True)
        (proj_dir / "common" / "SKILL.md").write_text(
            "---\nname: common\ndescription: 项目版\n---\nP", encoding="utf-8")
        skills = sk.load_skills(str(tmp_path / "proj"))
        by_name = {s.name: s for s in skills}
        assert set(by_name) == {"common", "only-user"}
        assert by_name["common"].description == "项目版"
        assert by_name["common"].source == "project"
        assert by_name["only-user"].source == "user"

    def test_load_skills_empty(self, tmp_path, monkeypatch):
        """无任何技能时返回空列表。"""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "emptyhome"))
        assert sk.load_skills(str(tmp_path / "noproj")) == []


class TestPrompt:
    """skills_to_prompt / skills_section / match_skills_for_message 测试。"""

    def test_prompt_empty(self):
        """空技能列表返回空串。"""
        assert sk.skills_to_prompt([]) == ""

    def test_prompt_summary_only(self):
        """摘要模式：只输出名字+描述+时机，不输出正文。"""
        skill = sk.Skill(
            name="s1", description="描述1", when_to_use="时机1",
            trigger_words=["a", "b"], content="正文内容",
        )
        out = sk.skills_to_prompt([skill])
        assert "# 可用技能（Skills）" in out
        assert "s1" in out
        assert "描述1" in out
        assert "时机1" in out
        # 正文不应出现在 system prompt 摘要里
        assert "正文内容" not in out

    def test_prompt_partial_fields(self):
        """字段缺失时不输出对应内容。"""
        skill = sk.Skill(name="s2", content="x")
        out = sk.skills_to_prompt([skill])
        assert "s2" in out
        # 正文不注入摘要
        assert "x" not in out

    def test_skills_section(self, tmp_path, monkeypatch):
        """skills_section 集成：加载并拼接摘要。"""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        (home / ".jarvis" / "skills" / "x").mkdir(parents=True)
        (home / ".jarvis" / "skills" / "x" / "SKILL.md").write_text(
            "---\nname: x\ndescription: X技能\ntrigger_words: git, 提交\n---\n正文内容",
            encoding="utf-8")
        out = sk.skills_section(str(tmp_path / "proj"))
        assert "# 可用技能（Skills）" in out
        assert "X技能" in out
        # 正文不应出现在摘要里
        assert "正文内容" not in out
        # 无技能环境 → 空串
        empty_home = tmp_path / "empty-home"
        empty_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
        assert sk.skills_section(str(tmp_path / "proj")) == ""

    def test_match_skills_hit(self, tmp_path, monkeypatch):
        """触发词匹配命中时返回完整正文。"""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        (home / ".jarvis" / "skills" / "git-helper").mkdir(parents=True)
        (home / ".jarvis" / "skills" / "git-helper" / "SKILL.md").write_text(
            "---\nname: git-helper\ndescription: Git专家\ntrigger_words: git, 提交, 分支\n---\nGit 专业指令正文",
            encoding="utf-8")
        # 匹配触发词 "git"
        out = sk.match_skills_for_message("帮我 git 提交一下", str(tmp_path / "proj"))
        assert "git-helper" in out
        assert "Git 专业指令正文" in out  # 正文在匹配时加载

    def test_match_skills_no_hit(self, tmp_path, monkeypatch):
        """触发词不匹配时返回空串。"""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        (home / ".jarvis" / "skills" / "git-helper").mkdir(parents=True)
        (home / ".jarvis" / "skills" / "git-helper" / "SKILL.md").write_text(
            "---\nname: git-helper\ndescription: Git专家\ntrigger_words: git, 提交\n---\n正文",
            encoding="utf-8")
        # 不匹配任何触发词
        assert sk.match_skills_for_message("今天天气怎么样", str(tmp_path / "proj")) == ""

    def test_match_skills_no_skills(self, tmp_path, monkeypatch):
        """无 skill 时返回空串。"""
        empty_home = tmp_path / "empty-home"
        empty_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
        assert sk.match_skills_for_message("git 提交", str(tmp_path / "proj")) == ""


class TestListFiles:
    """list_skill_files 测试。"""

    def test_list_skill_files(self, tmp_path, monkeypatch):
        """返回用户级与项目级技能目录。"""
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        files = sk.list_skill_files(str(tmp_path / "proj"))
        assert files["user"] == home / ".jarvis" / "skills"
        assert files["project"] == tmp_path / "proj" / ".jarvis" / "skills"
