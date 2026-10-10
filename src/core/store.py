"""DocStore：文档主数据的统一读写入口（3.2 写者矩阵 + 3.5 不变量）。

所有写操作经 4.2.2 提交器（transactions.commit_doc）；本模块维护 I2 引用完整与 I3 双向镜像，
并实现块生命周期（4.5 状态机 / 4.4.6 历史 / 4.5.3 软删除）。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from .errors import BlockLocked, NotFound, RevisionConflict, RevisionGone, SchemaViolation
from .jsonl import dump_line, load_json, load_jsonl
from .models import (
    BLOCK_SNAPSHOT_FIELDS,
    Manifest,
    NoteBlock,
    ParagraphAnchor,
    block_snapshot,
    quote_matches,
    validate_anchor,
    validate_block,
)
from .paths import Layout
from .text import new_block_id, utc_now_iso
from .transactions import FileChange, append_change, commit_doc, doc_lock, recover_doc, replace_change


@dataclass
class DocMutation:
    """一次提交的全部主数据变更；未列出的文件保持不变。"""

    blocks: dict[str, list[dict]] = field(default_factory=dict)    # chapter_id -> 全量新行
    anchors: dict[str, list[dict]] = field(default_factory=dict)   # chapter_id -> 全量新行
    history: list[dict] = field(default_factory=list)              # 追加行（rev_id 去重）
    refs: list[dict] | None = None                                 # 整文件替换 refs/index.jsonl
    manifest_updates: dict | None = None


def sort_blocks(rows: Iterable[dict]) -> list[dict]:
    """不变量 I9：order 升序，同 order 按 block_id 字典序；任何位置不得按文件行序渲染。"""
    return sorted(rows, key=lambda r: (float(r.get("order", 0.0)), str(r.get("id", ""))))


# 进程级行缓存：key=(root, doc_id) -> (data_revision, {"anchors": {...}, "blocks": {...}})
# 只缓存"已提交状态"，每次读取先比对 manifest.data_revision，提交即失效（4.2.2 单写者）。
_ROW_CACHE: dict[tuple[str, str], tuple[int, dict]] = {}


class DocStore:
    def __init__(self, layout: Layout, doc_id: str):
        self.layout = layout
        self.doc_id = doc_id

    @property
    def _cache_key(self) -> tuple[str, str]:
        return (str(self.layout.root), self.doc_id)

    def _cached_rows(self, kind: str, chapter_id: str, revision: int, loader):
        """同一 data_revision 内复用解析结果，避免每次请求重复解析 JSONL。"""
        entry = _ROW_CACHE.get(self._cache_key)
        if entry is None or entry[0] != revision:
            entry = (revision, {"anchors": {}, "blocks": {}})
            _ROW_CACHE[self._cache_key] = entry
        bucket = entry[1].setdefault(kind, {})
        if chapter_id not in bucket:
            bucket[chapter_id] = loader()
        # 深拷贝：调用方就地修改嵌套字段（sources/links/tags）不得污染缓存
        return copy.deepcopy(bucket[chapter_id])

    @staticmethod
    def _invalidate(layout: Layout, doc_id: str) -> None:
        _ROW_CACHE.pop((str(layout.root), doc_id), None)

    # ------------------------------------------------------------------ 读取
    def manifest(self) -> dict:
        data = load_json(self.layout.manifest(self.doc_id), required=False)
        if data is None:
            raise NotFound(f"文档不存在: {self.doc_id}")
        return data

    def manifest_model(self) -> Manifest:
        return Manifest.model_validate(self.manifest())

    def data_revision(self) -> int:
        return int(self.manifest().get("data_revision", 1))

    def chapter_ids(self) -> list[str]:
        return [c["chapter_id"] for c in self.manifest().get("chapters", [])]

    def chapter(self, chapter_id: str) -> dict:
        for c in self.manifest().get("chapters", []):
            if c["chapter_id"] == chapter_id:
                return c
        raise NotFound(f"章节不存在: {chapter_id}")

    def anchor_rows(self, chapter_id: str) -> list[dict]:
        rev = self.data_revision()
        rows = self._cached_rows("anchors", chapter_id, rev,
                                 lambda: load_jsonl(self.layout.anchors(self.doc_id, chapter_id)))
        return [dict(r) for r in rows]      # 返回副本，调用方就地修改不污染缓存

    def all_anchor_rows(self) -> list[dict]:
        rows: list[dict] = []
        for ch in self.chapter_ids():
            rows.extend(self.anchor_rows(ch))
        return rows

    def anchors_by_id(self) -> dict[str, dict]:
        return {a["anchor_id"]: a for a in self.all_anchor_rows()}

    def block_rows(self, chapter_id: str, include_deleted: bool = False) -> list[dict]:
        rev = self.data_revision()
        rows = self._cached_rows("blocks", chapter_id, rev,
                                 lambda: load_jsonl(self.layout.blocks(self.doc_id, chapter_id)))
        rows = [dict(r) for r in rows]      # 副本
        if not include_deleted:
            rows = [r for r in rows if not r.get("deleted_at")]
        return sort_blocks(rows)

    def all_block_rows(self, include_deleted: bool = False) -> list[dict]:
        rows: list[dict] = []
        for ch in self.chapter_ids():
            rows.extend(self.block_rows(ch, include_deleted=include_deleted))
        return rows

    def blocks_by_id(self, include_deleted: bool = True) -> dict[str, dict]:
        return {b["id"]: b for b in self.all_block_rows(include_deleted=include_deleted)}

    def get_block(self, block_id: str, include_deleted: bool = True) -> dict:
        block = self.blocks_by_id(include_deleted=include_deleted).get(block_id)
        if block is None:
            raise NotFound(f"块不存在: {block_id}")
        return block

    def history_rows(self, block_id: str) -> list[dict]:
        return load_jsonl(self.layout.history(self.doc_id, block_id))

    def figure_rows(self) -> list[dict]:
        return load_jsonl(self.layout.figure_index(self.doc_id))

    def ref_rows(self) -> list[dict]:
        return load_jsonl(self.layout.refs_index(self.doc_id))

    # ------------------------------------------------------- 候选状态与不变量
    def candidate_state(self, overrides: dict[str, bytes]) -> tuple[dict[str, dict], list[dict]]:
        """以 overrides（doc 相对路径 -> 文件内容）覆盖磁盘，得到候选锚点/块状态。"""

        def rows_for(rel: str, chapter_id: str) -> list[dict]:
            if rel in overrides:
                return [json.loads(x) for x in overrides[rel].decode("utf-8").split("\n") if x.strip()]
            return load_jsonl(self.layout.anchors(self.doc_id, chapter_id) if rel.startswith("anchors/")
                              else self.layout.blocks(self.doc_id, chapter_id))

        anchors: dict[str, dict] = {}
        blocks: list[dict] = []
        for ch in self.chapter_ids():
            for a in rows_for(f"anchors/{ch}.jsonl", ch):
                anchors[a["anchor_id"]] = a
            blocks.extend(rows_for(f"blocks/{ch}.jsonl", ch))
        return anchors, blocks

    @staticmethod
    def compute_backlinks(blocks: Iterable[dict]) -> dict[str, list[str]]:
        """I3：锚点 note_block_ids ≡ 引用该锚点的未删除块集合。"""
        out: dict[str, list[tuple]] = {}
        for b in blocks:
            if b.get("deleted_at"):
                continue
            for src in b.get("sources", []):
                out.setdefault(src["anchor_id"], []).append(
                    (b.get("chapter_id", ""), float(b.get("order", 0.0)), b["id"]))
        return {
            aid: [t[2] for t in sorted(set(v))]
            for aid, v in out.items()
        }

    def backlink_changes(self, candidate_blocks: list[dict],
                         anchor_rows_by_chapter: dict[str, list[dict]] | None = None,
                         only_anchor_ids: set[str] | None = None) -> list[FileChange]:
        """重算反向索引并生成锚点文件变更（经 sync_backlinks 同一提交维护，3.2）。"""
        backlinks = self.compute_backlinks(candidate_blocks)
        changes: list[FileChange] = []
        by_chapter = anchor_rows_by_chapter or {
            ch: self.anchor_rows(ch) for ch in self.chapter_ids()
        }
        for ch, rows in by_chapter.items():
            touched = False
            new_rows = []
            for a in rows:
                aid = a["anchor_id"]
                if only_anchor_ids is not None and aid not in only_anchor_ids:
                    new_rows.append(a)
                    continue
                want = backlinks.get(aid, [])
                if list(a.get("note_block_ids", [])) != want:
                    a = dict(a)
                    a["note_block_ids"] = want
                    touched = True
                new_rows.append(a)
            if touched:
                changes.append(replace_change(self.layout, self.doc_id, f"anchors/{ch}.jsonl", new_rows))
        return changes

    def integrity_check(self, overrides: dict[str, bytes], *, check_quotes: bool = False) -> None:
        """I2/I3 与 4.7 校验：候选状态非法则拒绝提交。"""
        anchors, blocks = self.candidate_state(overrides)
        view_boxes = {p["page"]: p["view_box"] for p in self.manifest().get("pages", [])}
        seen: set[str] = set()
        for a in anchors.values():
            try:
                model = ParagraphAnchor.model_validate(a)
            except Exception as exc:          # 字段类型/枚举不合法 → 统一 422
                raise SchemaViolation(f"锚点数据非法: {exc}") from exc
            validate_anchor(model, view_boxes=view_boxes, seen_ids=seen)
        anchor_ids = set(anchors)
        heading_ids = {a["anchor_id"] for a in anchors.values() if a.get("content_type") == "heading"}
        block_ids = {b["id"] for b in blocks}
        for b in blocks:
            try:
                model = NoteBlock.model_validate(b)
            except Exception as exc:          # 字段类型/枚举不合法 → 统一 422
                raise SchemaViolation(f"块数据非法: {exc}") from exc
            validate_block(model, anchor_ids=anchor_ids,
                           heading_ids=heading_ids, block_ids=block_ids,
                           profile=self.manifest().get("profile", "textbook"))
            if check_quotes:
                for src in b.get("sources", []):
                    a = anchors.get(src["anchor_id"])
                    if a is not None and not quote_matches(a.get("content", ""), src.get("quote", "")):
                        raise SchemaViolation(
                            f"quote 不是锚点内容的连续子串: block={b['id']} anchor={src['anchor_id']}")
        # I3 双向镜像
        backlinks = self.compute_backlinks(blocks)
        for aid, a in anchors.items():
            if list(a.get("note_block_ids", [])) != backlinks.get(aid, []):
                raise SchemaViolation(
                    f"反向索引与块来源不一致: {aid}", detail={"want": backlinks.get(aid, []),
                                                               "got": a.get("note_block_ids")})

    # ------------------------------------------------------------------ 提交
    def commit(self, mutation: DocMutation, *, base_revision: int | None = None,
               sync_backlinks: bool = True, check_quotes: bool = False,
               validate: bool = True, now: str | None = None) -> int:
        base = self.data_revision() if base_revision is None else int(base_revision)
        changes: list[FileChange] = []
        for ch, rows in mutation.blocks.items():
            changes.append(replace_change(self.layout, self.doc_id, f"blocks/{ch}.jsonl", rows))
        for ch, rows in mutation.anchors.items():
            changes.append(replace_change(self.layout, self.doc_id, f"anchors/{ch}.jsonl", rows))
        if mutation.refs is not None:
            changes.append(replace_change(self.layout, self.doc_id, "refs/index.jsonl", mutation.refs))

        # 历史：按 block 文件分组追加，rev_id 去重（幂等前滚）
        hist_by_file: dict[str, list[dict]] = {}
        for row in mutation.history:
            hist_by_file.setdefault(row["block_id"], []).append(row)
        for block_id, rows in hist_by_file.items():
            path = self.layout.history(self.doc_id, block_id)
            existing = {r.get("rev_id") for r in load_jsonl(path)}
            todo = [r for r in rows if r.get("rev_id") not in existing]
            if todo:
                changes.append(append_change(self.layout, self.doc_id,
                                             f"history/{block_id}.jsonl", todo))

        if sync_backlinks:
            anchors_override = {f"anchors/{ch}.jsonl": ("".join(dump_line(x) + "\n" for x in rows)).encode("utf-8")
                                for ch, rows in mutation.anchors.items()}
            candidates: dict[str, bytes] = dict(anchors_override)
            for ch, rows in mutation.blocks.items():
                candidates[f"blocks/{ch}.jsonl"] = ("".join(dump_line(x) + "\n" for x in rows)).encode("utf-8")
            _, candidate_blocks = self.candidate_state(candidates)
            anchor_rows = {ch: list(rows) for ch, rows in mutation.anchors.items()} or None
            changes.extend(self.backlink_changes(candidate_blocks, anchor_rows))

        validator = None
        if validate:
            def validator(cands: dict[str, bytes]) -> None:  # noqa: F811
                self.integrity_check(cands, check_quotes=check_quotes)

        result = commit_doc(self.layout, self.doc_id, base, changes,
                            manifest_updates=mutation.manifest_updates,
                            validate=validator, now=now)
        self._invalidate(self.layout, self.doc_id)      # 提交后缓存失效
        return result.new_revision

    def recover(self) -> dict:
        with doc_lock(self.layout, self.doc_id):
            return recover_doc(self.layout, self.doc_id)

    # ------------------------------------------------------------ 块生命周期
    def make_history_row(self, block: dict, author: str, message: str, now: str) -> dict:
        return {
            "rev_id": f"{block['id']}@r{int(block['revision'])}",
            "block_id": block["id"],
            "snapshot": block_snapshot(block),
            "author": author,
            "message": message,
            "created_at": now,
        }

    def create_block(self, payload: dict, *, author: str, message: str = "新建块",
                     now: str | None = None, chapter_id: str | None = None) -> dict:
        """新建块（4.5.2 第 6 条）：人工→manually_edited，agent→hybrid，AI→按校验结果。"""
        now = now or utc_now_iso()
        chapter = chapter_id or payload.get("chapter_id")
        if not chapter:
            raise SchemaViolation("新建块必须指定 chapter_id")
        existing = set(self.blocks_by_id(include_deleted=True))
        block = dict(payload)
        block["id"] = new_block_id(existing)
        block["doc_id"] = self.doc_id
        block["chapter_id"] = chapter
        block["revision"] = 1
        block["created_at"] = now
        block["updated_at"] = now
        block["deleted_at"] = None
        block.setdefault("sources", [])
        block.setdefault("links", [])
        block.setdefault("tags", [])
        block.setdefault("content_format", "md_latex")
        block.setdefault("basis", "anchored")
        block["created_by"] = author if author in ("ai", "human", "agent") else "human"
        if author == "human":
            block["status"] = "manually_edited"
            block["generation_protected"] = True
        elif author == "agent":
            block["status"] = "hybrid"
            block["generation_protected"] = True
        else:
            block.setdefault("status", "ai_generated")
            block.setdefault("generation_protected", False)
        rows = self.block_rows(chapter, include_deleted=True)
        rows.append(block)
        mutation = DocMutation(blocks={chapter: rows},
                               history=[self.make_history_row(block, block["created_by"], message, now)])
        self.commit(mutation, check_quotes=True)
        return block

    def update_block(self, block_id: str, patch: dict, *, expected_revision: int | None,
                     author: str, message: str = "编辑", now: str | None = None,
                     unlocked_for_agent: bool = False, allow_fields: tuple[str, ...] | None = None) -> dict:
        """手动/agent 编辑（4.5.2）：只改可变字段，revision+1，写历史，保护位恒为 true。"""
        now = now or utc_now_iso()
        block = dict(self.get_block(block_id))
        if expected_revision is not None and int(block["revision"]) != int(expected_revision):
            raise RevisionConflict("revision 冲突", detail={"current_revision": block["revision"]})
        if author == "agent" and block.get("status") == "manually_edited" and not unlocked_for_agent:
            raise BlockLocked("manually_edited 块对 agent 只读，请先解锁")
        allowed = allow_fields or ("title", "content", "content_format", "sources", "links",
                                   "order", "heading_anchor_id", "type", "tags", "status", "basis")
        for key, value in patch.items():
            if key in allowed:
                block[key] = value
        if "sources" in patch:
            block["sources"] = patch["sources"]
        # 状态机（4.5.2）
        if "status" in patch:
            block["status"] = patch["status"]
        elif author == "human":
            block["status"] = "manually_edited"
        elif author == "agent":
            block["status"] = "hybrid" if block.get("status") != "manually_edited" else "manually_edited"
        block["generation_protected"] = True
        block["revision"] = int(block["revision"]) + 1
        block["updated_at"] = now
        chapter = block["chapter_id"]
        rows = [dict(b) for b in self.block_rows(chapter, include_deleted=True)]
        rows = [block if b["id"] == block_id else b for b in rows]
        mutation = DocMutation(blocks={chapter: rows},
                               history=[self.make_history_row(block, author, message, now)])
        self.commit(mutation, check_quotes=("sources" in patch))
        return block

    def soft_delete_block(self, block_id: str, *, expected_revision: int | None = None,
                          author: str = "human", message: str = "删除块", now: str | None = None) -> dict:
        """软删除（4.5.3）：只写 deleted_at，同步移除 note_block_ids，不物理删除行。"""
        now = now or utc_now_iso()
        block = dict(self.get_block(block_id))
        if expected_revision is not None and int(block["revision"]) != int(expected_revision):
            raise RevisionConflict("revision 冲突", detail={"current_revision": block["revision"]})
        block["deleted_at"] = now
        if author in ("human", "agent"):
            block["generation_protected"] = True
        block["revision"] = int(block["revision"]) + 1
        block["updated_at"] = now
        chapter = block["chapter_id"]
        rows = [dict(b) for b in self.block_rows(chapter, include_deleted=True)]
        rows = [block if b["id"] == block_id else b for b in rows]
        mutation = DocMutation(blocks={chapter: rows},
                               history=[self.make_history_row(block, author, message, now)])
        self.commit(mutation)
        return block

    def restore_block(self, block_id: str, *, expected_revision: int | None = None,
                      author: str = "human", message: str = "恢复块", now: str | None = None) -> dict:
        """恢复软删除：status 不变，按 sources 重建 backlink（4.5.3）。"""
        now = now or utc_now_iso()
        block = dict(self.get_block(block_id))
        if expected_revision is not None and int(block["revision"]) != int(expected_revision):
            raise RevisionConflict("revision 冲突", detail={"current_revision": block["revision"]})
        block["deleted_at"] = None
        block["generation_protected"] = True
        block["revision"] = int(block["revision"]) + 1
        block["updated_at"] = now
        chapter = block["chapter_id"]
        rows = [dict(b) for b in self.block_rows(chapter, include_deleted=True)]
        rows = [block if b["id"] == block_id else b for b in rows]
        mutation = DocMutation(blocks={chapter: rows},
                               history=[self.make_history_row(block, author, message, now)])
        self.commit(mutation)
        return block

    def rollback_block(self, block_id: str, rev_id: str, *, expected_revision: int | None = None,
                       author: str = "human", now: str | None = None) -> dict:
        """回滚（4.4.6）：业务字段取快照值，revision 继续递增，保护位保持 true。"""
        now = now or utc_now_iso()
        block = dict(self.get_block(block_id))
        if expected_revision is not None and int(block["revision"]) != int(expected_revision):
            raise RevisionConflict("revision 冲突", detail={"current_revision": block["revision"]})
        target = None
        for row in self.history_rows(block_id):
            if row.get("rev_id") == rev_id:
                target = row
                break
        if target is None:
            raise RevisionGone(f"历史版本不存在或已被裁剪: {rev_id}")
        snap = dict(target.get("snapshot", {}))
        for key in BLOCK_SNAPSHOT_FIELDS:
            if key in snap:
                block[key] = snap[key]
        block["generation_protected"] = True  # 普通回滚后保护位恒为 true
        block["revision"] = int(block["revision"]) + 1
        block["updated_at"] = now
        chapter = block["chapter_id"]
        rows = [dict(b) for b in self.block_rows(chapter, include_deleted=True)]
        rows = [block if b["id"] == block_id else b for b in rows]
        mutation = DocMutation(blocks={chapter: rows},
                               history=[self.make_history_row(block, author, f"回滚到 {rev_id}", now)])
        self.commit(mutation, check_quotes=True)
        return block

    def allow_regeneration(self, block_id: str, *, expected_revision: int | None = None,
                           now: str | None = None) -> dict:
        """仅人工 UI 可调用：清除 generation_protected 并记历史（9.2）。"""
        now = now or utc_now_iso()
        block = dict(self.get_block(block_id))
        if expected_revision is not None and int(block["revision"]) != int(expected_revision):
            raise RevisionConflict("revision 冲突", detail={"current_revision": block["revision"]})
        block["generation_protected"] = False
        block["revision"] = int(block["revision"]) + 1
        block["updated_at"] = now
        chapter = block["chapter_id"]
        rows = [dict(b) for b in self.block_rows(chapter, include_deleted=True)]
        rows = [block if b["id"] == block_id else b for b in rows]
        mutation = DocMutation(blocks={chapter: rows},
                               history=[self.make_history_row(block, "human", "允许重新生成覆盖", now)])
        self.commit(mutation)
        return block

    def reorder_block(self, block_id: str, after: str | None, *, expected_revision: int | None = None,
                      author: str = "human", now: str | None = None) -> list[dict]:
        """把块 X 移到块 Y 之后（4.5.1）；after 为 null 表示移到开头。"""
        now = now or utc_now_iso()
        block = dict(self.get_block(block_id))
        if expected_revision is not None and int(block["revision"]) != int(expected_revision):
            raise RevisionConflict("revision 冲突", detail={"current_revision": block["revision"]})
        chapter = block["chapter_id"]
        rows = [dict(b) for b in self.block_rows(chapter, include_deleted=True)]
        others = sort_blocks([b for b in rows if b["id"] != block_id and not b.get("deleted_at")])
        if after is None:
            new_order = (min((float(b["order"]) for b in others), default=0.0)) - 10.0
        else:
            idx = next((i for i, b in enumerate(others) if b["id"] == after), None)
            if idx is None:
                raise NotFound(f"after 块不存在: {after}")
            cur = float(others[idx]["order"])
            nxt = next((float(b["order"]) for b in others[idx + 1:]), None)
            new_order = (cur + nxt) / 2.0 if nxt is not None else cur + 10.0
        block["order"] = float(new_order)
        block["revision"] = int(block["revision"]) + 1
        block["updated_at"] = now
        rows = [block if b["id"] == block_id else b for b in rows]
        mutation = DocMutation(blocks={chapter: rows},
                               history=[self.make_history_row(block, author, "重排", now)])
        self.commit(mutation)
        return sort_blocks([b for b in rows if not b.get("deleted_at")])

    def trim_history(self, block_id: str, keep_recent: int = 50) -> int:
        """显式历史裁剪（唯一例外）：保留 r1 + 最近 keep_recent 版，先备份再原子压缩。"""
        import shutil
        from .jsonl import atomic_write_bytes

        rows = self.history_rows(block_id)
        if len(rows) <= keep_recent + 1:
            return 0
        path = self.layout.history(self.doc_id, block_id)
        backup = path.with_suffix(".jsonl.bak-" + utc_now_iso().replace(":", ""))
        shutil.copy2(path, backup)
        kept = rows[:1] + rows[-keep_recent:]
        atomic_write_bytes(path, ("".join(dump_line(r) + "\n" for r in kept)).encode("utf-8"))
        return len(rows) - len(kept)
