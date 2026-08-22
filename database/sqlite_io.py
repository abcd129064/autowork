# -*- coding: utf-8 -*-
"""SQLite 只读工具（数据层基础设施）

提供基于 sqlite3.Connection 的只读读取辅助函数，供需要直接读取本地
SQLite 的场景复用（镜像推送机制 B 下线后，原 mysql_sync._read_sqlite 的
列交集/缺列补位语义由本模块承接，返回 dict 列表）。

约定：
- 只读：绝不执行 INSERT/UPDATE/DELETE/DDL，也不调用 commit()。
- 列交集：以 PRAGMA table_info 返回的实际列 ∩ 请求列；请求列中不存在于
  实际表的列，每行补 None。
- 表不存在：返回空列表（与历史 _read_sqlite 语义一致）。
"""

from typing import Sequence

import sqlite3


def read_sqlite_table(conn: sqlite3.Connection,
                      table: str,
                      columns: Sequence[str]) -> list:
    """读取指定表的全部行，返回 list[dict]

    Args:
        conn: sqlite3.Connection（只读使用，不写库）
        table: 表名（内部拼接 SQL，仅接受受信任表名）
        columns: 请求列序列；按此顺序作为 dict 的键

    Returns:
        list[dict]：每行一个 dict，键为 columns 顺序；请求列在实际表中
        缺失时该键值为 None（行数保持实际行数）。表不存在返回 []。
    """
    exist = [r[1] for r in conn.execute(
        f"PRAGMA table_info({table})").fetchall()]
    if not exist:
        return []  # 表不存在：无数据可读
    cols = [c for c in columns if c in exist]
    if cols:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        records = []
        for r in rows:
            rec = dict(zip(cols, r))
            # 输出键严格按请求列顺序；请求列中实际表缺失者补 None
            records.append({c: rec.get(c) for c in columns})
    else:
        # 请求列与实际列无交集：保留行数，所有请求列补 None
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        records = [{c: None for c in columns} for _ in range(count)]
    return records
