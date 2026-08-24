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

    def open_analysis(self, filters=None):
        """后台按当前筛选聚合 DataFrame → pygwalker HTML → 浏览器打开。

        filters: 记录页 _current_filters() 字典（含 cycle_start/keyword/...
        /date_from/date_to...），透传给 builder 按筛选查 DB。
        """
        if self._worker is not None and self._worker.isRunning():
            return
        from workers.aftersale_worker import AftersaleDBWorker
        # 透传 filters：AftersaleDBWorker 协议是 fn(*args, **kwargs)，
        # builder(filters) 接收 dict；filters 为空时不传参兼容旧无参 builder
        args = (filters,) if filters else ()
        self._worker = AftersaleDBWorker(self._builder, *args)
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
            # 预写默认 spec 到用户数据目录，走 json_file 模式加载（比 JSON 字符串
            # 注入稳定；用户拖拽改动不写回，下次打开仍为默认预置图表）
            from core.app_paths import get_app_dir
            spec_path = os.path.join(
                get_app_dir(), f"stats_spec_{self._kind}.json")
            with open(spec_path, "w", encoding="utf-8") as f:
                f.write(_build_default_spec(self._kind))
            html = self._pyg.to_html(df, appearance="dark" if dark else "light",
                                     spec=spec_path)
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


def _gw_fld(name: str, analytic: str = "dimension") -> dict:
    """Graphic Walker 字段描述（fid = rename 后的中文列名）。

    basename 给前端表头/聚合标题做回退显示，与 name 保持一致即可。
    """
    semantic = "temporal" if name in ("日期", "发生时间") else "nominal"
    return {"dragId": f"d_{name}", "fid": name, "name": name, "basename": name,
            "semanticType": semantic, "analyticType": analytic}


def _gw_count_measure() -> dict:
    """内置行计数度量：前端唯一认的 count 写法，照抄不能改。

    fid 固定 gw_count_fid；expression.op="one" 每行产 1，配 aggName="sum"
    即行数计数。自造 op:"count" 不在 GW 表达式白名单（one/bin/expr...）里，
    前端会判非法把整个度量丢掉。
    """
    return {"dragId": "m_row_count", "fid": "gw_count_fid", "name": "Row count",
            "basename": "Row count", "semanticType": "quantitative",
            "analyticType": "measure", "aggName": "sum", "computed": True,
            "expression": {"op": "one", "params": [], "as": "gw_count_fid"}}


def _gw_chart(vis_id: str, name: str, geom: str, dim: str, color: str = "") -> dict:
    """单张预置图表（新版 graphic-walker spec 模型）。

    dimensions/measures 只是左侧字段池；X 轴=encodings.columns、Y 轴=
    encodings.rows，饼图 arc 走 color+theta，均放完整字段对象。缺 channels
    时前端只渲染字段列表、坐标轴全空（旧 spec 的症状）。
    """
    dim_f = _gw_fld(dim)
    color_f = _gw_fld(color) if color else None
    count = _gw_count_measure()
    pool_dims = [dim_f] + ([color_f] if color_f else [])
    x_f = dim_f
    if geom == "line" and dim_f["semanticType"] == "temporal":
        # 原始时间戳每点计数必为 1（平线），走前端 dateTimeDrill 计算字段
        # 按月分箱趋势才有形态；timeUnit 取值同前端白名单 year/quarter/month/...
        x_f = {"dragId": f"d_{dim}_month", "fid": f"{dim}_month",
               "name": f"{dim}(月)", "basename": f"{dim}(月)",
               "semanticType": "temporal", "analyticType": "dimension",
               "computed": True, "timeUnit": "month",
               "expression": {"op": "dateTimeDrill", "as": f"{dim}_month",
                              "params": [{"type": "field", "value": dim},
                                         {"type": "value", "value": "month"}]}}
        pool_dims.append(x_f)
    enc = {"dimensions": pool_dims,
           "measures": [count], "rows": [], "columns": [], "color": [],
           "opacity": [], "size": [], "shape": [], "radius": [], "theta": [],
           "longitude": [], "latitude": [], "geoId": [], "details": [],
           "filters": [], "text": []}
    if geom == "arc":  # 饼图：维度进 color 分扇区，计数进 theta 定角度
        enc["color"] = [dim_f]
        enc["theta"] = [count]
    else:  # bar/line：X 轴=维度，Y 轴=计数，可选 color 分组
        enc["columns"] = [x_f]
        enc["rows"] = [count]
        if color_f:
            enc["color"] = [color_f]
    return {
        "visId": vis_id, "name": name, "encodings": enc,
        "config": {"defaultAggregated": True, "geoms": [geom],
                   "coordSystem": "generic", "limit": -1},
        "layout": {"showTableSummary": False, "format": {}, "resolve": {},
                   "size": {"mode": "auto", "width": 320, "height": 200},
                   "interactiveScale": False, "stack": "stack",
                   "showActions": False, "zeroScale": True},
    }


