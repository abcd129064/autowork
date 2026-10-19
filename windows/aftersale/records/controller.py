# -*- coding: utf-8 -*-
"""异步编排层（controller）：所有 QThread 的归属地

边界（架构评审约定）：
- 本模块**只**依赖 database / workers，不出现任何 widget、dialog、
  InfoBar、QMessageBox；界面层通过信号接收结果与错误。
- 页面（page.py）不再 new QThread，也不再持有 worker 属性；关窗/切页
  统一由 `stop_all()` 请求中断（pivot_page.detach_workers 调 page
  .detach_workers() → 本方法）。
- 一次查询只保留一个 query worker：新查询先 requestInterruption 旧
  worker 再起新的，结果用 seq 代数由页面丢弃过期帧。

信号（全部由页面在 `_connect_controller` 里连到视图槽）：
    rows_ready(result, seq) / load_failed(msg, seq)
    cycles_ready(list) / cycles_failed()
    recalc_finished()
    fingerprint_ready(object)
    mutation_done(tag, payload) / mutation_failed(tag, msg)
    export_done(count) / export_failed(msg)
    import_preview_ready(result) / import_done(count) / import_failed(msg)
    candidates_ready(dict)
"""

from PySide6.QtCore import QObject, Signal

from database import aftersale_db
from workers.aftersale_worker import AftersaleDBWorker


