# -*- coding: utf-8 -*-
"""跑视频记录页异步编排层：本页唯一的 QThread 归属地。

边界（与 windows/aftersale/records/controller.py 同口径）：
- 只依赖 PySide6.QtCore、database、workers。不 import 任何 widget、dialog、
  InfoBar、QMessageBox，也不 import windows.run_video.common：控件操作与
  提示文案留在 page.py，这里只负责「起任务、搬结果」。
- 页面不再 new QThread、不再持有 *_worker 属性。切页/关窗由
  page.detach_workers() 转调 stop_all()，统一 requestInterruption。
- 跨线程只有一条路：worker 信号 → controller 中继 → 页面槽。中继用普通
  lambda，在 worker 线程执行；controller 的信号连到主线程页面槽时 Qt 自动
  排队，页面槽一定在主线程跑。

信号契约（page.py 按此连线，名字与载荷固定）：
    rows_ready(object, object)       (total, rows), seq
    load_failed(str, object)         msg, seq
    stats_ready(object, object)      counts, seq
    signers_ready(object)            [署名, ...]
    candidates_ready(object, object) [类别, ...], category
    fingerprint_ready(object)        (count, max_updated_at, max_id)
    mutation_done(str, object)       tag("edit"/"delete"), payload
    mutation_failed(str, str)        tag, msg
    export_done(object)              导出行数
    export_failed(str)               msg
    signer_stats_ready(object)       [{signer, 问题, 未复现, 精度, 使用, total}]
    signer_stats_failed(str)         msg

worker 归属（每类任务一个独立属性，属性名一律以 _worker 结尾：
pivot_page 的目录扫描兜底依赖该后缀）：
    _query_worker / _count_worker / _signers_worker / _candidates_worker /
    _auto_worker / _mutate_worker / _del_worker / _export_worker /
    _signer_stats_worker

为什么要拆：原页面的 _stats_worker 同时承担指标卡计数、署名候选、类别候选、
署名统计四类任务，后起的任务直接顶掉前一个属性引用；旧任务晚到的结果还会
覆盖新筛选下的候选（切分类后类别下拉回跳）。拆开后每个任务独占 worker 与
信号，页面按数据类型分发，不再串线。
"""

from PySide6.QtCore import QObject, Signal

from database import ledger_db, table_db
from workers.aftersale_worker import AftersaleDBWorker


# ==================== 模块级数据库操作（从 page.py 原样搬出） ====================

def _count_by_category(date_from: str = "", date_to: str = "") -> dict:
    """分类计数（指标卡数据源），日期按视频日期 occurred_at 过滤。

    COALESCE 兼容旧库 occurred_at 为空时回退 created_at，与
    ledger_db._build_where 的日期口径一致，保证指标卡与列表同口径。
    放模块级而不是 controller 方法：worker 直接持有可调用对象，且这段 SQL
    不依赖 QObject，便于离线单测。
    """
    conn = table_db.get_conn()
    where, params = [], []
    if date_from:
        where.append("substr(COALESCE(NULLIF(occurred_at, ''), created_at),"
                     " 1, 10) >= ?")
        params.append(str(date_from).strip())
    if date_to:
        where.append("substr(COALESCE(NULLIF(occurred_at, ''), created_at),"
                     " 1, 10) <= ?")
        params.append(str(date_to).strip())
    sql = "SELECT category, COUNT(*) FROM ledger_records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY category"
    counts = {"total": 0}
    for c in ledger_db.CATEGORIES:
        counts[c] = 0
    for cat, n in conn.execute(sql, params).fetchall():
        counts[str(cat or "")] = int(n or 0)
        counts["total"] += int(n or 0)
    return counts


def _fetch_signers() -> list:
    """库中已有署名（去空、按名排序），供署名筛选下拉使用。"""
    conn = table_db.get_conn()
    return [str(r[0] or "").strip() for r in conn.execute(
        "SELECT DISTINCT signer FROM ledger_records "
        "WHERE signer != '' ORDER BY signer").fetchall()]


