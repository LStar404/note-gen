"""块类型注册表（9.5.3 扩展点）：类型 → 图标/中文名/导出模板/生成约束，全项目查表驱动。

域 profile（5.8）经本注册表注入激活类型集：textbook=五类；paper=五类+contribution/method/experiment。
新增块类型只需改：① 本注册表（含规范 4.4.4 约束表）；② 生成 JSON Schema（由本表动态生成）；③ 导出模板片段。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import SchemaViolation


@dataclass(frozen=True)
class BlockTypeSpec:
    type_id: str
    icon: str
    cn_name: str
    tex_env: str | None          # tex 导出环境名；None 表示降级为无编号段落
    template: str                # 导出模板片段名（src/export/templates/）
    source_policy: str           # required | optional | none
    allowed_relations: tuple[str, ...] = field(default_factory=tuple)
    synthesis_allowed: bool = False
    profiles: tuple[str, ...] = ("textbook", "paper")


SOURCE_RELATIONS = ("defines", "states", "proves", "motivates", "illustrates", "summarizes")

RELATION_CN = {
    "defines": "定义",
    "states": "陈述",
    "proves": "证明",
    "motivates": "动机",
    "illustrates": "图示",
    "summarizes": "归纳",
}

REGISTRY: dict[str, BlockTypeSpec] = {
    "definition": BlockTypeSpec(
        "definition", "📌", "定义", "definition", "md_block.j2",
        "required", ("defines",), False, ("textbook", "paper")),
    "theorem": BlockTypeSpec(
        "theorem", "📘", "定理", "theorem", "md_block.j2",
        "required", ("states", "proves"), False, ("textbook", "paper")),
    "motivation": BlockTypeSpec(
        "motivation", "💡", "动机", None, "md_block.j2",
        "optional", ("motivates",), True, ("textbook", "paper")),
    "figure": BlockTypeSpec(
        "figure", "🖼", "图示", "figure", "md_block.j2",
        "required", ("illustrates",), False, ("textbook", "paper")),
    "link": BlockTypeSpec(
        "link", "🔗", "延伸链接", None, "md_block.j2",
        "none", (), True, ("textbook", "paper")),
    # paper profile 扩展（5.8.3）
    "contribution": BlockTypeSpec(
        "contribution", "🎯", "贡献", "contribution", "md_block.j2",
        "required", ("summarizes",), False, ("paper",)),
    "method": BlockTypeSpec(
        "method", "🧭", "方法", "method", "md_block.j2",
        "required", ("defines", "states"), False, ("paper",)),
    "experiment": BlockTypeSpec(
        "experiment", "📊", "实验", "experiment", "md_block.j2",
        "required", ("illustrates", "summarizes"), False, ("paper",)),
}


def get_type(type_id: str) -> BlockTypeSpec:
    spec = REGISTRY.get(type_id)
    if spec is None:
        raise SchemaViolation(f"未知块类型: {type_id}", code="UNKNOWN_BLOCK_TYPE")
    return spec


def active_types(profile: str = "textbook") -> list[BlockTypeSpec]:
    return [s for s in REGISTRY.values() if profile in s.profiles]


def type_enum(profile: str = "textbook") -> list[str]:
    return [s.type_id for s in active_types(profile)]


def validate_type_constraints(type_id: str, *, basis: str, source_count: int) -> None:
    """规范 4.4.4 类型来源约束表（生成 prompt 与校验器共用）。"""
    spec = get_type(type_id)
    if spec.source_policy == "required" and source_count < 1:
        raise SchemaViolation(f"{type_id} 必须至少有一个来源锚点", code="SOURCE_REQUIRED")
    if spec.source_policy == "none" and source_count > 0:
        raise SchemaViolation(f"{type_id} 不要求来源，收到 {source_count} 个", code="SOURCE_NOT_ALLOWED")
    if basis == "synthesis" and not spec.synthesis_allowed:
        raise SchemaViolation(f"{type_id} 不允许 basis=synthesis", code="SYNTHESIS_NOT_ALLOWED")
    if basis == "anchored" and source_count < 1:
        raise SchemaViolation("basis=anchored 时 sources 必须非空", code="SOURCE_REQUIRED")


def outline_type_schema(profile: str = "textbook") -> dict:
    """生成 JSON Schema 的 type 枚举由注册表动态生成（附录 A 使用注 2）。"""
    return {"enum": type_enum(profile)}
