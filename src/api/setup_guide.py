"""配置指南（首次使用引导）：环境自检 + 配置就绪度（向导数据源）。

设计要点（ADR D35）：
- **只读自检**：本模块不改任何配置，纯函数 build_setup_status(state) 便于单测；
- 检查项三级：ok / warn / fail，只有 fail 才阻塞就绪（重型解析器未装只 warn）；
- 未知价格 + 非零预算**提前暴露** PRICE_UNKNOWN（5.3.5），不要等到生成中途才失败；
- 重型解析器（marker/docling/pix2text/rapidocr）装在独立 venv（ADR D31），
  这里只查 site-packages 目录是否存在，不 spawn 子进程（自检要即时返回）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, Request

from src.core.config import ENCODINGS, OUTPUT_LANGUAGES

router = APIRouter()

MIN_PYTHON = (3, 11)

# 向导步骤（顺序即引导顺序）；required=False 的步骤不阻塞 ready
STEP_DEFS: list[dict] = [
    {"id": "env", "title": "环境自检", "required": True,
     "summary": "Python 版本、数据目录可写、本地安全令牌已生成"},
    {"id": "model", "title": "接入模型", "required": True,
     "summary": "添加供应商（base_url / API Key）与至少一个模型——生成笔记必须"},
    {"id": "routes", "title": "任务路由", "required": False,
     "summary": "把提纲 / 填充 / agent / 链接分配到具体模型（留空回退 default）"},
    {"id": "parser", "title": "解析器与 OCR", "required": True,
     "summary": "选择 PDF 解析器与扫描页 OCR 策略"},
    {"id": "output", "title": "输出语言与编码", "required": True,
     "summary": "笔记语言（zh/en/ja）与导出编码"},
    {"id": "budget", "title": "成本与预算", "required": False,
     "summary": "并发、单章成本上限与模型价格（价格未知无法启用预算）"},
]

SLOT_LABELS = {"default": "默认", "outline": "提纲", "fill": "填充",
               "agent": "agent 编辑", "link": "链接"}


def state(request: Request):
    return request.app.state.app_state


# --------------------------------------------------------------------------- 自检工具
def _spec_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _parser_python(root: Path, parser_cfg: dict) -> Path | None:
    """解析器解释器：显式配置 > .venv-parsers > None（当前解释器）。"""
    raw = parser_cfg.get("python")
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = root / p
        return p if p.exists() else None
    for cand in (root / ".venv-parsers" / "Scripts" / "python.exe",
                 root / ".venv-parsers" / "bin" / "python",
                 root / ".venv-parsers" / "bin" / "python3"):
        if cand.exists():
            return cand
    return None


def _pkg_in_venv(python: Path | None, pkg: str) -> bool:
    """该解释器环境里是否装了 pkg（只查目录，不起子进程）。"""
    if python is None:
        return _spec_available(pkg)
    base = python.parent.parent
    for pat in (f"Lib/site-packages/{pkg}", f"lib/python*/site-packages/{pkg}"):
        if any(base.glob(pat)):
            return True
    return False


def _key_source(provider: dict, registry) -> tuple[bool, str, str | None]:
    """(是否已配置, 来源 env|plain|none, 掩码值)。"""
    masked = registry.masked_provider(provider).get("api_key")
    if provider.get("api_key_env") and registry.environ.get(provider["api_key_env"]):
        return True, "env", masked
    if provider.get("api_key"):
        return True, "plain", masked
    return False, "none", None


def _make_check(cid: str, level: str, title: str, detail: str, fix: str | None = None) -> dict:
    return {"id": cid, "level": level, "title": title, "detail": detail, "fix": fix}


# --------------------------------------------------------------------------- 主构建
def build_setup_status(state_obj) -> dict:
    """配置就绪度报告：steps（向导步骤）+ checks（环境自检）+ 当前配置摘要。"""
    cfg = state_obj.config
    root: Path = state_obj.root
    layout = state_obj.layout
    registry = state_obj.registry
    parser_cfg = cfg.get("parser", {}) or {}
    gen_cfg = cfg.get("generate", {}) or {}
    routes = gen_cfg.get("models", {}) or {}

    checks: list[dict] = []

    # ---- 环境 ----
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    checks.append(_make_check(
        "python", "ok" if py_ok else "fail", "Python 运行时",
        f"{sys.version.split()[0]}（要求 >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}）",
        None if py_ok else "请安装 Python 3.11 或更高版本"))

    data_writable = layout.data_dir.exists() and layout.data_dir.is_dir()
    if data_writable:
        try:
            probe = layout.data_dir / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            data_writable = False
            data_detail = f"data/ 不可写：{exc}"
    if data_writable:
        data_detail = f"{layout.data_dir} 可写"
    checks.append(_make_check(
        "data_dir", "ok" if data_writable else "fail", "数据目录",
        data_detail,
        None if data_writable else "检查 data/ 目录权限（笔记事实源都存放在那里）"))

    token_ok = (layout.data_dir / ".local_token").exists()
    checks.append(_make_check(
        "local_token", "ok" if token_ok else "warn", "本地安全令牌",
        "data/.local_token 已生成（10.6.5 本地 CSRF 防护）" if token_ok
        else "启动服务后自动生成 data/.local_token",
        None if token_ok else "启动一次服务（python cli.py serve）即可生成"))

    # ---- 解析器 ----
    py_path = _parser_python(root, parser_cfg)
    pypdf_ok = _spec_available("pypdf")
    checks.append(_make_check(
        "parser_pypdf", "ok" if pypdf_ok else "fail", "内置解析器 pypdf",
        "主环境可用（快速路径与兜底）" if pypdf_ok else "主环境缺少 pypdf",
        None if pypdf_ok else "python -m pip install -r requirements.txt"))

    marker_ok = _pkg_in_venv(py_path, "marker")
    checks.append(_make_check(
        "parser_marker", "ok" if marker_ok else "warn", "marker 解析器（推荐）",
        f"已安装于 {py_path}" if marker_ok else
        ("未检测到 marker（结构/公式/旋转页质量最好）" if py_path else "未配置 .venv-parsers"),
        None if marker_ok else
        'python -m venv .venv-parsers && .venv-parsers\\Scripts\\python -m pip install marker-pdf'))

    docling_ok = _pkg_in_venv(py_path, "docling")
    checks.append(_make_check(
        "parser_docling", "ok" if docling_ok else "warn", "docling 解析器",
        f"已安装于 {py_path}" if docling_ok else "未检测到 docling（复杂双栏/论文结构强）",
        None if docling_ok else
        '.venv-parsers\\Scripts\\python -m pip install docling'))

    ocr_ok = _pkg_in_venv(py_path, "rapidocr") or _spec_available("rapidocr")
    checks.append(_make_check(
        "ocr", "ok" if ocr_ok else "warn", "扫描页 OCR（rapidocr）",
        "可用（纯图片/扫描页自动识别）" if ocr_ok else "未检测到 rapidocr（扫描件将无法 OCR）",
        None if ocr_ok else
        '.venv-parsers\\Scripts\\python -m pip install rapidocr-onnxruntime'))

    pix_ok = _pkg_in_venv(py_path, "pix2text")
    checks.append(_make_check(
        "formula_ocr", "ok" if pix_ok else "warn", "公式 OCR 兜底（pix2text）",
        "可用" if pix_ok else
        f"未安装（可选；依赖 rapidocr<2，仅 Python <= 3.12 支持，当前 {sys.version.split()[0]}）",
        None if pix_ok else
        '.venv-parsers\\Scripts\\python -m pip install "pix2text"'))

    # ---- 模型与密钥 ----
    prov_rows = []
    any_keyed = False          # 至少有一个供应商「密钥 + 模型」齐备，可直接路由
    for p in registry.providers():
        has_key, source, masked = _key_source(p, registry)
        models = p.get("models") or []
        prov_rows.append({
            "id": p.get("id"), "label": p.get("label") or p.get("id"),
            "base_url": p.get("base_url"), "model_count": len(models),
            "models": [m.get("id") for m in models],
            "key_configured": has_key, "key_source": source, "key_masked": masked,
        })
        if has_key and models:
            any_keyed = True

    # 已配密钥但一个模型都没登记：密钥配了也用不上（旧实现与「完全没配密钥」混为一谈）
    key_only = [r["id"] for r in prov_rows if r["key_configured"] and not r["model_count"]]

    checks.append(_make_check(
        "models", "ok" if any_keyed else "fail", "模型接入",
        ("已接入 " + ", ".join(f"{r['id']}({r['model_count']})" for r in prov_rows if r["model_count"]))
        if any_keyed else "尚无「已配置密钥 + 含模型」的供应商——生成笔记需要至少一个",
        None if any_keyed else (
            "、".join(key_only) + " 已配置密钥但没有模型 id，先给它补至少一个模型（否则路由里选不到）"
            if key_only else "在下一步「接入模型」中添加供应商、API Key 与模型")))

    checks.append(_make_check(
        "keys", "ok" if any_keyed else "warn", "API Key",
        "已配置（环境变量或本地明文，接口只回显末 4 位）" if any_keyed else (
            "已配置 " + "、".join(key_only) + " 的密钥，但这些供应商未登记模型"
            if key_only else "尚未配置任何 API Key"),
        None if any_keyed else (
            "先补模型 id，再点「测试」验证密钥；返回 401 说明密钥无效，需换新密钥"
            if key_only else "推荐用环境变量（api_key_env），避免明文落盘")))

    # ---- 路由 ----
    default_ok = bool(routes.get("default"))
    checks.append(_make_check(
        "routes", "ok" if default_ok else "warn", "任务路由",
        ("default=" + str(routes.get("default"))) if default_ok else
        "default 未指定（将按注册顺序回退），建议显式指定",
        None if default_ok else "在「任务路由」一步为 default 选择模型"))

    agent_slot = routes.get("agent")
    if agent_slot:
        try:
            caps = registry.capabilities(agent_slot)
            tools_ok = bool(caps.get("tools"))
            detail = f"agent → {agent_slot}（tools 能力 {'有' if tools_ok else '缺失'}）"
        except Exception as exc:                       # 模型已被删除/改名
            tools_ok = False
            detail = f"agent → {agent_slot} 解析失败：{exc}"
        checks.append(_make_check(
            "agent_tools", "ok" if tools_ok else "fail", "agent 模型能力",
            detail,
            None if tools_ok else "agent 编辑必须选择具备 tools 能力的模型（否则 UI 会禁用）"))

    # ---- 预算与价格 ----
    budget = float(gen_cfg.get("max_cost_per_chapter") or 0.0)
    routed = [v for v in routes.values() if v]
    unpriced = []
    for name in dict.fromkeys(routed):
        try:
            if registry.pricing(name) is None:
                unpriced.append(name)
        except Exception:
            unpriced.append(name)
    if budget > 0 and unpriced:
        level, detail = "warn", (f"已启用单章预算 {budget} USD，但价格未知：{', '.join(unpriced)}"
                                 "（5.3.5：启用非零预算时会被 PRICE_UNKNOWN 拒绝）")
        fix = "为这些模型配置 pricing（input_per_million/output_per_million），或把预算设为 0"
    elif budget > 0:
        level, detail, fix = "ok", f"单章预算 {budget} USD，已路由模型价格均已知", None
    else:
        level, detail, fix = "ok", "未启用成本上限（max_cost_per_chapter=0）", None
    checks.append(_make_check("price", level, "成本与价格", detail, fix))

    # ---- 步骤完成度 ----
    env_done = py_ok and data_writable
    selected_parser = parser_cfg.get("default") or "auto"
    parser_avail = {"pypdf": pypdf_ok, "marker": marker_ok,
                    "docling": docling_ok, "auto": pypdf_ok or marker_ok or docling_ok}
    parser_done = parser_avail.get(selected_parser, False)
    output_done = (gen_cfg.get("output_language") in OUTPUT_LANGUAGES
                   and (cfg.get("export", {}) or {}).get("encoding") in ENCODINGS)
    budget_done = not unpriced

    done_map = {
        "env": env_done,
        "model": any_keyed,
        "routes": default_ok,
        "parser": parser_done,
        "output": output_done,
        "budget": budget_done,
    }
    detail_map = {
        "env": f"Python {sys.version.split()[0]} · data/ {'可写' if data_writable else '不可写'}",
        "model": (", ".join(f"{r['id']}（{r['model_count']} 个模型）" for r in prov_rows)
                  if prov_rows else "尚未添加供应商"),
        "routes": (", ".join(f"{SLOT_LABELS.get(k, k)}→{v}" for k, v in routes.items() if v)
                   or "全部回退 default"),
        "parser": f"解析器 {selected_parser}（{'可用' if parser_done else '不可用'}） · "
                  f"OCR {parser_cfg.get('ocr_mode', 'auto')}",
        "output": f"{gen_cfg.get('output_language', 'zh')} · "
                  f"{(cfg.get('export', {}) or {}).get('encoding', 'utf-8')}",
        "budget": (f"并发 {gen_cfg.get('concurrency', 2)} · "
                   f"单章上限 {budget if budget else '不限'} USD · "
                   f"温度 {gen_cfg.get('temperature', 0.2)}"
                   + (f" · 价格未知：{', '.join(unpriced)}" if unpriced else "")),
    }
    steps = [{**s, "done": done_map[s["id"]], "detail": detail_map[s["id"]]} for s in STEP_DEFS]

    ready = all(done_map[s["id"]] for s in STEP_DEFS if s["required"])
    first_run = not (root / "config.yaml").exists() and not any_keyed

    return {
        "ready": ready,
        "first_run": first_run,
        "steps": steps,
        "checks": checks,
        "providers": prov_rows,
        "routes": routes,
        "slots": [{"id": k, "label": v} for k, v in SLOT_LABELS.items()],
        "settings": {
            "parser": parser_cfg,
            "generate": {k: v for k, v in gen_cfg.items() if k != "models"},
            "export": cfg.get("export", {}),
        },
        "parser_python": str(py_path) if py_path else None,
        "parser_available": parser_avail,
        "options": {
            "parsers": ["auto", "marker", "docling", "pypdf"],
            "ocr_modes": ["auto", "force", "off"],
            "languages": list(OUTPUT_LANGUAGES),
            "encodings": list(ENCODINGS),
        },
    }


@router.get("/api/v1/setup/status")
def setup_status(request: Request):
    """配置指南数据源：环境自检 + 配置就绪度 + 当前配置摘要（只读）。"""
    return build_setup_status(state(request))
