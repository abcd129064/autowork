# -*- coding: utf-8 -*-
"""database.aftersale_query_sql 纯 SQL 构造回归（A3 queries 第一步）。"""
from database import aftersale_query_sql as q


def test_record_date_expr_prioritizes_occurred_then_created():
    assert "occurred_at LIKE '____-__-__%'" in q._RECORD_DATE_EXPR
    assert "ELSE created_at" in q._RECORD_DATE_EXPR
    assert q._RECORD_DATE_EXPR.startswith("substr(")


def test_build_where_empty_and_filters():
    where, params = q._build_where("", "", "")
    assert where == " WHERE deleted = 0"
    assert params == []

    where, params = q._build_where("", "硬件问题", "", "是", "否")
    assert where == (" WHERE deleted = 0 AND issue_type = ? AND "
                     "is_initiative = ? AND is_our_problem = ?")
    assert params == ["硬件问题", "是", "否"]


def test_build_where_keyword_expands_all_search_columns():
    where, params = q._build_where(" 球桌 ", "", "")
    assert where.count("LIKE ?") == len(q._SEARCH_FIELDS)
    assert params == ["%球桌%"] * len(q._SEARCH_FIELDS)
    assert where.startswith(" WHERE deleted = 0 AND (")


def test_build_order_whitelist_and_fallback():
    assert q._build_order("problem", "asc") == "problem ASC, id DESC"
    assert q._build_order("location", "asc") == (
        "room_name ASC, region ASC, table_no ASC, id DESC")
    assert q._build_order("resolved", "desc") == (
        "CASE resolved WHEN '是' THEN 1 WHEN '否' THEN 0 ELSE -1 END DESC, id DESC")
    assert q._build_order("ops", "asc") == "id DESC"
    assert q._build_order("", "") == "id DESC"
    assert "problem" in q.LIST_SORT_KEYS
    assert "ops" not in q.LIST_SORT_KEYS
