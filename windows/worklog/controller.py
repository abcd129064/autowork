# -*- coding: utf-8 -*-
"""工作留痕异步编排层：五个页签所有 QThread 的归属地

边界（沿用售后面板的架构评审约定）：
- 本模块只依赖 database / workers，不出现任何 widget、dialog、InfoBar；
  界面层通过信号接收结果与错误。
- 页面不 new QThread，也不持有 worker；切页/关窗统一由 ``stop_all()``
  请求中断（PivotPage.detach_workers 优先调页面的 detach_workers 钩子，
  找不到才扫 ``_worker`` 后缀属性，所以页面把本对象存成 ``_ctl`` 即可）。
- 每族查询只保留一个 worker：新查询先 requestInterruption 旧的再起新的，
  结果带 seq 代数由页面丢弃过期帧（先例 windows/aftersale/rank.py）。

信号一律带 seq：页签1 一次刷新并发三族查询（记录/分组/汇总），
筛选条件连点几下就会有旧结果晚到，没有代数守卫就会闪回上一份数据。
"""

from PySide6.QtCore import QObject, Signal

from database import worklog_db
from workers.worklog_worker import spawn


def _load_options() -> dict:
    """一次取齐下拉候选：成员 / 分类 / 历史项目名

    拆三个 worker 会多出两次线程创建与三处 error 落点，而这些候选彼此
    独立、总量都很小（几十条），一趟取完更简单。模块级函数而非方法：
    它跑在 worker 线程里，不该碰 self。
    """
    return {
        "members": worklog_db.list_members(),
        "categories": worklog_db.list_categories(),
        "projects": worklog_db.project_candidates(),
    }


