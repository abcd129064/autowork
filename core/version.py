# -*- coding: utf-8 -*-
"""应用版本号：基于 git 分支与提交次数自动计算

规则：{BASE_VERSION}.{当前分支累计提交数}，如 2.5.114；
非主分支（main/master）额外附分支标记，如 2.5.114-ai_build。
git 不可用时（打包后目录无 .git、git 未安装等）回退为
{BASE_VERSION}.0(+分支标记)，保证版本号始终可用。

主.次版本（BASE_VERSION）仍按语义化手工维护：
新增功能集 → 次版本 +1；修订号由提交次数自动得出，无需手工改。
"""

import json
import os
import subprocess
import sys

from core.app_paths import get_app_dir

# 主.次版本：手工维护（新增功能集 → 次版本 +1）
BASE_VERSION = "3.13"

# 视为"主分支"的名称：主分支版本号不带分支后缀
_MAIN_BRANCHES = {"main", "master"}

# 构建期写入的版本文件名（build_exe.py 生成到 exe 旁）；打包环境无 .git，
# 版本号在构建机算好后落盘，运行时优先读它，避免恒为 {BASE}.0
VERSION_FILENAME = "version.json"


def _read_frozen_version() -> str:
    """冻结环境读 exe 旁 version.json 的 version 字段；失败返回空串

    version.json 由 build_exe.py 在构建机（有 .git）写入，含完整版本号
    （BASE.提交数[-分支]）。打包产物运行时读它，绕开无 .git 只能回退
    {BASE}.0 的缺陷。开发环境（非 frozen）不走此路径。"""
    try:
        path = os.path.join(get_app_dir(), VERSION_FILENAME)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        v = str(data.get("version") or "").strip()
        return v
    except Exception:
        return ""


def _git(*args) -> str:
    """在应用目录执行 git 命令，返回去尾输出；失败返回空串"""
    try:
        out = subprocess.run(
            ["git", *args], cwd=get_app_dir(), capture_output=True,
            text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def get_branch_name() -> str:
    """当前分支名；detached HEAD / 非 git 环境返回空串"""
    b = _git("rev-parse", "--abbrev-ref", "HEAD")
    return "" if b == "HEAD" else b


def get_commit_count() -> int:
    """当前分支累计提交次数；失败返回 0"""
    try:
        return int(_git("rev-list", "--count", "HEAD") or 0)
    except ValueError:
        return 0


def get_app_version() -> str:
    """完整版本号：BASE.提交数[-分支]，如 2.5.114 或 2.5.114-ai_build

    优先级：① 冻结环境 exe 旁 version.json（构建期算好落盘）；
    ② git 实时计算（开发环境）；③ 回退 {BASE_VERSION}.0。"""
    if getattr(sys, "frozen", False):
        v = _read_frozen_version()
        if v:
            return v
    v = f"{BASE_VERSION}.{get_commit_count()}"
    b = get_branch_name()
    if b and b not in _MAIN_BRANCHES:
        v += f"-{b}"
    return v


# 模块级缓存（导入时计算一次，供各处直接引用）
APP_VERSION = get_app_version()
