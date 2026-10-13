"""学科模板与自定义块类型回归：注册表 → 生成 schema → 校验器 → API 同一事实源。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.core.block_types import install_types, type_enum, type_guide
from src.core.errors import SchemaViolation
from src.core.block_types import validate_type_constraints
from src.generate.prompts import build_outline_schema


@pytest.fixture(autouse=True)
def _clean_types():
    install_types([])
    yield
    install_types([])


def test_subject_presets_resolve():
    assert "law" in type_enum("physics") and "phenomenon" in type_enum("physics")
    assert "algorithm" in type_enum("cs")
    assert "symptom" in type_enum("medicine")
    assert "reaction" in type_enum("chemistry")
    assert type_enum("textbook") == ["definition", "theorem", "motivation", "figure", "link"]


def test_custom_type_feeds_schema_and_validator():
    bad = install_types([{"id": "case", "name": "病例", "icon": "🏥",
                          "hint": "病例：主诉/现病史/处置",
                          "source_policy": "required", "profiles": ["medicine"]}])
    assert bad == []
    assert "case" in type_enum("medicine")
    assert "case" not in type_enum("physics"), "profiles 限定生效"
    assert "病例" in type_guide("medicine")
    assert "case" in build_outline_schema("medicine")["properties"]["blocks"]["items"]["properties"]["type"]["enum"]
    # 校验器走同一注册表：自定义类型的来源策略必须被强制
    with pytest.raises(SchemaViolation):
        validate_type_constraints("case", basis="synthesis", source_count=0)
    install_types([{"id": "x y", "name": "非法 id"}])
    # 非法 id 不注入


def test_custom_type_override_preset_hint():
    install_types([{"id": "theorem", "name": "定理", "hint": "自定义的定理写作指引",
                    "source_policy": "required"}])
    assert "自定义的定理写作指引" in type_guide("textbook"), "同 id 覆盖预设生效"


def test_block_types_api_and_settings_roundtrip(workspace):
    app = create_app(workspace.root)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        headers = {"X-Local-Token": app.state.app_state.token_value}
        r = client.put("/api/v1/settings", json={"generate": {
            "profile": "medicine",
            "block_types": [{"id": "case", "name": "病例", "icon": "🏥",
                             "hint": "病例：主诉/现病史/处置", "source_policy": "required"}]}},
            headers=headers)
        assert r.status_code == 200, r.text
        r = client.get("/api/v1/block-types", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["profile"] == "medicine"
        ids = [t["id"] for t in body["types"]]
        assert "case" in ids and "symptom" in ids, ids
        assert "physics" in body["presets"]
