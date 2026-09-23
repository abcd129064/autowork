<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import { useDark, useECharts } from "@pureadmin/utils";
import { useAftersaleCharts, PALETTE, DIM_OPTIONS, MEASURE_OPTIONS, CHART_TYPE_OPTIONS } from "./utils/hook";
import { useRenderIcon } from "@/components/ReIcon/src/hooks";

import Refresh from "~icons/ep/refresh";
import TrendCharts from "~icons/ep/trend-charts";

defineOptions({
  name: "AftersaleStats"
});

const {
  loading,
  charts,
  filter,
  cycleOptions,
  custom,
  loadCharts,
  loadCustom,
  onSearch,
  resetFilter
} = useAftersaleCharts();

const formRef = ref();
const { isDark } = useDark();
const theme = computed(() => (isDark.value ? "dark" : "light"));
const router = useRouter();

/** 图表点击 → 跳列表带筛选（与总览页 goListWith 同模式） */
function goListWith(query: Record<string, string>) {
  router.push({ path: "/aftersale/list", query });
}

/** 统一的空态提示配置 */
const emptyOption = {
  title: {
    text: "暂无数据",
    left: "center",
    top: "center",
    textStyle: { color: "#909399", fontSize: 14, fontWeight: 400 }
  }
};

/* ---------------- 1. 地区分布（环形图） ---------------- */
const regionRef = ref();
const { setOptions: setRegion, getInstance: getRegionInstance } = useECharts(
  regionRef,
  {
    theme,
    renderer: "svg"
  }
);

function renderRegion() {
  const data = charts.value.region_dist ?? [];
  if (!data.length) return setRegion(emptyOption);
  setRegion({
    color: PALETTE,
    tooltip: { trigger: "item", formatter: "{b}<br/>{c} 条 ({d}%)" },
    legend: {
      type: "scroll",
      bottom: 0,
      icon: "circle",
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { fontSize: 12 }
    },
    series: [
      {
        type: "pie",
        radius: ["42%", "68%"],
        center: ["50%", "44%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: "transparent", borderWidth: 2 },
        label: { show: false },
        emphasis: {
          label: { show: true, fontSize: 16, fontWeight: "bold" }
        },
        data
      }
    ]
  });
}

/* ---------------- 2. 每日售后量（柱状图） ---------------- */
const dailyRef = ref();
const { setOptions: setDaily, getInstance: getDailyInstance } = useECharts(
  dailyRef,
  {
    theme,
    renderer: "svg"
  }
);

/** 每日图完整日期（与 x 轴 dataIndex 对齐，点击跳转用） */
let dailyDates: string[] = [];

function renderDaily() {
  const raw = charts.value.daily ?? [];
  if (!raw.length) {
    dailyDates = [];
    return setDaily(emptyOption);
  }
  // 日期点较多时抽样显示 x 轴标签，避免挤成一团（保留首尾）
  const step = Math.max(1, Math.ceil(raw.length / 12));
  dailyDates = raw.map(r => r.date);
  setDaily({
    color: [PALETTE[0]],
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      // 悬浮显示完整日期；点击柱子按同日期跳列表筛选
      formatter: (ps: any) => {
        const full = dailyDates[ps[0].dataIndex] ?? ps[0].name;
        return `${full}<br/>${ps[0].marker}售后量：${ps[0].value}`;
      }
    },
    xAxis: {
      type: "category",
      data: raw.map(r => r.date.slice(5)),
      axisLabel: {
        fontSize: 11,
        // 超过 12 条才抽样；否则全显示
        interval: raw.length > 12 ? step - 1 : 0
      },
      axisTick: { show: false }
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { type: "dashed" } },
      axisLabel: { fontSize: 11 }
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 18,
        itemStyle: { borderRadius: [3, 3, 0, 0] },
        data: raw.map(r => r.count)
      }
    ]
  });
}

/* ---------------- 3. 我方问题占比（环形图） ---------------- */
const ourRef = ref();
const { setOptions: setOur, getInstance: getOurInstance } = useECharts(ourRef, {
  theme,
  renderer: "svg"
});

