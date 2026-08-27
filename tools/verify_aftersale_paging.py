# -*- coding: utf-8 -*-
"""P0 等价性验证：SQL 分页(query_page/query_with_stats) == 旧 Python 实现
覆盖 tue/mon/custom/month 四种周期模式 + 筛选组合 + 空 occurred_at 回退。
"""
import sys, sqlite3, random
from datetime import datetime, timedelta
sys.path.insert(0, '.')
from database import aftersale_db as adb


def adb_schema():
    from database import schema
    return schema.to_sqlite_ddl("aftersale_records")


# ---- 内存库 + 随机数据（含空 occurred_at 回退场景） ----
conn = sqlite3.connect(":memory:")
conn.executescript(adb_schema())
rng = random.Random(42)
base = datetime(2026, 1, 1)
rows_buf = []
for i in range(3000):
    occ_missing = rng.random() < 0.15            # 15% 无 occurred_at → 回退 created_at
    occ = (base + timedelta(days=rng.randint(0, 120), hours=rng.randint(0, 23))
           ).strftime("%Y-%m-%d %H:%M:%S") if not occ_missing else ""
    cre = (base + timedelta(days=rng.randint(0, 120), hours=rng.randint(0, 23))
           ).strftime("%Y-%m-%d %H:%M:%S")
    rows_buf.append((occ, cre, f"T{rng.randint(1,50)}",
                     "是" if rng.random() < 0.6 else "否",
                     "是" if rng.random() < 0.4 else "否",
                     "是" if rng.random() < 0.5 else "否",
                     "校准" if rng.random() < 0.2 else "正常",
                     "球桌问题" if rng.random() < 0.5 else "设备故障"))
cols = ("occurred_at", "created_at", "table_no", "resolved",
        "is_initiative", "is_our_problem", "problem", "issue_type")
sql = (f"INSERT INTO aftersale_records ({', '.join(cols)}) "
       f"VALUES ({', '.join('?'*len(cols))})")
conn.executemany(sql, rows_buf)
conn.commit()
adb._conn = lambda: conn

# ---- 旧实现（对照）：Python 周期过滤 + 内存切片 ----
def old_query(page_no, page_size, keyword="", cycle_start="", issue_type="",
              resolved="", is_initiative="", is_our_problem=""):
    where, params = adb._build_where(keyword, issue_type, resolved,
                                     is_initiative, is_our_problem)
    cur = conn.execute(
        f"SELECT id, {', '.join(adb.RECORD_FIELDS)} FROM aftersale_records"
        f"{where} ORDER BY id DESC", params)
    rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    if cycle_start:
        rows = [r for r in rows if adb._match_cycle(r, cycle_start)]
    total = len(rows)
    off = (max(1, page_no)-1)*page_size
    return total, rows[off:off+page_size]

def old_stats(keyword="", cycle_start="", issue_type=""):
    where, params = adb._build_where(keyword, issue_type, "")
    cur = conn.execute(
        f"SELECT occurred_at, created_at, resolved, is_initiative, "
        f"is_our_problem FROM aftersale_records{where}", params)
    recs = [dict(zip(("occurred_at","created_at","resolved",
                      "is_initiative","is_our_problem"), r)) for r in cur.fetchall()]
    if cycle_start:
        recs = [r for r in recs if adb._match_cycle(r, cycle_start)]
    n = len(recs)
    return {"total": n, "resolved": sum(1 for r in recs if r["resolved"]=="是"),
            "unresolved": n - sum(1 for r in recs if r["resolved"]=="是"),
            "initiative": sum(1 for r in recs if r["is_initiative"]=="是"),
            "our_problem": sum(1 for r in recs if r["is_our_problem"]=="是")}

def old_stats_detail(keyword="", cycle_start="", issue_type="",
                      trend_start="", trend_end=""):
    """旧实现：全表取回 + Python 聚合（对照用）"""
    where, params = adb._build_where(keyword, issue_type, "")
    cur = conn.execute(
        "SELECT occurred_at, created_at, region, issue_type, resolved, "
        "is_initiative, is_our_problem FROM aftersale_records" + where, params)
    recs = [dict(zip(("occurred_at","created_at","region","issue_type",
                      "resolved","is_initiative","is_our_problem"), r))
            for r in cur.fetchall()]
    if cycle_start:
        recs = [r for r in recs if adb._match_cycle(r, cycle_start)]
    n = len(recs)
    summary = {"total": n, "resolved": sum(1 for r in recs if r["resolved"]=="是"),
               "unresolved": n - sum(1 for r in recs if r["resolved"]=="是"),
               "rate": int(round(sum(1 for r in recs if r["resolved"]=="是")*100/n)) if n else 0,
               "initiative": sum(1 for r in recs if r["is_initiative"]=="是"),
               "our_problem": sum(1 for r in recs if r["is_our_problem"]=="是")}
    daily_map = {}
    for r in recs:
        d = str(r["occurred_at"] or r["created_at"] or "")[:10]
        if not d: continue
        if trend_start and d < trend_start: continue
        if trend_end and d > trend_end: continue
        item = daily_map.setdefault(d, [0,0])
        item[0] += 1
        if r["resolved"] == "是": item[1] += 1
    daily = [{"date": d, "count": c, "resolved": rd} for d, (c, rd) in sorted(daily_map.items())]
    region_map = {}
    for r in recs:
        k = str(r["region"] or "").strip() or "未填地区"
        region_map[k] = region_map.get(k, 0) + 1
    regions = [{"region": k, "count": v} for k, v in sorted(region_map.items(), key=lambda kv: kv[1], reverse=True)]
    type_map = {}
    for r in recs:
        t = str(r["issue_type"] or "").strip() or "未分类"
        item = type_map.setdefault(t, [0,0])
        item[0] += 1
        if r["resolved"] == "是": item[1] += 1
    types = [{"issue_type": t, "count": c, "resolved": rd, "unresolved": c-rd}
             for t, (c, rd) in sorted(type_map.items(), key=lambda kv: kv[1][0], reverse=True)]
    return {"summary": summary, "daily": daily, "regions": regions, "types": types}


