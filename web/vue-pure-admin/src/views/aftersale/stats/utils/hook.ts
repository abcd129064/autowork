import { message } from "@/utils/message";
import { getCharts, postStatsQuery, getCycleOptions } from "@/api/aftersale";
import type { ChartsResult } from "@/api/aftersale";
import { ref, reactive, onMounted } from "vue";

/** 图表维度白名单（与后端 `/api/stats/query` 的 `_DIM` 对齐） */
export const DIM_OPTIONS = [
  { label: "地区", value: "region" },
  { label: "问题类型", value: "issue_type" },
  { label: "是否解决", value: "resolved" },
  { label: "主动发起", value: "is_initiative" },
  { label: "我方问题", value: "is_our_problem" },
  { label: "球桌号", value: "table_no" },
  { label: "填写人", value: "creator" },
  { label: "处理人", value: "resolver" },
  { label: "按日", value: "day" },
  { label: "按周", value: "week" }
];

/** 度量方式（后端仅接受 count / percent） */
export const MEASURE_OPTIONS = [
  { label: "数量", value: "count" },
  { label: "占比", value: "percent" }
];

/** 图表类型（后端仅接受 bar / line / pie / ring / hbar） */
export const CHART_TYPE_OPTIONS = [
  { label: "柱状图", value: "bar" },
  { label: "折线图", value: "line" },
  { label: "饼图", value: "pie" },
  { label: "环形图", value: "ring" },
  { label: "条形图", value: "hbar" }
];

/** 主题色板（跟随 Fluent 青蓝主色，供饼/环图循环取色） */
export const PALETTE = [
  "#00bcd4",
  "#36cfc9",
  "#597ef7",
  "#9254de",
  "#f759ab",
  "#ff7a45",
  "#ffc53d",
  "#73d13d",
  "#40a9ff",
  "#b37feb",
  "#ff85c0",
  "#ffa940"
];

export function useAftersaleCharts() {
  const loading = ref(true);
  const charts = ref<ChartsResult>({
    region_dist: [],
    daily: [],
    our_problem: { yes: 0, no: 0 },
    issue_type_dist: [],
    aging: [],
    table_top: [],
    room_top: [],
    total: 0
  });

  /** 顶部筛选（与列表页同口径） */
  const filter = reactive({
    cycle_start: "",
    issue_type: "",
    resolved: "",
    is_initiative: "",
    is_our_problem: ""
  });
  const cycleOptions = ref<string[]>([]);

  /** 自定义图表配置 */
  const custom = reactive({
    dim: "region",
    measure: "count",
    chart: "bar",
    loading: false,
    data: [] as Array<{ name: string; value: number }>
  });

  /** 去掉空值，避免空串被后端当成有效筛选 */
  function buildFilter() {
    const f: Record<string, any> = {};
    for (const [k, v] of Object.entries(filter)) {
      if (v !== "" && v !== null && v !== undefined) f[k] = v;
    }
    return f;
  }

  /** 拉取四件套图表数据 */
  async function loadCharts() {
    loading.value = true;
    try {
      const res = await getCharts(buildFilter());
      charts.value = res;
    } catch (err) {
      message("图表数据加载失败，请检查后端服务", { type: "error" });
      console.error("[aftersale/charts] getCharts failed:", err);
    } finally {
      loading.value = false;
    }
  }

  /** 自定义聚合查询 */
  async function loadCustom() {
    custom.loading = true;
    try {
      // ⚠️ 后端读的键是 `dimension` 不是 `dim`，写错直接 400
      const res = await postStatsQuery({
        dimension: custom.dim,
        measure: custom.measure,
        chart: custom.chart,
        sort: "value_desc",
        limit: 20,
        filter: buildFilter()
      });
      // ⚠️ 后端返回键是 `columns`；percent 度量时占比在 `percent` 字段
      const list: Array<any> = res?.columns ?? [];
      custom.data = list.map(r => ({
        name: r.name ?? "未填",
        value:
          custom.measure === "percent"
            ? Number(r.percent ?? 0)
            : Number(r.value ?? 0)
      }));
    } catch (err) {
      message("自定义图表查询失败", { type: "error" });
      console.error("[aftersale/charts] postStatsQuery failed:", err);
    } finally {
      custom.loading = false;
    }
  }

  async function loadCycleOptions() {
    try {
      const res = await getCycleOptions();
      cycleOptions.value = res.options ?? [];
    } catch (err) {
      console.error("[aftersale/charts] cycle-options failed:", err);
    }
  }

  function onSearch() {
    loadCharts();
    loadCustom();
  }

  function resetFilter(formEl) {
    if (!formEl) return;
    formEl.resetFields();
    onSearch();
  }

  onMounted(() => {
    loadCharts();
    loadCustom();
    loadCycleOptions();
  });

  return {
    loading,
    charts,
    filter,
    cycleOptions,
    custom,
    loadCharts,
    loadCustom,
    onSearch,
    resetFilter
  };
}
