# -*- coding: utf-8 -*-
"""冒烟：工作留痕面板（WorkLogHub）五页签与主窗口集成点离屏自证。

背景：AGENTS.md §5.1 记录两个解释器都不完整——E:\\ANACONDA 缺 qfluentwidgets，
   miniconda3 的 PySide6 需要内联引导（docs/Qt内联引导说明.md）才 import 得动。
   本脚本走引导，因此必须用 miniconda3 跑。
   刻意**不构造 MainWindow**：主窗口启动会写 config/ 与连 frpc，
   与本仓「跑测试前后 config/ database/ 零改动」的验收口径冲突。
   主窗口侧的四处集成点改为对源码做 AST/文本核对，运行时只验 Hub 与弹出窗。
   数据库一律换成临时目录里的独立 SQLite，并强制关掉 MySQL 主库模式：
   config/database.json 里 MySQL 是 enabled 且指向生产库，worklog_db 经
   table_db.get_conn 会直接打到生产（AGENTS.md §5.2「数据安全」同源约束）。

验证点：
  1. WorkLogHub 五页签文本与顺序（稿 299-303）、objectName、Pivot 缩窄生效
  2. 页头（稿 292-297）：标题 24px/700、副标题 13px、弹出按钮落进页头布局
  3. 五页签 switchTo 往返、detach_workers 不抛
  4. main_window 四处集成点 + 设置页启动项 + shutdown 的 Hub 清单
  5. open_hub_popout 的 nav_icons 键 == 五页签文本（少一个就退回内嵌 Pivot）
  6. HubPopoutWindow 拿到该 nav_icons 后是左侧子导航形态
  7. 五页签在种子数据下真的把内容填出来了（表格行/时间轴小节/导图图元/
     统计四卡两图一表/成员与分类），并跑通复制报告与导出 Excel 的后台链路

用法：
    C:\\Users\\shen_zhe\\miniconda3\\python.exe tools/smoke/smoke_worklog_hub.py
产物：stdout；退出码 0=全部通过，2=有断言失败，1=Hub 构造即失败
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# tools/smoke/ → 上溯三层 = 仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- Qt 内联引导（必须在 import PySide6 之前）----
import importlib.util

_handles = []
_spec = importlib.util.find_spec("PySide6")
_locs = list(getattr(_spec, "submodule_search_locations", None) or [])
if _locs:
    _pkg = _locs[0]
    for _d in (_pkg, os.path.dirname(_pkg),
               os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")):
        if os.path.isdir(_d):
            try:
                _handles.append(os.add_dll_directory(_d))
            except OSError:
                pass
    os.environ["QT_PLUGIN_PATH"] = os.path.join(_pkg, "plugins")
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                          os.path.join(_pkg, "plugins", "platforms"))

import ast  # noqa: E402
import tempfile  # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OK = True


def wait(ms):
    """真跑事件循环：页签取数全在 QThread 里，processEvents 收不到跨线程信号"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def check(tag, cond, detail=""):
    """累加断言而不遇错即停：一次运行尽量多暴露回归点"""
    global OK
    OK = OK and bool(cond)
    print(("  [PASS] " if cond else "  [FAIL] ") + tag
          + (" :: " + str(detail) if detail else ""))


def _by_class(root, name):
    """按类名找子控件：私有绘制类（_EntryCard/TagPill）不导出，冒烟只认名字"""
    from PySide6.QtWidgets import QWidget
    return [w for w in root.findChildren(QWidget)
            if type(w).__name__ == name]


def isolate_db():
    """把面板的库换成临时目录里的独立 SQLite，并关掉 MySQL 主库模式

    返回临时目录：导出 Excel 也落在这里，绝不碰仓库的 database/ 与生产库。
    """
    tmp = tempfile.mkdtemp(prefix="wl_smoke_")
    import core.app_paths
    core.app_paths.get_app_dir = lambda: tmp
    import database.backend as backend
    backend.is_mysql_test_mode = lambda: False
    import sqlite3
    import database.table_db as table_db
    from database import schema
    db_path = tmp + "/t.db"
    table_db.DB_PATH = db_path
    table_db._conn = None
    sl = sqlite3.connect(db_path)
    for table in schema.WORKLOG_TABLES:
        sl.executescript(schema.to_sqlite_ddl(table))
    sl.commit()
    sl.close()
    return tmp