function renderOur() {
  const { yes = 0, no = 0 } = charts.value.our_problem ?? {};
  const total = yes + no;
  if (!total) return setOur(emptyOption);
  const pct = ((yes / total) * 100).toFixed(1);
  setOur({
    color: ["#e6a23c", "#dcdfe6"],
    tooltip: { trigger: "item", formatter: "{b}<br/>{c} 条 ({d}%)" },
    legend: {
      bottom: 0,
      icon: "circle",
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { fontSize: 12 }
    },
    title: {
      text: `${pct}%`,
      subtext: "我方问题占比",
      left: "50%",
      top: "36%",
      textAlign: "center",
      textStyle: { fontSize: 26, fontWeight: 600 },
      subtextStyle: { fontSize: 12 }
    },
    series: [
      {
        type: "pie",
        radius: ["55%", "72%"],
        center: ["50%", "46%"],
        itemStyle: { borderColor: "transparent", borderWidth: 2 },
        label: { show: false },
        emphasis: { label: { show: false } },
        data: [
          { name: "我方问题", value: yes },
          { name: "非我方问题", value: no }
        ]
      }
    ]
  });
}

/* ---------------- 4. 问题类型分布（横向条形图） ---------------- */
const issueRef = ref();
const { setOptions: setIssue, getInstance: getIssueInstance } = useECharts(
  issueRef,
  {
    theme,
    renderer: "svg"
  }
);

function renderIssue() {
  // 横向条形图从下往上画，倒序让最大值在顶部
  const data = [...(charts.value.issue_type_dist ?? [])].reverse();
  if (!data.length) return setIssue(emptyOption);
  setIssue({
    color: [PALETTE[2]],
    grid: { left: 8, right: 30, top: 12, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: {
      type: "value",
      splitLine: { lineStyle: { type: "dashed" } },
      axisLabel: { fontSize: 11 }
    },
    yAxis: {
      type: "category",
      data: data.map(d => d.name),
      axisLabel: { fontSize: 11 },
      axisTick: { show: false }
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 14,
        itemStyle: { borderRadius: [0, 3, 3, 0] },
        label: {
          show: true,
          position: "right",
          fontSize: 11,
          color: "#909399"
        },
        data: data.map(d => d.value)
      }
    ]
  });
}

/* ---------------- 5. 未解决时长分布（横向条形图） ---------------- */
const agingRef = ref();
const { setOptions: setAging } = useECharts(agingRef, {
  theme,
  renderer: "svg"
});

function renderAging() {
  const data = charts.value.aging ?? [];
  if (!data.length) return setAging(emptyOption);
  setAging({
    color: ["#f56c6c"],
    grid: { left: 8, right: 30, top: 12, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: {
      type: "value",
      splitLine: { lineStyle: { type: "dashed" } },
      axisLabel: { fontSize: 11 }
    },
    yAxis: {
      type: "category",
      data: data.map(d => d.name),
      axisLabel: { fontSize: 11 },
      axisTick: { show: false }
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 14,
        itemStyle: { borderRadius: [0, 3, 3, 0] },
        label: {
          show: true,
          position: "right",
          fontSize: 11,
          color: "#909399"
        },
        data: data.map(d => d.value)
      }
    ]
  });
}

/* ---------------- 6. 球桌售后排行 TOP10（横向条形图） ---------------- */
const tableTopRef = ref();
const { setOptions: setTableTop, getInstance: getTableTopInstance } =
  useECharts(tableTopRef, {
    theme,
    renderer: "svg"
  });

/** 球桌排行原始行（渲染序=倒序，与 dataIndex 对齐，点击跳转取 room/table） */
let tableTopRows: Array<{ room_name: string; table_no: string }> = [];

function renderTableTop() {
  const data = [...(charts.value.table_top ?? [])].reverse();
  tableTopRows = data;
  if (!data.length) return setTableTop(emptyOption);
  setTableTop({
    color: [PALETTE[5]],
    grid: { left: 8, right: 36, top: 12, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: {
      type: "value",
      splitLine: { lineStyle: { type: "dashed" } },
      axisLabel: { fontSize: 11 }
    },
    yAxis: {
      type: "category",
      data: data.map(d => d.name),
      axisLabel: { fontSize: 11 },
      axisTick: { show: false }
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 14,
        itemStyle: { borderRadius: [0, 3, 3, 0] },
        label: {
          show: true,
          position: "right",
          fontSize: 11,
          color: "#909399"
        },
        data: data.map(d => d.value)
      }
    ]
  });
}

