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


def repair_surrogates(text: str) -> str:
    r"""修复 UTF-16 代理对/孤儿代理（pypdf 对 >BMP 字符的抽取产物）。

    合法代理对合并为真实码点（Mathematical Double-Struck 等数学字母）；
    孤儿代理无意义，直接丢弃。不修则 json.dumps(ensure_ascii=False) 写盘
    直接 UnicodeEncodeError（整书摄取硬崩溃）。
    """
    if not text:
        return text
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        o = ord(text[i])
        if 0xD800 <= o <= 0xDBFF and i + 1 < n:
            o2 = ord(text[i + 1])
            if 0xDC00 <= o2 <= 0xDFFF:
                out.append(chr(0x10000 + ((o - 0xD800) << 10) + (o2 - 0xDC00)))
                i += 2
                continue
        if 0xD800 <= o <= 0xDFFF:
            i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


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


# ---- 公式文本规范化（入库闸门 + 存量修复） ----
# 常见数学命令：用于判断「换行处是不是把反斜杠写丢了」
# （模型会写出 "$<换行>lim_{...}"、"(X,<换行>mathcal{A})" 这种）
MATH_COMMANDS = frozenset("""
mathbb mathcal mathfrak mathbf mathrm mathit mathsf mathtt mathds boldsymbol operatorname
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu
xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega
Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
lim limsup liminf log ln exp sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh
det dim ker deg gcd hom sup inf max min arg frac dfrac tfrac sqrt sum prod int iint iiint
oint bigcup bigcap bigvee bigwedge bigoplus bigotimes bigodot bigsqcup
partial nabla infty cdots ldots vdots ddots dots quad qquad
left right bigl bigr Bigl Bigr biggl biggr Biggl Biggr
text textbf textit textrm textsf texttt mathop mathrel mathord mathbin mathopen mathclose
subset subseteq supset supseteq in notin ni cup cap setminus times cdot ast circ oplus
otimes leq geq neq approx equiv sim simeq propto to mapsto rightarrow leftarrow
leftrightarrow Rightarrow Leftarrow Leftrightarrow forall exists neg land lor implies iff
wedge vee perp parallel angle triangle square lfloor rfloor lceil rceil langle rangle
vert Vert lvert rvert lVert rVert
begin end cases matrix pmatrix bmatrix vmatrix array aligned align
hspace vspace displaystyle textstyle scriptstyle
""".split())

GREEK_COMMANDS = {
    "σ": "sigma", "Σ": "Sigma", "μ": "mu", "λ": "lambda", "α": "alpha", "β": "beta",
    "γ": "gamma", "δ": "delta", "ε": "varepsilon", "ζ": "zeta", "η": "eta", "θ": "theta",
    "κ": "kappa", "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho", "τ": "tau", "φ": "varphi",
    "χ": "chi", "ψ": "psi", "ω": "omega", "Ω": "Omega", "Δ": "Delta", "Φ": "Phi",
    "Γ": "Gamma", "Θ": "Theta", "Λ": "Lambda", "Π": "Pi", "Ψ": "Psi",
}

_MATH_SPAN_RE = re.compile(r"\$\$[\s\S]*?\$\$|\$[^$]*?\$")
_TEXT_GROUP_RE = re.compile(r"\\text\{[^}]*\}")
# 异体字/私用区/替换字符/希伯来-阿拉伯呈现形式：数学里出现必然是模型垃圾输出
_JUNK_CHARS_RE = re.compile(r"[\uE000-\uF8FF\uFB1D-\uFB4F\uFBF0-\uFDFF\uFFFD]")
_FENCE_RE = re.compile(chr(96) * 3 + r"[\s\S]*?" + chr(96) * 3)
_NL_BEFORE_WORD_RE = re.compile(r"\n(?=([A-Za-z]{2,}))")
_DISPLAY_DELIMS = set("_^{([)]},")

_B = chr(92)
# 模型常把 aligned/array 的行分隔(双反斜杠)写成字面反斜杠+n（非命令名）：aligned 环境因此失衡
_ROW_BREAK_NL_RE = re.compile("(?<!" + _B * 2 + ")" + _B * 2 + "n(?![A-Za-z])")
# 平均积分 \fint：KaTeX 未定义 → 渲染红字。展开为纯 TeX 原语的"积分加横杠"记号，
# KaTeX / MathJax / LaTeX 通用（\mathop 让上下标仍挂在算子上）
_FINT_RE = re.compile(_B * 2 + "fint(?![A-Za-z])")
_FINT_EXPANSION = _B + "mathop{{" + _B + "int}" + _B + "!" * 4 + "-}"
# 希腊命令名后直接跟字母 = 未定义命令（KaTeX 红字）：中间补一个分隔空格。
# 但 \\nulldelimiterspace / \\multicolumn / \\pitchfork 等是真实长命令，不得误伤。
_GREEK_EXT_COMMANDS = {"nulldelimiterspace", "multicolumn", "multirow", "pitchfork"}
_GLUED_GREEK_RE = re.compile(
    _B * 2 + "(" + "|".join(sorted(GREEK_COMMANDS.values(), key=len, reverse=True)) + ")([A-Za-z]+)"
)