def seed_records():
    """三成员 × 两周记录：数量够触发分组、堆叠与导图三级，又不至于跑不完"""
    from PySide6.QtCore import QDate
    from database import worklog_db as wdb
    today = QDate.currentDate()
    mids = [wdb.insert_member(name) for name in ("张工", "李工", "王工")]
    titles = ("接口联调", "分页性能优化", "隧道巡检", "SOP 文档整理")
    count = 0
    for day in range(14):
        date = today.addDays(-day).toString("yyyy-MM-dd")
        for k in range(2):
            n = count
            wdb.insert_record(dict(
                work_date=date, member_id=mids[n % 3],
                title="%s-%d" % (titles[n % 4], n),
                project=("AutoWork", "球房运维", "")[n % 3],
                category=("开发", "运维", "文档", "测试")[n % 4],
                hours=round(0.5 + (n % 5) * 0.5, 1),
                progress=(n * 20) % 101,
                status="已完成" if n % 2 == 0 else "进行中"))
            count += 1
    return mids, count


# ==================== 主窗口源码侧核对 ====================

_MAIN = os.path.join(ROOT, "main_window", "main_window.py")
_HUBS = os.path.join(ROOT, "main_window", "hub_pages.py")
_SHUT = os.path.join(ROOT, "main_window", "shutdown.py")

PAGE_TEXTS = ("记录量化", "时间轴", "思维导图", "统计汇总", "成员与分类")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _nav_icons_for_worklog(main_src):
    """取出 open_hub_popout 里 nav_icons 字典中 worklogHub 那一支：[(页面文本, 图标名)]

    用 AST 而不是正则：字典跨多行、值是 FluentIcon 成员（非字面量），
    正则匹配中文键很容易被后续改动悄悄破坏。连图标名一起取出来，
    才能在 [6] 里用主窗口的真实映射建弹出窗（图标名写错会在运行时才炸）。
    """
    tree = ast.parse(main_src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "open_hub_popout")
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") == "nav_icons" for t in node.targets):
            continue
        value = node.value.func.value if isinstance(node.value, ast.Call) \
            else node.value
        for key_node, val_node in zip(value.keys, value.values):
            if ast.literal_eval(key_node) != "worklogHub":
                continue
            if not isinstance(val_node, ast.Dict):
                return None
            return [(ast.literal_eval(k), v.attr)
                    for k, v in zip(val_node.keys, val_node.values)]
    return None