/* ---------------- 7. 球房售后排行 TOP10（横向条形图） ---------------- */
const roomTopRef = ref();
const { setOptions: setRoomTop, getInstance: getRoomTopInstance } = useECharts(
  roomTopRef,
  {
    theme,
    renderer: "svg"
  }
);

function renderRoomTop() {
  const data = [...(charts.value.room_top ?? [])].reverse();
  if (!data.length) return setRoomTop(emptyOption);
  setRoomTop({
    color: [PALETTE[2]],
    grid: { left: 8, right: 36, top: 12, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: {
      type: "value",
      splitLine: { lineStyle: { type: "dashed" } },
      axisLabel: { fontSize: 11 }
    },
    yAxis: {
      type: "category",
      data: data.map(d => d.name),
      axisLabel: { fontSize: 11 },
      axisTick: { show: false }
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 14,
        itemStyle: { borderRadius: [0, 3, 3, 0] },
        label: {
          show: true,
          position: "right",
          fontSize: 11,
          color: "#909399"
        },
        data: data.map(d => d.value)
      }
    ]
  });
}

/* ---------------- 8. 自定义图表 ---------------- */
const customRef = ref();
const { setOptions: setCustom } = useECharts(customRef, {
  theme,
  renderer: "svg"
});

function renderCustom() {
  const data = custom.data ?? [];
  if (!data.length) return setCustom(emptyOption);

  const names = data.map(d => d.name);
  const values = data.map(d => d.value);
  const isPercent = custom.measure === "percent";
  const suffix = isPercent ? "%" : " 条";

  // 饼 / 环
  if (custom.chart === "pie" || custom.chart === "ring") {
    return setCustom({
      color: PALETTE,
      tooltip: { trigger: "item", formatter: `{b}<br/>{c}${suffix} ({d}%)` },
      legend: {
        type: "scroll",
        bottom: 0,
        icon: "circle",
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { fontSize: 12 }
      },
      series: [
        {
          type: "pie",
          radius: custom.chart === "ring" ? ["42%", "68%"] : "62%",
          center: ["50%", "46%"],
          itemStyle: { borderColor: "transparent", borderWidth: 2 },
          label: { show: false },
          emphasis: { label: { show: true, fontSize: 15, fontWeight: "bold" } },
          data: data.map(d => ({ name: d.name, value: d.value }))
        }
      ]
    });
  }

  // 条形（横向）
  if (custom.chart === "hbar") {
    return setCustom({
      color: [PALETTE[2]],
      grid: { left: 8, right: 40, top: 12, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: {
        type: "value",
        splitLine: { lineStyle: { type: "dashed" } },
        axisLabel: { fontSize: 11, formatter: `{value}${suffix}` }
      },
      yAxis: {
        type: "category",
        data: [...names].reverse(),
        axisLabel: { fontSize: 11 },
        axisTick: { show: false }
      },
      series: [
        {
          type: "bar",
          barMaxWidth: 14,
          itemStyle: { borderRadius: [0, 3, 3, 0] },
          label: {
            show: true,
            position: "right",
            fontSize: 11,
            color: "#909399",
            formatter: `{c}${suffix}`
          },
          data: [...values].reverse()
        }
      ]
    });
  }

  // 折线 / 柱状
  setCustom({
    color: [PALETTE[0]],
    grid: { left: 8, right: 20, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: names,
      axisLabel: { fontSize: 11, rotate: names.length > 10 ? 40 : 0 },
      axisTick: { show: false }
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { type: "dashed" } },
      axisLabel: { fontSize: 11, formatter: `{value}${suffix}` }
    },
    series: [
      custom.chart === "line"
        ? {
            type: "line",
            smooth: true,
            symbolSize: 6,
            areaStyle: { opacity: 0.12 },
            data: values
          }
        : {
            type: "bar",
            barMaxWidth: 24,
            itemStyle: { borderRadius: [3, 3, 0, 0] },
            data: values
          }
    ]
  });
}

/**
 * 图表点击 → 跳列表筛选：
 * - 地区环形图：点扇区 → region=<地区>
 * - 每日柱状图：点柱子 → occurred_at=<完整日期>（dataIndex 对齐 dailyDates）
 * - 我方问题环：点扇区 → is_our_problem=是/否
 * - 类型条形图：点条目 → issue_type=<类型>
 * - 球桌排行：点条目 → room_name+table_no（联合精确筛选，跨球房同名桌不串）
 * - 球房排行：点条目 → room_name
 * off+on 幂等：主题切换/resize 重绘后重绑也不会重复触发
 */