def _normalize_math_span(seg: str) -> str:
    display = seg.startswith("$$")

    def _nl(m: "re.Match[str]") -> str:
        rest = seg[m.end():]
        word = _NL_BEFORE_WORD_RE.match("\n" + rest)
        cmd = word.group(1) if word else ""
        if cmd and cmd in MATH_COMMANDS:
            nxt = rest[len(cmd):len(cmd) + 1]
            if not display or nxt == "" or nxt in _DISPLAY_DELIMS:
                return "\\"            # 该换行其实是丢掉的 LaTeX 反斜杠
        return m.group(0)

    out = _NL_BEFORE_WORD_RE.sub(_nl, seg)
    if display:
        out = _ROW_BREAK_NL_RE.sub(lambda m: _B * 2, out)   # "\n" → 行分隔 "\\"
    out = _FINT_RE.sub(lambda m: _FINT_EXPANSION, out)      # \fint → 通用记号
    if not display:
        out = out.replace("\n", " ")     # 行内公式不得跨行：否则 $ 配对错位
    # 裸希腊字母 → LaTeX 命令（\text{} 内是文本模式，保持不动）
    protected: list[str] = []

    def _mask(m: "re.Match[str]") -> str:
        protected.append(m.group(0))
        return chr(0) + str(len(protected) - 1) + chr(0)

    work = _TEXT_GROUP_RE.sub(_mask, out)
    for greek, name in GREEK_COMMANDS.items():
        if greek in work:
            # 后随字母时必须留出分隔：否则 Δu → \\Deltau 会被当成一个未定义命令
            work = re.sub(re.escape(greek) + r"(?=[A-Za-z])", lambda m: _B + name + " ", work)
            work = work.replace(greek, _B + name)
    # 反向修复：历史上粘连好的 \\Deltau（希腊命令名后直接跟字母）→ 命令 + 空格

    def _unglue(m: "re.Match[str]") -> str:
        word = m.group(1) + m.group(2)
        return m.group(0) if word in _GREEK_EXT_COMMANDS else m.group(1) + " " + m.group(2)

    work = _GLUED_GREEK_RE.sub(_unglue, work)
    for i, original in enumerate(protected):
        work = work.replace(chr(0) + str(i) + chr(0), original)
    return _JUNK_CHARS_RE.sub("", work)


def normalize_math_text(content: str) -> str:
    """公式文本规范化（入库闸门 + 存量修复）。

    1) 行内公式跨行会让前端不跨行匹配 → $ 配对错位、散文被当公式渲染；
       若换行处紧跟已知命令名，说明模型把反斜杠写丢成了换行（"$<NL>lim_{...}"），
       还原为反斜杠；其余行内换行并成空格
    2) 数学里的裸 Unicode 希腊字母（σ/λ/μ…）改用 LaTeX 命令
    3) 清除数学里的异体字/私用区垃圾字符
    """
    if not content or "$" not in content:
        return content
    fences: list[str] = []               # 代码块里的 $ 不是公式

    def _keep_fence(m: "re.Match[str]") -> str:
        fences.append(m.group(0))
        return chr(1) + str(len(fences) - 1) + chr(1)

    text = _FENCE_RE.sub(_keep_fence, content)
    text = _MATH_SPAN_RE.sub(lambda m: _normalize_math_span(m.group(0)), text)
    for i, fence in enumerate(fences):
        text = text.replace(chr(1) + str(i) + chr(1), fence)
    return text


# ---- 参考文本对齐排版修复（PDF 抽取伪影：词中空格化） ----
def squash_key(s: str) -> tuple[str, list[int]]:
    """对齐指纹：NFKC + casefold + 去空白，其余字符（含字形残留）保留。

    保留字形残留是刻意的：pypdf 与 pymupdf 对同一公式字形给出不同的垃圾表示
    （"/Omega1" vs "\x02"），保留它们才不会把两段不同排版误判为相同内容。
    返回指纹与每个指纹字符在原串中的下标（用于把对齐结果映射回原文）。
    """
    chars: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s or ""):
        for norm in unicodedata.normalize("NFKC", ch):
            if norm.isspace():
                continue
            chars.append(norm.casefold())
            idx.append(i)
    return "".join(chars), idx


def respaces_from_reference(text: str, reference: str) -> str:
    """用参考文本的排版替换 text 中被"逐字母空格化"的片段（内容一字不改）。

    对齐指纹（去空白）相同的片段才用参考文本的写法替换；指纹不同的片段
    （如两个解析器对同一公式字形给出的不同残留）原样保留。返回修复后的文本；
    修复前后指纹必须一致（构造保证），否则原样返回。
    """
    sq_text, idx_text = squash_key(text)
    sq_ref, idx_ref = squash_key(reference)
    if not sq_text or not sq_ref:
        return text
    from difflib import SequenceMatcher

    matcher = SequenceMatcher(None, sq_text, sq_ref, autojunk=False)
    out: list[str] = []
    last = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        start = idx_text[i1]
        end = idx_text[i2 - 1] + 1
        ref_start = idx_ref[j1]
        ref_end = idx_ref[j2 - 1] + 1
        out.append(text[last:start])
        out.append(reference[ref_start:ref_end])
        last = end
    out.append(text[last:])
    merged = "".join(out)
    if squash_key(merged)[0] != sq_text:      # 安全闸门：内容不得有任何变化
        return text
    return merged


def slugify(value: str, fallback: str = "doc") -> str:
    """doc_id 候选：仅 [a-z0-9-]，中文/空名用 fallback（4.2.1）。"""
    v = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    v = re.sub(r"[^a-zA-Z0-9]+", "-", v).strip("-").lower()
    v = re.sub(r"-{2,}", "-", v)
    return v if len(v) >= 4 else fallback
