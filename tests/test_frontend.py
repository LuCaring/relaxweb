#!/usr/bin/env python3
"""前端模块静态检查：python3 tests/test_frontend.py

不起服务、不开浏览器，只读 assets/ 下的前端源码和两个页面，
检查拆分/搬移代码时最容易犯、又只在浏览器里才炸的几类错误：
  1. import 的名字必须真被来源模块导出 —— 否则整页 SyntaxError 白屏
  2. 用到的项目内名字必须在本模块定义或 import —— 否则运行期 ReferenceError
     共享状态 state 的字段（currentUser/myRoom/roomChat…）必须写成 state.xxx，
     漏写前缀会命中这一条
  3. 页面实际加载的模块入口要能走到所有模块 —— 漏 import 会让 registerView 不执行
  4. 页面里引用的本地脚本/样式都存在，且带 ?v= 版本号（避免浏览器缓存旧代码）
  5. 前端不许用原生 alert/confirm/prompt —— 它们会阻塞主线程，自动化测试会卡死；
     请改用 assets/js/dialog.js 的 alertDialog/confirmDialog（直播间用 window.LiveDialog）
  6. ES 模块只能通过 window 读经典脚本提供的全局组件 —— 那些组件必须真被赋值到
     window 上（顶层 const/let 只进全局词法环境，不是 window 属性），
     否则调用方拿到 undefined 后静默短路，点了没反应
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
JS_DIR = ASSETS / "js"
PAGES = ["index.html", "game.html", "dungeon-beta.html"]

# 原生弹窗调用：alert( / confirm( / prompt(（含 window.alert 写法）
NATIVE_DIALOG = re.compile(r"(?<![\w.$])(?:window\.)?(alert|confirm|prompt)\s*\(")

# 通过 window 读这些名字是正常的：浏览器内置 API，以及 deploy/serve.py 注入的配置。
# 其余 window.X 必须是项目自己赋值出来的（见 web 检查 6）。
EXTERNAL_GLOBALS = {
    "LIVE_CONFIG",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "requestAnimationFrame", "cancelAnimationFrame",
    "matchMedia", "addEventListener", "removeEventListener",
    "AudioContext", "webkitAudioContext",
}

# 服务端注入的全局对象由 deploy/serve.py 提供，前端只能读 window.LIVE_CONFIG
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def strip_noise(text):
    """去掉注释与字符串内容，只留下会参与求值的标识符。

    模板字符串只保留 ${...} 里的表达式：里面的标识符是真会求值的。
    """
    text = re.sub(r"^\s*import .*$", "", text, flags=re.M)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(
        r"`(?:[^`\\]|\\.)*`",
        lambda m: "\n" + "\n".join(re.findall(r"\$\{([^}]*)\}", m.group(0))) + "\n",
        text,
        flags=re.S,
    )
    text = re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)
    text = re.sub(r"'(?:[^'\\]|\\.)*'", "''", text)
    # 展开运算符 ...x 的第 3 个点会被当成属性访问的点，抹掉它才好按“点前缀=属性”判断
    text = text.replace("...", " ")
    return text


def top_level(text):
    """模块顶层的函数/常量/类名。"""
    return set(
        re.findall(
            r"(?m)^(?:export )?(?:async )?(?:function|const|let|class) ([A-Za-z_$][\w$]*)",
            text,
        )
    )


def state_fields(core_raw):
    """core.js 里 state 对象的字段名——它们等价于全局共享变量。"""
    block = re.search(r"export const state = \{(.*?)\n\};", core_raw, flags=re.S)
    if not block:
        return set()
    return set(re.findall(r"(?m)^\s*([A-Za-z_$][\w$]*)\s*:", block.group(1)))


def imports_of(raw):
    """{本地模块名: {导入的名字}}，同时保留路径用于导出校验。"""
    named = {}
    sources = []
    for m in re.finditer(r'(?m)^\s*import\s+(.*?)\s+from\s+"([^"]+)"', raw, flags=re.S):
        clause, path = m.group(1), m.group(2)
        sources.append(path)
        local = path.split("/")[-1].removesuffix(".js")
        names = set()
        braces = re.search(r"\{(.*)\}", clause, flags=re.S)
        if braces:
            names |= {n.strip() for n in braces.group(1).split(",") if n.strip()}
        default = clause.split(",")[0].strip()
        if default and not default.startswith("{") and default != "*":
            names.add(default)
        named.setdefault(local, set()).update(names)
    for m in re.finditer(r'(?m)^\s*import\s+"([^"]+)"', raw):
        sources.append(m.group(1))
    return named, sources


def main():
    files = sorted(p for p in JS_DIR.rglob("*.js"))
    rel = {p.stem: p.relative_to(JS_DIR).as_posix() for p in files}
    raw = {p.stem: p.read_text(encoding="utf-8") for p in files}
    code = {name: strip_noise(text) for name, text in raw.items()}

    # 1. 导出校验
    exported = {
        name: (
            set(re.findall(r"(?m)^export (?:async )?(?:function|const|let|class) ([A-Za-z_$][\w$]*)", text))
            # `export { a, b as c }`（含 `export { … } from "…"` 再导出）
            | {n.split(" as ")[-1].strip()
               for m in re.findall(r"(?m)^export\s*\{([^}]+)\}", text)
               for n in m.split(",") if n.strip()}
        )
        for name, text in raw.items()
    }
    bad = []
    for name, text in raw.items():
        for m in re.finditer(r'(?m)^\s*import\s+\{(.*?)\}\s+from\s+"([^"]+)"', text, flags=re.S):
            target = m.group(2).split("/")[-1].removesuffix(".js")
            if target not in exported:
                bad.append(f"{rel[name]} 引用了不存在的模块 {m.group(2)}")
                continue
            for imported in {n.strip() for n in m.group(1).split(",") if n.strip()}:
                if imported not in exported[target]:
                    bad.append(f"{rel[name]} 从 {rel[target]} 导入 {imported}，但后者没有导出它")
    check("import 的名字都有对应导出", not bad, "; ".join(bad))

    # 2. 名字缺失（含 state 字段漏写前缀）
    #    只有「导出」的名字才可能被别的模块 import；模块私有的同名变量不算问题
    fields = state_fields(raw.get("core", ""))
    owner = {}
    for name in code:
        for symbol in exported[name]:
            owner.setdefault(symbol, name)
    for field in fields:
        owner[field] = "core"
    imported = {name: set() for name in code}
    for name, text in raw.items():
        named, _ = imports_of(text)
        for names in named.values():
            imported[name] |= names

    missing_report = []
    for name in sorted(code):
        body = code[name]
        missing = []
        for symbol, src in owner.items():
            if src == name or symbol in top_level(code[name]) or symbol in imported[name]:
                continue
            if re.search(r"(?<![\w.$])" + re.escape(symbol) + r"\b", body):
                hint = " 应写作 state." + symbol if symbol in fields else f"（导出在 {rel[src]}，需 import）"
                missing.append(symbol + hint)
        if missing:
            missing_report.append(f"{rel[name]}: " + ", ".join(sorted(missing)))
    check("模块内引用的名字都有来源", not missing_report, "; ".join(missing_report))

    # 3. 从两个页面实际加载的模块入口出发，所有模块都要可达
    entries = set()
    for page in PAGES:
        html = (ROOT / page).read_text(encoding="utf-8")
        for src in re.findall(r'<script[^>]+type="module"[^>]+src="assets/js/([\w/-]+)\.js', html):
            entries.add(src.split("/")[-1])
    reachable = set(entries)
    queue = list(entries)
    while queue:
        name = queue.pop()
        if name not in code:
            continue
        _, sources = imports_of(raw[name])
        for path in sources:
            target = path.split("/")[-1].removesuffix(".js")
            if target in code and target not in reachable:
                reachable.add(target)
                queue.append(target)
    unreachable = sorted(set(code) - reachable)
    check(
        f"页面入口（{'、'.join(sorted(rel[e] for e in entries))}）可达全部模块",
        not unreachable,
        "未加载: " + ", ".join(rel[n] for n in unreachable),
    )

    # 4. 页面引用的本地资源存在且带版本号
    problems = []
    for page in PAGES:
        html = (ROOT / page).read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="(assets/[^"]+)"', html)
        if not refs:
            problems.append(f"{page} 没有引用任何 assets 资源")
        for ref in refs:
            if "?" not in ref:
                problems.append(f"{page} 的 {ref} 没有 ?v= 版本号")
            if not (ROOT / ref.split("?")[0]).exists():
                problems.append(f"{page} 引用的 {ref} 不存在")
    check("页面资源存在且带版本号", not problems, "; ".join(problems))

    # 5. 不允许原生 alert/confirm/prompt（含 HTML 内联脚本）
    sources = {
        p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(ASSETS.rglob("*.js"))
    }
    for page in PAGES:
        html = (ROOT / page).read_text(encoding="utf-8")
        inline = "\n".join(re.findall(r"(?s)<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html))
        if inline.strip():
            sources[page] = inline
    natives = []
    for name, text in sources.items():
        for lineno, line in enumerate(text.splitlines(), start=1):
            for hit in NATIVE_DIALOG.finditer(line):
                natives.append(f"{name}:{lineno} {hit.group(0).strip()}")
    check(
        "没有原生 alert/confirm/prompt",
        not natives,
        "改用 alertDialog/confirmDialog（直播间用 window.LiveDialog）: " + "; ".join(natives),
    )

    # 6. window.X 读到的全局必须真有脚本赋值（经典脚本的顶层 const 不是 window 属性）
    assigned = set()
    for path in sorted(ASSETS.rglob("*.js")):
        assigned |= set(
            re.findall(
                r"(?:\bwindow|\bself|\bglobalThis)\.([A-Za-z_$][\w$]*)\s*=",
                path.read_text(encoding="utf-8"),
            )
        )
    dangling = set()
    for name in sorted(code):
        for hit in re.finditer(r"(?<![\w.$])window\.([A-Za-z_$][\w$]*)", code[name]):
            symbol = hit.group(1)
            if symbol not in EXTERNAL_GLOBALS and symbol not in assigned:
                dangling.add(f"{rel[name]} 读 window.{symbol}，但没有脚本赋值它")
    detail = "; ".join(sorted(dangling))
    if dangling:
        detail += "  ← 若是浏览器内置 API，请加进 EXTERNAL_GLOBALS"
    check("window 上读的全局都有脚本赋值", not dangling, detail)

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