function bindChartClicks() {
  const regionChart = getRegionInstance();
  regionChart?.off("click");
  regionChart?.on("click", (p: any) => {
    if (p?.name) goListWith({ region: String(p.name) });
  });

  const dailyChart = getDailyInstance();
  dailyChart?.off("click");
  dailyChart?.on("click", (p: any) => {
    const date = dailyDates[p?.dataIndex];
    if (date) goListWith({ occurred_at: date });
  });

  const ourChart = getOurInstance();
  ourChart?.off("click");
  ourChart?.on("click", (p: any) => {
    if (p?.name === "我方问题") goListWith({ is_our_problem: "是" });
    else if (p?.name === "非我方问题") goListWith({ is_our_problem: "否" });
  });

  const issueChart = getIssueInstance();
  issueChart?.off("click");
  issueChart?.on("click", (p: any) => {
    if (p?.name) goListWith({ issue_type: String(p.name) });
  });

  // 球桌排行：dataIndex 对齐倒序后的 tableTopRows（不能用 name 拆"·"，
  // 球房名本身可能含"·"）；带 room_name+table_no 双参精确筛选
  const tableTopChart = getTableTopInstance();
  tableTopChart?.off("click");
  tableTopChart?.on("click", (p: any) => {
    const row = tableTopRows[p?.dataIndex];
    if (row?.room_name) {
      goListWith({ room_name: row.room_name, table_no: row.table_no || "" });
    }
  });

  const roomTopChart = getRoomTopInstance();
  roomTopChart?.off("click");
  roomTopChart?.on("click", (p: any) => {
    if (p?.name) goListWith({ room_name: String(p.name) });
  });
}

/** 数据变化后统一重绘（nextTick 等 DOM 尺寸就绪） */
async function renderAll() {
  await nextTick();
  renderRegion();
  renderDaily();
  renderOur();
  renderIssue();
  renderAging();
  renderTableTop();
  renderRoomTop();
  bindChartClicks();
}

watch(charts, renderAll, { deep: true });
watch(
  () => [custom.data, custom.chart, custom.measure],
  () => nextTick(renderCustom),
  { deep: true }
);

// 主题切换 / 窗口缩放时重绘，避免 SVG 尺寸僵在旧值
watch(theme, () => nextTick(renderAll));
let resizeTimer: ReturnType<typeof setTimeout> | null = null;
function onResize() {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    renderAll();
    nextTick(renderCustom);
  }, 200);
}

onMounted(async () => {
  // 等父级 el-col 布局稳定后再首绘
  await nextTick();
  renderAll();
  nextTick(renderCustom);
  window.addEventListener("resize", onResize);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  if (resizeTimer) clearTimeout(resizeTimer);
});
</script>

