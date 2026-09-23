<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from "vue";
import { useDark, useECharts } from "@pureadmin/utils";
import { useRenderIcon } from "@/components/ReIcon/src/hooks";
import {
  useAftersaleRank,
  RANGE_OPTIONS,
  LIMIT_OPTIONS,
  SORT_OPTIONS
} from "./utils/hook";
import { PALETTE } from "../stats/utils/hook";
import type { RankRow } from "@/api/aftersale";

import Refresh from "~icons/ep/refresh";
import Download from "~icons/ri/download-2-line";
import ArrowRight from "~icons/ri/arrow-right-s-line";

defineOptions({
  name: "AftersaleRank"
});

const formRef = ref();
const { isDark } = useDark();
const theme = computed(() => (isDark.value ? "dark" : "light"));

const {
  loading,
  filter,
  level,
  limit,
  sort,
  drillRoom,
  rows,
  summaryCards,
  cycleOptions,
  regions,
  yesNoOptions,
  onSearch,
  resetFilter,
  switchLevel,
  drillInto,
  drillOut,
  goListWith,
  exportCsv
} = useAftersaleRank();

/* ---------------- 排行横向条形图 ---------------- */
const rankRef = ref();
const { setOptions: setRank, getInstance: getRankInstance } = useECharts(
  rankRef,
  { theme, renderer: "svg" }
);

/** 渲染序（倒序=最大值在顶部）与 dataIndex 对齐的原始行，点击跳转用 */
let chartRows: RankRow[] = [];

const emptyOption = {
  title: {
    text: "暂无数据",
    left: "center",
    top: "center",
    textStyle: { color: "#909399", fontSize: 14, fontWeight: 400 }
  }
};

/** 名称过长时截断显示（tooltip 里保留全名） */
function shortName(n: string, max = 14) {
  return n.length > max ? n.slice(0, max - 1) + "…" : n;
}

function renderRank() {
  const data = [...rows.value].reverse();
  chartRows = data;
  if (!data.length) return setRank(emptyOption);
  setRank({
    color: [level.value === "room" ? PALETTE[2] : PALETTE[5]],
    grid: { left: 8, right: 44, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (ps: any) => {
        const p = ps?.[0];
        const row = chartRows[p?.dataIndex];
        if (!row) return "";
        return (
          `<b>${row.name}</b><br/>售后量：${row.total} 条（${row.share}%）` +
          `<br/>未解决：${row.unresolved} · 我方问题：${row.our_problem}` +
          `<br/>最近发生：${row.last_occurred || "-"}` +
          `<br/><span style="color:#909399">点击跳记录列表</span>`
        );
      }
    },
    xAxis: {
      type: "value",
      minInterval: 1,
      splitLine: { lineStyle: { type: "dashed" } },
      axisLabel: { fontSize: 11 }
    },
    yAxis: {
      type: "category",
      data: data.map(r => shortName(r.name)),
      axisLabel: { fontSize: 11 },
      axisTick: { show: false }
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 16,
        itemStyle: { borderRadius: [0, 3, 3, 0] },
        label: {
          show: true,
          position: "right",
          fontSize: 11,
          color: "#909399"
        },
        data: data.map(r => r.total)
      }
    ]
  });
}

function bindChartClick() {
  const inst = getRankInstance();
  inst?.off("click");
  inst?.on("click", (p: any) => {
    const row = chartRows[p?.dataIndex];
    if (row) goListWith(row);
  });
}

/** 图表高度随榜单行数自适应（TOP50/全部时不挤成一条缝） */
const chartHeight = computed(() =>
  Math.max(320, Math.min(rows.value.length * 26 + 70, 900))
);

async function renderAll() {
  await nextTick();
  renderRank();
  bindChartClick();
}

watch(rows, renderAll);
watch(theme, () => nextTick(renderAll));
let resizeTimer: ReturnType<typeof setTimeout> | null = null;
function onResize() {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderAll, 200);
}

onMounted(() => {
  window.addEventListener("resize", onResize);
});
onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  if (resizeTimer) clearTimeout(resizeTimer);
});

/* ---------------- 明细表 ---------------- */
const chartTitle = computed(() => {
  if (drillRoom.value) return `「${drillRoom.value}」内球桌排行`;
  return level.value === "room" ? "球房售后排行" : "球桌售后排行";
});

/** 前三名徽章配色（金/银/铜） */
const MEDALS = ["#b8860b", "#8a8a8a", "#a0522d"];

function rowClick(row: RankRow) {
  if (level.value === "room") drillInto(row);
  else goListWith(row);
}