class WorklogController(QObject):
    """工作留痕面板的后台任务编排器

    每个页签各持一个实例（页签1/2/4 都要查记录，共用一个会互相中断），
    这样 detach_workers 能按页面粒度收尾，不会把别的页签正在跑的查询打断。
    """

    rows_ready = Signal(object, object)
    groups_ready = Signal(object, object)
    totals_ready = Signal(object, object)
    stacked_ready = Signal(object, object)
    cats_ready = Signal(object, object)
    summary_ready = Signal(object, object)
    load_failed = Signal(str, object)
    options_ready = Signal(object)
    options_failed = Signal(str)
    mutation_done = Signal(str, object)
    mutation_failed = Signal(str, str)
    export_done = Signal(object)
    export_failed = Signal(str)
    report_ready = Signal(str)
    report_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 属性名统一以 _worker 结尾，stop_all 靠 dir() 扫这个后缀；
        # 少一个后缀，关窗时那个线程就没人中断，页面销毁后回调会崩
        self._rows_worker = None
        self._groups_worker = None
        self._totals_worker = None
        self._stacked_worker = None
        self._cats_worker = None
        self._summary_worker = None
        self._options_worker = None
        self._mutate_worker = None
        self._export_worker = None
        self._report_worker = None

    # ---------- 运行状态 ----------

    @staticmethod
    def _running(worker) -> bool:
        """worker 是否还在跑；底层 C++ 对象已销毁时按未运行处理

        线程收尾后再去 isRunning 一个已 deleteLater 的包装对象会抛
        RuntimeError，这里吞掉，调用方才不用每次判断都套 try。
        """
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())
        except RuntimeError:
            return False

    def is_running(self, *attrs) -> bool:
        """给定属性名里是否有正在运行的

        名字可带或不带前导下划线与 ``_worker`` 后缀：调用方按业务叫
        "rows" 更直观，属性上却带前后缀，统一在这里补。
        """
        for attr in attrs:
            name = attr if attr.startswith("_") else "_" + attr
            if not name.endswith("_worker"):
                name += "_worker"
            if self._running(getattr(self, name, None)):
                return True
        return False

    def stop_all(self):
        """请求中断所有后台线程；只 interrupt 不 wait，与主窗口口径一致

        扫 dir(self) 找 *_worker 后缀而不是逐个点名：新加线程时漏改清单
        不会报错，只会在关窗时崩，所以让扫描自动覆盖更安全。
        """
        for attr in dir(self):
            if not attr.endswith("_worker"):
                continue
            worker = getattr(self, attr, None)
            if worker is None:
                continue
            try:
                worker.requestInterruption()
            except (RuntimeError, TypeError):
                pass

    # ---------- 查询 ----------

    def _start_query(self, attr, signal, seq, func, *args, **kwargs):
        """起一族查询：先中断同族的上一条，结果与错误都带 seq 回传

        只 interrupt 不 disconnect —— disconnect 会连 worker 内部的
        finished→_release 一起断掉，保活集合随之泄漏；第二道保险是页面
        侧的 seq 守卫。error 必须接（spawn 把它做成必填位置参数），
        只接 result 的 worker 一旦抛异常就静默失败，界面停在旧数据上。
        """
        cur = getattr(self, attr, None)
        if self._running(cur):
            cur.requestInterruption()
        worker = spawn(func,
                       lambda result, _s=seq: signal.emit(result, _s),
                       lambda msg, _s=seq: self.load_failed.emit(msg, _s),
                       *args, **kwargs)
        setattr(self, attr, worker)

    def query_records(self, seq, **filters):
        """记录明细（页签1 表格 / 页签2 时间轴 / 页签3 思维导图共用）"""
        self._start_query("_rows_worker", self.rows_ready, seq,
                          worklog_db.query_records, **filters)

    def query_groups(self, seq, **filters):
        """按天聚合（页签1 分组行 + 「今日*」汇总卡的唯一来源）"""
        self._start_query("_groups_worker", self.groups_ready, seq,
                          worklog_db.daily_groups, **filters)

    def query_totals(self, seq, **filters):
        """区间总计（「本周人均工时」卡 / 页签4 汇总卡）"""
        self._start_query("_totals_worker", self.totals_ready, seq,
                          worklog_db.totals, **filters)

    def query_stacked(self, seq, date_from, date_to, member_id=0):
        """按天×成员堆叠（页签4 柱状图）"""
        self._start_query("_stacked_worker", self.stacked_ready, seq,
                          worklog_db.stacked_daily, date_from, date_to,
                          member_id)

    def query_categories(self, seq, date_from, date_to, member_id=0):
        """按分类聚合工时（页签4 环图）"""
        self._start_query("_cats_worker", self.cats_ready, seq,
                          worklog_db.category_hours, date_from, date_to,
                          member_id)

    def query_member_summary(self, seq, date_from, date_to):
        """成员汇总（页签4 底部表格）"""
        self._start_query("_summary_worker", self.summary_ready, seq,
                          worklog_db.member_summary, date_from, date_to)

    def fetch_options(self) -> bool:
        """拉下拉候选；已有在跑则跳过（返回 False）"""
        if self._running(self._options_worker):
            return False
        worker = spawn(_load_options,
                       lambda data: self.options_ready.emit(data),
                       lambda msg: self.options_failed.emit(msg))
        self._options_worker = worker
        return True

    # ---------- 写操作 ----------

    def mutate(self, tag: str, func, *args) -> bool:
        """单条写操作（新增/编辑/删除/改进度/成员与分类维护）

        tag 告诉页面这是哪一种写，据此决定怎么收尾（刷新哪几族查询、
        InfoBar 写什么文案）。不做「已有在跑就跳过」的拦截：连续录入
        会连着提交多条，拦下来就等于吞掉用户的输入。
        """
        worker = spawn(func,
                       lambda payload, _t=tag:
                       self.mutation_done.emit(_t, payload),
                       lambda msg, _t=tag:
                       self.mutation_failed.emit(_t, msg),
                       *args)
        self._mutate_worker = worker
        return True

    def export_xlsx(self, path: str, date_from="", date_to="",
                    member_id=0) -> bool:
        """导出整表；已有导出在跑则忽略本次触发（同一文件并发写会互相截断）

        走 query_records(limit=0) 的整表口径：分页查询被钳在 1~500 行，
        拿它导出会在数据量大的时候静默少行（售后面板踩过）。
        """
        if self._running(self._export_worker):
            return False
        worker = spawn(worklog_db.export_xlsx,
                       lambda count: self.export_done.emit(count),
                       lambda msg: self.export_failed.emit(msg),
                       path, date_from, date_to, member_id)
        self._export_worker = worker
        return True

    def build_report(self, date_from="", date_to="", member_id=0,
                     title="") -> bool:
        """后台拼报告纯文本；剪贴板与 InfoBar 由页面在槽里做

        QClipboard 只能在 GUI 线程碰，所以 worker 只负责拼串。
        """
        if self._running(self._report_worker):
            return False
        worker = spawn(worklog_db.build_report_text,
                       lambda text: self.report_ready.emit(text),
                       lambda msg: self.report_failed.emit(msg),
                       date_from, date_to, member_id, title)
        self._report_worker = worker
        return True


__all__ = ["WorklogController"]