def main() -> int:
    app = QApplication.instance() or QApplication(list(sys.argv))
    tmp = isolate_db()

    print("\n[1] WorkLogHub 结构")
    try:
        from windows.worklog.hub import WorkLogHub
        hub = WorkLogHub()
        hub.resize(1400, 860)
        hub.show()
        for _ in range(6):
            app.processEvents()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("1.1 WorkLogHub 构造无异常", False, repr(e))
        return 1
    check("1.1 WorkLogHub 构造无异常", True)
    check("1.2 objectName=worklogHub", hub.objectName() == "worklogHub",
          hub.objectName())

    texts = [t for _p, t, _i in getattr(hub, "_page_meta", [])]
    check("1.3 五页签文本与顺序（稿 299-303）", tuple(texts) == PAGE_TEXTS, texts)
    names = [p.objectName() for p in hub.pages()]
    check("1.4 子页 objectName 均非空且唯一",
          len(set(names)) == 5 and all(names), names)

    sizes = [(i.font().pixelSize(), i.height()) for i in hub.pivot.items.values()]
    check("1.5 lock_pivot_width：item 13px/24px",
          sizes and all(s == (13, 24) for s in sizes), sizes)

    print("\n[2] 页头（稿 292-297）")
    lay = hub.layout()
    check("2.1 布局四槽 [页头, Pivot 行, 下划线, 页签体]",
          lay.count() == 4, lay.count())
    head = lay.itemAt(0).widget()
    check("2.2 页头是 QWidget 且含标题", head is not None)
    from qfluentwidgets import TitleLabel, CaptionLabel
    titles = head.findChildren(TitleLabel)
    check("2.3 标题「工作留痕」24px/700",
          bool(titles) and titles[0].text() == "工作留痕"
          and titles[0].font().pixelSize() == 24
          and titles[0].font().weight() == 700,
          [(t.text(), t.font().pixelSize(), t.font().weight()) for t in titles])
    subs = [c for c in head.findChildren(CaptionLabel) if "量化管理" in c.text()]
    check("2.4 副标题 13px", bool(subs) and subs[0].font().pixelSize() == 13,
          [(s.text(), s.font().pixelSize()) for s in subs])
    check("2.5 弹出按钮与提示文本已落进页头（不是页面右上角浮动）",
          hub.btn_popout.parent() is head and hub.lbl_popout_hint.parent() is head,
          (hub.btn_popout.parent(), hub.lbl_popout_hint.parent()))
    check("2.6 提示文本改为设计稿文案「弹出此窗口」",
          hub.lbl_popout_hint.text() == "弹出此窗口", hub.lbl_popout_hint.text())
    check("2.7 Pivot 下划线在第 3 槽",
          type(lay.itemAt(2).widget()).__name__ == "Hairline",
          type(lay.itemAt(2).widget()).__name__)
    check("2.8 页签体在第 4 槽（QStackedWidget）",
          lay.itemAt(3).widget() is hub.stack)

    print("\n[3] 页签切换与线程收尾")
    bad_stack, bad_pivot = [], []
    for page in hub.pages():
        hub.switchTo(page)
        app.processEvents()
        cur = hub.stack.currentWidget()
        if cur is not page:
            bad_stack.append((page.objectName(), cur.objectName()))
        # currentRouteKey() 才是当前项的键；currentItem() 返回的是 PivotItem 控件
        key = hub.pivot.currentRouteKey()
        if key != page.objectName():
            bad_pivot.append((page.objectName(), key))
    check("3.1 五页签 switchTo 全部生效", not bad_stack, bad_stack)
    check("3.2 Pivot 高亮随页签同步", not bad_pivot, bad_pivot)
    try:
        hub.detach_workers()
        check("3.3 detach_workers 不抛", True)
    except Exception as e:
        check("3.3 detach_workers 不抛", False, repr(e))

    print("\n[4] 主窗口集成点（源码核对，不启动 MainWindow）")
    main_src = _read(_MAIN)
    check("4.1 _build_hub_pages 实例化 worklog_hub",
          "self.worklog_hub = WorkLogHub(self)" in main_src)
    check("4.2 一级导航元组含 (worklog_hub, FluentIcon.EDIT, \"工作留痕\")",
          '(self.worklog_hub, FluentIcon.EDIT, "工作留痕")' in main_src)
    order = main_src.find('(self.tool_hub, FluentIcon.DEVELOPER_TOOLS, "工具")')
    check("4.3 导航项排在「工具」之后、分隔线之前",
          0 < order < main_src.find('(self.worklog_hub, FluentIcon.EDIT')
          < main_src.find("self.navigationInterface.addSeparator()"))
    check("4.4 启动默认页字典含 worklogHub",
          '"worklogHub": self.worklog_hub,' in main_src)
    check("4.5 弹出窗标题映射含「工作留痕面板」",
          '"worklogHub": "工作留痕面板"' in main_src)
    check("4.6 设置页启动项下拉含「工作留痕」",
          '("工作留痕", "worklogHub")' in _read(_HUBS))
    check("4.7 关窗清理的 Hub 清单含 worklog_hub",
          '"worklog_hub"' in _read(_SHUT))
    check("4.8 未按设计稿误改 windows/management/window.py",
          "worklog" not in _read(os.path.join(
              ROOT, "windows", "management", "window.py")).lower())

    print("\n[5] nav_icons 覆盖五页签")
    pairs = _nav_icons_for_worklog(main_src)
    check("5.1 解析到 worklogHub 的 nav_icons", pairs is not None, pairs)
    if pairs is not None:
        check("5.2 键序 == 五页签文本（少一个会退回内嵌 Pivot 形态）",
              tuple(t for t, _i in pairs) == PAGE_TEXTS, pairs)

    print("\n[6] 弹出窗为左侧子导航形态")
    try:
        from qfluentwidgets import FluentIcon
        from main_window.hub_popout import HubPopoutWindow

        class _StubMain:
            def _append_log(self, *_a):
                pass

            def _load_settings(self):
                return {}

        # 用主窗口源码里的真实图标映射建窗：图标名写错要到用户点弹出才炸
        bad_icons = [n for _t, n in (pairs or [])
                     if getattr(FluentIcon, n, None) is None]
        check("6.1 nav_icons 的 FluentIcon 成员都存在", not bad_icons, bad_icons)
        nav_icons = {t: getattr(FluentIcon, n) for t, n in (pairs or [])
                     if n not in bad_icons}
        pop = HubPopoutWindow(WorkLogHub, _StubMain(), "工作留痕面板",
                              nav_icons=nav_icons)
        for _ in range(6):
            app.processEvents()
        keys = list(pop.navigationInterface.panel.items.keys())
        check("6.2 左侧导航挂了五个子页", len(keys) == 5, keys)
        check("6.3 Hub 空壳已隐藏（视图改挂窗口导航）", pop.hub.isHidden())
        check("6.4 弹出窗内不再提供二次弹出",
              not pop.hub.btn_popout.isVisible()
              and not pop.hub.lbl_popout_hint.isVisible())
        check("6.5 switchTo 已重绑到弹出窗（跨页跳转仍可路由）",
              pop.hub.switchTo == pop.switchTo)
        pop.close()
        pop.deleteLater()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("6.x 弹出窗链路", False, repr(e))

    print("\n[7] 五页签内容渲染（种子数据）")
    mids, seeded = seed_records()
    check("7.0 种子数据写入", seeded >= 20 and len(mids) == 3, (seeded, mids))

    rp = hub.records_page
    hub.switchTo(rp)
    rp.refresh()
    wait(1600)
    from windows.worklog.delegate import ROLE_GROUP
    rows = rp._table.rowCount()
    groups = sum(1 for r in range(rows)
                 if rp._table.item(r, 0)
                 and rp._table.item(r, 0).data(ROLE_GROUP))
    check("7.1 记录表按天分组渲染（稿 308-410）",
          rows > 0 and groups >= 2, (rows, groups))
    cards = [c.v_label.text() for c in (rp.card_hours, rp.card_tasks,
                                        rp.card_rate, rp.card_percap)]
    check("7.2 记录页汇总卡四格都有数", all(cards), cards)

    tp = hub.timeline_page
    hub.switchTo(tp)
    tp.refresh()
    wait(1600)
    sections = tp._host_lay.count() - 1          # 末尾还有一个 addStretch
    entries = _by_class(tp._host, "_EntryCard")
    check("7.3 时间轴出小节与条目卡（稿 412-455）",
          sections >= 2 and len(entries) >= sections, (sections, len(entries)))

    mp = hub.mindmap_page
    hub.switchTo(mp)
    mp.refresh()
    wait(1600)
    scene = mp.canvas.view._scene
    kinds = {}
    for item in scene.items():
        kinds[type(item).__name__] = kinds.get(type(item).__name__, 0) + 1
    # 稿 508：根 1 + 分支 N + 叶子 M，连线数 = 分支数（根→分支）+ 叶子数
    expect_links = kinds.get("_GroupNode", 0) + kinds.get("_LeafNode", 0)
    check("7.4 导图三级图元齐（稿 459-518）",
          kinds.get("_RootNode") == 1 and kinds.get("_GroupNode", 0) >= 2
          and kinds.get("_LeafNode", 0) >= 2, kinds)
    check("7.5 贝塞尔连线数量对得上", kinds.get("_LinkItem") == expect_links,
          (kinds.get("_LinkItem"), expect_links))
    node = next(i for i in scene.items()
                if type(i).__name__ == "_GroupNode")
    key, before = node.key, kinds.get("_LeafNode", 0)
    mp._on_group_clicked(node)
    wait(300)
    after = sum(1 for i in mp.canvas.view._scene.items()
                if type(i).__name__ == "_LeafNode")
    check("7.6 分支折叠后叶子消失（稿 512 折叠态）", after < before,
          (before, after))
    node2 = next(i for i in mp.canvas.view._scene.items()
                 if type(i).__name__ == "_GroupNode" and i.key == key)
    mp._on_group_clicked(node2)
    wait(300)
    back = sum(1 for i in mp.canvas.view._scene.items()
               if type(i).__name__ == "_LeafNode")
    check("7.7 再展开叶子回来", back == before, (after, back))

    sp = hub.stats_page
    hub.switchTo(sp)
    sp.refresh()
    wait(1600)
    scards = [c.v_label.text() for c in (sp.card_hours, sp.card_tasks,
                                         sp.card_rate, sp.card_percap)]
    check("7.8 统计页四卡有数（稿 531-540）", all(scards), scards)
    check("7.9 按日 = 近 7 天堆叠柱（稿 544-552）",
          len(sp.bars._bars) == 7 and sp.bars._hot == 6,
          [(b[0], b[1]) for b in sp.bars._bars])
    check("7.10 环图按分类切片（稿 555-565）",
          len(sp.donut._slices) >= 2 and sp.donut._total > 0,
          [(s[0], s[1]) for s in sp.donut._slices])
    check("7.11 成员汇总表按成员出行（稿 574-589）",
          sp._table.rowCount() == 3, sp._table.rowCount())
    sp.seg_period.setCurrentItem("week")
    wait(1600)
    check("7.12 切按周重查（稿 590 切换即重查）",
          len(sp.bars._bars) == 4 and sp._totals is not None,
          [b[0] for b in sp.bars._bars])
    sp.seg_period.setCurrentItem("month")
    wait(1600)
    check("7.13 切按月重查", len(sp.bars._bars) == 6, [b[0] for b in sp.bars._bars])
    sp.seg_period.setCurrentItem("day")
    wait(1600)

    sp._on_copy_report()
    wait(1600)
    text = QApplication.clipboard().text()
    check("7.14 复制本期文字报告走后台 + 剪贴板（稿 591）",
          bool(text) and "统计" in text, text.splitlines()[:1])

    xlsx = os.path.join(tmp, "smoke.xlsx")
    accepted = sp._ctl.export_xlsx(xlsx, "2000-01-01", "2100-01-01", 0)
    wait(2500)
    check("7.15 导出 Excel 落盘（整表口径，不分页）",
          accepted and os.path.exists(xlsx) and os.path.getsize(xlsx) > 0,
          (accepted, os.path.exists(xlsx),
           os.path.getsize(xlsx) if os.path.exists(xlsx) else 0))

    mb = hub.members_page
    hub.switchTo(mb)
    # 成员页只在首次 showEvent 拉候选（[3] 那趟还是空库），这里显式重拉
    mb._ctl.fetch_options()
    wait(1600)
    pills = _by_class(mb._cat_host, "TagPill")
    check("7.16 成员表与分类标签流渲染（稿 598-628）",
          mb._table.rowCount() == 3 and len(pills) >= 7,
          (mb._table.rowCount(), len(pills)))

    hub.hide()
    hub.deleteLater()
    app.processEvents()
    print("\n结果：" + ("全部通过" if OK else "存在失败项"))
    return 0 if OK else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