def old_cycle_options():
    """旧实现：全表取回两列 + Python 逐行归属（对照用）"""
    cur = conn.execute("SELECT occurred_at, created_at FROM aftersale_records")
    cycles = {adb._record_cycle(occ, cre) for occ, cre in cur.fetchall()}
    cycles.discard(None)
    return sorted(cycles, reverse=True)


# ---- 四种周期模式 × 多周期 × 筛选组合 对照 ----
mode_sets = {
    "tue":    {"type": "tue"},
    "mon":    {"type": "mon"},
    "custom": {"type": "custom", "start": "2026-01-05", "span": 10},
    "month":  {"type": "month"},
}
fails = []
checks = 0
for mname, mode in mode_sets.items():
    adb.save_cycle_mode(mode)
    # 取库中几个真实周期作为筛选目标
    cyc = set()
    for (occ, cre) in conn.execute("SELECT occurred_at, created_at FROM aftersale_records"):
        c = adb._record_cycle(occ, cre)
        if c: cyc.add(c)
    for c in sorted(cyc)[:3]:
        for combo in [dict(), dict(keyword="校准"), dict(issue_type="球桌问题"),
                      dict(resolved="否"), dict(is_initiative="是")]:
            t_old, rows_old = old_query(1, 50, cycle_start=c, **combo)
            t_new, rows_new = adb.query_page(1, 50, cycle_start=c, **combo)
            checks += 1
            if t_old != t_new:
                fails.append(f"[{mname}] {c} {combo}: total 旧={t_old} 新={t_new}")
            if [r["id"] for r in rows_old] != [r["id"] for r in rows_new]:
                fails.append(f"[{mname}] {c} {combo}: 首页 id 集不一致")
            # 中页抽查
            t_old2, rows_old2 = old_query(3, 50, cycle_start=c, **combo)
            t_new2, rows_new2 = adb.query_page(3, 50, cycle_start=c, **combo)
            if [r["id"] for r in rows_old2] != [r["id"] for r in rows_new2]:
                fails.append(f"[{mname}] {c} {combo}: 第3页 id 集不一致")
            # 统计
            s_old = old_stats(keyword=combo.get("keyword",""), cycle_start=c,
                              issue_type=combo.get("issue_type",""))
            _, _, s_new = adb.query_with_stats(1, 50, cycle_start=c, **combo)
            for k in s_old:
                checks += 1
                if s_old[k] != s_new[k]:
                    fails.append(f"[{mname}] {c} {combo}: stats.{k} 旧={s_old[k]} 新={s_new[k]}")

            # 详细统计弹窗对照（summary/daily/regions/types）
            d_old = old_stats_detail(keyword=combo.get("keyword",""),
                                     cycle_start=c, issue_type=combo.get("issue_type",""))
            d_new = adb.query_stats_detail(keyword=combo.get("keyword",""),
                                           cycle_start=c, issue_type=combo.get("issue_type",""))
            for key in ("summary", "daily"):
                checks += 1
                if d_old[key] != d_new[key]:
                    fails.append(f"[{mname}] {c} {combo}: stats_detail.{key} 不一致\n"
                                 f"  旧={d_old[key]}\n  新={d_new[key]}")
            # regions/types：count 并列时旧实现顺序依赖扫描序（不稳定），
            # 按主键排序后比较集合内容
            for key in ("regions", "types"):
                pk = "region" if key == "regions" else "issue_type"
                checks += 1
                if sorted(d_old[key], key=lambda x: x[pk]) != sorted(d_new[key], key=lambda x: x[pk]):
                    fails.append(f"[{mname}] {c} {combo}: stats_detail.{key} 不一致\n"
                                 f"  旧={d_old[key]}\n  新={d_new[key]}")

        # 周期下拉选项对照（DISTINCT 日期 vs 全表两列）
        checks += 1
        o_old, o_new = old_cycle_options(), adb.get_cycle_options()
        if o_old != o_new:
            fails.append(f"[{mname}] get_cycle_options 不一致: 旧={o_old} 新={o_new}")

print(f"对照检查 {checks} 项")
if fails:
    print(f"FAIL {len(fails)} 项：")
    [print("  ", f) for f in fails[:15]]
    sys.exit(1)
print("PASS: SQL 分页与旧 Python 实现完全等价（四种周期模式 × 周期 × 筛选 × 统计）")
