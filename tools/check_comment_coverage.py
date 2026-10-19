# -*- coding: utf-8 -*-
"""注释覆盖率审计（只读，不改任何文件）。

统计口径与 AGENTS.md 2.5 节一致：注释行 + docstring 行 除以 有效总行数。
空行、import 块、样式字符串与非文档性的多行字符串块不计入分母；
自动生成的界面代码整文件剔除，防止把无注释的机器产物算成债务。

存量模块的达标线是 30%-40%，新增或重写的代码是 50%，本工具只负责报数，
不负责判定，因为「注释是否有价值」取决于它有没有解释关键分支。

用法：python tools/check_comment_coverage.py [--json OUT] [--top N]
退出码：0 正常；1 参数错误
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {"__pycache__", ".git", "node_modules", "build", "dist", "out",
             ".pytest_cache", "pytest-of-shen_zhe", ".qoder", ".workbuddy",
             "vendor", "web", "_scratch", "_archive", "logs"}

UI_GENERATED_MARKERS = ("retranslateUi", "connectSlotsByName",
                        "pyside6-uic", "pyuic5", "DO NOT EDIT")


def iter_py_files():
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in sorted(fn):
            if f.endswith(".py"):
                yield os.path.join(dp, f)


def multiline_string_spans(lines):
    """返回每个三引号字符串块的 (起始行, 结束行)，1 基，含端点。"""
    spans = []
    i = 0
    while i < len(lines):
        m = re.match(r"\s*[rbfu]{0,2}(\"\"\"|\'\'\')", lines[i])
        if not m:
            i += 1
            continue
        delim = m.group(1)
        start = i
        rest = lines[i][lines[i].index(delim) + 3:]
        if delim in rest:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and delim not in lines[j]:
            j += 1
        spans.append((start + 1, min(j + 1, len(lines))))
        i = j + 1
    return spans


def import_spans(lines):
    """连续的 import / from 行（含括号续行）算一个 import 块。"""
    spans = []
    i = 0
    while i < len(lines):
        t = lines[i].lstrip()
        if not (t.startswith("import ") or t.startswith("from ")):
            i += 1
            continue
        start = i
        depth = lines[i].count("(") - lines[i].count(")")
        while depth > 0 and i + 1 < len(lines):
            i += 1
            depth += lines[i].count("(") - lines[i].count(")")
        spans.append((start + 1, i + 1))
        i += 1
    return spans


def measure(lines):
    spans = multiline_string_spans(lines)
    imports = import_spans(lines)

    def in_ranges(ln, ranges):
        return any(a <= ln <= b for a, b in ranges)

    # 判定哪些多行字符串块是 docstring：首行紧随 def/class 或模块开头
    doc_lines = set()
    for a, b in spans:
        prev = ""
        for k in range(a - 2, -1, -1):
            if lines[k].strip():
                prev = lines[k].strip()
                break
        head = lines[a - 1].lstrip()[:3]
        if (head in ('"""', "'''")
                and (prev.endswith(":")
                     or prev.startswith(("def ", "class ", "async def "))
                     or a <= 3)):
            doc_lines.update(range(a, b + 1))

    total = 0
    covered = 0
    for idx, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            covered += 1
            continue
        if in_ranges(idx, imports):
            continue
        if in_ranges(idx, spans):
            if idx in doc_lines:
                covered += 1
            continue
        total += 1
    return total, covered


def main():
    args = sys.argv[1:]
    json_out = None
    if "--json" in args:
        i = args.index("--json")
        try:
            json_out = args[i + 1]
        except IndexError:
            print("--json 需要一个输出路径", file=sys.stderr)
            return 1
    top = 30
    if "--top" in args:
        try:
            top = int(args[args.index("--top") + 1])
        except (IndexError, ValueError):
            print("--top 需要一个整数", file=sys.stderr)
            return 1

    rows = []
    excluded = []
    for path in iter_py_files():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        if any(m in src for m in UI_GENERATED_MARKERS):
            excluded.append(rel)
            continue
        lines = src.splitlines()
        total, covered = measure(lines)
        if total == 0:
            continue
        rows.append({"file": rel, "lines": total, "covered": covered,
                     "ratio": round(covered / total, 4)})

    grand_lines = sum(r["lines"] for r in rows)
    grand_cov = sum(r["covered"] for r in rows)
    ratio = grand_cov / grand_lines if grand_lines else 0.0
    print(f"审计文件 {len(rows)} 个，剔除自动生成界面代码 {len(excluded)} 个")
    print(f"有效总行 {grand_lines}，注释+docstring {grand_cov}，"
          f"整体覆盖率 {ratio:.1%}")
    below = [r for r in rows if r["ratio"] < 0.30]
    print(f"低于 30% 的文件 {len(below)} 个，"
          f"涉及 {sum(r['lines'] for r in below)} 行")

    rows.sort(key=lambda r: r["ratio"])
    print(f"\n--- 覆盖率最低的 {min(top, len(rows))} 个文件 ---")
    for r in rows[:top]:
        print(f"  {r['ratio']:6.1%}  {r['lines']:6d}  {r['file']}")

    if excluded:
        print("\n剔除的自动生成界面代码：" + ", ".join(excluded))

    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump({"total_lines": grand_lines, "covered": grand_cov,
                       "ratio": ratio, "files": rows,
                       "excluded": excluded}, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
