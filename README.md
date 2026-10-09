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

## 开发

```bash
pytest                      # 单元 + 集成 + 黄金样本
cd viewer && pnpm install && pnpm dev   # 开发模式（/api 代理到 8787）
cd viewer && pnpm build     # 生产构建 → viewer/dist，由 FastAPI 同源托管
```
