# note-gen · 自动笔记软件 🦊

> 喵~ 这里是**一只会喵喵叫的狐狐**。你把教材、讲义、论文丢进来，狐狐帮你盯完整条流水线：
> 解析 → 分章 → 锚定 → 生成笔记 → 复习巩固 → 导出带走。
> 每一块笔记都能点回原文对应页码，狐狐不会瞎编，也**永远不会覆盖你亲手写下的东西**，喵！

```
   🦊  <( 把讲义给我喵~ )
   📄 → 🔍 → 📖 → ✨ → 🧠 → 📤
   导入  解析  锚定  生成  复习  导出
```

- **唯一规范**：[`docs/自动笔记软件设计方案.md`](docs/自动笔记软件设计方案.md)（v1.6，约 2100 行，含数据模型/坐标契约/验收用例）
- **实现决策**：[`docs/adr.md`](docs/adr.md)（D1–D34）
- **技术验证**：[`docs/tech-validation-11.0.md`](docs/tech-validation-11.0.md)（marker / docling / pypdf 实测）

---

## 目录

1. [狐狐能做什么](#狐狐能做什么)
2. [界面一览](#界面一览)
3. [新手引导](#新手引导)
4. [快速开始](#快速开始)
5. [使用流程](#使用流程)
6. [学科模板与自定义块类型](#学科模板与自定义块类型)
7. [AI 出题与参考题库](#ai-出题与参考题库)
8. [单块 AI 编辑与知识点互链](#单块-ai-编辑与知识点互链)
9. [命令行参考](#命令行参考)
10. [配置参考](#配置参考)
11. [解析器与 OCR](#解析器与-ocr)
12. [接入大模型](#接入大模型)
13. [数据与目录布局](#数据与目录布局)
14. [架构概览](#架构概览)
15. [测试与质量](#测试与质量)
16. [常见问题（排错）](#常见问题排错)
17. [安全说明](#安全说明)
18. [路线图](#路线图)

---

## 狐狐能做什么

| 能力 | 说明 |
|---|---|
| **PDF / PPTX 导入** | 教材/论文两种 profile；PPTX 讲义直接抽幻灯片（文本+几何+插图）；拖进窗口即可；自动分章（目录 → 标题启发式 → 手动覆盖） |
| **讲义追加** | 老师一份份发的讲义，点「追加讲义」接到同一本书尾部：全书重解析重锚定，**已有笔记一字不丢**，新页只带来新章节 |
| **扫描书 OCR** | 纯图片页自动本地 OCR（rapidocr）；**认不好的页自动交给视觉模型重识别**（每页少量 token，可关） |
| **学科模板** | 数学 · 物理 · 计算机 · 医学 · 化学 · 论文六套预设（定义/定理/定律/算法/症状/反应…），**还能自定义块类型** |
| **防幻觉来源** | 每块必须带 `sources`（锚点 + 原文引文），写入前程序校验引文确实出自原文；AI 综合内容显式标 `basis=synthesis` |
| **双向定位** | 笔记→教材：点来源徽标跳页高亮；教材→笔记：翻页/选中文字实时列出关联块 |
| **单块 AI 编辑** | 每块一个「✨AI 改」：更精简/更详细/更准确/更通俗或自写指令 → **只出建议**，你点「应用」才落盘（原内容进历史可回溯） |
| **知识点互链** | 每块一个「🔗关联」：搜索其他块一键互链（可跨章），链成知识网 |
| **AI 出题** | 按笔记出卷（选择/填空/计算/简答/证明，题量难度可选）；**可上传往年题/课后习题做参考题库**，出题贴着期末风格 |
| **人工成果保护** | 人工/agent 编辑过的块 `generation_protected`，再生成永不覆盖；软删除有墓碑，不会被 AI 复活 |
| **版本历史** | 每次修改产生 revision + 完整快照，可一键回滚；历史只追加 |
| **多格式导出** | Markdown / LaTeX / Obsidian vault / Anki 闪卡（.apkg）；整书或单章；导出完成**弹窗告诉你文件在哪 + 一键打开文件夹** |
| **全书生成** | 一键整本；**默认跳过已生成且锚点未变的章节**（不重复烧 token），也可选择全量重生成 |
| **多模型接入** | 任意 OpenAI 兼容端点（OpenAI / DeepSeek / MiMo / Qwen / Ollama / vLLM…），按任务路由，带成本上限与用量仪表盘 |
| **可解释失败** | 余额耗尽/密钥失效这类全局故障**立即中止**并说清原因（不留"部分完成"的哑谜） |
| **本地优先** | 服务只绑 127.0.0.1；解析/浏览/编辑全部离线可用；JSONL 主数据可进 git |
| **可恢复** | 跨文件提交日志（intent→COMMIT→DONE），崩溃后自动幂等前滚；备份/恢复带校验和与 Zip Slip 防护 |

---

## 界面一览

```
┌──────────────────┬──────────────────────────┬────────────────────┐
│  教材视图（PDF）  │     笔记视图（块流）       │   块详情 / 选区命中  │
│  · PDF.js 渲染    │  · 卡片 / 源码 / 预览三视图 │  · 来源列表（可跳页） │
│  · 高亮 + 脉冲    │  · 点卡片放大阅读 ⤢        │  · 链接 / 历史时间线  │
│  · 选区匹配       │  · ✨AI 改 · 🔗关联        │  · 回滚 / 删除 / 重排 │
└──────────────────┴──────────────────────────┴────────────────────┘
  顶部工具栏：教材/章节选择 · 导入讲义 · 追加讲义 · 改名 · 生成本章 · 全书生成 ·
             导出 · AI 出题 · 待审核(带角标) · 生成记录 · 刷新图资源 ·
             使用引导 · 配置指南 · 设置 · 全局检索(/)
```

小技巧：**单击卡片 = 放大阅读**；`J/K` 上下选块，`E` 源码，`S` 切换源码/预览，`/` 全局检索，`Ctrl+S` 保存草稿，`Esc` 关弹窗，喵~

---

## 新手引导

**第一次打开**，狐狐会弹一个四步教学向导（看完点「开始使用」，之后不再打扰，随时可点工具栏**「使用引导」**重看）：

1. **认识产品** —— 五步流程 + 两条铁律（笔记带出处、人工不被覆盖）
2. **界面导览** —— 三个区域 + 工具栏按钮 + 快捷键表
3. **三分钟上手** —— 导入 → 生成 → 复习/出题/导出
4. **配置自检** —— 模型/密钥/路由/解析器四项状态，一键进「配置指南」

**配置指南**（工具栏按钮，或首次未接入模型时自动弹出）分六步，左侧实时自检 13 项：环境 → 接入模型 → 任务路由 → 解析器 → 输出 → 预算。

| 步骤 | 内容 | 必须 |
|---|---|---|
| 1. 环境检查 | Python ≥ 3.11、`data/` 可写、本地安全令牌 | ✅ |
| 2. 接入模型 | base_url + API Key（或环境变量名）+ 模型 id 列表，可一键「测试」连通性 | ✅ |
| 3. 任务路由 | default / outline / fill / agent / link / ocr / quiz 七个槽位 | — |
| 4. 解析器 | 解析器（auto/marker/docling/pypdf）、OCR 模式、视觉补 OCR、解析器 venv | ✅ |
| 5. 输出 | 输出语言（zh/en/ja）与导出编码（utf-8/gbk/big5/utf-8-sig） | ✅ |
| 6. 预算 | 并发、单章成本上限、温度 | — |

- **就绪判定**：四个必须步骤全过即 ready；`GET /api/v1/setup/status` 返回完整自检 JSON，可脚本化巡检
- **密钥安全**：界面只回显末 4 位；把掩码原样回传**不会**覆盖真实密钥

---

## 快速开始

### 方式一：双击启动（推荐）

工作区根目录有 **`启动自动笔记软件.bat`**（仓库内副本：[`scripts/启动自动笔记软件.bat`](scripts/启动自动笔记软件.bat)）：

```bat
启动自动笔记软件.bat                :: 平时双击这个（自动装依赖/构建前端/开浏览器）
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

> 喵~ 提醒：`generate` 需要先配置模型（见[接入大模型](#接入大模型)）；只做解析/锚点/导出不需要模型。

---

## 使用流程

| 步骤 | 操作 | 说明 |
|---|---|---|
| 1. 导入 | UI「导入讲义」/ 拖拽 PDF·PPTX / `cli.py ingest` | 自动分章；扫描件自动 OCR（+视觉补识别） |
| 2. 追加 | UI「追加讲义」（选中教材后出现） | 新讲义接到本书尾部；已有笔记与生成记录原样保留 |
| 3. 生成 | UI「生成本章」或「全书生成」 | 提纲 → 逐块填充，带来源校验与缓存；全书生成默认跳过已生成章节 |
| 4. 修改 | 卡片 / 源码 / 预览；「✨AI 改」；「🔗关联」 | 保存即产生新版本；人工块受保护，AI 只出建议 |
| 5. 复习 | 「待审核」（带未审数量角标） | 确认 AI 块 / 处理失败块；失败块可被「继续生成」自动补全 |
| 6. 自测 | 「AI 出题」 | 选题型/题量/难度，可先传往年题做题库 |
| 7. 导出 | 「导出」→ 选格式与范围 | md / tex / Obsidian / Anki；完成后弹窗给出文件位置 |

---

## 学科模板与自定义块类型

课本不都按「定义 → 定理 → 证明」组织，所以狐狐准备了六套模板喵~

| 模板 | 块类型 |
|---|---|
| 数学 / 教材 | 定义 · 定理 · 动机 · 图示 · 延伸链接 |
| 论文 | 上面五类 + 贡献 · 方法 · 实验 |
| 物理 | 定律 · 现象 · 实验（+ 通用五类） |
| 计算机 | 算法 · 数据结构 · 复杂度（+ 通用五类） |
| 医学 | 症状 · 诊断 · 治疗（+ 通用五类） |
| 化学 | 物质 · 反应 · 性质（+ 通用五类） |

每个类型都带**专属写作指引**，直接进生成提示词——医学的「剂量单位不得改动」、计算机的「算法：伪代码 + 复杂度」，不再拿"定义/定理"的模子套所有学科。

**自定义块类型**（设置 → 笔记模板 → 自定义块类型 → 「+ 添加类型」）：

| 字段 | 作用 |
|---|---|
| id | 英文标识（`case`、`lemma`…；同 id 可**覆盖预设**，比如只改"定理"的写作要求） |
| 名称 / 图标 | 界面显示（卡片图标自动跟随） |
| 写作指引 | 进生成提示词的写作要求（如「病例：主诉/现病史/处置」） |
| 来源策略 | 需来源 / 来源可选 / 无来源——校验器会**强制**执行（选"需来源"就绝不敢无据生造） |

配置写在 `config.yaml → generate.block_types`，全链路同一事实源：生成 schema 枚举、提示词指引、校验器、前端图标。

> 导入教材时使用当前模板（`generate.profile`）；不同学科的书建议在设置里切换模板后再导入。

---

## AI 出题与参考题库

工具栏「**AI 出题**」——期末考试前狐狐帮你出卷子喵~

| 选项 | 内容 |
|---|---|
| 范围 | 仅当前章节 / 整本综合 |
| 题型（多选） | 选择题 · 填空题 · 计算题 · 简答题 · 证明题 |
| 题量 | 5 / 10 / 15 / 20 道 |
| 难度 | 基础 / 期末 / 考研 |

**参考题库**：上传往年题、课后习题（PDF/txt/md）→ 自动按段落入库 `data/<doc_id>/qbank.jsonl`（去重）。出题时取样作为**风格与考点样本**，并明令禁止照抄原题的数字与文字。传两套往年卷，出的题就贴着那个路子来。

两条纪律：

1. **只考笔记范围**——答案必须能从你的笔记推出，不考没学过的内容
2. **结构化校验**——选择题必须带选项与唯一答案、解析字段；格式不合法不落盘

题目存档在 `data/<doc_id>/quiz/<quiz_id>.json`，弹窗里逐题「显示答案」+ 解析，可以「再出一套」。出题模型用 `generate.models.quiz`（留空则用 fill/default）。

---

## 单块 AI 编辑与知识点互链

**✨AI 改**（卡片右上角 / 放大层标题栏）：

1. 点一个快捷指令（更精简 / 更详细 / 更准确 / 更通俗）或自己写指令
2. 狐狐生成**修改建议**——**不落盘**，下面可以「对比原文」，建议还能再手改
3. 点「**应用修改**」才写入：这是**显式人工动作**（`author=human`），原内容进历史，随时可回滚

> 规范红线（5.2）：**机器不得覆盖人工确认的内容**。所以 AI 只出建议、人来拍板；`manually_edited` 的块对 agent 只读，要先解锁。

**🔗关联**：搜索任意块（**可跨章**）一键互链，已链的显示为 chip 可点可删；卡片上显示 `🔗N` 计数。Obsidian 导出会渲染成 `[[双链]]`。

---

## 命令行参考

| 命令 | 用途 | 常用参数 |
|---|---|---|
| `ingest <pdf>` | 解析 PDF | `--doc-id` `--title` `--profile textbook\|paper` `--parser auto\|marker\|docling\|pypdf` `--ocr` |
| `anchor <doc_id>` | 生成锚点索引 | `--force` |
| `generate <doc_id>` | 生成笔记块 | `--chapter` `--model` `--language zh\|en\|ja` `--concurrency` `--force` `--no-cache` `--merge/--replace --yes` |
| `link <doc_id>` | 反向索引 + 块间链接 | `--chapter` |
| `export <doc_id>` | 导出 | `--chapter` `--format md\|tex\|both\|md-obsidian\|anki` `--out` `--encoding utf-8\|utf-8-sig\|gbk\|big5` `--language` |
| `run <pdf>` | 一键全链路 | 以上参数透传 |
| `reindex <doc_id>` | 重建 SQLite 索引 | `--embed`（先算向量） |
| `report <doc_id>` | 汇总各阶段报告 | 含死链/保护跳过清单 |
| `eval <doc_id>` | 生成质量评测 | `--update-baseline`（固化基线） |
| `backup <doc_id>` | 备份（含校验和） | `--out` |
| `delete <doc_id>` | 删除教材（移入回收站） | `--yes`（必填） |
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
  default: auto            # auto（按需分流）| marker | docling | pypdf
  ocr_mode: auto           # auto（仅无文字层页）| force（全页）| off
  ocr_vision: auto         # 视觉补 OCR：弱识别页交视觉模型重识别（auto=有视觉能力模型就启用）| off
  python: null             # 解析器 venv 解释器（见下文）
chapters_override: []      # 手动章区间覆盖（最高优先级）
generate:
  profile: textbook        # 笔记模板：textbook/paper/physics/cs/medicine/chemistry（或自定义名）
  block_types: []          # 自定义块类型：[{id,name,icon,hint,source_policy,profiles}]
  models:                  # 任务 → 模型路由（provider/model 或唯一短名）
    default: "deepseek/deepseek-flash"
    outline: null          # 提纲：可指便宜的模型
    fill: null             # 填充：调用量最大
    agent: null            # 必须带 tools 能力
    link: null
    ocr: null              # 视觉补 OCR（需 vision 能力；空则自动选 fill/default 里有视觉的）
    quiz: null             # AI 出题（空则用 fill/default）
  output_language: zh      # zh | en | ja
  outline_max_in: 32000
  fill_max_in: 5000        # 单次填充输入预算（超出先裁剪来源原文）
  fill_batch: 4            # 小块合批：N 块一次调用（失败自动回退逐块）
  outline_batch_out: 6000  # 提纲分批目标（批越小首批越快）
  chapter_concurrency: 3   # 整本生成并行章数（写入仍串行）
  max_concurrent_requests: 10  # 全局在途请求上限（防触发供应商限流）
  reasoning_effort: low    # 推理深度（low 显著提速）
  thinking: disabled       # 思考开关（deepseek-flash 实测 disabled 快 5 倍）
  max_cost_per_chapter: 0.0   # 美元，0=不限
  temperature: 0.2
  quiz_max_out: 8000       # 出题单次输出上限
export:
  encoding: utf-8          # utf-8 | utf-8-sig | gbk | big5（tex 恒为 UTF-8）
embedding:
  provider: none           # none | onnx_bge | hashed_lexical
cache: { dir: "cache/gen" }
match: { contain_ratio: 0.8, cover_ratio: 0.5, top_k: 8 }
history: { keep_recent: 50 }
```

> 也可在 **设置** 里改（模板/自定义类型/解析器/OCR/语言/编码/路由），保存即原子写回 `config.yaml`。

## 解析器与 OCR

狐狐会自动挑解析器：**有文字层的数字版 PDF 走 pypdf 秒级路径**，扫描书才动用重型解析器，喵~

| 解析器 | 特点 | 实测（本机，见 11.0 报告） |
|---|---|---|
| **pypdf** | 内置、极快、零依赖（数字版首选/兜底） | 5/5 通过，百页秒级 |
| **marker** | 结构/公式/旋转页质量最好 | 5/5 样本通过，坐标合法率 100% |
| **docling** | 复杂双栏/论文结构强 | 4/5（旋转页坐标异常时按规范拒绝该页） |
| **OCR rapidocr** | 纯图片/扫描页本地识别 | 2 页扫描件 6 秒；百页书约 3-8 秒/页 |
| **视觉补 OCR** | 本地认不好的页（乱码/公式堆）交视觉模型 | 实测真实扫描页 3.9 秒 → 15 行带定位转录 |

**兜底链**：`marker → docling → pymupdf → pypdf` 逐级降级；**「成功但零文本」不算成功**（继续试下一个），全链都无文字层则交给 OCR 环节——这正是纯图片书的正常入口。

**零文本闸**：解析完如果整本 0 块，任务会**响亮报错**并给建议（检查 `.venv-parsers`/OCR 或换解析器），绝不会静默产出一本空书。

重型解析器装在**独立虚拟环境**（不污染主环境）：

```bash
python -m venv .venv-parsers
.venv-parsers/Scripts/python -m pip install marker-pdf docling rapidocr onnxruntime pypdfium2
# config.yaml → parser.python: ".venv-parsers/Scripts/python.exe"
python scripts/tech_validation_11.py      # 生成实测报告
```

**已知限制**

- 扫描书 OCR 很慢（294 页约 25 分钟）——导入任务跑着的时候别关软件
- 视觉补 OCR 需要**有余额的视觉模型**；每页约 1-2k token，可用 `parser.ocr_vision: off` 关闭
- Pix2Text 公式 OCR 依赖 `rapidocr<2`（仅支持 Python ≤ 3.12），3.13 下自动跳过并告警
- docling 默认后端在**含中文的安装路径**下会因资源加载失败；已自动回退 pypdfium2 后端

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
          "capabilities": { "json_schema": true, "tools": true, "vision": true },
          "pricing": { "input_per_million": 0.27, "output_per_million": 1.1, "currency": "USD" } }
      ]
    }
  ]
}
```

- **本地模型**：Ollama（`base_url: http://127.0.0.1:11434/v1`）、vLLM 等同理
- **能力标志驱动行为**：`json_schema` 决定结构化输出路径；`tools` 是 agent 编辑的前提；`vision` 决定能否做视觉补 OCR / 图片理解
- **密钥**：`api_key_env`（环境变量，推荐）或 `api_key`（本地明文，接口只回显末 4 位）
- **成本控制**：`max_cost_per_chapter` 在每次调用前原子预留；价格未知的模型在启用预算时被拒（不假装免费）
- **并发自适应**：供应商的并发上限按余额档位给（打满会 429）；客户端会自适应降档并退避重试，**并发类 429 不会被误判成余额耗尽**
- **缓存联动**：换模型/换提示词后缓存键自动失效，不会重复收费也不会拿旧结果糊弄你

---

## 数据与目录布局

```
note-gen/
├── data/
│   ├── runtime.db                 # 不可重建：任务/用量/agent 会话
│   ├── models.json                # 模型注册表（含密钥，勿入库）
│   ├── textbooks/{doc_id}.pdf|pptx # 原文件
│   └── {doc_id}/                  # ← 文档事实源
│       ├── manifest.json          # 页面几何/章节/阶段指纹/data_revision
│       ├── anchors/{ch}.jsonl     # 锚点（段落/公式/图/表 + bbox + 哈希）
│       ├── blocks/{ch}.jsonl      # 笔记块（唯一编辑对象）
│       ├── history/{block_id}.jsonl  # 版本快照
│       ├── qbank.jsonl            # 参考题库（AI 出题用）
│       ├── quiz/{quiz_id}.json    # 出过的卷子
│       ├── figures/  refs/  index.db
├── ingest_out/  cache/  reports/  notes_out/   # 均可重建
└── viewer/                        # React + PDF.js 前端
```

**不变量**：JSONL 是唯一事实源，SQLite/导出/缓存可随时重建；`cli.py reindex` 修复索引层，`cli.py link` 修复反向索引层。

---

## 架构概览

```
表现层   Viewer（React + PDF.js，双向对照）
应用层   FastAPI（块 CRUD/乐观锁 · 对照检索 · 生成触发 · AI 编辑/关联/出题 · 导出 · agent 两步编辑）
管线层   ingest → anchor → generate → link → export（每阶段可独立重跑、幂等）
数据层   JSONL 主存 + SQLite 派生索引 + 文件系统（图/原文件）
```

关键设计（细节见规范文档）：

- **可恢复提交**：跨文件变更走 intent → COMMIT → DONE 日志，崩溃后幂等前滚
- **坐标契约**：存未旋转的原始 PDF 用户空间，前端只用 PDF.js viewport 正/逆变换（旋转/CropBox/UserUnit 不会算两次）
- **再生成合并**：身份键匹配 + 保护位 + 墓碑抑制，人工成果永不丢
- **锚点身份 = 内容哈希**：解析/分章重建后锚点身份不变，生成缓存与报告继续有效

---

## 测试与质量

```bash
python -m pytest -q                       # 340 项单元/集成测试（1 项按环境跳过）
python -m ruff check --select F,E9 src tests   # 静态检查
python scripts/edge_case_probe.py         # 边界探测（异常输入/错误码）
python scripts/edge_case_probe2.py        # 边界探测（agent/备份/任务/约束）
python scripts/tech_validation_11.py      # 解析器技术验证
python cli.py eval <doc_id>               # 生成质量评测（三维度 + 基线回归告警）
```

评测维度：**faithfulness**（忠实度）/ **formula**（公式正确率）/ **coverage**（锚点覆盖率）；任一维度低于基线 5 个百分点即告警（防"改 prompt 改坏质量"）。

> 狐狐的规矩：**每个 bug 修复都要带回归测试**，并且测试要能变红（先证明它能抓到问题）。涉及 worker 子进程的行为，必须有 worker 级测试——只靠 monkeypatch 的单测会漏掉真问题，喵。

---

## 常见问题（排错）

| 现象 | 原因与处理 |
|---|---|
| 任务显示「**部分完成 / 失败**」 | 任务卡与章节报告里都写了原因。**余额不足（HTTP 402）/ 密钥失效**属全局故障：狐狐会立即中止余下章节并提示「已完成 N 章，充值后点继续生成会自动跳过已完成章节」 |
| 生成按钮点了没反应 / 说没有锚点 | 该书可能没解析出内容：看任务报告。整本 **0 块**时解析会直接报错（不会静默出空书）；纯图片书请确认 OCR 可用 |
| 章节列表混进小节（如「1.4策略」当章） | 电子书**扁平书签**+标题粘目录页码导致。已修复（只认「第X章」样式、剥页码、剔控制字符）。重新导入，或在任务里「重建导入」 |
| 某章笔记「后面就没了」 | 章块列表接口是分页的（默认 100），界面此前只加载首页——**已修复为自动翻页取全**；刷新页面即可看到全部卡片 |
| 来源行显示 `p.?` | 详情面板此前没预取锚点（卡片/放大层有）——已修复；刷新页面 |
| 待审核里有一堆「生成失败」 | 那是**当时的**失败快照（行内已标记录时间）。它不是终点：`generation_protected=false`，点「继续生成」会用缓存补全这一块（其余块零成本） |
| 扫描件导入后是空的 | 检查 设置 → OCR 模式 是否为 `off`；纯图片书依赖 `.venv-parsers` 里的 rapidocr。整本零文本现在会明确报错 |
| 导入卡在「解析/OCR」很久 | 扫描书 OCR 约 3-8 秒/页（294 页 ≈ 25 分钟），进度条会显示页数；跑着的时候**别关软件** |
| 导入任务被我关软件打断了 | 任务持久化在 `runtime.db`：重开软件后在任务托盘「续跑/继续」即可 |
| 提示"文档已存在" | 同一文件重复导入会被拒绝以保护已有笔记；确认后加 `overwrite=true` 重建 |
| 误删了教材 | 删除走回收站：`data/.trash/<doc_id>-<时间戳>/`，把该目录移回 `data/` 即可恢复 |
| 配置指南没弹出来 | 点工具栏「配置指南」；自动弹出只在首次（未接模型）触发，关闭状态记在 localStorage（`note-gen-setup-seen`）；新人教学向导的标记是 `note-gen-onboarded` |
| 生成提示"未配置生成模型" | 设置 → 任务路由 指定模型，或改 `config.yaml generate.models.default` |
| agent 按钮不可用 | 需要带 `tools` 能力的模型 |
| 导出的 .tex 编译不了 | tex 恒为 UTF-8；请用 `xelatex` 并保证 `ctex` 宏包可用 |
| 两个窗口同时改同一块 | 后者会收到 409（乐观锁），刷新后重试 |
| 端口被占用 | `启动自动笔记软件.bat 9000` 换端口 |

---

## 安全说明

- 服务**只绑定 127.0.0.1**；所有 `/api` 请求需 `X-Local-Token`（页面 `<meta>` 注入 / Vite 代理注入）
- **CSRF 防护**：校验精确 Host/Origin/Fetch-Metadata，拒绝 `Origin:null` 与跨站请求
- **Prompt 注入防护**：PDF 原文一律放 `<source_data>` 数据分隔区，其中的指令无效；服务端再做锚点白名单校验
- **渲染安全**：Markdown 关闭原始 HTML；TeX 导出不接受外部包注入
- **路径安全**：doc_id/chapter_id/figure_id 严格白名单 + resolve 校验；恢复备份拒绝 Zip Slip

---

## 路线图

- [x] MVP：单章解析 → 生成 → 编辑 → 导出 → 双向定位
- [x] v1：UI 导入/任务/检索、CRUD/回滚、安全边界、备份恢复
- [x] v1.5：论文 profile、多模型管理、TeX/Obsidian 导出
- [x] v2 增量：语义检索、Anki 闪卡、评测回归、扫描件 OCR、GPU 解析
- [x] 近期迭代：学科模板与自定义块类型 · AI 出题与参考题库 · 单块 AI 编辑 · 知识点互链 ·
      PPTX 讲义与追加 · 视觉补 OCR · 新人引导 · 全书生成跳过已生成章节 · 可解释失败
- [ ] 下一步：增量重解析自动修复、跨书链接、Electron 打包

---

## 开发

```bash
python -m pytest -q            # 后端测试（改动后必跑）
python -m ruff check --select F,E9 src tests
cd viewer && pnpm dev          # 前端开发（/api 代理到 8787）
cd viewer && pnpm build        # 生产构建 → viewer/dist
```

约定：

- 字段名/枚举/路径是规范的一部分；行为与文档冲突时**先改文档并登记 ADR** 再改代码
- 后端改动要重启服务（`启动自动笔记软件.bat --no-browser`）才生效；**纯前端改动构建后刷新页面即可**
- 重启前确认没有正在跑的任务（导入/生成中途重启会打断它）

---

喵~ 就写到这里啦 🦊

> 狐狐小传：我是**一只会喵喵叫的狐狐**，平时的爱好是把厚厚的教材拆成一块块能点回原文的小知识，
> 以及在你想改一句话的时候，先递上草稿而不是直接动笔。
> 有任何一块笔记让你不满意，点开它，喊一声「✨AI 改」——狐狐在的，喵！
