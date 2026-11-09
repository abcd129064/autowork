# -*- coding: utf-8 -*-
"""冒烟：画质提升工作台（VideoEnhanceHub）骨架与八处集成点离屏自证。

背景：工具页新增「画质提升」页签（design/video_enhance_tool.html），纯 ffmpeg
   滤镜链。引擎在 windows/tools/video_enhance.py（离线单测 tests/test_video_enhance.py
   已覆盖），线程在 workers/video_enhance_worker.py，本脚本只管界面与集成点。

   AGENTS.md §5.1：两个解释器都不完整——E:\\ANACONDA 缺 qfluentwidgets，
   miniconda3 的 PySide6 需要内联引导（docs/Qt内联引导说明.md）才 import 得动，
   所以本脚本必须用 miniconda3 跑。
   刻意**不构造 MainWindow**：主窗口启动会写 config/ 并连 frpc，与本仓
   「跑测试前后 config/ database/ 零改动」的验收口径冲突。八处集成点改为对
   源码做 AST/文本核对，运行时只验 Hub 与弹出窗。

验证点：
  1. Hub 五子页文本与顺序、objectName、Pivot 缩窄生效
  2. 弹出按钮改由 Pivot 行布局定位（不浮动，避免与外层 ToolHub 的按钮叠在一起）
  2b. 真构造一层 ToolHub，把内外两颗弹出按钮换算到同一坐标系验不重叠
      （ToolHub 构造失败时跳过不判失败：它还挂着别的会话在改的工作区）
  3. 五子页 switchTo 往返、paramsChanged 广播、detach_workers 不抛
  4. 八处注册点 A~H（tool_hub×2 / main_window×2 / hub_popout / app_settings /
     settings_mixin / hub_pages / shutdown）
  5. nav_icons 双覆盖：toolHub 六键、videoEnhanceHub 五键（少一个退回内嵌 Pivot）
  6. HubPopoutWindow 拿到该 nav_icons 后是左侧子导航形态
  7. worker 属性名三处一致（hub / _BUSY_ATTRS / shutdown 7b）
  8. 真 ffmpeg 端到端（找不到 ffmpeg 则跳过，不算失败）

用法：
    C:\\Users\\shen_zhe\\miniconda3\\python.exe tools/smoke/smoke_video_enhance_hub.py
产物：tools/_scratch/enhance_smoke/（不入库）
退出码：0=全部通过，2=有断言失败，1=Hub 构造即失败
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

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

OK = True
OUT = os.path.join(ROOT, "tools", "_scratch", "enhance_smoke")

PAGE_TEXTS = ("增强任务", "滤镜调参", "编码输出", "环境检测", "质量对比")
TAB_TEXT = "画质提升"


def check(tag, cond, detail=""):
    """累加断言而不遇错即停：一次运行尽量多暴露回归点"""
    global OK
    OK = OK and bool(cond)
    print(("  [PASS] " if cond else "  [FAIL] ") + tag
          + (" :: " + str(detail) if detail else ""))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _nav_icons_for(main_src, hub_key):
    """取 open_hub_popout 里 nav_icons 字典中某个 Hub 那一支：[(页面文本, 图标名)]

    用 AST 而不是正则：字典跨多行、值是 FluentIcon 成员（非字面量），
    正则匹配中文键很容易被后续改动悄悄破坏。连图标名一起取出来，才能在 [6]
    里用主窗口的真实映射建弹出窗（图标名写错要到用户点弹出才炸）。
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
            if ast.literal_eval(key_node) != hub_key:
                continue
            if not isinstance(val_node, ast.Dict):
                return None
            return [(ast.literal_eval(k), v.attr)
                    for k, v in zip(val_node.keys, val_node.values)]
    return None