def _build_default_spec(kind: str) -> str:
    """默认预置图表 spec（打开即有图表，无需手动拖拽）。

    charts: (图表名, 几何类型, 维度列, 可选颜色列)；度量统一用内置行计数。
    几何类型取前端 GEOM_TYPES 白名单值：bar/line/arc（饼图是 arc 不是 pie）。
    """
    if kind == "aftersale":
        charts = [
            ("问题类型分布", "bar", "类型"),
            ("地区分布", "bar", "地区"),
            ("解决率趋势", "line", "发生时间", "是否解决"),
            ("我方问题/主动发起占比", "arc", "是否我方问题"),
        ]
    else:
        charts = [
            ("分类占比", "arc", "分类"),
            ("四分类趋势", "line", "日期", "分类"),
            ("署名工作量", "bar", "署名"),
        ]
    config = [_gw_chart(f"chart{i}", name, geom, dim, *rest)
              for i, (name, geom, dim, *rest) in enumerate(charts)]
    return json.dumps(
        {"version": "0.5.0", "config": config,
         "chart_map": {}, "workflow_list": []},
        ensure_ascii=False)


# ==================== 数据源（pygwalker 自助分析 DataFrame） ====================

def _aftersale_options(filters=None) -> object:
    """售后记录 DataFrame（pygwalker 自助分析数据源）。

    filters（records 页 _current_filters）含 cycle_start/issue_type/resolved/
    is_initiative/is_our_problem/keyword；空/None 时拉全量。
    """
    import pandas as pd
    from database import aftersale_db

    f = filters or {}
    _, rows = aftersale_db.query_page(
        1, 100000,
        keyword=f.get("keyword", ""),
        cycle_start=f.get("cycle_start", ""),
        issue_type=f.get("issue_type", ""),
        resolved=f.get("resolved", ""),
        is_initiative=f.get("is_initiative", ""),
        is_our_problem=f.get("is_our_problem", ""),
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.rename(columns={k: v for k, v in _AFTERSALE_CN.items()
                              if k in df.columns})


def _ledger_options(filters=None) -> object:
    """跑视频记录 DataFrame（pygwalker 自助分析数据源）。

    filters（records 页 _current_filters）含 category/kind/signer/keyword/
    date_from/date_to/repro；空/None 时拉全量。
    """
    import pandas as pd
    from database import ledger_db

    f = filters or {}
    _, rows = ledger_db.query_page(
        1, 100000,
        keyword=f.get("keyword", ""),
        category=f.get("category", ""),
        kind=f.get("kind", ""),
        signer=f.get("signer", ""),
        date_from=f.get("date_from", ""),
        date_to=f.get("date_to", ""),
        repro=f.get("repro", ""),
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.rename(columns={k: v for k, v in _LEDGER_CN.items()
                              if k in df.columns})