# -*- coding: utf-8 -*-
"""回归测试：管理面板 GUI 线程零同步数据库查询

背景（2026-09-20 卡死修复）：用户长时间使用运维管理面板后，清空搜索误点
搜索 / 300 行/页翻页会概率性彻底卡死 ~10 秒。根因：MySQL 网络闪断后
thread-local 旧连接半开（healthy 仍为 True），GUI 线程回调里残留的同步
table_db 查询（球桌页 _populate 的 get_submission_stats、健康页
_refresh_display、xqzg 实时行同步落库）会一直挂到 TCP 读超时才返回。

本测试从源码层面锁死三条不变式：
1. 各页 _populate/_on_query_finished/_refresh_display 函数体内不得出现
   任何 table_db.<查询> 直接调用（同步查询必须走 _DBQueryWorker）。
2. _DBQueryWorker.run 必须有 isInterruptionRequested 中断检查点（防止
   快速翻页时陈旧 Worker 照常打库堆积成连接风暴）。
3. _query_tables_page_with_stats 与球桌页加载链路对接（返回三元组）。
"""
import ast
import inspect

from windows.management import common as mgmt
from windows.management import health_page as hp
from windows.management import table_page as tp


def _body_calls_clean(func) -> list:
    """去 docstring/注释后再收集调用点（AST 级精确判定）"""
    src = textwrap_getsource(func)
    tree = ast.parse(src)
    fn = tree.body[0]
    body = fn.body
    # 跳过函数 docstring
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    calls = []
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "table_db"):
            calls.append(node.func.attr)
    return calls


def textwrap_getsource(func):
    import textwrap
    return textwrap.dedent(inspect.getsource(func))


# ---- 不变式 1：GUI 回调零同步查询 ----------------------------------------

def test_table_populate_has_no_sync_db_query():
    """球桌页 _populate 不得同步查库（旧版 get_submission_stats 即卡死根因）"""
    calls = _body_calls_clean(tp.TablePage._populate)
    assert not calls, f"_populate 出现同步 table_db 调用: {calls}"


def test_table_on_query_finished_has_no_sync_db_query():
    calls = _body_calls_clean(tp.TablePage._on_query_finished)
    assert not calls, f"_on_query_finished 出现同步 table_db 调用: {calls}"


def test_table_xqzg_live_done_saves_via_worker():
    """xqzg 实时行落库必须走 Worker，不允许 GUI 线程同步 save_xqzg"""
    src = textwrap_getsource(tp.TablePage._on_xqzg_live_done)
    assert "_DBQueryWorker(table_db.save_xqzg" in src, \
        "xqzg 实时行落库未走 Worker（GUI 线程同步写库会冻结界面）"


def test_health_refresh_display_has_no_sync_db_query():
    """健康页定时刷新不得 GUI 线程同步查库（闪断窗口内会整界面冻结）"""
    calls = _body_calls_clean(hp.HealthPage._refresh_display)
    assert not calls, f"_refresh_display 出现同步 table_db 调用: {calls}"


# ---- 不变式 2：Worker 中断检查点 ------------------------------------------

def test_db_query_worker_checks_interruption():
    src = inspect.getsource(mgmt._DBQueryWorker.run)
    assert "isInterruptionRequested" in src, \
        "_DBQueryWorker.run 丢失中断检查点：快速翻页时陈旧查询会照常打库堆积"


# ---- 不变式 3：球桌页统计随分页同批返回 ------------------------------------

def test_tables_page_with_stats_returns_triple():
    """_query_tables_page_with_stats 在 Worker 线程一次返回 (total, rows, hf)"""
    total, rows, hf = mgmt._query_tables_page_with_stats(
        1, 20, "", 30, True, True, True)
    assert isinstance(total, int)
    assert isinstance(rows, list)
    assert "by_table" in hf and "by_device" in hf


def test_table_page_size_options_cover_300():
    """卡死场景之一为 300 行/页翻页：确认该档位仍在且加载走异步链"""
    src = inspect.getsource(tp.TablePage._init_ui)
    assert "300" in src
    src_load = inspect.getsource(tp.TablePage._load_local)
    assert "_DBQueryWorker" in src_load and "_query_tables_page_with_stats" in src_load