<template>
  <div class="main-content">
    <!-- 筛选栏：与列表页同口径，四图联动 -->
    <el-form
      ref="formRef"
      :inline="true"
      :model="filter"
      class="search-form bg-bg_color w-full pl-8 pt-3 overflow-auto"
    >
      <el-form-item label="账期：" prop="cycle_start">
        <el-select
          v-model="filter.cycle_start"
          placeholder="全部周期"
          clearable
          filterable
          class="w-40!"
        >
          <el-option
            v-for="item in cycleOptions"
            :key="item"
            :label="item"
            :value="item"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="是否解决：" prop="resolved">
        <el-select
          v-model="filter.resolved"
          placeholder="全部"
          clearable
          class="w-28!"
        >
          <el-option label="是" value="是" />
          <el-option label="否" value="否" />
        </el-select>
      </el-form-item>
      <el-form-item label="主动发起：" prop="is_initiative">
        <el-select
          v-model="filter.is_initiative"
          placeholder="全部"
          clearable
          class="w-28!"
        >
          <el-option label="是" value="是" />
          <el-option label="否" value="否" />
        </el-select>
      </el-form-item>
      <el-form-item label="我方问题：" prop="is_our_problem">
        <el-select
          v-model="filter.is_our_problem"
          placeholder="全部"
          clearable
          class="w-28!"
        >
          <el-option label="是" value="是" />
          <el-option label="否" value="否" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button
          type="primary"
          :icon="useRenderIcon('ri/search-line')"
          :loading="loading"
          @click="onSearch"
        >
          搜索
        </el-button>
        <el-button :icon="useRenderIcon(Refresh)" @click="resetFilter(formRef)">
          重置
        </el-button>
      </el-form-item>
    </el-form>

    <!-- 图表统计（四件套） -->
    <el-card shadow="never" class="chart-card">
      <template #header>
        <div class="card-header">
          <span class="font-medium">图表统计</span>
          <span class="text-text_color_regular text-sm">
            共 {{ charts.total }} 条 · 跟随上方筛选条件
          </span>
        </div>
      </template>

      <el-row :gutter="16" v-loading="loading">
        <el-col :xs="24" :sm="12" :lg="12" class="mb-4">
          <div class="chart-title">
            地区分布<span class="chart-hint">点击扇区跳列表筛选</span>
          </div>
          <div ref="regionRef" class="chart-box chart-clickable" />
        </el-col>
        <el-col :xs="24" :sm="12" :lg="12" class="mb-4">
          <div class="chart-title">
            每日售后量<span class="chart-hint">点击柱子按发生日期筛选</span>
          </div>
          <div ref="dailyRef" class="chart-box chart-clickable" />
        </el-col>
        <el-col :xs="24" :sm="12" :lg="12" class="mb-4">
          <div class="chart-title">
            我方问题占比<span class="chart-hint">点击扇区跳列表筛选</span>
          </div>
          <div ref="ourRef" class="chart-box chart-clickable" />
        </el-col>
        <el-col :xs="24" :sm="12" :lg="12" class="mb-4">
          <div class="chart-title">
            问题类型分布<span class="chart-hint">点击条目跳列表筛选</span>
          </div>
          <div ref="issueRef" class="chart-box chart-clickable" />
        </el-col>
        <el-col :xs="24" :sm="12" :lg="12" class="mb-4">
          <div class="chart-title">
            未解决时长分布
            <span class="chart-hint">仅统计未解决记录 · 超期越久越需优先处理</span>
          </div>
          <div ref="agingRef" class="chart-box" />
        </el-col>
        <el-col :xs="24" :sm="12" :lg="12" class="mb-4">
          <div class="chart-title">
            球房售后排行 TOP10
            <span class="chart-hint">点击条目跳列表筛选该球房</span>
          </div>
          <div ref="roomTopRef" class="chart-box chart-clickable" />
        </el-col>
        <el-col :xs="24" :sm="12" :lg="12" class="mb-4">
          <div class="chart-title">
            球桌售后排行 TOP10
            <span class="chart-hint">球房·桌号联合统计 · 点击跳列表筛选</span>
          </div>
          <div ref="tableTopRef" class="chart-box chart-clickable" />
        </el-col>
      </el-row>
    </el-card>

    <!-- 自定义图表 -->
    <el-card shadow="never" class="chart-card">
      <template #header>
        <div class="card-header">
          <span class="font-medium">自定义图表</span>
          <span class="text-text_color_regular text-sm">
            自由组合维度与度量
          </span>
        </div>
      </template>

      <el-form :inline="true" :model="custom" class="mb-2">
        <el-form-item label="维度：">
          <el-select v-model="custom.dim" class="w-36!" @change="loadCustom">
            <el-option
              v-for="item in DIM_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="度量：">
          <el-select
            v-model="custom.measure"
            class="w-28!"
            @change="loadCustom"
          >
            <el-option
              v-for="item in MEASURE_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="图表：">
          <el-select v-model="custom.chart" class="w-28!" @change="loadCustom">
            <el-option
              v-for="item in CHART_TYPE_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :icon="useRenderIcon(TrendCharts)"
            :loading="custom.loading"
            @click="loadCustom"
          >
            查询
          </el-button>
        </el-form-item>
      </el-form>

      <div ref="customRef" class="chart-box chart-box--custom" />
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.main-content {
  margin: 24px 24px 0 !important;
}

.search-form {
  :deep(.el-form-item) {
    margin-bottom: 12px;
  }
}

.chart-card {
  margin-bottom: 12px;
}

.card-header {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.chart-title {
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin-bottom: 4px;
  padding-left: 2px;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.chart-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.chart-clickable {
  cursor: pointer;
}

.chart-box {
  width: 100%;
  height: 280px;
}

.chart-box--custom {
  height: 360px;
}

@media (max-width: 768px) {
  .chart-box {
    height: 240px;
  }

  .chart-box--custom {
    height: 300px;
  }
}
</style>
