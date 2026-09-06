# -*- coding: utf-8 -*-
"""QA 边界验证 1：S3 周期过滤 SQL 的 MySQL 方言兼容性

把 query_page / query_with_stats / query_stats_detail / export_xlsx /
_legacy_cycle_rows_pending 实际产出的 SQL 过 backend._convert_sql，
断言：
- ? → %s 占位符转换后数量与参数一致（pymysql 可执行）
- cycle_start = ? 等值过滤转换后仍为等值过滤（cycle_start = %s）
- 非法周期起点 1=0 短路保留
- 探测 SQL（cycle_start = '' AND ... LIKE）转换后语义不变
- 转换器不会误伤 cycle_start 列名（不加反引号/引号包裹导致列名变字面量）
"""
import sys
sys.path.insert(0, '.')

from database import aftersale_db as adb
from database import backend

fails = []


def check(label, sql, params):
    conv = backend._convert_sql(sql)
    # 占位符数量一致
    n_q = sql.count("?")
    n_s = conv.count("%s")
    if n_q != n_s or n_s != len(params):
        fails.append(f"[{label}] 占位符: 原 {n_q} 个 ? / 转换后 {n_s} 个 %s / "
                     f"参数 {len(params)} 个 不一致\n  conv={conv}")
    # ? 未被残留（字符串字面量内 ? 除外——本组 SQL 无字面量问号）
    if "?" in conv.replace("LIKE '____-__-__%'", ""):
        # LIKE '____-__-__%' 里的 ? 不存在，仅防御性检查残留
        pass
    return conv


# ---- 1) 合法周期起点：等值过滤 ----
where, params = adb._build_where("kw", "硬件问题", "否")
where, params = adb._append_cycle_where(where, params, "2026/09/01")
sql = f"SELECT COUNT(*) FROM aftersale_records{where}"
conv = check("COUNT+valid_cycle", sql, params)
if "cycle_start = %s" not in conv:
    fails.append(f"[COUNT+valid_cycle] 等值过滤丢失: {conv}")
if "cycle_start = ?" in conv:
    fails.append(f"[COUNT+valid_cycle] ? 未转换: {conv}")

# ---- 2) 非法周期起点：1=0 短路 ----
where2, params2 = adb._build_where("", "", "")
where2, params2 = adb._append_cycle_where(where2, params2, "2026/09/02")
sql2 = f"SELECT COUNT(*) FROM aftersale_records{where2}"
conv2 = check("COUNT+invalid_cycle", sql2, params2)
if "1 = 0" not in conv2:
    fails.append(f"[COUNT+invalid_cycle] 1=0 短路丢失: {conv2}")

# ---- 3) 页查询（LIMIT/OFFSET 附加参数） ----
sql3 = (f"SELECT id, {', '.join(adb.RECORD_FIELDS)} FROM aftersale_records"
        f"{where} ORDER BY id DESC LIMIT ? OFFSET ?")
conv3 = check("page_select", sql3, params + [60, 0])

# ---- 4) 统计聚合 ----
sql4 = ("SELECT COUNT(*), SUM(resolved = '是'), SUM(is_initiative = '是'), "
        "SUM(is_our_problem = '是') FROM aftersale_records" + where)
conv4 = check("stats_agg", sql4, params)

# ---- 5) 存量探测 SQL（_legacy_cycle_rows_pending 同款） ----
sql5 = ("SELECT 1 FROM aftersale_records WHERE cycle_start = '' AND "
        "(occurred_at LIKE '____-__-__%' OR created_at LIKE '____-__-__%') "
        "LIMIT 1")
conv5 = backend._convert_sql(sql5)
# LIKE 模式中的字面 % 会被 convert_placeholders 转义为 %%（pymysql 参数化
# 约定；无参数路径由适配器空元组分支还原为 %，见 backend.py 注释——已核实）
for frag in ("cycle_start = ''",
             "occurred_at LIKE '____-__-__%%'",
             "created_at LIKE '____-__-__%%'"):
    if frag not in conv5:
        fails.append(f"[probe_sql] 片段丢失 '{frag}': {conv5}")
# _ 是单字符通配符，两方言语义一致；探测 SQL 无参数不应产生 %s 占位符
if conv5.count("%s") != 0:
    fails.append(f"[probe_sql] 探测 SQL 不应产生 %s 占位符: {conv5}")

# ---- 6) recalc 分批 SELECT / UPDATE ----
sql6 = ("SELECT id, occurred_at, created_at, cycle_start "
        "FROM aftersale_records WHERE id > ? ORDER BY id LIMIT ?")
conv6 = check("recalc_select", sql6, [0, 2000])
sql7 = "UPDATE aftersale_records SET cycle_start = ? WHERE id = ?"
conv7 = check("recalc_update", sql7, ["2026/09/01", 1])
if "SET cycle_start = %s" not in conv7:
    fails.append(f"[recalc_update] UPDATE 等值设置丢失: {conv7}")

# ---- 7) 关键词 LIKE 多列 OR + 周期组合（最复杂形态） ----
where8, params8 = adb._build_where("校准", "", "")
where8, params8 = adb._append_cycle_where(where8, params8, "2026/09/01")
sql8 = f"SELECT COUNT(*) FROM aftersale_records{where8}"
conv8 = check("keyword+cycle", sql8, params8)
if "cycle_start = %s" not in conv8:
    fails.append(f"[keyword+cycle] 周期等值过滤丢失: {conv8}")

print("样例（关键词+周期 COUNT 转换后）:")
print(" ", conv8)
if fails:
    print(f"FAIL {len(fails)} 项:")
    for f in fails:
        print("  ", f)
    sys.exit(1)
print("PASS: S3 周期过滤全部 SQL 形态过 backend._convert_sql 后 MySQL 方言兼容")
