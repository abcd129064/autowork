# -*- coding: utf-8 -*-
"""API.md 与实际代码的一致性审计（只读，不改任何文件）

对比 docs/API.md 中登记的符号与各模块 AST 实际符号，输出：
1. 文档中有、代码中已删的函数/类（含所在行号，便于定位清理）
2. 代码中有、文档未登记的公开函数/类
3. API.md 未覆盖的模块
用法：python tools/api_doc_audit.py [--json OUT]
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "API.md")

# 模块名 → 源文件（相对 ROOT）；None=包/多文件，跳过符号级审计
MODULE_FILES = {}


def _register(mod):
    path = mod.replace(".", "/") + ".py"
    if os.path.isfile(os.path.join(ROOT, path)):
        MODULE_FILES[mod] = path


def discover_doc_modules():
    """从 API.md 提取登记的模块：### core.xxx / ## workers/xxx.py 标题"""
    doc_syms = {}   # module -> set(symbol names)
    doc_order = []  # 出现顺序
    cur = None
    with open(DOC, encoding="utf-8") as f:
        text = f.read()
    for line in text.splitlines():
        m3 = re.match(r"^### ([a-zA-Z_][\w.]*)", line)
        m2 = re.match(r"^## ([\w/]+\.py)", line)
        if m3:
            mod = m3.group(1)
            cur = mod
            if mod not in doc_syms:
                doc_syms[mod] = set()
                doc_order.append(mod)
                _register(mod)
        elif m2:
            mod = m2.group(1).replace("/", ".").replace(".py", "")
            cur = mod
            if mod not in doc_syms:
                doc_syms[mod] = set()
                doc_order.append(mod)
                _register(mod)
        elif cur:
            # 表格行/代码块里的 `name(` 视为登记符号
            for s in re.findall(r"`([A-Za-z_][\w]*)\s*\(", line):
                doc_syms[cur].add(s)
            # `#### 类 X` / **模块级单例** X
            mc = re.match(r"^#### 类 `?([A-Za-z_]\w*)`?", line)
            if mc:
                doc_syms[cur].add(mc.group(1))
            for s in re.findall(r"^\|\s*`([A-Za-z_]\w*)", line):
                doc_syms[cur].add(s)
    return doc_syms, doc_order


def code_symbols(path):
    """AST 提取顶层函数/类 + 类的公开方法"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    funcs, classes = {}, {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node.lineno
        elif isinstance(node, ast.ClassDef):
            methods = {}
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[sub.name] = sub.lineno
            classes[node.name] = (node.lineno, methods)
    return funcs, classes


def main():
    doc_syms, doc_order = discover_doc_modules()
    report = {"deleted": {}, "undocumented": {}, "missing_modules": [],
              "no_source": []}
    for mod in doc_order:
        rel = MODULE_FILES.get(mod)
        if rel is None:
            # 尝试包 __init__ 或多文件模块
            if os.path.isdir(os.path.join(ROOT, mod.replace(".", "/"))):
                continue  # 包级，跳过符号审计
            report["no_source"].append(mod)
            continue
        funcs, classes = code_symbols(os.path.join(ROOT, rel))
        docd = doc_syms[mod]
        # 1) 文档有、代码已删（仅列明显的函数/类名，方法命中类即算存在）
        gone = []
        for s in sorted(docd):
            if s in funcs or s in classes:
                continue
            # 方法名出现在任意类里也算存在
            if any(s in m for _, m in classes.values()):
                continue
            if s in ("get", "set", "run", "start", "stop", "close", "load",
                     "save", "init", "show", "test", "main", "emit"):
                continue  # 常见方法名噪音
            gone.append(s)
        if gone:
            report["deleted"][mod] = gone
        # 2) 代码有、文档未登记（顶层公开符号）
        miss = []
        for s in sorted(list(funcs) + list(classes)):
            if s.startswith("_"):
                continue
            if s in docd:
                continue
            miss.append(s)
        if miss:
            report["undocumented"][mod] = miss
    # 3) 源码模块未被文档覆盖（限业务层）
    for layer in ("core", "database", "workers", "main_window", "windows",
                  "win_api"):
        d = os.path.join(ROOT, layer)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py") and fn != "__init__.py":
                mod = f"{layer}.{fn[:-3]}"
                if mod not in doc_syms:
                    report["missing_modules"].append(mod)

    out = json.dumps(report, ensure_ascii=False, indent=1)
    if "--json" in sys.argv:
        out_path = sys.argv[sys.argv.index("--json") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"written -> {out_path}")
    print(out)


if __name__ == "__main__":
    main()
