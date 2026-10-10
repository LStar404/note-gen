# note-gen · 自动笔记软件

上传一本教材（或一篇论文）的 PDF，自动生成**模块化、可双向定位、可增删改**的 Markdown / LaTeX 笔记。

> 把"从 PDF 到知识笔记"变成一次导入 + 一次生成；笔记的每一块都能点回原文高亮，也永远不会被 AI 重写覆盖。

- **唯一规范**：[`docs/自动笔记软件设计方案.md`](docs/自动笔记软件设计方案.md)（v1.6，约 2100 行，含数据模型/坐标契约/验收用例）
- **实现决策**：[`docs/adr.md`](docs/adr.md)（D1–D34）
- **技术验证**：[`docs/tech-validation-11.0.md`](docs/tech-validation-11.0.md)（marker / docling / pypdf 实测）

---

## 目录

1. [核心能力](#核心能力)
2. [界面一览](#界面一览)
3. [快速开始](#快速开始)
4. [使用流程](#使用流程)
5. [命令行参考](#命令行参考)
6. [配置参考](#配置参考)
7. [解析器与 OCR](#解析器与-ocr)
8. [接入大模型](#接入大模型)
9. [数据与目录布局](#数据与目录布局)
10. [架构概览](#架构概览)
11. [测试与质量](#测试与质量)
12. [常见问题（排错）](#常见问题排错)
13. [安全说明](#安全说明)
14. [路线图](#路线图)

---

## 核心能力

| 能力 | 说明 |
|---|---|
| **PDF 导入** | 教材/论文两种 profile；自动识别章节（目录 → 标题正则 → 手动覆盖）；纯图片扫描页自动 OCR |
| **五类笔记块** | 定义 definition / 定理 theorem / 动机 motivation / 图示 figure / 延伸链接 link（论文另有贡献/方法/实验） |
| **防幻觉来源** | 每块必须带 `sources`（锚点 + 原文引文），写入前程序校验引文确实出自原文；AI 综合内容显式标注 `basis=synthesis` |
| **双向定位** | 笔记→教材：点来源徽标跳页并高亮原文；教材→笔记：翻页/选区实时列出关联块 |
| **人工成果保护** | 人工/agent 编辑过的块 `generation_protected`，再生成永不覆盖；软删除有墓碑，不会被 AI 复活 |
| **版本历史** | 每次修改产生 revision + 完整快照，可一键回滚；历史只追加 |
| **多格式导出** | Markdown / LaTeX / Obsidian vault / Anki 闪卡（.apkg）；整书或单章；**导出编码** utf-8 / utf-8-sig / gbk / big5 |
| **多语言输出** | 生成与导出标签支持 简体中文 / English / 日本語 |
| **多模型接入** | 任意 OpenAI 兼容端点（OpenAI / DeepSeek / Qwen / Kimi / GLM / Ollama / vLLM…），按任务路由，带成本上限与用量仪表盘 |
| **本地优先** | 服务只绑 127.0.0.1；解析/浏览/编辑全部离线可用；JSONL 主数据可进 git |
| **可恢复** | 跨文件提交日志（intent→COMMIT→DONE），崩溃后自动恢复；备份/恢复带校验和与 Zip Slip 防护 |

---

## 界面一览

```
┌──────────────────┬──────────────────────────┬────────────────────┐
│  教材视图（PDF）  │     笔记视图（块流）       │   块详情 / 选区命中  │
│  · PDF.js 渲染    │  · 卡片 / 源码 / 预览三视图 │  · 来源列表          │
│  · 高亮 + 脉冲    │  · 来源徽标可点跳原文       │  · 链接 / 历史时间线  │
│  · 选区匹配       │  · agent 指令 → 计划弹层    │  · 回滚 / 删除 / 重排 │
└──────────────────┴──────────────────────────┴────────────────────┘
   顶部工具栏：教材选择 · 章节导航 · 导入 · 生成 · 导出 · 待审核 · 设置 · 全局检索
```

---

## 快速开始

### 方式一：双击启动（推荐）

工作区根目录已有 **`启动自动笔记软件.bat`**（仓库内副本：[`scripts/启动自动笔记软件.bat`](scripts/启动自动笔记软件.bat)），双击即可：

1. 自动检查/安装依赖（缺包时 `pip install -r requirements.txt`）
2. 自动构建前端（`viewer/dist` 缺失时 `pnpm install && pnpm build`）
3. 启动服务并**自动打开浏览器** → `http://127.0.0.1:8787`

```bat
启动自动笔记软件.bat                :: 平时双击这个
启动自动笔记软件.bat --no-browser   :: 只启动服务
启动自动笔记软件.bat 9000           :: 换端口
```

### 方式二：手动

```bash
# 1) 安装依赖（Python >= 3.11）
python -m pip install -r requirements.txt

# 2) 构建前端
cd viewer && pnpm install && pnpm run build && cd ..

# 3) 启动
python cli.py serve            # → http://127.0.0.1:8787
```

### 方式三：纯命令行

```bash
python cli.py run data/textbooks/我的教材.pdf --doc-id my-book --title "我的教材"
# 一键串联：ingest → anchor → generate → link → reindex → export
```

> **注意**：`generate` 需要先配置模型（见[接入大模型](#接入大模型)）；只做解析/锚点/导出不需要。

---

## 使用流程

| 步骤 | 操作 | 说明 |
|---|---|---|
| 1. 导入 | UI「导入 PDF」或 `cli.py ingest` | 自动识别章节；扫描件自动 OCR |
| 2. 生成 | UI「生成本章」或 `cli.py generate` | 两次生成（提纲→逐块填充），带来源校验与缓存 |
| 3. 编辑 | 卡片/源码/预览三视图 | 保存即产生新版本；人工块受保护 |
| 4. 对照 | 点来源徽标 / 在 PDF 选中文字 | 双向跳转 + 高亮 |
| 5. 导出 | UI 导出按钮或 `cli.py export` | md / tex / Obsidian / Anki |

---

## 命令行参考

| 命令 | 用途 | 常用参数 |
|---|---|---|
| `ingest <pdf>` | 解析 PDF | `--doc-id` `--title` `--profile textbook|paper` `--parser auto|marker|docling|pypdf` `--ocr` |
| `anchor <doc_id>` | 生成锚点索引 | `--force` |
| `generate <doc_id>` | 生成笔记块 | `--chapter` `--model` `--language zh|en|ja` `--concurrency` `--force` `--no-cache` `--merge/--replace --yes` |
| `link <doc_id>` | 反向索引 + 块间链接 | `--chapter` |
| `export <doc_id>` | 导出 | `--chapter` `--format md|tex|both|md-obsidian|anki` `--out` `--encoding utf-8|utf-8-sig|gbk|big5` `--language` |
| `run <pdf>` | 一键全链路 | 以上参数透传 |
| `reindex <doc_id>` | 重建 SQLite 索引 | `--embed`（先算向量） |
| `report <doc_id>` | 汇总各阶段报告 | 含死链/保护跳过清单 |
| `eval <doc_id>` | 生成质量评测 | `--update-baseline`（固化基线） |
| `backup <doc_id>` | 备份（含校验和） | `--out` |
| `restore <zip>` | 恢复（防 Zip Slip） | `--as-new-doc` |
| `serve` | 启动 Web 服务 | `--host` `--port` |

所有命令支持 `--dry-run`；退出码：**0** 成功 / **1** 参数错误 / **2** 运行错误。

---

## 配置参考（`config.yaml`）

```yaml
app:
  host: 127.0.0.1          # 只绑本地（NFR-1）
  port: 8787
  cors_origins: ["http://127.0.0.1:5173"]
parser:
  default: marker          # auto | marker | docling | pypdf（失败自动回退）
  ocr_mode: auto           # auto（仅无文字层页）| force（全页）| off
  python: null             # 解析器 venv 解释器（见下文）
chapters_override: []      # 手动章区间覆盖（最高优先级）
generate:
  models:                  # 任务 → 模型路由（provider/model 或唯一短名）
    default: "openai/gpt-4o-mini"
    outline: null          # 提纲：便宜模型
    fill: null
    agent: null            # 必须具备 tools 能力
    link: null
  output_language: zh      # zh | en | ja
  outline_max_in: 32000
  fill_max_in: 8000
  concurrency: 2
  max_cost_per_chapter: 0.0   # 美元，0=不限；未知价格的模型在此设非零时被拒绝
  temperature: 0.2
export:
  encoding: utf-8          # utf-8 | utf-8-sig | gbk | big5（tex 恒为 UTF-8）
embedding:
  provider: none           # none | onnx_bge | hashed_lexical（后者为词法回退）
  model_dir: null
  dim: 1024
  threshold: 0.55
cache: { dir: "cache/gen" }
match: { contain_ratio: 0.8, cover_ratio: 0.5, top_k: 8 }
history: { keep_recent: 50 }
```

> 也可在 **设置 → 通用设置** 里改解析器 / OCR / 语言 / 编码，保存即原子写回 `config.yaml`。

---

## 解析器与 OCR

| 解析器 | 特点 | 实测（本机，见 11.0 报告） |
|---|---|---|
| **marker** | 结构/公式/旋转页质量最好（默认） | 5/5 样本通过，坐标合法率 100% |
| **docling** | 复杂双栏/论文结构强 | 4/5（旋转页坐标系问题会按规范拒绝该页） |
| **pypdf** | 内置、极快、零依赖 | 5/5，适合快速路径与兜底 |
| **OCR（rapidocr）** | 纯图片/扫描页自动识别 | 2 页扫描件 6 秒，中文识别正确 |

重型解析器装在**独立虚拟环境**（不污染主环境）：

```bash
python -m venv .venv-parsers
.venv-parsers/Scripts/python -m pip install marker-pdf docling
# config.yaml → parser.python: ".venv-parsers/Scripts/python.exe"
python scripts/tech_validation_11.py      # 生成实测报告
```

**已知限制**
- Pix2Text 公式 OCR 依赖 `rapidocr<2`（仅支持 Python ≤ 3.12），3.13 下自动跳过并告警
- docling 默认后端在**含中文的安装路径**下会因自带资源加载失败；已自动回退 pypdfium2 后端

---

## 接入大模型

在 **设置 → 供应商与模型** 里添加（或手改 `data/models.json`）：

```json
{
  "providers": [
    {
      "id": "deepseek", "label": "DeepSeek", "protocol": "openai_compat",
      "base_url": "https://api.deepseek.com/v1",
      "api_key_env": "DEEPSEEK_API_KEY",
      "capabilities": { "json_schema": true, "tools": true, "vision": false },
      "models": [
        { "id": "deepseek-chat", "context_window": 65536, "max_output_tokens": 8192,
          "capabilities": { "json_schema": true, "tools": true, "vision": false },
          "pricing": { "input_per_million": 0.27, "output_per_million": 1.1, "currency": "USD" } }
      ]
    }
  ]
}
```

- **本地模型**：Ollama（`base_url: http://127.0.0.1:11434/v1`）、vLLM 等同理
- **能力标志**驱动行为：`json_schema` 决定结构化输出路径；`tools` 是 agent 编辑的前提（无则 UI 自动禁用）
- **密钥**：`api_key_env`（环境变量，推荐）或 `api_key`（本地明文，接口只回显末 4 位）
- **成本控制**：`max_cost_per_chapter` 在**每次调用前**原子预留；价格未知的模型在启用预算时被拒（不假装免费）
- **缓存联动**：换模型后缓存键自动失效，重算不会重复收费

---

## 数据与目录布局

```
note-gen/
├── data/
│   ├── runtime.db                 # 不可重建：任务/用量/agent 会话
│   ├── models.json                # 模型注册表（含密钥，勿入库）
│   ├── textbooks/{doc_id}.pdf     # 原 PDF
│   └── {doc_id}/                  # ← 文档事实源
│       ├── manifest.json          # 页面几何/章节/阶段指纹/data_revision
│       ├── anchors/{ch}.jsonl     # 锚点（段落/公式/图/表 + bbox + 哈希）
│       ├── blocks/{ch}.jsonl      # 笔记块（唯一编辑对象）
│       ├── history/{block_id}.jsonl  # 版本快照
│       ├── figures/  refs/  index.db
├── ingest_out/  cache/  reports/  notes_out/   # 均可重建
└── viewer/                        # React + PDF.js 前端
```

**不变量**：JSONL 是唯一事实源，SQLite/导出/缓存可随时重建；`cli.py reindex` 修复索引层，`cli.py link` 修复反向索引层。

---

## 架构概览

```
表现层   Viewer（React + PDF.js，双向对照）
应用层   FastAPI（块 CRUD/乐观锁 · 对照检索 · 生成触发 · 导出 · agent 两步编辑）
管线层   ingest → anchor → generate → link → export（每阶段可独立重跑、幂等）
数据层   JSONL 主存 + SQLite 派生索引 + 文件系统（图/原 PDF）
```

关键设计（细节见规范文档）：
- **可恢复提交**：跨文件变更走 intent → COMMIT → DONE 日志，崩溃后幂等前滚
- **坐标契约**：存未旋转的原始 PDF 用户空间，前端只用 PDF.js viewport 正/逆变换（旋转/CropBox/UserUnit 不会算两次）
- **再生成合并**：身份键匹配 + 保护位 + 墓碑抑制，人工成果永不丢

---

## 测试与质量

```bash
pytest                                  # 68 项单元/集成测试
python scripts/edge_case_probe.py       # 15 项边界探测（异常输入/错误码）
python scripts/edge_case_probe2.py      # 16 项边界探测（agent/备份/任务/约束）
python scripts/tech_validation_11.py    # 解析器技术验证
python cli.py eval <doc_id>             # 生成质量评测（三维度 + 基线回归告警）
```

评测维度：**faithfulness**（忠实度）/ **formula**（公式正确率）/ **coverage**（锚点覆盖率）；
任一维度低于基线 5 个百分点即告警（防"改 prompt 改坏质量"）。

---

## 常见问题（排错）

| 现象 | 原因与处理 |
|---|---|
| 导入任务显示 `failed` | 任务卡会直接显示原因；常见：损坏 PDF、文档已存在（409）、解析器不可用 |
| 提示"文档已存在" | 同一 PDF 重复导入会被拒绝以保护已有笔记；确认后可加 `overwrite=true` 重建 |
| 生成提示"未配置生成模型" | 到 设置 → 任务路由 指定模型，或改 `config.yaml generate.models.default` |
| agent 按钮不可用 | 需要具备 `tools` 能力的模型（设置里勾选能力或换模型） |
| 扫描件导入后是空的 | 检查 设置 → OCR 模式 是否为 `off`；设为 `auto/force` 后重新导入 |
| 导出的 .tex 编译不了 | tex 恒为 UTF-8；请用 `xelatex` 并保证 `ctex` 宏包可用 |
| 两个窗口同时改同一块 | 后者会收到 409（乐观锁），刷新后重试 |
| 端口被占用 | `启动自动笔记软件.bat 9000` 换端口 |

---

## 安全说明

- 服务**只绑定 127.0.0.1**；所有 `/api` 请求需 `X-Local-Token`（生产由页面 `<meta>` 注入，开发由 Vite 代理注入）
- **CSRF 防护**：校验精确 Host/Origin/Fetch-Metadata，拒绝 `Origin:null` 与跨站请求
- **Prompt 注入防护**：PDF 原文一律放 `<source_data>` 数据分隔区，其中的指令无效；服务端再做锚点白名单校验
- **渲染安全**：Markdown 关闭原始 HTML；TeX 导出不接受外部包注入
- **路径安全**：doc_id/chapter_id/figure_id 严格白名单 + resolve 校验；恢复备份拒绝 Zip Slip

---

## 路线图

- [x] MVP：单章解析 → 生成 → 编辑 → 导出 → 双向定位
- [x] v1：UI 导入/任务/检索、CRUD/回滚、安全边界、备份恢复
- [x] v1.5：论文 profile、多模型管理、TeX/Obsidian 导出
- [x] v2 增量：sqlite-vec 语义检索、Anki 闪卡、评测回归自动化、扫描件 OCR
- [ ] v2：增量重解析自动修复、跨书链接、Electron 打包

---

## 开发

```bash
pytest                       # 后端测试
cd viewer && pnpm dev        # 前端开发（/api 代理到 8787）
cd viewer && pnpm build      # 生产构建 → viewer/dist
```

改动请遵守规范文档：字段名/枚举/路径是规范的一部分；行为与文档冲突时先改文档并登记 ADR 再改代码。
