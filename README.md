# note-gen（自动笔记软件）

上传一本教材（或一篇论文）的 PDF，自动生成**模块化、可双向定位、可增删改**的 Markdown / LaTeX 笔记。

- 唯一规范：[`docs/自动笔记软件设计方案.md`](docs/自动笔记软件设计方案.md)（v1.6）
- 决策记录：[`docs/adr.md`](docs/adr.md)

## 快速开始

```bash
# 1) 安装依赖（Python >= 3.11）
python -m pip install -e ".[dev]"

# 2) 一键：解析 → 锚点 → 生成 → 链接 → 重建索引 → 导出
python cli.py run data/textbooks/linear-algebra-5e.pdf --doc-id linear-algebra-5e

# 3) 启动本地应用（FastAPI 托管 Viewer）
python cli.py serve
# 打开 http://127.0.0.1:8787
```

## 管线命令

```bash
python cli.py ingest   <pdf_path> [--doc-id ID] [--profile textbook|paper] [--parser auto|marker|docling|pypdf] [--ocr] [--force]
python cli.py anchor   <doc_id> [--force]
python cli.py generate <doc_id> [--chapter ch01] [--model NAME] [--concurrency 2] [--force] [--no-cache] [--merge|--replace] [--yes]
python cli.py link     <doc_id> [--chapter ch01]
python cli.py export   <doc_id> [--chapter ch01] [--format md|tex|both|md-obsidian] [--out DIR]
python cli.py run      <pdf_path> [...]          # ingest→anchor→generate→link→reindex→export
python cli.py reindex  <doc_id>
python cli.py report   <doc_id>
python cli.py eval     <doc_id>
python cli.py backup   <doc_id> [--out DIR]
python cli.py restore  <backup.zip> [--as-new-doc]
python cli.py serve    [--host 127.0.0.1] [--port 8787]
```

## 目录布局

见规范 4.8：`data/{doc_id}/` 为文档事实源（manifest/anchors/blocks/history/figures/refs/index.db），
`ingest_out/`、`cache/`、`reports/`、`notes_out/` 均可重建。

## 解析器与 11.0 技术验证

重型解析器（Marker/Docling/Pix2Text）安装在独立解释器中，与主环境隔离（ADR D31）：

```bash
python -m venv .venv-parsers
.venv-parsers/Scripts/python -m pip install marker-pdf docling     # torch 等重型依赖
# config.yaml → parser.python: ".venv-parsers/Scripts/python.exe"
python scripts/tech_validation_11.py        # 生成 docs/tech-validation-11.0.md
```

- `--parser auto` 依次尝试 marker → docling → pypdf（内置离线兜底）。
- 坐标换算参数记录在每页 `coordinate_meta`；缺少元数据时该页标记 failed，不猜 scale/rotation。
- **已知限制**：Pix2Text 公式 OCR 依赖 `rapidocr<2`（需 Python ≤ 3.12），Python 3.13 下不可用；
  此时公式 OCR 自动跳过并在报告中记录（解析器自身仍可识别公式块）。

## v2 增量

- **Anki 闪卡**：`python cli.py export <doc_id> --format anki`（definition/theorem/motivation → .apkg，genanki 不可用时回退 CSV）
- **语义检索**：`config.yaml embedding.provider: onnx_bge|hashed_lexical`，`python cli.py reindex <doc_id> --embed` 写入向量，`/api/v1/search?mode=vec` 走 sqlite-vec Top-K（不可用自动回落 FTS）
- **评测回归**：`python cli.py eval <doc_id> [--update-baseline]`（faithfulness/formula/coverage 三维度，低于基线 5 个百分点告警，用例 tests/eval/cases.jsonl + 人工标签 labels.json）

## 开发

```bash
pytest                      # 单元 + 集成 + 黄金样本
cd viewer && pnpm install && pnpm dev   # 开发模式（/api 代理到 8787）
cd viewer && pnpm build     # 生产构建 → viewer/dist，由 FastAPI 同源托管
```
