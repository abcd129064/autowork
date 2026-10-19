# -*- coding: utf-8 -*-
"""注释覆盖率审计（只读，不改任何文件）。

口径：注释行 + docstring 行 除以 有效总行数；有效行排除空行、import 块、
被判定为界面/样式自动产物的行。新增或重写代码目标约 50%，存量模块只报数。

用法：python tools/check_comment_coverage.py [--json OUT] [--top N]
产物：无（纯 stdout）
"""
import ast
import io
import json
import os
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {"__pycache__", ".git", "node_modules", "build", "dist", "out",
             ".pytest_cache", "pytest-of-shen_zhe", ".qoder", ".workbuddy",
             "vendor", "web", "_scratch", "_archive"}

# 自动生成的界面代码：整文件判定，不计入覆盖率分母
UI_GENERATED_MARKERS = ("retranslateUi", "connectSlotsByName",
                        "pyside6-uic", "pyuic5", "DO NOT EDIT")


def iter_py_files():
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".py"):
                yield os.path.join(dp, f)


def is_ui_generated(src):
    return any(m in src for m in UI_GENERATED_MARKERS)


def measure(src):
    """返回 (有效行数, 注释行数)。import 块整体不计入分母。"""
    tree = ast.parse(src)
    lines = src.splitlines()

    # import 块：连续的 import / from 行（含括号续行）整段剔除
    import_lines = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            start, end = node.lineno, node.end_lineno
            # 向前吞掉同一 import 块前的空行分隔，保持块完整
            for ln in range(start, end + 1):
                import_lines.add(ln)

    # docstring 行
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                for ln in range(node.body[0].lineno,
                                node.body[0].end_lineno + 1):
                    doc_lines.add(ln)

    # 注释行：用 tokenize 精确取 COMMENT，且排除 docstring 内的 # 噪声
    comment_lines = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                comment_lines.add(tok.start[0])
    except tokenize.TokenError:
        pass

    # 三引号字符串块（非 docstring 的样式串/QSS）视为样式字符串剔除
    style_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for ln in range(node.lineno, node.end_lineno + 1):
                style_lines.add(ln)
    style_lines -= doc_lines

    total = 0
    for i, raw in enumerate(lines, 1):
        if i in import_lines or i in style_lines:
            continue
        if not raw.strip():
            continue
        total += 1

    covered = 0
    for i in range(1, len(lines) + 1):
        if i in import_lines or i in style_lines:
            continue
        if not lines[i - 1].strip():
            continue
        if i in comment_lines or i in doc_lines:
            covered += 1

    return total, covered


def main():
    rows = []
    for p in iter_py_files():
        rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
        try:
            with open(p, encoding="utf-8") as f:
                src = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        if is_ui_generated(src):
            rows.append((rel, -1, -1))
            continue
        try:
            total, covered = measure(src)
        except SyntaxError:
            continue
        rows.append((rel, total, covered))

    audited = [r for r in rows if r[1] > 0]
    grand_total = sum(r[1] for r in audited)
    grand_cov = sum(r[2] for r in audited)
    print(f"files audited: {len(audited)} (ui-generated excluded: "
          f"{len(rows) - len(audited)})")
    print(f"effective lines: {grand_total}, comment+doc lines: {grand_cov}, "
          f"ratio: {grand_cov / grand_total:.1%}" if grand_total else "n/a")

    audited.sort(key=lambda r: r[2] / r[1] if r[1] else 0)
    print("\n--- lowest coverage ---")
    for rel, total, cov in audited[:30]:
        print(f"  {cov / total:6.1%}  {total:6d}  {rel}")

    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump([{"file": r[0], "lines": r[1], "covered": r[2],
                        "ratio": (r[2] / r[1]) if r[1] > 0 else None}
                       for r in audited], f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