def _rect_in(w, ancestor):
    """把控件几何换算到 ancestor 坐标系

    直接比 geometry() 会假阴性：外层按钮是 ToolHub 的直接子控件，内层按钮挂在
    VideoEnhanceHub → PivotPage 行布局里，两者坐标系差好几层，数值上「重叠」
    其实各在自己的父里。必须 mapTo 到同一个祖先再比。
    """
    return QRect(w.mapTo(ancestor, QPoint(0, 0)), w.size())


class StubMain(QWidget):
    """只顶宿主接口，不碰 config/ 与 frpc（构造 MainWindow 会两者都碰）"""

    def __init__(self):
        super().__init__()
        self.videos_dir = ""
        self.logs = []
        self.saved = []

    def _append_log(self, t):
        self.logs.append(t)

    def _load_settings(self):
        return {}

    def _save_settings(self, d):
        self.saved.append(d)

    def _show_info_bar(self, m, k="info", title=None, duration=2500):
        print(f"    <info:{k}> {m}")

    def _release_worker_safe(self, w):
        try:
            w.deleteLater()
        except RuntimeError:
            pass


def main() -> int:
    app = QApplication.instance() or QApplication(list(sys.argv))
    from windows.tools import video_enhance as ve
    from windows.tools.video_enhance_hub import (VideoEnhanceHub, WORKER_ATTRS,
                                                 W_PROBE)

    stub = StubMain()
    for a in WORKER_ATTRS:
        setattr(stub, a, None)

    print("\n[1] VideoEnhanceHub 结构")
    try:
        hub = VideoEnhanceHub(stub, stub)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("1.1 Hub 构造无异常", False, repr(e))
        return 1
    check("1.1 Hub 构造无异常", True)
    check("1.2 objectName=videoEnhanceHub",
          hub.objectName() == "videoEnhanceHub", hub.objectName())

    meta = list(getattr(hub, "_page_meta", []) or [])
    texts = tuple(t for _p, t, _i in meta)
    check("1.3 五子页文本与顺序", texts == PAGE_TEXTS, texts)
    names = [p.objectName() for p, _t, _i in meta]
    check("1.4 子页 objectName 均非空且唯一",
          len(set(names)) == 5 and all(names), names)
    check("1.5 stack 与 pivot 都是 5 项",
          hub.stack.count() == 5 and len(hub.pivot.items) == 5,
          (hub.stack.count(), len(hub.pivot.items)))
    sizes = [(i.font().pixelSize(), i.height()) for i in hub.pivot.items.values()]
    check("1.6 lock_pivot_width：item 13px/24px",
          sizes and all(s == (13, 24) for s in sizes), sizes)

    print("\n[2] 内联弹出按钮（改由 Pivot 行布局定位）")
    stub.resize(1300, 800)
    stub.show()          # 父不 show，后代 isVisible() 一律 False
    hub.resize(1200, 720)
    hub.show()
    for _ in range(6):
        app.processEvents()
    btn, pv = hub.btn_popout, hub.pivot
    check("2.1 按钮可见且落在 Pivot 右侧（不与其重叠）",
          btn.isVisible() and btn.x() > pv.x() + pv.width() - 1,
          f"btn.x={btn.x()} pivot.x={pv.x()} pivot.w={pv.width()}")
    check("2.2 按钮在本页可视区内", 0 < btn.x() < hub.width(),
          f"x={btn.x()} w={hub.width()}")
    check("2.3 提示文案改为「弹出工作台」",
          hub.lbl_popout_hint.text() == "弹出工作台", hub.lbl_popout_hint.text())
    check("2.4 提示文本与按钮同显隐",
          hub.lbl_popout_hint.isVisible() == btn.isVisible())
    hub.resize(900, 600)
    for _ in range(4):
        app.processEvents()
    check("2.5 缩窄后按钮仍贴着右边（布局定位，不是 move 硬摆）",
          btn.x() > pv.x() + pv.width() - 1 and btn.x() < hub.width(),
          f"btn.x={btn.x()} w={hub.width()}")
    hub.resize(1200, 720)

    print("\n[2b] 与外层 ToolHub 弹出按钮的位置关系（_inline_popout 的存在理由）")
    # 单独一份 StubMain：ToolHub 会建第二个 VideoEnhanceHub 实例并往宿主写
    # logs/saved，共用会让 [3] 的落盘断言读到别人的记录
    th_stub = StubMain()
    for a in WORKER_ATTRS:
        setattr(th_stub, a, None)
    try:
        from main_window.tool_hub import ToolHub
        th = ToolHub(th_stub)
    except Exception as e:
        # ToolHub 里还挂着别的会话正在改的工作区，构造失败不代表画质提升有问题
        print(f"  [SKIP] ToolHub 构造失败，跳过碰撞核对：{e!r}")
        th = None
    if th is not None:
        # ToolHub 的构造参数同时是宿主窗口**和 Qt 父对象**（tool_hub.py:
        # super().__init__(parent) + self._win = parent）。父不 show，子树
        # isVisible() 恒为 False 且布局根本不跑——按钮会停在 100x30 的初值上，
        # 量出来的「不重叠」是假通过。
        th_stub.resize(1560, 940)
        th_stub.show()
        th.resize(1400, 820)
        th.show()
        inner = th.video_enhance_work
        th.switchTo(inner)
        for _ in range(6):
            app.processEvents()
        bad, geo = [], []
        for w in (1500, 1100, 900):
            th.resize(w, 820)
            for _ in range(4):
                app.processEvents()
            o, i = th.btn_popout, inner.btn_popout
            go, gi = _rect_in(o, th), _rect_in(i, th)
            geo.append((w, go.getRect(), gi.getRect()))
            if not (th.isVisible() and o.isVisibleTo(th) and i.isVisibleTo(th)):
                bad.append((w, "控件树没显示出来", th.isVisible(),
                            o.isVisibleTo(th), i.isVisibleTo(th)))
            elif go.intersects(gi):
                bad.append((w, "重叠", go.getRect(), gi.getRect()))
        # 内层按钮必须真被布局撑开（浮动版是 move 硬摆，宽高恒为初值）
        check("2b.1 内外两颗弹出按钮在 1500/1100/900 宽度下均可见且不重叠",
              not bad, bad or geo)
        check("2b.2 两颗按钮都已被布局量出真实尺寸",
              all(g[1][2] > 30 and g[2][2] > 10 and g[2][3] > 10 for g in geo), geo)
        # 外层浮在页面右上角 y≈10；内层进了 Pivot 行，纵向必须整体落在外层之下，
        # 否则窗口一窄两颗就会并排叠住——这正是 _inline_popout 要解决的问题
        o_top = _rect_in(th.btn_popout, th).top()
        i_top = _rect_in(inner.btn_popout, th).top()
        check("2b.3 内层按钮纵向落在外层之下",
              i_top >= o_top + th.btn_popout.height(),
              f"outer_top={o_top} inner_top={i_top} h={th.btn_popout.height()}")
        th.hide()
        th_stub.hide()
        th.deleteLater()
        th_stub.deleteLater()

    print("\n[3] 切页 / 参数广播 / 收尾")
    bad_stack, bad_pivot = [], []
    for page, text, _i in meta:
        hub.switchTo(page)
        app.processEvents()
        if hub.stack.currentWidget() is not page:
            bad_stack.append(text)
        # currentRouteKey() 才是当前项的键；currentItem() 返回的是 PivotItem 控件
        if hub.pivot.currentRouteKey() != page.objectName():
            bad_pivot.append(text)
        rf = getattr(page, "refresh", None)
        if rf is not None:
            rf()
        app.processEvents()
    check("3.1 五子页 switchTo 全部生效", not bad_stack, bad_stack)
    check("3.2 Pivot 高亮随页签同步", not bad_pivot, bad_pivot)

    hits = []
    hub.paramsChanged.connect(lambda: hits.append(1))
    hub.set_param("crf", 21)
    app.processEvents()
    check("3.3 set_param 触发 paramsChanged 且写进 params",
          hits and hub.params.get("crf") == 21, (len(hits), hub.params.get("crf")))
    check("3.4 滤镜页 -vf 串随参数刷新",
          "cas=" in hub.filter_page.vf_edit.text() or not hub.params.get("sharpen_on"),
          hub.filter_page.vf_edit.text()[:120])
    check("3.5 编码页命令预览跟着刷新",
          bool(hub.encode_page.cmd_view.toPlainText().strip()))
    hub.apply_preset("off")
    app.processEvents()
    check("3.6 apply_preset(off) 后滤镜链只剩 format",
          hub.filter_page.vf_edit.text().strip() in
          ('-vf "format=yuv420p"', ""), hub.filter_page.vf_edit.text())
    hub.apply_preset("standard")
    app.processEvents()

    before = len(stub.saved)
    hub.save_now()
    app.processEvents()
    check("3.7 save_now 经宿主 _save_settings（不落 config/）",
          len(stub.saved) > before, f"{before}->{len(stub.saved)}")
    keys = set()
    for d in stub.saved:
        keys |= set(d)
    check("3.8 写回的键都是登记过的 enhance_* ",
          keys <= {"enhance_params", "enhance_history", "enhance_last_dir"}, keys)

    try:
        hub.detach_workers()
        check("3.9 detach_workers 不抛", True)
    except Exception as e:
        check("3.9 detach_workers 不抛", False, repr(e))

    print("\n[4] 八处注册点（源码核对，不启动 MainWindow）")
    tool_src = _read("main_window", "tool_hub.py")
    main_src = _read("main_window", "main_window.py")
    pop_src = _read("main_window", "hub_popout.py")
    shut_src = _read("main_window", "shutdown.py")
    sm_src = _read("main_window", "settings_mixin.py")
    hp_src = _read("main_window", "hub_pages.py")
    st_src = _read("core", "app_settings.py")

    # A：tool_hub 延迟 import + addPage。延迟是必须的——video_enhance_hub 顶层
    # 要借 tool_hub 的 _card/_make_terminal 等助手，放模块头就是 import 环。
    check("4.A1 ToolHub 内延迟 import VideoEnhanceHub",
          "from windows.tools.video_enhance_hub import VideoEnhanceHub" in tool_src
          and tool_src.index("from windows.tools.video_enhance_hub")
          > tool_src.index("class ToolHub"))
    check(f'4.A2 addPage(video_enhance_work, "{TAB_TEXT}")',
          f'self.addPage(self.video_enhance_work, "{TAB_TEXT}")' in tool_src)
    a_add = tool_src.index(f'self.addPage(self.video_enhance_work, "{TAB_TEXT}")')
    a_cls = tool_src.index("class ToolHub")
    a_lock = tool_src.index("self.lock_pivot_width()", a_cls)
    check("4.A3 新页在 lock_pivot_width 之前注册（晚于它则锁宽不含新项）",
          a_cls < a_add < a_lock, (a_cls, a_add, a_lock))

    # B：外层 toolHub 的 nav_icons 必须含同名键，否则弹出工具面板退回内嵌 Pivot
    check(f'4.B1 nav_icons["toolHub"] 含 "{TAB_TEXT}"',
          f'"{TAB_TEXT}": FluentIcon.' in main_src)
    # C：videoEnhanceHub 自己的标题与五键
    check('4.C1 标题映射含 "videoEnhanceHub": "画质提升工作台"',
          '"videoEnhanceHub": "画质提升工作台"' in main_src)
    check('4.C2 nav_icons 含 "videoEnhanceHub" 支',
          '"videoEnhanceHub": {' in main_src)

    # D：hub_popout 转发 + busy 守卫
    check("4.D1 HubPopoutWindow 提供 open_hub_popout 转发",
          "def open_hub_popout(self, hub):" in pop_src)
    miss_d = [a for a in WORKER_ATTRS if f'"{a}"' not in pop_src]
    check("4.D2 _BUSY_ATTRS 覆盖四个 worker 属性", not miss_d, miss_d)

    # E：设置键登记（未登记也能落 misc，但语义要显式）
    miss_e = [k for k in ("ffmpeg_path", "ffprobe_path", "enhance_params",
                          "enhance_history", "enhance_last_dir")
              if f'"{k}":' not in st_src]
    check("4.E1 KEY_DOMAIN 登记五个新键", not miss_e, miss_e)
    check("4.E2 ffmpeg/ffprobe 归 paths 域，enhance_* 归 misc 域",
          '"ffmpeg_path": "paths"' in st_src
          and '"enhance_params": "misc"' in st_src)

    # F：设置页可见入口
    check('4.F1 DEFAULT_PATHS 含 ffmpeg_path/ffprobe_path 且默认空（走 PATH）',
          '"ffmpeg_path": ""' in sm_src and '"ffprobe_path": ""' in sm_src)
    check('4.F2 _group_paths 有两行 ("ffmpeg_path", "ffmpeg", "file")',
          '("ffmpeg_path", "ffmpeg", "file")' in hp_src
          and '("ffprobe_path", "ffprobe", "file")' in hp_src)

    # G：工具页工作区总数。原先是读 windows/tools/smoke_fluent_mainwindow.py
    # 里写死的 th.stack.count()（该脚本已下线），改成直接数 tool_hub 的
    # addPage——写死数字的坑就在这：并发会话加了「视频修复」没人
    # 同步，冒烟反而掩盖漏注册。
    add_lines = [l.strip() for l in tool_src.splitlines()
                 if l.strip().startswith("self.addPage(self.")]
    check("4.G1 ToolHub 注册六个工作区且含画质提升",
          len(add_lines) == 6
          and f'self.addPage(self.video_enhance_work, "{TAB_TEXT}")' in add_lines,
          add_lines)

    # H：关窗清理（漏了这里，关窗后 ffmpeg 子进程还在跑）
    miss_h = [a for a in WORKER_ATTRS if f"'{a}'" not in shut_src]
    check("4.H1 shutdown 7b 清理清单覆盖四个 worker 属性", not miss_h, miss_h)

    print("\n[5] nav_icons 覆盖判定")
    tool_pairs = _nav_icons_for(main_src, "toolHub")
    ve_pairs = _nav_icons_for(main_src, "videoEnhanceHub")
    check("5.1 解析到 toolHub 的 nav_icons", tool_pairs is not None, tool_pairs)
    if tool_pairs is not None:
        check(f'5.2 toolHub 六键且含 "{TAB_TEXT}"（少一个退回内嵌 Pivot）',
              len(tool_pairs) == 6
              and any(t == TAB_TEXT for t, _i in tool_pairs),
              [t for t, _i in tool_pairs])
    check("5.3 解析到 videoEnhanceHub 的 nav_icons", ve_pairs is not None, ve_pairs)
    if ve_pairs is not None:
        check("5.4 五键与顺序 == 五子页文本",
              tuple(t for t, _i in ve_pairs) == PAGE_TEXTS,
              [t for t, _i in ve_pairs])
    from qfluentwidgets import FluentIcon
    # 图标名写错不会在启动时炸，要到用户点「弹出面板」才炸，所以在这里先查一遍
    really_bad = [n for _t, n in (ve_pairs or []) + (tool_pairs or [])
                  if getattr(FluentIcon, n, None) is None]
    check("5.5 用到的 FluentIcon 成员都存在", not really_bad, really_bad)

    print("\n[6] 弹出窗为左侧子导航形态")
    try:
        from main_window.hub_popout import HubPopoutWindow
        nav_icons = {t: getattr(FluentIcon, n) for t, n in (ve_pairs or [])}
        pop = HubPopoutWindow(VideoEnhanceHub, stub, "画质提升工作台",
                              nav_icons=nav_icons)
        for _ in range(6):
            app.processEvents()
        keys = list(pop.navigationInterface.panel.items.keys())
        check("6.1 左侧导航挂了五个子页", len(keys) == 5, keys)
        check("6.2 Hub 空壳已隐藏（视图改挂窗口导航）", pop.hub.isHidden())
        check("6.3 弹出窗内不再提供二次弹出",
              not pop.hub.btn_popout.isVisible()
              and not pop.hub.lbl_popout_hint.isVisible())
        check("6.4 switchTo 已重绑到弹出窗（跨页跳转仍可路由）",
              pop.hub.switchTo == pop.switchTo)
        check("6.5 子页 _hub_container 已置空",
              all(getattr(p, "_hub_container", "x") is None
                  for p, _t, _i in list(pop.hub._page_meta)))
        # busy 守卫必须穿到宿主主窗口：弹出窗与内嵌视图是同一 Hub 类的两个实例，
        # 各自一份守卫的话两边能同时起 ffmpeg 抢 CPU。守卫挂在**窗口**上
        # （hub.worker() 读 self._win），HubPopoutWindow 再经 _BUSY_ATTRS 代理到主窗口。
        stub._ve_probe_worker = "SENTINEL_R"
        check("6.6 busy 守卫读穿到宿主窗口",
              pop.hub.worker(W_PROBE) == "SENTINEL_R", pop.hub.worker(W_PROBE))
        setattr(pop, "_ve_probe_worker", "SENTINEL_W")
        check("6.7 busy 守卫写穿到宿主窗口",
              getattr(stub, "_ve_probe_worker") == "SENTINEL_W",
              getattr(stub, "_ve_probe_worker"))
        stub._ve_probe_worker = None
        pop.hub.switchTo(pop.hub.compare_page)
        app.processEvents()
        check("6.8 弹出窗内跳兄弟页不抛", True)
        pop.close()
        pop.deleteLater()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("6.x 弹出窗链路", False, repr(e))

    print("\n[7] worker 属性名三处一致")
    hub_src = _read("windows", "tools", "video_enhance_hub.py")
    declared = [a for a in WORKER_ATTRS if f'"{a}"' in hub_src]
    check("7.1 WORKER_ATTRS 四个名字都在 hub 源码里", len(declared) == 4, declared)
    check("7.2 hub_popout 与 shutdown 用的是同一批名字",
          all(f'"{a}"' in pop_src and f"'{a}'" in shut_src for a in WORKER_ATTRS))
    check("7.3 STAGES 含 preview（滤镜页「抽一帧预览」要用）",
          "preview" in getattr(__import__(
              "workers.video_enhance_worker", fromlist=["STAGES"]), "STAGES", ()))

    hub.hide()
    hub.detach_workers()
    hub.deleteLater()
    stub.hide()
    stub.deleteLater()
    app.processEvents()

    print("\n[8] 真 ffmpeg 端到端（找不到就跳过）")
    ff, fp = ve.locate_ffmpeg({})
    if not ff:
        print("  [SKIP] PATH 里没有 ffmpeg，跳过端到端；"
              "命令拼装已由 tests/test_video_enhance.py 离线覆盖")
    else:
        os.makedirs(OUT, exist_ok=True)
        cap = ve.detect_capabilities(ff)
        check("8.1 detect_capabilities ok", cap.get("ok"), cap.get("reason"))
        print(f"    ffmpeg {cap.get('version')} · 编码器 {len(cap.get('encoders') or [])}"
              f" · libvmaf {cap.get('has_vmaf')}")
        src = os.path.join(OUT, "src.mp4")
        gen = [ff, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src]
        import subprocess
        rc = subprocess.run(gen, capture_output=True).returncode
        check("8.2 生成 2s 测试源", rc == 0 and os.path.isfile(src), f"rc={rc}")
        info = ve.probe(src, fp)
        check("8.3 probe 出分辨率与时长",
              info.get("ok") and info.get("width") == 320
              and abs(float(info.get("duration") or 0) - 2.0) < 0.5, info)

        p = ve.apply_preset(ve.default_params(), "standard")
        p["scale_on"], p["scale_mode"], p["scale_height"] = True, "height", 480
        dst = os.path.join(OUT, "dst.mp4")
        p["out_mode"], p["out_dir"], p["on_exist"] = "custom", OUT, "overwrite"
        p["name_tpl"] = "dst"
        p["encoder"], p["codec_preset"], p["rate_mode"], p["crf"] = \
            "libx264", "ultrafast", "crf", 23
        lines, pcts = [], []
        # run_enhance 成功返回报告 dict，失败是 raise EnhanceError（不是 ok=False）
        try:
            r = ve.run_enhance(src, dst, p, ff, fp, on_line=lines.append,
                               on_progress=lambda pct, sp: pcts.append(pct),
                               info=info)
            check("8.4 run_enhance 成功且产物落地",
                  os.path.isfile(dst) and r.get("dst") == dst, r)
            check("8.4b 报告含滤镜链/编码器/耗时字段",
                  all(k in r for k in ("filter_chain", "encoder", "elapsed",
                                       "size_before", "size_after")), sorted(r))
        except Exception as e:
            check("8.4 run_enhance 成功且产物落地", False, repr(e))
            r = {}
        if os.path.isfile(dst):
            out_info = ve.probe(dst, fp)
            check("8.5 放大到 480 生效（源 240）",
                  out_info.get("height") == 480, out_info.get("height"))
            check("8.6 音频按 copy 保留", out_info.get("audio_codec") == "aac",
                  out_info.get("audio_codec"))
            # 进度键值行被 _stream_ffmpeg 刻意从 on_line 里滤掉（每秒好几条会把
            # 终端刷成噪声），只喂给 on_progress，所以这里要验回调而不是终端行。
            # 2s 合成源编完只要 ~90ms，短于 ffmpeg 约 1s 的进度间隔，所以只能
            # 断言「收到了收尾的 100%」，递增过程留给真录像（离线单测已覆盖解析）
            check("8.7 on_progress 回调收到收尾 100%",
                  pcts and max(pcts) >= 99.0
                  and pcts == sorted(pcts), (len(pcts), pcts[:3], pcts[-3:]))
            check("8.7b 终端只收可读行，不含 out_time_ms= 噪声",
                  not any("out_time_ms=" in str(x) for x in lines),
                  f"{len(lines)} 行输出")

        prev = os.path.join(OUT, "prev")
        raw = os.path.join(prev, "a.png")
        ok1, why1 = ve.extract_frame(src, 1.0, raw, ff)
        ok2, why2 = ve.extract_preview_frame(src, 1.0,
                                             os.path.join(prev, "b.png"),
                                             p, ff, info)
        check("8.8 抽原帧 + 过链帧都出图",
              ok1 and ok2, f"{why1} / {why2}")
        if ok1 and ok2:
            sa = os.path.getsize(os.path.join(prev, "a.png"))
            sb = os.path.getsize(os.path.join(prev, "b.png"))
            check("8.9 两张 PNG 尺寸不同（滤镜链确实生效）", sa != sb, (sa, sb))

        # run_metric 直接返回分数（float）或 None，失败是 raise EnhanceError
        try:
            score = ve.run_metric(src, dst, "psnr", ff, fp)
            check("8.10 PSNR 评测出数（放大后走 scaled 分支）",
                  isinstance(score, float) and score > 0, score)
        except Exception as e:
            check("8.10 PSNR 评测出数", False, repr(e))

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
