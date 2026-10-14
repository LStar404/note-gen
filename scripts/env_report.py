"""环境自检喵：Python/依赖/GPU（N 卡·核显·A 卡）/密钥安全一次看清。

只读脚本：不改任何文件、不发任何网络请求（除了 git 本地查询）。
用法：python scripts/env_report.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv-parsers" / "Scripts" / "python.exe"
VENV_PY_POSIX = ROOT / ".venv-parsers" / "bin" / "python"
PARSER_PKGS = ("torch", "torchvision", "onnxruntime", "rapidocr", "pymupdf", "docling",
               "marker-pdf", "pillow", "pypdfium2")
MAIN_PKGS = ("pypdf", "python-pptx", "numpy", "pillow")

_CHILD = """
import importlib.metadata as md, json
out = {}
for n in %r:
    try:
        out[n] = md.version(n)
    except Exception:
        out[n] = None
try:
    import torch
    out["torch_cuda"] = torch.version.cuda
    out["cuda_available"] = bool(torch.cuda.is_available())
    out["device"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
except Exception as exc:
    out["torch_error"] = str(exc)[:200]
try:
    import onnxruntime as ort
    out["ort_providers"] = list(ort.get_available_providers())
except Exception as exc:
    out["ort_error"] = str(exc)[:200]
print(json.dumps(out, ensure_ascii=False))
"""


def _py_versions(python: str, names: tuple[str, ...]) -> dict:
    code = _CHILD % (list(names),)
    try:
        pr = subprocess.run([python, "-c", code], capture_output=True, timeout=180)
        if pr.returncode != 0:
            return {"_error": pr.stderr.decode("utf-8", "replace")[-300:]}
        return json.loads(pr.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception as exc:
        return {"_error": str(exc)[:200]}


def _head(title: str) -> None:
    print()
    print("=" * 62)
    print(f"  {title}")
    print("=" * 62)


def _git(*args: str) -> str:
    try:
        pr = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, timeout=60)
        return (pr.stdout or pr.stderr).decode("utf-8", "replace").strip()
    except Exception as exc:
        return f"(git 不可用: {exc})"


def main() -> int:
    # Windows 控制台默认 GBK：先切 UTF-8，否则 emoji/中文会直接抛 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _head("1. 运行环境")
    print(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    print(f"项目根目录  : {ROOT}")
    print(f"解析器 venv : {VENV_PY if VENV_PY.exists() else VENV_PY_POSIX}"
          f"  {'✅ 存在' if (VENV_PY.exists() or VENV_PY_POSIX.exists()) else '❌ 未创建'}")

    main_env = _py_versions(sys.executable, MAIN_PKGS)
    print("\n主环境依赖（解析快路径 / PPTX / 导出用）:")
    for name in MAIN_PKGS:
        print(f"  {name:<14} {main_env.get(name) or '—'}")

    _head("2. 解析器环境与 GPU")
    venv_python = str(VENV_PY if VENV_PY.exists() else VENV_PY_POSIX)
    if not (VENV_PY.exists() or VENV_PY_POSIX.exists()):
        print("解析器 venv 不存在——重排版解析（marker/docling）与 OCR 不可用。")
        print("创建：python -m venv .venv-parsers")
        print("      .venv-parsers/Scripts/python -m pip install marker-pdf docling rapidocr "
              "onnxruntime pypdfium2")
    else:
        venv = _py_versions(venv_python, PARSER_PKGS)
        for name in PARSER_PKGS:
            print(f"  {name:<14} {venv.get(name) or '—'}")
        if venv.get("_error"):
            print(f"  ⚠️ 解析器环境探测失败：{venv['_error']}")
        cuda = venv.get("cuda_available")
        print(f"\n  torch.version.cuda : {venv.get('torch_cuda') or '—（CPU 版 torch）'}")
        print(f"  CUDA 可用          : {'✅ True' if cuda else '❌ False'}")
        print(f"  设备               : {venv.get('device') or '—（CPU 推理）'}")
        provs = venv.get("ort_providers") or []
        if provs:
            print(f"  ONNX 执行后端      : {', '.join(provs)}")

    _head("3. 结论与建议喵")
    cuda = (venv.get("cuda_available") if (VENV_PY.exists() or VENV_PY_POSIX.exists()) else None)
    if cuda:
        print("N 卡 CUDA 已启用 ✅ 重排版解析（marker/docling）会吃 GPU。")
        print("提醒：生成笔记走云端 API，与本机 GPU 无关喵。")
    else:
        print("当前无可用 CUDA —— 走 CPU 方案，全程仍可用，只是解析更慢：")
        print("  · 数字版 PDF：保持 parser.default: auto（走 pypdf，秒级，不碰 GPU）")
        print("  · 扫描书 OCR：CPU 约 3-8 秒/页（onnxruntime 本来就在 CPU 跑）")
        print("  · 重排版书：marker/docling 在 CPU 上很慢，建议只对少数必需的书用")
        print("\n  N 卡装 GPU 版 torch：")
        print("    .venv-parsers/Scripts/python -m pip install --proxy '' \\")
        print("      --index-url https://download.pytorch.org/whl/cu130 "
              "torch==2.14.1+cu130 torchvision==0.29.1+cu130")
        print("    .venv-parsers/Scripts/python -m pip install \"pillow==10.4.0\"")
        print("  核显 / 纯 CPU：")
        print("    .venv-parsers/Scripts/python -m pip install \\")
        print("      --index-url https://download.pytorch.org/whl/cpu torch torchvision")
        print("  A 卡 / Intel 核显（Windows）：OCR 换 DirectML 后端即可吃 GPU——")
        print("    .venv-parsers/Scripts/python -m pip uninstall -y onnxruntime")
        print("    .venv-parsers/Scripts/python -m pip install onnxruntime-directml")
        print("    （marker/docling 在 Windows+A 卡上仍走 CPU；ROCm 仅 Linux）")

    _head("4. 密钥安全（不会进仓库喵）")
    for path in ("data/models.json", "data/.local_token"):
        ignored = _git("check-ignore", "-v", path)
        tracked = _git("ls-files", path)
        ok = bool(ignored) and not tracked
        print(f"  {path:<24} {'✅ 已忽略且未被跟踪' if ok else '❌ 请检查 .gitignore'}")
        if ignored:
            print(f"      {ignored.splitlines()[0]}")
    hist = _git("log", "--all", "--name-only", "--pretty=format:")
    leaked = [ln for ln in hist.splitlines() if ln.strip().endswith(("models.json", ".local_token"))]
    print(f"  git 历史中是否出现过密钥文件：{'❌ ' + ', '.join(sorted(set(leaked))) if leaked else '✅ 从未出现'}")
    print("\n  提醒：config.yaml 会被提交但不含密钥；密钥请用 data/models.json 或 api_key_env。")
    print("  若真提交过密钥：立刻去供应商后台吊销换新，再用 git filter-repo/BFG 清历史。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
