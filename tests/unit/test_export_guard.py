"""导出守护回归：手改过的导出文件不得被静默覆盖（Anki CSV/apkg 此前绕过守护）。"""

from __future__ import annotations

from src.core.store import DocStore
from src.export.anki_export import _export_csv, export_anki
from src.export.md_export import _safe_write, _safe_write_bytes


CARDS = [{"front": "定义", "back_text": "正文", "tags": ["数学"], "pages": [12]}]


def test_safe_write_keeps_user_edited_file(tmp_path):
    out = tmp_path / "out"
    p1 = _safe_write(out, "a.md", "第一版")
    assert p1.read_text(encoding="utf-8") == "第一版"
    p1.write_text("用户手改", encoding="utf-8")
    p2 = _safe_write(out, "a.md", "第二版")
    assert p1.read_text(encoding="utf-8") == "用户手改", "人工改动不得被覆盖"
    assert p2.name == "a.exported.md" and p2.read_text(encoding="utf-8") == "第二版"
    # 未改动时仍覆盖同一路径（幂等导出）
    p3 = _safe_write(out, "b.md", "x")
    p4 = _safe_write(out, "b.md", "y")
    assert p3 == p4 and p4.read_text(encoding="utf-8") == "y"


def test_safe_write_bytes_guard(tmp_path):
    out = tmp_path / "out"
    p1 = _safe_write_bytes(out, "d.apkg", b"PK\x03\x04old")
    p1.write_bytes(b"user")
    p2 = _safe_write_bytes(out, "d.apkg", b"PK\x03\x04new")
    assert p1.read_bytes() == b"user"
    assert p2.name == "d.exported.apkg" and p2.read_bytes() == b"PK\x03\x04new"


def test_export_anki_does_not_clobber_user_file(sample_doc, tmp_path):
    layout, doc_id = sample_doc
    store = DocStore(layout, doc_id)
    out = tmp_path / "anki"
    # CSV 路径（genanki 不可用时的回退）：相同守护规则
    first = _export_csv(store, CARDS, out, "")
    first.write_text("用户手改的 CSV", encoding="utf-8")
    second = _export_csv(store, CARDS, out, "")
    assert first.read_text(encoding="utf-8") == "用户手改的 CSV", "手改过的 CSV 被覆盖了"
    assert ".exported." in second.name, second.name
    # 真实导出入口（有 genanki 走 .apkg，否则 CSV）：重名文件同样不覆盖
    out2 = tmp_path / "anki2"
    p1 = export_anki(store, None, out_dir=out2)
    p1.write_bytes(b"user-edit")
    p2 = export_anki(store, None, out_dir=out2)
    assert p1.read_bytes() == b"user-edit", "手改过的 Anki 导出被覆盖了"
    assert ".exported." in p2.name, p2.name