/* el-table 插槽 row 是 DefaultRow（无业务类型），统一在这层收窄后再进 hook */
function onRowClick(row: any) {
  rowClick(row as RankRow);
}
function onDetail(row: any) {
  goListWith(row as RankRow);
}
function onDrill(row: any) {
  drillInto(row as RankRow);
}

function onTableSort(data: { prop: string; order: string | null }) {
  const { prop, order } = data;
  // el-table 客户端排序（仅影响表格显示；图表与后端序不变）
  if (!order) return;
  const dir = order === "ascending" ? 1 : -1;
  rows.value = [...rows.value].sort((a, b) => {
    const va = (a as any)[prop];
    const vb = (b as any)[prop];
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
    return String(va ?? "").localeCompare(String(vb ?? ""), "zh-CN") * dir;
  });
}
</script>

<template>
  <div class="main-content">
    <!-- ① 筛选栏 -->
    <el-form
      ref="formRef"
      :inline="true"
      :model="filter"
      class="search-form bg-bg_color w-full pl-8 pt-3 overflow-auto"
    >
      <el-form-item label="时间范围：">
        <el-radio-group
          v-model="filter.range_type"
          @change="onSearch"
        >
          <el-radio-button
            v-for="opt in RANGE_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="filter.range_type === 'cycle'" label="账期：">
        <el-select
          v-model="filter.cycle_start"
          placeholder="选择账期"
          filterable
          class="w-40!"
          @change="onSearch"
        >
          <el-option
            v-for="item in cycleOptions"
            :key="item"
            :label="item"
            :value="item"
          />
        </el-select>
      </el-form-item>
      <el-form-item v-if="filter.range_type === 'custom'" label="起止：">
        <el-date-picker
          v-model="filter.custom"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="—"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          class="w-60!"
          @change="onSearch"
        />
      </el-form-item>
      <el-form-item label="是否解决：">
        <el-select
          v-model="filter.resolved"
          placeholder="全部"
          clearable
          class="w-24!"
          @change="onSearch"
        >
          <el-option
            v-for="item in yesNoOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="我方问题：">
        <el-select
          v-model="filter.is_our_problem"
          placeholder="全部"
          clearable
          class="w-24!"
          @change="onSearch"
        >
          <el-option
            v-for="item in yesNoOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="主动发起：">
        <el-select
          v-model="filter.is_initiative"
          placeholder="全部"
          clearable
          class="w-24!"
          @change="onSearch"
        >
          <el-option
            v-for="item in yesNoOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="地区：">
        <el-select
          v-model="filter.region"
          placeholder="全部"
          clearable
          filterable
          class="w-28!"
          @change="onSearch"
        >
          <el-option v-for="item in regions" :key="item" :label="item" :value="item" />
        </el-select>
      </el-form-item>
      <el-form-item label="关键字：">
        <el-input
          v-model="filter.keyword"
          placeholder="球房/桌号/问题"
          clearable
          class="w-40!"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
      </el-form-item>
      <el-form-item>
        <el-button
          type="primary"
          :icon="useRenderIcon('ri/search-line')"
          :loading="loading"
          @click="onSearch"
        >
          查询
        </el-button>
        <el-button :icon="useRenderIcon(Refresh)" @click="resetFilter(formRef)">
          重置
        </el-button>
        <el-button :icon="useRenderIcon(Download)" @click="exportCsv">
          导出 CSV
        </el-button>
      </el-form-item>
    </el-form>

    <!-- ② 概览指标 -->
    <div class="summary-row" v-loading="loading">
      <div v-for="card in summaryCards" :key="card.key" class="summary-card">
        <div class="summary-card__label">{{ card.label }}</div>
        <div
          class="summary-card__value"
          :class="{ 'text-danger': (card as any).danger }"
        >
          {{ card.value }}
        </div>
      </div>
      <div class="summary-hint">跟随上方筛选口径 · 占比按记录总数计算</div>
    </div>

    <!-- ③④ 两级排行：左图右表 -->
    <el-card shadow="never" class="rank-card" v-loading="loading">
      <template #header>
        <div class="rank-toolbar">
          <el-radio-group
            :model-value="level"
            @update:model-value="switchLevel($event as 'room' | 'table')"
          >
            <el-radio-button value="room">球房排行</el-radio-button>
            <el-radio-button value="table">球桌排行</el-radio-button>
          </el-radio-group>
          <el-select v-model="limit" class="w-28!" @change="onSearch">
            <el-option
              v-for="opt in LIMIT_OPTIONS"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <span class="toolbar-label">排序</span>
          <el-select v-model="sort" class="w-30!" @change="onSearch">
            <el-option
              v-for="opt in SORT_OPTIONS"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <div class="crumb">
            <span>排行范围：</span>
            <el-link
              v-if="drillRoom"
              type="primary"
              :underline="false"
              @click="drillOut"
            >
              全部球房
            </el-link>
            <template v-if="drillRoom">
              <component :is="useRenderIcon(ArrowRight)" class="crumb-sep" />
              <b>{{ drillRoom }}</b>
            </template>
            <b v-else>全部球房</b>
          </div>
        </div>
      </template>

      <el-row :gutter="16">
        <el-col :xs="24" :lg="10">
          <div class="chart-title">
            {{ chartTitle }}
            <span class="chart-hint">点击条目跳记录列表筛选</span>
          </div>
          <div
            ref="rankRef"
            class="chart-box chart-clickable"
            :style="{ height: chartHeight + 'px' }"
          />
        </el-col>
        <el-col :xs="24" :lg="14">
          <div class="chart-title">
            明细排行
            <span class="chart-hint">
              {{
                level === "room"
                  ? "点球房行下钻该球房桌号榜"
                  : "点行跳记录列表 · 列头可排序"
              }}
            </span>
          </div>
          <el-table
            :data="rows"
            size="small"
            :max-height="chartHeight"
            class="rank-table"
            :header-cell-style="{
              background: 'var(--el-fill-color-light)',
              color: 'var(--el-text-color-primary)'
            }"
            @row-click="onRowClick"
            @sort-change="onTableSort"
          >
            <el-table-column label="排名" width="64" align="center">
              <template #default="{ row }">
                <span
                  v-if="row.rank <= 3"
                  class="medal"
                  :style="{
                    color: MEDALS[row.rank - 1],
                    background: MEDALS[row.rank - 1] + '1f'
                  }"
                >
                  {{ row.rank }}
                </span>
                <span v-else class="rank-num">{{ row.rank }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="level === 'room' ? '球房' : '球桌'"
              prop="name"
              min-width="180"
              show-overflow-tooltip
            />
            <el-table-column label="总量" prop="total" width="80" sortable>
              <template #default="{ row }">
                <b>{{ row.total }}</b>
              </template>
            </el-table-column>
            <el-table-column label="占比" prop="share" width="80" sortable>
              <template #default="{ row }">{{ row.share }}%</template>
            </el-table-column>
            <el-table-column
              label="未解决"
              prop="unresolved"
              width="84"
              sortable
            >
              <template #default="{ row }">
                <span v-if="row.unresolved" class="text-danger">
                  {{ row.unresolved }}
                </span>
                <span v-else class="text-muted">0</span>
              </template>
            </el-table-column>
            <el-table-column
              label="我方问题"
              prop="our_problem"
              width="96"
              sortable
            />
            <el-table-column label="最近发生" prop="last_occurred" width="106" sortable />
            <el-table-column label="操作" width="128" align="center">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click.stop="onDetail(row)">
                  明细
                </el-button>
                <el-button
                  v-if="level === 'room'"
                  link
                  type="info"
                  size="small"
                  @click.stop="onDrill(row)"
                >
                  下钻
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-col>
      </el-row>
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

.summary-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr)) minmax(0, 2fr);
  gap: 12px;
  margin-bottom: 12px;
  align-items: stretch;
}

.summary-card {
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 12px 16px;
}

.summary-card__label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.summary-card__value {
  font-size: 24px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--el-text-color-primary);
}

.summary-hint {
  display: flex;
  align-items: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 0 4px;
}

.text-danger {
  color: var(--el-color-danger);
}

.text-muted {
  color: var(--el-text-color-placeholder);
}

.rank-card {
  margin-bottom: 12px;
}

.rank-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.toolbar-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.crumb {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--el-text-color-secondary);

  b {
    color: var(--el-text-color-primary);
    font-weight: 500;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.crumb-sep {
  font-size: 14px;
}

.chart-title {
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin-bottom: 6px;
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
}

.rank-table {
  :deep(.el-table__row) {
    cursor: pointer;
  }
}

.medal {
  display: inline-flex;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
}

.rank-num {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

@media (max-width: 1200px) {
  .summary-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .summary-hint {
    grid-column: span 2;
  }
}

@media (max-width: 768px) {
  .summary-row {
    grid-template-columns: minmax(0, 1fr);
  }

  .summary-hint {
    grid-column: span 1;
  }
}
</style>
