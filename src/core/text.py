"""文本归一化、哈希、token 估算与 ID 生成（规范 4.2.1 / 5.2）。"""

from __future__ import annotations

import hashlib
import re
import secrets
import string
import unicodedata
from datetime import datetime, timezone

_BASE62 = string.ascii_letters + string.digits


def utc_now_iso() -> str:
    """时间规范：UTC ISO-8601，格式 YYYY-MM-DDTHH:MM:SSZ。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_text(s: str) -> str:
    """身份与 quote 验证用归一化；保持大小写、数学符号、连字符和标点（5.2 精确算法）。"""
    s = unicodedata.normalize("NFC", s or "")
    return re.sub(r"\s+", " ", s).strip()


def text_hash(s: str) -> str:
    """归一化内容 SHA-256 hex（64 位，不带前缀）。"""
    return hashlib.sha256(normalize_text(s).encode("utf-8")).hexdigest()


def search_normalize(s: str) -> str:
    """仅供检索；不得用于身份哈希（5.2）。"""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"\s+", "", s)


def estimate_tokens(s: str) -> int:
    """token 估算：ASCII 约 4 字符/token，CJK 约 1 字符/token；只作预估（5.3.2）。"""
    if not s:
        return 0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    other = len(s) - cjk
    return cjk + (other + 3) // 4


def doc_namespace(doc_id: str) -> str:
    return hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:8]


def base_anchor_id(doc_id: str, content: str) -> str:
    return f"a_{doc_namespace(doc_id)}_{text_hash(content)[:16]}"


def with_suffix(base_id: str, n: int) -> str:
    """重复实例后缀：第一个用基础 ID，后续 -2/-3/...（5.2）。"""
    return base_id if n <= 1 else f"{base_id}-{n}"


def new_block_id(existing: set[str] | None = None) -> str:
    existing = existing or set()
    for _ in range(64):
        cand = "b_" + "".join(secrets.choice(_BASE62) for _ in range(12))
        if cand not in existing:
            return cand
    raise RuntimeError("block_id 生成冲突，重试失败")


def new_tx_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "-" + secrets.token_hex(4)


# ---- JSON 转义撞车修复（LaTeX 反斜杠被当成转义符吃掉） ----
# 模型在 JSON 字符串里写裸 LaTeX（"\bigcap"）时，\b \f \n \r \t \u 会被 JSON
# 解析器当作转义符：\bigcap → 0x08 + "igcap"，渲染出来就是 "Digcap" 这种破公式。
# 这些控制字符在笔记文本里永不合法，故按「已知 LaTeX 命令」白名单还原。
# 白名单只管转义字母开头的命令；命中不了就原样保留（宁可不修，不可改坏换行）。
_ESCAPE_LETTERS = "bfnrtu"
LATEX_COMMANDS = frozenset("""
backslash bar because begin beta between bf bigcap bigcirc bigcup bigl bigm bigodot bigoplus
bigotimes bigr bigsqcup bigtriangledown bigtriangleup biguplus bigvee bigwedge bmatrix bm
bmod boldsymbol bot bottom bowtie boxed bullet binom
fallingdotseq fbox fcolorbox fill flat forall frac frown footnotesize
nabla natural ne neg neq newcommand newline ni nleftarrow nleq ngeq ncong nmid nolimits
nonumber norm not notin nparallel nprec nrightarrow nsim nsubseteq nsucc nsupseteq
ntriangleleft ntriangleright nu nvdash nwarrow nearrow
rangle rbrace rceil ref rfloor rho right rightarrow rightarrowtail rightharpoondown
rightharpoonup rightleftharpoons risingdotseq rlap rm rule rvert rVert
tag tan tanh tau tbinom text textbf textit textrm textsc texttt tfrac thanks therefore theta
thickapprox thinspace tilde times to today top triangle triangledown triangleleft triangleright
ubrace underbrace underline uparrow updownarrow uplus upsilon
varepsilon varphi varrho varsigma vartheta vec vdots vee vert vDash vdash vline vmatrix
vskip vspace
""".split())

# JSON 合法转义只有 " \ / b f n r t u。负向后顾保证幂等（已经转义过的 \\bigcap
# 不再二次改写——parse_json_output 与 json_repair_loads 都可能各调一次）。
# 该正则同时覆盖两类撞车：
#   1) \i \m \s \l \p … 本就非法，json.loads 直接失败，只能靠 json_repair 猜；
#   2) \b \f \n \r \t 合法但语义撞车，会把 \bigcap 吃成 0x08+"igcap"。
_INVALID_ESCAPE_RE = re.compile(r"(?<!\\)\\([A-Za-z])")


def _longest_command(cand: str) -> str:
    """取最长的已知 LaTeX 命令前缀（命令名必须是白名单里完整的一条）。"""
    while cand:
        if cand in LATEX_COMMANDS:
            return cand
        cand = cand[:-1]
    return ""


def repair_json_escapes(raw: str) -> str:
    """解析前修复：把撞上 JSON 转义的裸 LaTeX 片段还原为 \\X（双反斜杠）。

    - 非法转义（i / m / s 这类非 JSON 转义字母）一律补反斜杠：模型写的就是 LaTeX；
    - 合法但语义撞车的那几个转义字母：只在能构成已知 LaTeX 命令时补，
      避免把真正的换行/制表符改坏；
    - 真正的 unicode 转义与普通换行/制表不动。
    """
    if not raw or "\\" not in raw:
        return raw

    def _invalid(m: "re.Match[str]") -> str:
        letter = m.group(1)
        rest = raw[m.end():]
        if letter in _ESCAPE_LETTERS:
            if letter == "u" and len(rest) >= 4 and all(
                    c in "0123456789abcdefABCDEF" for c in rest[:4]):
                return m.group(0)            # 合法 \uXXXX
            if not _longest_command(letter + re.match(r"[A-Za-z]*", rest).group(0)):
                return m.group(0)            # 白名单外：可能是真换行/制表，原样保留
        return "\\\\" + letter               # 只把反斜杠补成 \\，后续字母原样

    return _INVALID_ESCAPE_RE.sub(_invalid, raw)


# 控制字符 → LaTeX。0x00/0x08/0x0b/0x0c 在笔记里永远不合法（无条件还原）；
# 0x09/0x0d 可能合法（缩进/回车），只在能构成已知命令时还原。
# 0x0a（换行）在文本里太常见，只由 parse 前的 repair_json_escapes 处理。
_CONTROL_FIXED = {0x00: "\\", 0x08: "\\b", 0x0B: "\\v", 0x0C: "\\f"}
_CONTROL_GUESS = {0x09: "t", 0x0D: "r"}
# 零宽/不可见格式字符：公式里混进来就是隐形炸弹（\to\ufeff\to 这种），直接删掉
_INVISIBLE = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF}


def sanitize_control_chars(text: str) -> str:
    """入库闸门 + 存量修复：还原 JSON 转义撞车留下的控制字符。"""
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        code = ord(text[i])
        if code in _CONTROL_FIXED:
            out.append(_CONTROL_FIXED[code])
            i += 1
            continue
        if code in _INVISIBLE:
            i += 1
            continue
        if code in _CONTROL_GUESS:
            j = i + 1
            while j < n and text[j].isascii() and text[j].isalpha():
                j += 1
            cand = _longest_command(_CONTROL_GUESS[code] + text[i + 1:j])
            if not cand:
                out.append(text[i])
                i += 1
                continue
            out.append("\\" + cand)
            i += len(cand)                       # 控制字符 + 已消费的字母
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def slugify(value: str, fallback: str = "doc") -> str:
    """doc_id 候选：仅 [a-z0-9-]，中文/空名用 fallback（4.2.1）。"""
    v = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    v = re.sub(r"[^a-zA-Z0-9]+", "-", v).strip("-").lower()
    v = re.sub(r"-{2,}", "-", v)
    return v if len(v) >= 4 else fallback
