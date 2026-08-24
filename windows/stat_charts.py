# -*- coding: utf-8 -*-
"""统计图表模块（pygwalker 独立窗口）

售后/跑视频面板「记录与统计」页的统计图表入口：后台聚合原始记录为
pandas DataFrame，经 pygwalker 生成单文件 HTML，在系统浏览器（独立窗口）
打开。本模块提供无 UI 宿主 StatsOpener，由记录页按钮触发。

方案 C 背景（演进记录）：
- 方案 A（QWebEngineView 内嵌 ECharts）：WebEngine GPU/DComp 合成与
  FluentWindowBase 的 Mica backdrop 冲突，实测失败（黑屏/导航栏变黑）。
- 方案 B（matplotlib FigureCanvas）：纯 QPainter 无冲突，但静态图视觉
  不达预期、无拖拽分析。
- 方案 C（pygwalker）：Tableau 式自助探索（ECharts 底层）。桌面内嵌必须
  QWebEngineView（重现 A 冲突），故采用独立窗口——pyg.to_html 生成单文件
  HTML + 系统浏览器打开，主窗口零 WebEngine 副作用，Mica 不受影响。
"""
import json
import os
import tempfile
import threading

from PySide6.QtCore import QObject, Signal

from qfluentwidgets import isDarkTheme