class RecordsController(QObject):
    """售后记录页的后台任务编排器，也是本页所有 QThread 的唯一归属地。

    页面只连信号、不持有线程，切页或关窗时停掉本对象即可；
    线程若挂在页面上，页面先被销毁而线程还在跑，回调就会触到死 Qt 对象。
    """

    rows_ready = Signal(object, object)
    load_failed = Signal(str, object)
    cycles_ready = Signal(object)
    cycles_failed = Signal()
    recalc_finished = Signal()
    fingerprint_ready = Signal(object)
    mutation_done = Signal(str, object)
    mutation_failed = Signal(str, str)
    export_done = Signal(object)
    export_failed = Signal(str)
    import_preview_ready = Signal(object)
    import_done = Signal(object)
    import_failed = Signal(str)
    candidates_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 属性名统一以 _worker 结尾，stop_all 靠 dir() 扫这个后缀；
        # 少一个后缀，关窗时那个线程就没人中断，页面销毁后回调会崩
        self._query_worker = None
        self._cycles_worker = None
        self._recalc_worker = None
        self._auto_worker = None
        self._mutate_worker = None
        self._batch_worker = None
        self._export_worker = None
        self._import_worker = None
        self._cand_worker = None

    # ---------- 运行状态 ----------

    @staticmethod
    def _running(worker) -> bool:
        """判断 worker 是否还在跑；底层 C++ 对象已销毁时按未运行处理。

        线程收尾后去 isRunning 一个已 deleteLater 的包装对象会抛
        RuntimeError，这里吞掉，调用方才不用每次判断都套 try。
        """
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())
        except RuntimeError:      # 底层 C++ 对象已销毁
            return False

    def is_running(self, *attrs) -> bool:
        """给定的 worker 属性名里是否有正在运行的，名字可带或不带前导下划线。

        调用方按业务叫 "query" 更直观，属性上却带下划线；统一在这里补，
        免得每个调用点各写一次 "_query_worker" 字面量。
        """
        for attr in attrs:
            name = attr if attr.startswith("_") else "_" + attr
            if self._running(getattr(self, name, None)):
                return True
        return False

    def stop_all(self):
        """请求中断所有后台线程，只 interrupt 不 wait，与主窗口口径一致。

        扫 dir(self) 找 *_worker 后缀而不是逐个点名：新加线程时漏改这份
        清单不会报错，只会在关窗时崩，所以让扫描自动覆盖更安全。
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

    def query(self, filters: dict, page_no: int, page_size: int, seq,
              sort_key: str = "", sort_order: str = "desc"):
        """分页查询 + 统计，先中断上一条查询，结果带 seq 回传

        排序参数原样传给数据库层，页面不再对已加载的 60 行做本地排序。
        """
        if self._running(self._query_worker):
            # 只 interrupt 不 disconnect：worker 检测到中断会静默返回，
            # 不发 result_ready；disconnect 连内部 finished→_release 一起断，
            # 会造成保活集合泄漏。第二道保险是页面侧 seq 守卫。
            self._query_worker.requestInterruption()
        f = filters
        worker = AftersaleDBWorker(
            aftersale_db.query_with_stats,
            page_no, page_size,
            keyword=f["keyword"], cycle_start=f["cycle_start"],
            issue_type=f["issue_type"], resolved=f["resolved"],
            is_initiative=f["is_initiative"],
            is_our_problem=f["is_our_problem"],
            sort_key=sort_key, sort_order=sort_order)
        worker.result_ready.connect(
            lambda result, _s=seq: self.rows_ready.emit(result, _s))
        worker.error.connect(
            lambda msg, _s=seq: self.load_failed.emit(msg, _s))
        self._query_worker = worker
        worker.start()

    # ---------- 周期 / 重算 / 指纹 ----------

    def fetch_cycles(self):
        """拉周期选项；已有在跑则跳过（返回 False）"""
        if self._running(self._cycles_worker):
            return False
        worker = AftersaleDBWorker(aftersale_db.get_cycle_options)
        worker.result_ready.connect(
            lambda result: self.cycles_ready.emit(result))
        worker.error.connect(lambda _m: self.cycles_failed.emit())
        self._cycles_worker = worker
        worker.start()
        return True

    def recalc_cycles(self):
        """首次加载前的一次性周期物化列兜底重算；失败与成功同样继续"""
        worker = AftersaleDBWorker(aftersale_db.recalc_cycle_starts_on_load)
        worker.result_ready.connect(lambda _v: self.recalc_finished.emit())
        worker.error.connect(lambda _m: self.recalc_finished.emit())
        self._recalc_worker = worker
        worker.start()

    def check_fingerprint(self):
        """自动刷新用的变更指纹探测；已有在跑则跳过（返回 False）。

        探测失败一律静默：下一轮定时还会再探，弹错误框只会把偶发的
        锁表噪声变成用户可见的打扰。
        """
        if self._running(self._auto_worker):
            return False
        worker = AftersaleDBWorker(aftersale_db.change_fingerprint)
        worker.result_ready.connect(
            lambda fp: self.fingerprint_ready.emit(fp))
        worker.error.connect(lambda _m: None)   # 探测失败静默，下个周期再试
        self._auto_worker = worker
        worker.start()
        return True

    # ---------- 写操作 ----------

    def _start_mutation(self, attr: str, tag: str, func, args) -> bool:
        """起一个写操作 worker，结果按 tag 回给页面，并写回 attr 保活。

        tag 说明这是编辑、删除还是批量，页面据它决定怎么收尾；
        写回 attr 是防止局部变量出栈后 worker 被回收、线程中途断掉。
        """
        worker = AftersaleDBWorker(func, *args)
        worker.result_ready.connect(
            lambda payload, _t=tag: self.mutation_done.emit(_t, payload))
        worker.error.connect(
            lambda msg, _t=tag: self.mutation_failed.emit(_t, msg))
        setattr(self, attr, worker)
        worker.start()
        return True

    def mutate(self, tag: str, func, *args) -> bool:
        """单条写操作（编辑/删除/复制/快速解决）"""
        return self._start_mutation("_mutate_worker", tag, func, args)

    def batch_mutate(self, tag: str, func, *args) -> bool:
        """批量写操作；已有批量任务在跑则忽略本次触发。

        批量必须挡重复：同一批数据并发写会互相覆盖，而且进度只有一个
        信号来源，同时跑两份就没法判断何时算结束。
        """
        if self._running(self._batch_worker):
            return False
        return self._start_mutation("_batch_worker", tag, func, args)

    # ---------- 导入 / 导出 ----------

    def export_xlsx(self, path: str, filters: dict) -> bool:
        if self._running(self._export_worker):
            return False
        f = filters
        worker = AftersaleDBWorker(
            aftersale_db.export_xlsx, path,
            keyword=f["keyword"], cycle_start=f["cycle_start"],
            issue_type=f["issue_type"], resolved=f["resolved"],
            is_initiative=f["is_initiative"],
            is_our_problem=f["is_our_problem"])
        worker.result_ready.connect(
            lambda count: self.export_done.emit(count))
        worker.error.connect(lambda msg: self.export_failed.emit(msg))
        self._export_worker = worker
        worker.start()
        return True

    def parse_excel(self, path: str) -> bool:
        """导入第一步：后台解析文件但不写库，成功后由页面弹预览确认。

        解析与落库拆两步，是为了让用户在写库前看到行数与异常行；
        合成一步就只能等写完了才发现解析错了，回滚成本高得多。
        """
        if self._running(self._import_worker):
            return False
        worker = AftersaleDBWorker(aftersale_db.parse_excel_rows, path)
        worker.result_ready.connect(
            lambda result: self.import_preview_ready.emit(result))
        worker.error.connect(lambda msg: self.import_failed.emit(msg))
        self._import_worker = worker
        worker.start()
        return True

    def commit_import(self, path: str) -> bool:
        """导入第二步：确认后真正写库"""
        if self._running(self._import_worker):
            return False
        worker = AftersaleDBWorker(aftersale_db.import_excel_rows, path)
        worker.result_ready.connect(lambda count: self.import_done.emit(count))
        worker.error.connect(lambda msg: self.import_failed.emit(msg))
        self._import_worker = worker
        worker.start()
        return True

    # ---------- 表单动态候选 ----------

    def fetch_candidates(self) -> bool:
        """拉取表单动态候选字段；已有请求在跑则跳过并返回 False。

        候选只是输入框下拉的增强项，打开表单时拉一次即可，
        空结果由页面用内置默认值兜底。
        """
        if self._running(self._cand_worker):
            return False
        worker = AftersaleDBWorker(aftersale_db.get_field_candidates)
        worker.result_ready.connect(
            # 不接 error：候选是增强项，失败时页面按内置默认值继续，不必打扰用户
            lambda data: self.candidates_ready.emit(data))
        self._cand_worker = worker
        worker.start()
        return True


__all__ = ["RecordsController"]
