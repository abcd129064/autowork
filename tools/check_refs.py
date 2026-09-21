# -*- coding: utf-8 -*-
"""引用断链检查（只读，不改任何文件）。

扫描仓库文本文件里出现的 `tools/`、`tests/`、`design/`、`docs/` 相对路径，
逐个核对磁盘上是否还存在，报告「文档/脚本里写了、但文件已不在」的断链。
目录搬迁、脚本改名后跑一遍，可一次性抓出所有需要改的引用。

用法：
    python tools/check_refs.py            # 只扫已入库文件
    python tools/check_refs.py --all      # 连未入库（未忽略）文件一起扫
退出码：0 = 无新增断链；1 = 存在断链。

两类引用不计入失败（只在末尾列为「既有债务」）：
  1. `_archive/` 下的归档脚本——冻结的历史代码，不改；
  2. KNOWN_STALE 里登记的历史报告类文档——写作当时引用的临时脚本已删。
另外，通配写法（`tools/probe/probe_*.py` 这类）会被截断成前缀 token，
只要同目录下存在以该前缀开头的条目即视为有效引用，不报断链。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):   # Windows 控制台默认 GBK，先切 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
TEXT_EXT = (".py", ".md", ".html", ".json", ".txt", ".ini", ".spec",
            ".ts", ".tsx", ".vue", ".toml", ".mermaid")
MAX_SIZE = 512 * 1024

# 前面不能是路径/单词字符，避免把 web/aftersale_front/tools/ 下的文件误当成本目录脚本
REF = re.compile(r"(?<![\w./\\-])((?:tools|tests|design|docs)/"
                 r"[A-Za-z0-9_.\-\u4e00-\u9fff]+)")
TRIM = ".,;:!?)]}'\"`，。、；：！？）】"

# 历史报告类文档：引用的临时脚本/设计稿在写作后已删，属既有债务，不算新增断链
KNOWN_STALE = {
    "docs/QSS使用审计与弃用评估.md",
    "docs/Qt内联引导说明.md",
    "docs/newbv_inventory_fields.md",
    "docs/架构评审与SQL代码优化方案.md",
    "design/remote_session_v2.html",
    "main_window/hub_pages.py",
    "main_window/pivot_page.py",
    "overview.md",
    "tools/deploy/prod_ssh.py",   # 用法示例里的 _recon.sh 是用户自备脚本
}


def git(*args):
    r = subprocess.run(["git", "-c", "core.quotePath=false"] + list(args),
                       cwd=ROOT, capture_output=True)
    return (r.stdout + r.stderr).decode("utf-8", "replace").splitlines()


def targets(scan_all):
    files = [f for f in git("ls-files") if f]
    if scan_all:
        files += [f for f in git("ls-files", "--others", "--exclude-standard")
                  if f]
    out = []
    for f in files:
        p = os.path.join(ROOT, f)
        if not f.endswith(TEXT_EXT) or not os.path.isfile(p):
            continue
        if os.path.getsize(p) > MAX_SIZE:
            continue
        if "/_archive/" in f:      # 归档脚本是冻结历史，不参与断链判定
            continue
        out.append(f)
    return out


def is_glob_prefix(token):
    """判断 token 是否为通配写法被截断后的前缀（如 `tools/probe/probe_`）。

    正则不收录 `*` `{` 等字符，因此 `tools/probe/probe_*.py` 只会捕获到
    `tools/probe/probe_`。同目录下存在以该前缀开头的条目即算引用有效。
    """
    parent, stem = os.path.dirname(token), os.path.basename(token)
    if not parent or not stem:
        return False
    try:
        entries = os.listdir(os.path.join(ROOT, parent))
    except OSError:
        return False
    return any(e.startswith(stem) for e in entries)


def main():
    scan_all = "--all" in sys.argv[1:]
    files = targets(scan_all)
    broken = {}
    debt = {}
    checked = 0
    for rel in files:
        path = os.path.join(ROOT, rel)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        sink = debt if rel in KNOWN_STALE else broken
        for i, line in enumerate(text.splitlines(), 1):
            for m in REF.finditer(line):
                token = m.group(1).rstrip(TRIM)
                if not token or token.endswith("/"):
                    continue
                checked += 1
                if not os.path.exists(os.path.join(ROOT, token)) \
                        and not is_glob_prefix(token):
                    sink.setdefault(rel, []).append((i, token))
    print("扫描文件 %d 个 / 路径引用 %d 处" % (len(files), checked))
    if debt:
        print("\n既有债务（历史文档，不计入失败）：%d 处 / %d 个文件"
              % (sum(len(v) for v in debt.values()), len(debt)))
        for rel in sorted(debt):
            print("  %-46s %d 处" % (rel, len(debt[rel])))
    if not broken:
        print("\nPASS：无新增断链引用")
        return 0
    print("\nFAIL：断链 %d 处，分布在 %d 个文件"
          % (sum(len(v) for v in broken.values()), len(broken)))
    for rel in sorted(broken):
        print("\n  %s" % rel)
        for ln, token in broken[rel][:12]:
            print("    L%-5d %s" % (ln, token))
        if len(broken[rel]) > 12:
            print("    ... 另 %d 处" % (len(broken[rel]) - 12))
    return 1


if __name__ == "__main__":
    sys.exit(main())