class RecordsController(QObject):
    """跑视频记录页的后台任务编排器（九类任务各持独立 worker）"""

    # 信号名与载荷由 page.py 的 _connect_controller 固定消费，改名即断线
    rows_ready = Signal(object, object)
    load_failed = Signal(str, object)
    stats_ready = Signal(object, object)
    signers_ready = Signal(object)
    candidates_ready = Signal(object, object)
    fingerprint_ready = Signal(object)
    mutation_done = Signal(str, object)
    mutation_failed = Signal(str, str)
    export_done = Signal(object)
    export_failed = Signal(str)
    signer_stats_ready = Signal(object)
    signer_stats_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 每个属性只服务一类任务；名字带 _worker 后缀，目录扫描才能兜底中断
        self._query_worker = None
        self._count_worker = None
        self._signers_worker = None
        self._candidates_worker = None
        self._auto_worker = None
        self._mutate_worker = None
        self._del_worker = None
        self._export_worker = None
        self._signer_stats_worker = None
        # 类别候选代际号：连续切分类时只让最后一次请求的结果落地，
        # 防止旧分类的候选晚到后覆盖当前下拉。
        self._cand_seq = 0

    # ---------- 运行状态 / 生命周期 ----------

    @staticmethod
    def _running(worker) -> bool:
        """worker 是否在跑；底层 C++ 对象已销毁时按未运行处理。"""
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())
        except RuntimeError:
            return False

    @classmethod
    def _supersede(cls, worker) -> None:
        """用新任务取代同类旧任务：只 requestInterruption，不 disconnect。

        被中断的 AftersaleDBWorker 在 run() 返回处检测中断后静默退出；
        这里若 disconnect，会连它内部 finished→_release 一起断开，worker
        模块的保活集合泄漏，线程对象可能在收尾前被 GC 回收。第二道保险
        是结果自带的 seq/代际号，页面仍可丢弃过期帧。
        """
        if not cls._running(worker):
            return
        try:
            worker.requestInterruption()
        except (RuntimeError, TypeError):
            pass

    def stop_all(self):
        """请求中断本页所有后台线程（只 interrupt，不 wait）。

        page.detach_workers()（切页/关窗）调用；不 wait 沿用主窗口口径：
        等待 DB 查询收尾会卡住 UI 关闭。扫描 *_worker 后缀而不是硬编码
        清单，后续新增任务属性无需再改这里。
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

    def is_running(self, *attrs) -> bool:
        """给定 worker 属性中是否有仍在运行的。

        attr 可带或不带前导下划线（"query_worker" 与 "_query_worker"
        等价）。页面自动刷新节流用它判断本页是否还有任务在跑。
        """
        for attr in attrs:
            name = str(attr)
            if not name.startswith("_"):
                name = "_" + name
            if self._running(getattr(self, name, None)):
                return True
        return False

    # ---------- 查询 / 指标卡 / 筛选候选 ----------

    def query(self, filters: dict, page_no: int, page_size: int, seq):
        """分页查询：先中断上一条，再起新的；结果与错误都带回 seq。

        seq 由页面生成，页面据此丢弃过期帧（自动刷新与手动筛选可能重叠）。
        筛选字段缺失按空串处理，与 ledger_db.query_page 的默认值一致。
        """
        self._supersede(self._query_worker)
        f = filters or {}
        worker = AftersaleDBWorker(
            ledger_db.query_page, page_no, page_size,
            f.get("keyword", ""), f.get("category", ""),
            f.get("kind", ""), f.get("signer", ""),
            f.get("date_from", ""), f.get("date_to", ""),
            f.get("repro", ""))
        worker.result_ready.connect(
            lambda result, _s=seq: self.rows_ready.emit(result, _s))
        worker.error.connect(
            lambda msg, _s=seq: self.load_failed.emit(msg, _s))
        self._query_worker = worker
        worker.start()

    def count_by_category(self, date_from: str = "", date_to: str = "",
                          seq=None):
        """指标卡计数：独立 _count_worker，与列表查询同口径。

        原实现与署名候选共用 _stats_worker，刷新一多就被后起任务顶掉；
        这里单独走一条通道。计数失败保持静默（信号契约里没有
        stats_failed），指标卡继续显示上一次的值，下轮刷新再试。
        """
        self._supersede(self._count_worker)
        worker = AftersaleDBWorker(_count_by_category, date_from, date_to)
        worker.result_ready.connect(
            lambda counts, _s=seq: self.stats_ready.emit(counts, _s))
        worker.error.connect(lambda _m: None)
        self._count_worker = worker
        worker.start()

    def load_signers(self):
        """刷新署名筛选候选：独立 _signers_worker。

        失败静默：署名下拉保留旧选项即可，不值得为一次候选查询打断用户；
        页面拿到 signers_ready 后自行保留当前选中项。
        """
        self._supersede(self._signers_worker)
        worker = AftersaleDBWorker(_fetch_signers)
        worker.result_ready.connect(
            lambda signers: self.signers_ready.emit(signers))
        worker.error.connect(lambda _m: None)
        self._signers_worker = worker
        worker.start()

    def load_kind_options(self, category: str):
        """类别筛选候选：空分类取内置候选，非空分类查库，结果带 category。

        空分类不碰数据库，与原实现一样同步填下拉：页面初始化时调用即可
        拿到候选，不必等一轮事件循环。非空分类走 _candidates_worker，查询
        失败回退该分类的内置候选，保证下拉永远可用。
        结果带 category 之外还做代际守卫：连续切分类时旧分类的候选即使
        晚到也不会覆盖当前下拉。
        """
        cat = (category or "").strip()
        self._cand_seq += 1
        gen = self._cand_seq
        # 旧候选查询立即中断：即使有代际号兜底，也没必要让过期 SQL 继续跑
        self._supersede(self._candidates_worker)
        if not cat:
            self.candidates_ready.emit(self._flat_kind_candidates(), "")
            return
        def _on_cands(cands, _c=cat, _g=gen):
            self._emit_candidates(cands, _c, _g)

        def _on_cands_error(_msg, _c=cat, _g=gen):
            # 查库失败回退该分类的内置候选，下拉不留空
            self._emit_candidates(
                list(ledger_db.KIND_CANDIDATES.get(_c, ())), _c, _g)

        worker = AftersaleDBWorker(ledger_db.get_kind_candidates, cat)
        worker.result_ready.connect(_on_cands)
        worker.error.connect(_on_cands_error)
        self._candidates_worker = worker
        worker.start()

    @staticmethod
    def _flat_kind_candidates() -> list:
        """内置类别候选扁平化去重（保序）：四个分类的候选大量重叠。"""
        out, seen = [], set()
        for values in ledger_db.KIND_CANDIDATES.values():
            for k in values:
                if k not in seen:
                    seen.add(k)
                    out.append(k)
        return out

    def _emit_candidates(self, cands, category: str, gen: int):
        """候选结果的代际守卫：只发最新一次请求的结果。"""
        if gen != self._cand_seq:
            return
        self.candidates_ready.emit(list(cands or ()), category)

    # ---------- 自动刷新指纹 ----------

    def check_fingerprint(self) -> bool:
        """自动刷新探针：返回本次是否真的发起了查询。

        已有探针在跑则返回 False，本周期跳过，避免慢查询叠加。失败静默：
        探针失败不该弹窗，下个周期再试。查询/写库在跑时的节流属于页面
        策略（页面用 is_running 判断），这里不替页面做决定。
        """
        if self._running(self._auto_worker):
            return False
        worker = AftersaleDBWorker(ledger_db.change_fingerprint)
        worker.result_ready.connect(
            lambda fp: self.fingerprint_ready.emit(fp))
        worker.error.connect(lambda _m: None)
        self._auto_worker = worker
        worker.start()
        return True

    # ---------- 写操作 ----------

    def _start_mutation(self, attr: str, tag: str, func, *args):
        """起一条写任务：成功/失败都带 tag 回主线程。

        payload 是 database 层的返回值（update_record/delete_record 为
        bool）。原页面只关心成功与否，这里保留原始返回值，页面按需取用。
        写操作各自独立属性，编辑与删除不会互相顶掉，也不会与查询抢通道。
        """
        worker = AftersaleDBWorker(func, *args)
        worker.result_ready.connect(
            lambda payload, _t=tag: self.mutation_done.emit(_t, payload))
        worker.error.connect(
            lambda msg, _t=tag: self.mutation_failed.emit(_t, msg))
        setattr(self, attr, worker)
        worker.start()

    def update_record(self, rec_id: int, collected: dict):
        """编辑保存（tag="edit"）：独立 _mutate_worker。"""
        self._start_mutation("_mutate_worker", "edit",
                             ledger_db.update_record, rec_id, collected)

    def delete_record(self, rec_id: int):
        """删除（tag="delete"）：独立 _del_worker。"""
        self._start_mutation("_del_worker", "delete",
                             ledger_db.delete_record, rec_id)

    # ---------- 导出 / 署名统计 ----------

    def export_xlsx(self, path: str):
        """导出 xlsx（独立 _export_worker）：成功回导出行数，失败回错误文案。"""
        worker = AftersaleDBWorker(ledger_db.export_xlsx, path)
        worker.result_ready.connect(
            lambda count: self.export_done.emit(count))
        worker.error.connect(lambda msg: self.export_failed.emit(msg))
        self._export_worker = worker
        worker.start()

    def signer_stats(self):
        """署名统计（独立 _signer_stats_worker）：结果供页面弹统计框。

        原实现与指标卡计数共用 _stats_worker，统计弹窗与自动刷新同时触发
        时会互相顶掉；独立后互不影响。
        """
        worker = AftersaleDBWorker(ledger_db.stats_by_signer)
        worker.result_ready.connect(
            lambda stats: self.signer_stats_ready.emit(stats))
        worker.error.connect(lambda msg: self.signer_stats_failed.emit(msg))
        self._signer_stats_worker = worker
        worker.start()


__all__ = ["RecordsController"]
