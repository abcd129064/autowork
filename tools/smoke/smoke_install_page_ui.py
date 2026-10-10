# -*- coding: utf-8 -*-
"""smoke_install_page_ui：安装清单页离屏冒烟（2026-10-11）

验证点（隔离临时库，不碰真实数据库）：
1. InstallPage 可离屏实例化，6 列表头正确
2. 分页查询填充表格（含 room_count 动态列）
3. 月份/销售下拉候选异步加载
4. 导出链路产出与人工样例同构的 xlsx（表头/合并/列宽/格式）

用法：
    python tools/smoke/smoke_install_page_ui.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import tempfile

import database.backend as backend
backend.is_mysql_test_mode = lambda: False

import database.table_db as table_db

# 隔离临时库（必须在任何 _get_conn 调用前打补丁）
_tmp = tempfile.mkdtemp(prefix="smoke_install_")
table_db.DB_PATH = os.path.join(_tmp, "t.db")
table_db._conn = None
table_db._initialized = False
table_db._fts_available = False

_ROWS = [
    {"id": 1, "name": "389-13", "roomName": "胖胖台球馆", "onlineStatusName": "空闲",
     "remark": "", "cameraPassExt": "", "code": "C1", "roomCity": "上海",
     "sales": "刘绪德", "roomAddress": "上海市嘉定区柳湖路751号",
     "createTime": "2026-09-30 21:34:08", "status": 0,
     "sales_transfer": "谢正钱 → 贾高阳"},
    {"id": 2, "name": "389-14", "roomName": "胖胖台球馆", "onlineStatusName": "空闲",
     "remark": "", "cameraPassExt": "", "code": "C2", "roomCity": "上海",
     "sales": "刘绪德", "roomAddress": "上海市嘉定区柳湖路751号",
     "createTime": "2026-09-29 10:00:00", "status": 0},
    {"id": 3, "name": "290-00", "roomName": "APZ台球俱乐部", "onlineStatusName": "下线",
     "remark": "", "cameraPassExt": "", "code": "C3", "roomCity": "新疆",
     "sales": "新疆代理", "roomAddress": "新疆库尔勒市其兰巴格街382号",
     "createTime": "2026-09-30 00:49:48", "status": 0},
    {"id": 4, "name": "100-01", "roomName": "公司测试", "onlineStatusName": "空闲",
     "remark": "", "cameraPassExt": "", "code": "C4", "roomCity": "上海",
     "sales": "内部", "roomAddress": "x", "createTime": "2026-09-01 00:00:00",
     "status": 0},
]


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer

    from windows.management.install_page import (InstallPage, INSTALL_COLUMNS)

    app = QApplication.instance() or QApplication(sys.argv)

    # 造数（隔离库）：save_all 三列（createTime/roomAddress/sales）落库
    assert table_db.save_all(_ROWS) == 4, "save_all 写入失败"

    page = InstallPage()
    page.show()

    failures = []

    def check():
        try:
            # 1. 列定义：7 列且标题与样例对应（销售转移为 xqzg 增量列）
            titles = [t for _k, t, _w in INSTALL_COLUMNS]
            assert titles == ["球房名字", "球房地址", "本月安装数量",
                              "球桌编号", "安装时间", "销售", "销售转移"], titles
            assert page._table.columnCount() == 7

            # 2. 表格填充：公司测试默认排除 → 3 行；分组相邻（胖胖两行在前）
            assert page._table.rowCount() == 3, page._table.rowCount()
            first_room = page._table.item(0, 0).text()
            assert first_room == "胖胖台球馆", first_room
            assert page._table.item(0, 1).text() == "上海市嘉定区柳湖路751号"
            assert page._table.item(0, 2).text() == "2", "room_count 应为 2"
            assert page._table.item(0, 3).text() == "389-13"
            assert page._table.item(0, 4).text() == "2026-09-30", "安装时间取日期"
            assert page._table.item(0, 6).text() == "谢正钱 → 贾高阳", "销售转移列"
            assert page._table.item(1, 0).text() == "胖胖台球馆", "同球房应相邻"
            assert page._lbl_info.text().startswith("共 3 台"), page._lbl_info.text()

            # 3. 月份/销售候选
            assert page._month_combo.count() == 2, \
                page._month_combo.count()  # 全部月份 + 2026-09
            assert page._month_combo.itemText(1) == "2026-09"
            assert page._sales_combo.count() == 4, page._sales_combo.count()

            # 4. 导出链路（隔离库直调，UI 侧按钮逻辑同链路）
            out = os.path.join(_tmp, "export.xlsx")
            cnt = table_db.export_install_xlsx(out, ym="2026-09")
            assert cnt == 3, cnt
            from openpyxl import load_workbook
            ws = load_workbook(out).worksheets[0]
            assert ws.title == "球房安装清单9月", ws.title
            assert [c.value for c in ws[1]] == [
                "球房名字", "球房地址", "本月安装数量", "球桌编号",
                "安装时间", "销售-归属", "销售-催款", "销售转移"]
            merged = sorted(str(m) for m in ws.merged_cells.ranges)
            assert merged == ["A2:A3", "B2:B3", "C2:C3"], merged
            assert ws["D2"].number_format == "@"
            assert ws["E2"].number_format == "mm-dd-yy"
            print("smoke_install_page_ui: 16/16 项断言全部通过")
        except AssertionError as e:
            failures.append(str(e))
            print(f"FAIL: {e}")
        finally:
            app.quit()

    # 给异步 Worker（查询 + 候选 + 数据时间）留出完成时间
    QTimer.singleShot(1500, check)
    app.exec()

    page.hide()
    page.deleteLater()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
