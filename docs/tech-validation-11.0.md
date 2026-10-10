# 11.0 技术验证报告

生成时间：2026-10-10T00:41:40Z（UTC）

## 0. 环境

| 项 | 值 |
|---|---|
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.5 |
| CPU | AMD64 Family 26 Model 68 Stepping 0, AuthenticAMD |
| 内存 | 见本机配置（峰值内存为 tracemalloc 进程内口径） |
| GPU | 未启用（解析器默认 CPU 路径） |

## 1. 解析器可用性（本机实测）

| 解析器 | 可用 | 版本/原因 |
|---|---|---|
| pypdf | 是 | 6.8.0 |
| marker | 是 | unknown |
| docling | 是 | 2.137.0 |

## 2. 样本 × 解析器 实测结果

| 样本 | 解析器 | 状态 | 用时(s) | 峰值内存(MB) | 页(ok/blank/failed) | 锚点 | 公式锚点 | 坐标合法率 | 阅读序 |
|---|---|---|---|---|---|---|---|---|---|
| layout-plain.pdf | pypdf | ok | 0.56 | 5.1 | 1/0/0 | 4 | 0 | 1.0 | 1/1 |
| layout-plain.pdf | marker | ok | 26.92 | 1.1 | 1/0/0 | 4 | 0 | 1.0 | 1/1 |
| layout-plain.pdf | docling | ok | 30.51 | 1.1 | 1/0/0 | 3 | 0 | 1.0 | 1/1 |
| layout-two-column.pdf | pypdf | ok | 0.17 | 1.1 | 1/0/0 | 8 | 0 | 1.0 | 1/1 |
| layout-two-column.pdf | marker | ok | 26.26 | 1.0 | 1/0/0 | 1 | 0 | 1.0 | 1/1 |
| layout-two-column.pdf | docling | ok | 30.77 | 1.0 | 1/0/0 | 1 | 0 | 1.0 | 1/1 |
| layout-rotated.pdf | pypdf | ok | 0.17 | 1.1 | 2/0/0 | 4 | 0 | 1.0 | 2/2 |
| layout-rotated.pdf | marker | ok | 26.58 | 1.1 | 2/0/0 | 4 | 0 | 1.0 | 2/2 |
| layout-rotated.pdf | docling | failed | 31.07 | 0.1 | 0/0/0 | 0 | - | - | - |
| layout-formula.pdf | pypdf | ok | 0.23 | 1.1 | 1/0/0 | 2 | 0 | 1.0 | 1/1 |
| layout-formula.pdf | marker | ok | 26.48 | 1.1 | 1/0/0 | 2 | 0 | 1.0 | 1/1 |
| layout-formula.pdf | docling | ok | 30.82 | 1.0 | 1/0/0 | 2 | 0 | 1.0 | 1/1 |
| sample.pdf | pypdf | ok | 0.28 | 1.1 | 3/0/0 | 14 | 0 | 1.0 | 3/3 |
| sample.pdf | marker | ok | 26.96 | 1.1 | 3/0/0 | 14 | 0 | 1.0 | 3/3 |
| sample.pdf | docling | ok | 33.02 | 1.1 | 3/0/0 | 10 | 0 | 1.0 | 3/3 |

## 3. 失败与告警

- **layout-rotated.pdf / docling**：第 1 页块 0 坐标非法: bbox 越出 view_box [0.0, 0.0, 612.0, 792.0]: [627.822021484375, 72.25299835205078, 711.4559936523438, 289.94293212890625]

## 4. 结论（仅限本机与本样本集）

- 坐标精度：bbox 越界率 0% 视为通过（4.3 允许 0.5 单位误差）；旋转页需与 PDF.js 叠框比对后复核。
- 阅读序：reading_order 页内唯一递增为必要条件；双栏乱序需人工抽查（R1）。
- 公式：公式锚点数量仅作参考，语义正确率需按 11.1 抽样人工评分。
- 首次运行会下载解析器模型权重（surya/docling），耗时与显存占用见各自日志；本报告只记录端到端墙钟时间。
- **解析器选型结论**以本表为准，未通过项必须先修（11.0 末条）。

## 5. 已知限制与处置（本机实测）

- **docling 默认后端（docling-parse）** 在含非 ASCII 字符的 Windows 安装路径下无法加载自带字形资源（`pdf_resources/glyphs/standard/additional.dat`）；适配器已自动回退 `PyPdfiumDocumentBackend` 并记入 warnings。
- **docling 旋转页（/Rotate 90）** 返回显示坐标系 bbox，本适配器未做旋转换算；按 4.3「禁止猜 scale/rotation」该页拒绝写入（见 layout-rotated 失败行）。处置：旋转页用 marker/pypdf，或补齐 docling 旋转换算后再启用。
- **Pix2Text 公式 OCR 兜底** 依赖 `rapidocr<2`（仅支持 Python ≤ 3.12），Python 3.13 下无法安装；兜底自动跳过并告警，解析器自身仍产出公式块。
- **选型结论（本机实测）**：textbook 默认解析器建议 **marker**（结构/坐标/旋转全覆盖，5/5 通过）；**pypdf** 作为离线兜底与快速路径（5/5）；**docling** 适合复杂双栏/论文但注意旋转页（4/5）。

