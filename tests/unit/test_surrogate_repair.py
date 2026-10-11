"""代理对回归：pypdf 抽取 >BMP 字符产出 UTF-16 代理对，进 JSON 写盘必崩。"""

from __future__ import annotations

import json

from src.core.text import repair_surrogates
from src.ingest.mathtype_decode import MSBM_MAP


def test_repair_surrogates_merges_pairs():
    # pypdf 给 \ud835\udd3d（MATHEMATICAL DOUBLE-STRUCK CAPITAL F 的代理对）
    raw = chr(0xD835) + chr(0xDD3D)
    assert repair_surrogates("域 " + raw + " 称为有序域") == "域 \U0001D53D 称为有序域"
    # 正常文本原样
    assert repair_surrogates("abc 中文 \u2264") == "abc 中文 \u2264"
    # 孤儿代理丢弃（不崩）
    assert repair_surrogates("a" + chr(0xD800) + "b") == "ab"
    assert repair_surrogates("a" + chr(0xDDDD) + "b") == "ab"
    assert repair_surrogates("") == ""


def test_repaired_text_survives_json_write(tmp_path):
    raw = chr(0xD835) + chr(0xDDDD)
    merged = chr(0x10000 + ((0xD835 - 0xD800) << 10) + (0xDDDD - 0xDC00))
    t = repair_surrogates("有序域 " + raw)
    payload = json.dumps({"text": t}, ensure_ascii=False)
    (tmp_path / "x.json").write_text(payload, encoding="utf-8")   # 不得抛 UnicodeEncodeError
    assert json.loads(payload)["text"] == "有序域 " + merged


def test_msbm_table_has_no_surrogate_literals():
    """表值是代理对字面量时解码输出进 JSON 必崩——全部必须是合法码点。"""
    for name, val in MSBM_MAP.items():
        assert len(val) == 1, name
        assert not (0xD800 <= ord(val) <= 0xDFFF), name
        val.encode("utf-8")   # 不得抛
