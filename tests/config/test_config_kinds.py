"""S1：enum / alias_map / model_table 三种 kind 的解析与校验。

这一层的判据只有一条：**解析失败必须给出可照做的写法**，而不是只说"格式错误"——
调用方是模型，它要据此改写；说"错了"它只会换个猜法再试一次。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.config.config_specs import (
    ALIAS_MAP_MAX_ITEMS,
    CUSTOM_MODEL_FIELDS,
    ConfigSpec,
    parse_text_value,
    validate_value,
)

ENUM = ConfigSpec(
    "thinking_effort", "llm", "thinking_effort", "thinking_effort", "enum",
    "思考强度", "想得越久越贵", choices=("low", "medium", "high"),
)
ALIAS_MAP = ConfigSpec(
    "models", "llm.models", "models", "models", "alias_map",
    "模型别名表", "给模型起中文名",
)
MODEL_TABLE = ConfigSpec(
    "custom_models", "llm", "custom_models", "custom_models", "model_table",
    "自定义模型", "接一个不在内置清单里的模型",
)


class TestEnum:
    @pytest.mark.parametrize("raw,expect", [
        ("high", "high"),
        ("HIGH", "high"),
        ("  Medium  ", "medium"),
        ("最高", "high"),      # 别名
        ("最低档", "low"),
    ])
    def test_accepts_canonical_case_and_alias(self, raw, expect):
        assert parse_text_value(ENUM, raw) == expect

    def test_rejects_unknown_and_lists_choices(self):
        with pytest.raises(ValueError) as e:
            parse_text_value(ENUM, "very-high")
        msg = str(e.value)
        assert "low" in msg and "medium" in msg and "high" in msg
        assert "very-high" in msg

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            validate_value(ENUM, 3)


class TestAliasMap:
    def test_parses_pairs(self):
        assert parse_text_value(ALIAS_MAP, "a=甲;b=乙") == {"a": "甲", "b": "乙"}

    def test_empty_means_clear(self):
        assert parse_text_value(ALIAS_MAP, "") == {}
        assert parse_text_value(ALIAS_MAP, "   ") == {}

    def test_full_width_separators(self):
        assert parse_text_value(ALIAS_MAP, "a＝甲；b＝乙") == {"a": "甲", "b": "乙"}

    def test_duplicate_last_wins_and_reports(self):
        notes: list[str] = []
        got = parse_text_value(ALIAS_MAP, "a=甲;a=乙", notes)
        assert got == {"a": "乙"}
        assert any("重复" in n for n in notes)

    def test_missing_equals_gives_example(self):
        with pytest.raises(ValueError) as e:
            parse_text_value(ALIAS_MAP, "a甲")
        assert "名=展示名" in str(e.value)

    def test_empty_alias_rejected(self):
        with pytest.raises(ValueError):
            parse_text_value(ALIAS_MAP, "=甲")

    def test_too_many_items_rejected(self):
        raw = ";".join(f"m{i}=名{i}" for i in range(ALIAS_MAP_MAX_ITEMS + 1))
        with pytest.raises(ValueError) as e:
            parse_text_value(ALIAS_MAP, raw)
        assert str(ALIAS_MAP_MAX_ITEMS) in str(e.value)

    def test_structured_dict_also_validated(self):
        assert validate_value(ALIAS_MAP, {"a": "甲"}) == {"a": "甲"}
        with pytest.raises(ValueError):
            validate_value(ALIAS_MAP, ["a"])


class TestModelTable:
    def test_add(self):
        got = parse_text_value(MODEL_TABLE, "add glm-x provider_type=zai model_type=text")
        assert got == {"op": "add", "name": "glm-x",
                       "fields": {"provider_type": "zai", "model_type": "text"}}

    def test_remove(self):
        got = parse_text_value(MODEL_TABLE, "remove glm-x")
        assert got == {"op": "remove", "name": "glm-x", "fields": {}}

    def test_unknown_field_rejected_with_allowed_list(self):
        with pytest.raises(ValueError) as e:
            parse_text_value(MODEL_TABLE, "add x api_kye=abc")
        msg = str(e.value)
        assert "api_kye" in msg
        for field in CUSTOM_MODEL_FIELDS:
            assert field in msg

    def test_unknown_op_rejected(self):
        with pytest.raises(ValueError):
            parse_text_value(MODEL_TABLE, "update x name=x")

    def test_empty_gives_example(self):
        with pytest.raises(ValueError) as e:
            parse_text_value(MODEL_TABLE, "   ")
        assert "add" in str(e.value) and "remove" in str(e.value)

    def test_remove_with_fields_rejected(self):
        with pytest.raises(ValueError):
            parse_text_value(MODEL_TABLE, "remove x name=y")

    def test_field_without_equals_rejected(self):
        with pytest.raises(ValueError):
            parse_text_value(MODEL_TABLE, "add x provider_type")


class TestNoPartialWriteContract:
    """解析层只负责"能不能写"；不通过时调用方一个字节都不该动。"""

    def test_bad_value_raises_before_any_value_returned(self):
        for bad in ("", "very-high"):
            with pytest.raises(ValueError):
                parse_text_value(ENUM, bad)
