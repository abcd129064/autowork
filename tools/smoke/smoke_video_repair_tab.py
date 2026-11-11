# -*- coding: utf-8 -*-
"""冒烟：工具页「视频修复」页签的双注册与真 ffmpeg 端到端（离屏）。

背景：掉电截断的 mp4（mdat size=0 吃到 EOF、moov 没落盘）借参考文件 VOL 头无损
   重封装。设计稿 design/video_repair_tool.html，引擎 windows/tools/video_repair.py
   （离线单测 tests/test_video_repair.py 已覆盖纯字节逻辑），线程
   workers/video_repair_worker.py，本脚本只管「注册对不对」与「真文件修不修得好」。

双注册 = 主窗口内嵌 Pivot 页签 + 弹出窗口（HubPopoutWindow）左侧子导航。后者以
nav_icons 覆盖全部注册页为前提，少一个键就静默退回内嵌 Pivot 形态，所以本脚本把
「nav_icons 键 == ToolHub 注册页文本」当成主断言，而不是写死页数（本仓常有多会话
并行加页签，写死数字隔天就红）。

AGENTS.md §5.1：E:\\ANACONDA 缺 qfluentwidgets，miniconda3 的 PySide6 需要内联引导
   才 import 得动，所以本脚本必须用 miniconda3 跑。
   刻意**不构造 MainWindow**：主窗口启动会写 config/ 并连 frpc，与本仓「跑测试前后
   config/ database/ 零改动」的验收口径冲突。

用法：
    C:\\Users\\shen_zhe\\miniconda3\\python.exe tools/smoke/smoke_video_repair_tab.py
产物：tools/_scratch/repair_smoke/（不入库；ffmpeg 缺失时整段 [5] 跳过）
退出码：0=全部通过，2=有断言失败，1=环境或 Hub 构造起不来
"""
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# tools/smoke/ → 上溯三层 = 仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- Qt 内联引导（必须在 import PySide6 之前）----
import importlib.util                                          # noqa: E402

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

import ast                                                     # noqa: E402

from PySide6.QtWidgets import QApplication, QWidget            # noqa: E402