class StatsOpener(QObject):
    """pygwalker 独立窗口打开器（无 UI 宿主，由记录页按钮触发）。

    builder: 无参函数，返回 pandas.DataFrame（原始记录，pygwalker 自助分析）。
    kind: 'aftersale' / 'ledger'（临时文件前缀）。
    finished(bool, str): 打开成功/失败信号（后台线程 emit，Qt 自动回主线程）。
    """

    finished = Signal(bool, str)

    def __init__(self, builder, kind, parent=None):
        super().__init__(parent)
        self._builder = builder
        self._kind = kind
        self._worker = None
        self._data = None
        self._pyg = None   # 主线程延迟导入（后台线程 import 会与 Qt GUI 冲突卡死）

    def open_analysis(self):
        """后台聚合 DataFrame → pygwalker HTML → 浏览器打开。"""
        if self._worker is not None and self._worker.isRunning():
            return
        from workers.aftersale_worker import AftersaleDBWorker
        self._worker = AftersaleDBWorker(self._builder)
        self._worker.result_ready.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, df):
        self._data = df
        # isDarkTheme 只能主线程读取（后台线程访问 Qt 全局会阻塞）
        dark = isDarkTheme()
        if self._pyg is None:
            import pygwalker as pyg  # 主线程导入（后台线程 import 会卡 Qt GUI）
            self._pyg = pyg
        # pyg.to_html 生成 2~3MB HTML 需数秒，放后台线程避免卡 UI
        threading.Thread(target=self._gen_and_open, args=(df, dark),
                         daemon=True).start()

    def _gen_and_open(self, df, dark):
        """pygwalker 生成单文件 HTML → 系统浏览器打开（独立窗口）。

        后台线程运行：只做纯计算（to_html/写文件/webbrowser），
        不访问任何 Qt GUI 对象；UI 状态经 finished 信号回主线程。
        """
        try:
            if df is None or (hasattr(df, "empty") and df.empty):
                raise ValueError("没有可分析的记录数据")
            spec = _build_default_spec(self._kind)
            html = self._pyg.to_html(df, appearance="dark" if dark else "light",
                                     spec=spec)
            fd, path = tempfile.mkstemp(
                suffix=".html", prefix=f"autowork_{self._kind}_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(html)
            url = "file:///" + path.replace("\\", "/")
            import webbrowser
            webbrowser.open(url)
            self.finished.emit(
                True, "已打开统计图表窗口。拖拽字段即可分析，"
                      "关闭浏览器标签页即结束。")
        except Exception as e:
            self.finished.emit(False, f"生成统计页面失败：{e}")

    def _on_error(self, msg):
        self.finished.emit(False, f"数据加载失败：{msg}")


# ==================== 字段中文映射 + 预置图表 spec ====================

# 数据库列名 → 中文展示名（pygwalker 自助分析直接显示中文字段）
_AFTERSALE_CN = {
    "created_at": "填写时间", "occurred_at": "发生时间", "creator": "填写人",
    "issue_type": "类型", "table_no": "桌号", "room_name": "球房",
    "region": "地区", "problem": "问题", "cause": "发生原因",
    "resolved": "是否解决", "solution": "解决方案", "resolver": "解决人",
    "response_time": "响应时间", "snk_code": "SNK编码", "device_code": "设备编码",
    "cycle_start": "周期", "updated_at": "更新时间",
    "is_initiative": "是否主动发起", "is_our_problem": "是否我方问题",
}
_LEDGER_CN = {
    "category": "分类", "kind": "类别", "room_name": "球房",
    "video_name": "视频名", "frame": "帧数", "description": "描述",
    "repro": "复现", "new_program": "新程序", "remark": "备注",
    "signer": "署名", "created_at": "填写时间", "occurred_at": "日期",
    "updated_at": "更新时间",
}


def _gw_fld(name: str, analytic: str, agg: str = "") -> dict:
    """Graphic Walker 字段描述（fid = 列名，pygwalker 0.5.x 约定）。"""
    semantic = "temporal" if name in ("日期", "发生时间") else "nominal"
    f = {"dragId": f"d_{name}", "fid": name, "name": name,
         "semanticType": semantic, "analyticType": analytic}
    if agg:
        f["aggName"] = agg
    return f


def _gw_measure_count(dim_name: str) -> dict:
    """度量：按 dim_name 计数的 count 表达式（fid 虚拟避免与 dim 字段重复）。

    Graphic Walker spec 不允许同一字段同时出现在 dim 和 mea；用 expression
    让 mea 引用真实字段做 count，fid 用虚拟 'gw_count_fid' 规避冲突。
    """
    return {"dragId": f"m_{dim_name}", "fid": "gw_count_fid",
            "name": "计数", "semanticType": "quantitative",
            "analyticType": "measure", "aggName": "count",
            "expression": {"op": "count", "fid": dim_name}}


def _build_default_spec(kind: str) -> str:
    """默认预置图表 spec（打开即有图表，无需手动拖拽）。

    charts: (图表名, 几何类型, 维度列, 可选颜色列)；measures 用 count 聚合。
    字段 fid = 中文列名（df.rename 后），与 pygwalker raw_fields 一致。
    """
    if kind == "aftersale":
        charts = [
            ("问题类型分布", "bar", "类型"),
            ("地区分布", "bar", "地区"),
            ("解决率趋势", "line", "发生时间", "是否解决"),
            ("我方问题/主动发起占比", "pie", "是否我方问题"),
        ]
    else:
        charts = [
            ("分类占比", "pie", "分类"),
            ("四分类趋势", "line", "日期", "分类"),
            ("署名工作量", "bar", "署名"),
        ]
    config = []
    for i, (name, geom, dim, *rest) in enumerate(charts):
        color = rest[0] if rest else None
        encodings = {
            "dimensions": [_gw_fld(dim, "dimension")],
            "measures": [_gw_measure_count(dim)],
            "color": [_gw_fld(color, "dimension")] if color else [],
            "filters": [],
        }
        config.append({
            "visId": f"chart{i}", "name": name,
            "encodings": encodings,
            "config": {"defaultAggregated": True, "geoms": [geom],
                       "stack": "stack"},
        })
    return json.dumps(
        {"version": "0.5.0", "config": config,
         "chart_map": {}, "workflow_list": []},
        ensure_ascii=False)


# ==================== 数据源（pygwalker 自助分析 DataFrame） ====================

def _aftersale_options() -> object:
    """售后记录 DataFrame（pygwalker 自助分析数据源）。

    返回 aftersale_records 全量行；pygwalker 拖拽字段自行聚合出图，
    不再预定义 4+3 图（交互式探索替代静态图表）。
    """
    import pandas as pd
    from database import aftersale_db

    _, rows = aftersale_db.query_page(1, 100000)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.rename(columns={k: v for k, v in _AFTERSALE_CN.items()
                              if k in df.columns})


def _ledger_options() -> object:
    """跑视频记录 DataFrame（pygwalker 自助分析数据源）。"""
    import pandas as pd
    from database import ledger_db

    _, rows = ledger_db.query_page(1, 100000)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.rename(columns={k: v for k, v in _LEDGER_CN.items()
                              if k in df.columns})