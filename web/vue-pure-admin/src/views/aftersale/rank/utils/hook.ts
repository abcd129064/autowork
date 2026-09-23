import dayjs from "dayjs";
import { message } from "@/utils/message";
import { ref, reactive, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { getRank, getCycleOptions, getRecords, YES_NO_OPTIONS } from "@/api/aftersale";
import type { RankRow, RankResult } from "@/api/aftersale";

/** 时间范围类型（默认近 90 天，与看板兜底口径一致） */
export type RangeType = "cycle" | "d30" | "d90" | "all" | "custom";

export const RANGE_OPTIONS: Array<{ label: string; value: RangeType }> = [
  { label: "近 30 天", value: "d30" },
  { label: "近 90 天", value: "d90" },
  { label: "全部", value: "all" },
  { label: "按账期", value: "cycle" },
  { label: "自定义", value: "custom" }
];

/** 榜单深度选项（"全部" 走后端 limit 上限 200） */
export const LIMIT_OPTIONS = [
  { label: "TOP 10", value: 10 },
  { label: "TOP 20", value: 20 },
  { label: "TOP 50", value: 50 },
  { label: "全部", value: 200 }
];

/** 排序选项（与后端 _RANK_SORT 白名单对齐） */
export const SORT_OPTIONS = [
  { label: "总量", value: "total" },
  { label: "未解决", value: "unresolved" },
  { label: "我方问题", value: "our_problem" },
  { label: "最近发生", value: "last_occurred" }
];

export function useAftersaleRank() {
  const loading = ref(true);
  const router = useRouter();

  /** 顶部筛选 */
  const filter = reactive({
    range_type: "d90" as RangeType,
    cycle_start: "",
    custom: [] as string[], // [start, end] yyyy-MM-dd
    resolved: "",
    is_initiative: "",
    is_our_problem: "",
    region: "",
    keyword: "" // 球房名/桌号/问题关键字
  });

  /** 榜单状态：级别 / 深度 / 排序 / 下钻球房 */
  const level = ref<"room" | "table">("room");
  const limit = ref(10);
  const sort = ref("total");
  /** 下钻锁定的球房（仅 level=table 时生效；空=全局联合榜） */
  const drillRoom = ref("");

  const rows = ref<RankRow[]>([]);
  const summary = ref<RankResult["summary"]>({
    rooms: 0,
    tables: 0,
    total: 0,
    unresolved: 0
  });

  const cycleOptions = ref<string[]>([]);
  const regions = ref<string[]>([]);
  const yesNoOptions = YES_NO_OPTIONS;

  /** 概览指标卡 */
  const summaryCards = computed(() => [
    { key: "rooms", label: "覆盖球房", value: summary.value.rooms },
    { key: "tables", label: "覆盖球桌", value: summary.value.tables },
    { key: "total", label: "记录总数", value: summary.value.total },
    {
      key: "unresolved",
      label: "未解决",
      value: summary.value.unresolved,
      danger: true
    }
  ]);

  /** 把 range_type 折算成后端 start/end/cycle_start 参数 */
  function buildTimeParams() {
    const p: Record<string, string> = {};
    switch (filter.range_type) {
      case "cycle":
        if (filter.cycle_start) p.cycle_start = filter.cycle_start;
        break;
      case "d30":
        p.start = dayjs().subtract(29, "day").format("YYYY-MM-DD");
        p.end = dayjs().format("YYYY-MM-DD");
        break;
      case "d90":
        p.start = dayjs().subtract(89, "day").format("YYYY-MM-DD");
        p.end = dayjs().format("YYYY-MM-DD");
        break;
      case "all":
        // 后端无时间条件时兜底近 90 天，"全部" 用极早 start 显式绕开兜底
        p.start = "2000-01-01";
        break;
      case "custom":
        if (filter.custom?.[0]) p.start = filter.custom[0];
        if (filter.custom?.[1]) p.end = filter.custom[1];
        break;
    }
    return p;
  }

  async function loadRank(showLoading = true) {
    if (showLoading) loading.value = true;
    try {
      const res = await getRank({
        level: level.value,
        limit: limit.value,
        sort: sort.value,
        room_name: drillRoom.value,
        resolved: filter.resolved,
        is_initiative: filter.is_initiative,
        is_our_problem: filter.is_our_problem,
        region: filter.region,
        keyword: filter.keyword.trim(),
        ...buildTimeParams()
      });
      rows.value = res.rows ?? [];
      summary.value = res.summary ?? summary.value;
    } catch (err) {
      message("排行数据加载失败，请检查后端服务", { type: "error" });
      console.error("[aftersale/rank] getRank failed:", err);
    } finally {
      loading.value = false;
    }
  }

  function onSearch() {
    loadRank();
  }

  function resetFilter(formEl?: any) {
    formEl?.resetFields?.();
    filter.range_type = "d90";
    filter.cycle_start = "";
    filter.custom = [];
    filter.resolved = "";
    filter.is_initiative = "";
    filter.is_our_problem = "";
    filter.region = "";
    filter.keyword = "";
    drillRoom.value = "";
    onSearch();
  }

  /** 分段切换 球房/球桌（切回球房时退出下钻） */
  function switchLevel(lv: "room" | "table") {
    if (level.value === lv) return;
    level.value = lv;
    if (lv === "room") drillRoom.value = "";
    loadRank();
  }

  /** 下钻：球房行 → 该球房内桌号榜 */
  function drillInto(row: RankRow) {
    if (level.value === "room" && row.room_name) {
      level.value = "table";
      drillRoom.value = row.room_name;
      loadRank();
    }
  }

  /** 面包屑返回全局榜 */
  function drillOut() {
    if (!drillRoom.value) return;
    drillRoom.value = "";
    loadRank();
  }

  /** 排行条目 → 记录列表带筛选（复用列表页 URL query 同步机制） */
  function goListWith(row: RankRow) {
    const query: Record<string, string> = { room_name: row.room_name };
    // 全局球桌榜/下钻榜都带桌号；球房榜只带球房
    if (level.value === "table" && row.table_no) query.table_no = row.table_no;
    // 把时间口径一并带过去（账期直传；自定义/近N天折算 occurred_at 区间不可行，
    // 列表页无区间参数，仅账期模式跟随）
    if (filter.range_type === "cycle" && filter.cycle_start) {
      query.cycle_start = filter.cycle_start;
    }
    router.push({ path: "/aftersale/list", query });
  }

  /** 导出当前榜单为 CSV（前端生成，带 BOM 防 Excel 中文乱码） */
  function exportCsv() {
    if (!rows.value.length) {
      message("当前榜单没有可导出的数据", { type: "warning" });
      return;
    }
    const isTable = level.value === "table";
    const headers = isTable
      ? ["排名", "球房", "桌号", "总量", "占比%", "未解决", "我方问题", "主动发起", "最近发生"]
      : ["排名", "球房", "总量", "占比%", "未解决", "我方问题", "主动发起", "最近发生"];
    const body = rows.value.map(r =>
      isTable
        ? [r.rank, r.room_name, r.table_no, r.total, r.share, r.unresolved, r.our_problem, r.initiative, r.last_occurred]
        : [r.rank, r.room_name, r.total, r.share, r.unresolved, r.our_problem, r.initiative, r.last_occurred]
    );
    const csv = [headers, ...body]
      .map(line =>
        line
          .map(v => {
            const s = String(v ?? "");
            return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
          })
          .join(",")
      )
      .join("\r\n");
    const scope = drillRoom.value ? `_${drillRoom.value}` : "";
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `售后排行_${level.value}${scope}_${dayjs().format("YYYYMMDD_HHmmss")}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    message(`已导出 ${rows.value.length} 行榜单`, { type: "success" });
  }

  /** 账期候选 + 地区候选（与列表页同款一次大页去重） */
  async function loadOptions() {
    try {
      const res = await getCycleOptions();
      cycleOptions.value = res.options ?? [];
    } catch (err) {
      console.error("[aftersale/rank] cycle-options failed:", err);
    }
    try {
      const res = await getRecords({ page: 1, page_size: 200 });
      regions.value = Array.from(
        new Set((res.rows ?? []).map(r => r.region).filter(Boolean))
      ).sort();
    } catch (err) {
      console.error("[aftersale/rank] loadFacets failed:", err);
    }
  }

  onMounted(() => {
    loadRank();
    loadOptions();
  });

  return {
    loading,
    filter,
    level,
    limit,
    sort,
    drillRoom,
    rows,
    summary,
    summaryCards,
    cycleOptions,
    regions,
    yesNoOptions,
    loadRank,
    onSearch,
    resetFilter,
    switchLevel,
    drillInto,
    drillOut,
    goListWith,
    exportCsv
  };
}