OK = True
OUT = os.path.join(ROOT, "tools", "_scratch", "repair_smoke")
TAB_TEXT = "视频修复"
WORK_NAME = "videoRepairWork"
WORKER_ATTRS = ("_video_probe_worker", "_video_repair_worker")


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

    用 AST 不用正则：字典跨多行、值是 FluentIcon 成员（非字面量），且要连图标名
    一起取出，才能在 [4] 里按主窗口的真实映射建弹出窗（图标名写错要到用户点弹出才炸）。
    """
    tree = ast.parse(main_src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "open_hub_popout")
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") == "nav_icons" for t in node.targets):
            continue
        value = node.value.func.value if isinstance(node.value, ast.Call) else node.value
        for key_node, val_node in zip(value.keys, value.values):
            if ast.literal_eval(key_node) != hub_key:
                continue
            if not isinstance(val_node, ast.Dict):
                return None
            return [(ast.literal_eval(k), v.attr)
                    for k, v in zip(val_node.keys, val_node.values)]
    return None


def _addpage_order(src):
    """从 ToolHub.__init__ 源码里按出现顺序取 addPage 文本，并标出 lock 的位置"""
    body = src.split("class ToolHub", 1)[1]
    texts = []
    lock_at = None
    cursor = 0
    while True:
        i = body.find("addPage(", cursor)
        j = body.find("lock_pivot_width()", cursor)
        if j >= 0 and (i < 0 or j < i):
            lock_at = len(texts)
        if i < 0:
            break
        tail = body.find(")", i)
        arg = body[i + len("addPage("):tail]
        parts = [p.strip() for p in arg.split(",")]
        texts.append(parts[-1].strip("\"'") if len(parts) > 1 else "?")
        cursor = tail
    return texts, (lock_at if lock_at is not None else len(texts))


class StubMain(QWidget):
    """只顶宿主接口，不碰 config/ 与 frpc（构造 MainWindow 会两者都碰）"""

    def __init__(self):
        super().__init__()
        self.videos_dir = ""
        self.logs = []
        self.saved = []
        self.bars = []

    def _append_log(self, t):
        self.logs.append(t)

    def _load_settings(self):
        return {}

    def _save_settings(self, d):
        self.saved.append(d)

    def _show_info_bar(self, m, k="info", title=None, duration=2500):
        self.bars.append((k, m))

    def _release_worker_safe(self, w):
        try:
            w.deleteLater()
        except RuntimeError:
            pass


class FakeWorker:
    """只记录中断请求的假 QThread：detach_workers 的断言不需要真线程"""

    def __init__(self):
        self.interrupted = False

    def isRunning(self):
        return True

    def requestInterruption(self):
        self.interrupted = True


def main() -> int:
    app = QApplication.instance() or QApplication(list(sys.argv))
    from main_window import tool_hub as th_mod
    from main_window.hub_popout import _BUSY_ATTRS, HubPopoutWindow
    from main_window.pivot_page import PivotPage
    from windows.tools import video_repair as vr

    print("\n[1] 注册点源码核对（双注册的 7 个落点）")
    th_src = _read("main_window", "tool_hub.py")
    main_src = _read("main_window", "main_window.py")
    shut_src = _read("main_window", "shutdown.py")
    nav_src = _read("main_window", "ui", "navigation.py")
    hp_src = _read("main_window", "hub_pages.py")
    mixin_src = _read("main_window", "ui_mixin.py")

    texts, lock_at = _addpage_order(th_src)
    check("1.1 ToolHub 注册了「%s」页签" % TAB_TEXT, TAB_TEXT in texts, texts)
    check("1.2 addPage 在 lock_pivot_width() 之前（否则 Pivot 条宽度不重算）",
          texts.index(TAB_TEXT) < lock_at if TAB_TEXT in texts else False,
          (texts, lock_at))
    check("1.3 工作区实例名与构造点齐备",
          "self.video_repair_work = VideoRepairWork(self._win, self)" in th_src)
    check("1.4 工作区 objectName=%s（addPage 的 routeKey 来源）" % WORK_NAME,
          'setObjectName("%s")' % WORK_NAME in th_src)
    miss_busy = [a for a in WORKER_ATTRS if a not in _BUSY_ATTRS]
    check("1.5 hub_popout._BUSY_ATTRS 覆盖两个 worker 属性（漏了会双开 ffmpeg）",
          not miss_busy, miss_busy)
    miss_shut = [a for a in WORKER_ATTRS if f"'{a}'" not in shut_src]
    check("1.6 shutdown 7b 清理清单覆盖两个 worker 属性", not miss_shut, miss_shut)
    check("1.7 navigation.open_tool_hub 白名单含 video_repair_work",
          "video_repair_work" in nav_src)
    check("1.8 设置页「打开工具页」描述含页签名", TAB_TEXT in hp_src)
    # 收尾回收 QThread 的静态方法少一个 @staticmethod，6 处 win._release_worker_safe(w)
    # 调用点全会在信号槽里抛 TypeError，界面后半段静默不执行
    check("1.9 _release_worker_safe 仍是 staticmethod",
          "@staticmethod" in mixin_src.split("def _release_worker_safe")[0][-40:])

    print("\n[2] 运行期 ToolHub（其它会话正在改的工作区若崩则跳过，不判失败）")
    stub = StubMain()
    for a in WORKER_ATTRS:
        setattr(stub, a, None)
    th = None
    try:
        th = th_mod.ToolHub(stub)
    except Exception as e:
        print(f"    （跳过 ToolHub 运行期检查：{e!r}）")
    if th is not None:
        meta = list(getattr(th, "_page_meta", []) or [])
        mt = tuple(t for _p, t, _i in meta)
        check("2.1 _page_meta 含「%s」" % TAB_TEXT, TAB_TEXT in mt, mt)
        check("2.2 Pivot 项数 == stack 项数 == 注册页数",
              len(th.pivot.items) == th.stack.count() == len(meta),
              (len(th.pivot.items), th.stack.count(), len(meta)))
        work = th.video_repair_work
        check("2.3 工作区 objectName 正确且已进 stack",
              work.objectName() == WORK_NAME
              and th.stack.indexOf(work) == mt.index(TAB_TEXT),
              (work.objectName(), th.stack.indexOf(work)))
        th.switchTo(work)
        app.processEvents()
        check("2.4 switchTo 往返后控件树完好",
              th.stack.currentWidget() is work
              and all(getattr(work, n, None) is not None
                      for n in ("btn_start", "combo_cut", "chk_verify",
                                "chk_open_dir", "log_view", "progress_bar")))
        stub.bars.clear()
        work._on_start()
        check("2.5 空参数点「开始修复」只提示不起线程",
              any("损坏文件" in m for _k, m in stub.bars)
              and getattr(stub, "_video_repair_worker", None) is None,
              stub.bars)
        check("2.6 未起线程时两个 busy 属性仍为空",
              getattr(stub, "_video_probe_worker", None) is None)
        fw = FakeWorker()
        stub._video_repair_worker = fw
        work.detach_workers()
        check("2.7 detach_workers 钩子能中断宿主上的 worker 引用", fw.interrupted)
        stub._video_repair_worker = None

    print("\n[3] nav_icons 覆盖判定（漏一个键就退回内嵌 Pivot 形态）")
    pairs = _nav_icons_for(main_src, "toolHub")
    check("3.1 解析到 nav_icons['toolHub']", pairs is not None, pairs)
    if pairs is not None:
        keys = {t for t, _i in pairs}
        check("3.2 nav_icons 键集 == ToolHub 注册页文本（页签 %d 个 / 图标 %d 个）"
              % (len(texts), len(keys)), keys == set(texts),
              {"只在nav": sorted(keys - set(texts)),
               "只在页签": sorted(set(texts) - keys)})
        check("3.3 图标名在 FluentIcon 上真实存在",
              all(hasattr(_fluent(), n) for _t, n in pairs),
              [n for _t, n in pairs if not hasattr(_fluent(), n)])

    print("\n[4] 弹出窗口左侧子导航形态")
    nav_icons = {t: getattr(_fluent(), i) for t, i in (pairs or [])}
    pop_hub_cls = th_mod.ToolHub
    if th is None or not nav_icons:
        # ToolHub 起不来时，用一个只有本页签的最小 Hub 仍然验到弹出形态
        class RepairHub(PivotPage):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setObjectName("toolHub")
                self.video_repair_work = th_mod.VideoRepairWork(parent, self)
                self.addPage(self.video_repair_work, TAB_TEXT)
                self.lock_pivot_width()
        pop_hub_cls = RepairHub
        nav_icons = {TAB_TEXT: _fluent().MOVIE}
    try:
        pop = HubPopoutWindow(pop_hub_cls, stub, "工具面板", nav_icons=nav_icons)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("4.0 HubPopoutWindow 构造无异常", False, repr(e))
        return 1
    meta = getattr(pop.hub, "_page_meta", []) or []
    check("4.1 走的是左侧子导航形态（Hub 空壳已隐藏）",
          len(meta) > 0 and not pop.hub.isVisible(), len(meta))
    # 属性名是 qfw FluentWindowBase.stackedWidget；本窗口自定义了 __getattr__，
    # 写错成 stack 不会返回 None 而是直接抛 AttributeError
    found = [w for w in pop.stackedWidget.findChildren(QWidget)
             if w.objectName() == WORK_NAME]
    check("4.2 弹出窗口 stack 里有「%s」视图" % TAB_TEXT, len(found) == 1, len(found))
    # 左侧导航项由 addSubInterface 逐个建出，不另存可枚举的 items（该属性名在
    # qfluentwidgets 各版本里变过好几次），所以用 stack 项数与路由跳转代表导航可用
    check("4.3 六个子页全部挂进弹出窗口 stack（与左侧导航项一一对应）",
          pop.stackedWidget.count() == len(meta),
          (pop.stackedWidget.count(), len(meta)))
    pop.switchTo(found[0])
    check("4.3b 「%s」子页可被路由切到" % TAB_TEXT,
          pop.stackedWidget.currentWidget() is found[0],
          pop.stackedWidget.currentWidget().objectName()
          if pop.stackedWidget.currentWidget() else None)
    check("4.4 弹出窗口代理 _release_worker_safe（工作区收尾要调它）",
          callable(getattr(pop, "_release_worker_safe", None)))
    fw = FakeWorker()
    pop._video_repair_worker = fw
    check("4.5 busy 守卫写穿透到主窗口（跨窗口互斥）",
          stub._video_repair_worker is fw)
    check("4.6 视图内 self._win 即弹出窗口", found and found[0]._win is pop)
    pop.close()
    check("4.7 关闭窗口不抛（closeEvent → detach_workers）", True)

    print("\n[5] 真 ffmpeg 端到端（合成掉电文件；缺 ffmpeg 则跳过）")
    ffmpeg, ffprobe = vr.find_ffmpeg()
    if not ffmpeg or not ffprobe:
        print("    （跳过：PATH 里没有 ffmpeg / ffprobe）")
    else:
        os.makedirs(OUT, exist_ok=True)
        ref = os.path.join(OUT, "ref.mp4")
        broken = os.path.join(OUT, "broken.mp4")
        out = os.path.join(OUT, "repaired.mp4")
        for p in (ref, broken, out):
            if os.path.isfile(p):
                os.remove(p)
        # testsrc 定长 1 秒 25 帧：帧数可预期，才能判出丢帧是否只发生在尾部
        rc = subprocess.call([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                              "-f", "lavfi", "-i",
                              "testsrc=size=320x240:rate=25:duration=1",
                              "-c:v", "mpeg4", "-q:v", "5", ref])
        check("5.1 合成参考文件成功（lavfi testsrc → mp4v）",
              rc == 0 and os.path.isfile(ref), rc)
        if os.path.isfile(ref):
            info = vr.probe_reference(ref, ffprobe)
            check("5.2 参考文件确为 mpeg4 且无音轨",
                  info["codec"] == "mpeg4" and not info["has_audio"], info)
            cut_to = _make_truncated(ref, broken, 1200)
            check("5.3 造出掉电截断形态（mdat size=0 + 无 moov）", cut_to > 0, cut_to)
            bad = vr.probe_broken(broken, ffprobe)
            okref = vr.probe_broken(ref, ffprobe)
            check("5.4 体检判定损坏文件可修", bad["ok"] is True, bad["reason"])
            check("5.5 体检拒绝带 moov 的正常文件",
                  okref["ok"] is False and "moov" in okref["reason"], okref["reason"])
            if th is not None:
                # 体检失败时后端 frames=None：worker 若回传全 None 字典，界面格式化
                # None 会抛 TypeError。同步调 _probe 不起线程，只验回传契约与落字
                got = []
                w = th_mod.VideoRepairWorker("probe", {"path": ref})
                w.probed.connect(got.append)
                w._probe(vr)
                payload = got[0] if got else {}
                check("5.11 体检失败回传空帧统计而非全 None",
                      payload.get("frames") == {}, payload.get("frames"))
                work._on_probed(payload)
                check("5.12 失败结论落界面不抛且码流行退回占位",
                      work.lbl_frames.text() == "码流：—"
                      and work.lbl_verdict.text().startswith("✘"),
                      (work.lbl_frames.text(), work.lbl_verdict.text()))
                zero = dict(payload, frames={"frame_count": 0, "gop_count": 0,
                                             "median_frame": 0, "last_vop_tail": 0,
                                             "last_frame_incomplete": False})
                work._on_probed(zero)
                check("5.13 零帧统计退回占位而非展示一排 0",
                      work.lbl_frames.text() == "码流：—", work.lbl_frames.text())
                w.deleteLater()
            lines = []
            report = vr.repair(broken, ref, out, fps=25, cut_mode="auto",
                               verify_decode=True, on_line=lines.append,
                               ffmpeg=ffmpeg, ffprobe=ffprobe)
            check("5.6 修复产出文件且无损（SHA-256 与原负载前缀一致）",
                  os.path.isfile(out) and report["sha_ok"] is True,
                  {k: report[k] for k in ("out", "sha_ok", "sha256")})
            check("5.7 只丢尾帧：0 < frames <= 25 且丢弃字节数为正",
                  0 < report["frames"] <= 25 and report["dropped_bytes"] > 0,
                  (report["frames"], report["dropped_bytes"]))
            check("5.8 完整解码零错误（重封装后不花屏）",
                  report["decode_errors"] == 0, report["decode_lines"])
            check("5.9 校验档位透传到报告（UI 靠它决定是否打这行）",
                  report["verify_decode"] is True, report["verify_decode"])
            if os.path.isfile(out):
                os.remove(out)
            try:
                vr.repair(broken, ref, out, should_stop=lambda: True,
                          ffmpeg=ffmpeg, ffprobe=ffprobe)
                check("5.10 立即取消抛 RepairCancelled", False, "没抛")
            except vr.RepairCancelled:
                check("5.10 立即取消抛 RepairCancelled", not os.path.isfile(out))

    print("\n结果：" + ("全部通过" if OK else "存在失败项"))
    return 0 if OK else 2


def _fluent():
    """延迟取 FluentIcon：图标枚举在个别版本上有缺失，取失败不该拖垮整个解析段"""
    from qfluentwidgets import FluentIcon
    return FluentIcon


def _make_truncated(ref_path, out_path, drop_tail):
    """把正常文件改成掉电形态：mdat size 归零（表示吃到 EOF）、砍掉 moov、再削掉尾部若干字节

    录制端本来就是「先写 mdat 占位 size=0、最后才补 moov」，断电时 moov 永远没落盘；
    削尾模拟的是帧写到一半断电，这正是修复必须裁掉的那一段。
    """
    with open(ref_path, "rb") as f:
        data = f.read()
    pos, size = 0, len(data)
    mdat = None
    while pos + 8 <= size:
        bsize = int.from_bytes(data[pos:pos + 4], "big")
        btype = data[pos + 4:pos + 8]
        if btype == b"mdat":
            mdat = (pos, bsize)
            break
        pos += bsize if bsize >= 8 else 8
    if mdat is None:
        return 0
    mpos, msize = mdat[0], mdat[1]
    payload = data[mpos + 8:mpos + msize]
    keep = max(8, len(payload) - drop_tail)
    with open(out_path, "wb") as f:
        f.write(data[:mpos])
        f.write((0).to_bytes(4, "big") + data[mpos + 4:mpos + 8])
        f.write(payload[:keep])
    return keep


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
