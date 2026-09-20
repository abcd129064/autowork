<script setup lang="tsx">
import dayjs from "dayjs";
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from "vue";
import { useRouter } from "vue-router";
import ReCol from "@/components/ReCol";
import { useDark, useECharts } from "@pureadmin/utils";
import {
  getRecords,
  getCharts,
  getHealth,
  isYes
} from "@/api/aftersale";
import type { AftersaleRecord, AftersaleStats } from "@/api/aftersale";
import { PALETTE } from "@/views/aftersale/stats/utils/hook";
import { message } from "@/utils/message";

import FileListIcon from "~icons/ri/file-list-3-line";
import ErrorIcon from "~icons/ri/error-warning-line";
import ThumbIcon from "~icons/ri/thumb-up-line";
import UserWarnIcon from "~icons/ri/alert-line";

defineOptions({
  name: "Welcome"
});

const router = useRouter();

/** ===== KPI ===== */
const stats = ref<AftersaleStats>({
  total: 0,
  unresolved: 0,
  initiative: 0,
  our_problem: 0
});
const statCards = computed(() => [
  {
    label: "记录总数",
    value: stats.value.total,
    icon: FileListIcon,
    color: "#409eff",
    query: {} as Record<string, string>
  },
  {
    label: "未解决",
    value: stats.value.unresolved,
    icon: ErrorIcon,
    color: "#f56c6c",
    query: { resolved: "否" }
  },
  {
    label: "主动发起",
    value: stats.value.initiative,
    icon: ThumbIcon,
    color: "#67c23a",
    query: { is_initiative: "是" }
  },
  {
    label: "我方问题",
    value: stats.value.our_problem,
    icon: UserWarnIcon,
    color: "#e6a23c",
    query: { is_our_problem: "是" }
  }
]);

/** ===== 图表 ===== */
const theme = computed(() => (isDark.value ? "dark" : "light"));
const dailyRef = ref();
const typeRef = ref();
const {
  setOptions: setDaily,
  getInstance: getDailyInstance
} = useECharts(dailyRef, { theme, renderer: "svg" });
const {
  setOptions: setType,
  getInstance: getTypeInstance
} = useECharts(typeRef, { theme, renderer: "svg" });

/** 图表点击 → 跳列表筛选（每日点击按发生日期，类型点击按问题类型） */
let dailyDates: string[] = [];
let chartClickBound = false;
function bindChartClick() {
  if (chartClickBound) return;
  const dailyChart = getDailyInstance();
  if (dailyChart) {
    dailyChart.on("click", params => {
      if (params.componentType !== "series") return;
      const date = dailyDates[params.dataIndex];
      if (date) goListWith({ occurred_at: date });
    });
    chartClickBound = true;
  }
  const typeChart = getTypeInstance();
  if (typeChart) {
    typeChart.on("click", params => {
      if (params.componentType === "series" && params.name) {
        goListWith({ issue_type: String(params.name) });
      }
    });
  }
}

const emptyOption = {
  title: {
    text: "暂无数据",
    left: "center",
    top: "center",
    textStyle: { color: "#909399", fontSize: 14, fontWeight: "normal" }
  }
};

function renderDaily(daily: Array<{ date: string; count: number }>) {
  if (!daily?.length) return setDaily(emptyOption);
  dailyDates = daily.map(d => d.date); // 供点击跳转取完整日期
  const step = Math.max(1, Math.ceil(daily.length / 12));
  setDaily({
    tooltip: { trigger: "axis" },
    grid: { left: 40, right: 20, top: 30, bottom: 30 },
    xAxis: {
      type: "category",
      data: daily.map(d => d.date.slice(5)),
      axisLabel: { interval: daily.length > 12 ? step - 1 : 0 }
    },
    yAxis: { type: "value", minInterval: 1 },
    series: [
      {
        name: "售后量",
        type: "bar",
        data: daily.map(d => d.count),
        itemStyle: { color: PALETTE[0], borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 22
      }
    ]
  });
}

function renderType(list: Array<{ name: string; value: number }>) {
  if (!list?.length) return setType(emptyOption);
  // 横向条形图反转后最大值在顶部
  const sorted = [...list].sort((a, b) => a.value - b.value);
  setType({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 90, right: 40, top: 10, bottom: 24 },
    xAxis: { type: "value", minInterval: 1 },
    yAxis: {
      type: "category",
      data: sorted.map(d => d.name || "未填"),
      axisLabel: { width: 76, overflow: "truncate" }
    },
    series: [
      {
        name: "数量",
        type: "bar",
        data: sorted.map(d => d.value),
        itemStyle: { color: PALETTE[3], borderRadius: [0, 3, 3, 0] },
        barMaxWidth: 14,
        label: { show: true, position: "right" }
      }
    ]
  });
}

/** ===== 最近记录 ===== */
const loading = ref(true);
const recent = ref<AftersaleRecord[]>([]);

const recentColumns: TableColumnList = [
  { label: "发生时间", prop: "occurred_at", width: 100 },
  {
    label: "类型",
    prop: "issue_type",
    width: 96,
    cellRenderer: ({ row }) =>
      row.issue_type ? (
        <el-tag size="small" type="info" effect="plain">
          {row.issue_type}
        </el-tag>
      ) : (
        <span>-</span>
      )
  },
  { label: "地区", prop: "region", width: 80 },
  { label: "门店", prop: "room_name", minWidth: 130, showOverflowTooltip: true },
  { label: "问题", prop: "problem", minWidth: 180, showOverflowTooltip: true },
  {
    label: "解决",
    prop: "resolved",
    width: 78,
    align: "center",
    cellRenderer: ({ row }) => (
      <el-tag
        size="small"
        type={isYes(row.resolved) ? "success" : "danger"}
        effect="plain"
      >
        {isYes(row.resolved) ? "已解决" : "未解决"}
      </el-tag>
    )
  }
];

/** ===== 数据源状态 ===== */
const dbStatus = ref<{ ok: boolean; db: string } | null>(null);

async function loadAll() {
  loading.value = true;
  try {
    // 最近记录 + 全口径统计（一次请求两用）
    const rec = await getRecords({ page: 1, page_size: 10 });
    recent.value = rec.rows ?? [];
    if (rec.stats) stats.value = rec.stats;
    // 90 天趋势 + 分布（不带账期时后端默认统计最近 90 天）
    const charts = await getCharts({});
    renderDaily(charts.daily ?? []);
    renderType(charts.issue_type_dist ?? []);
    // 数据源状态，失败静默
    getHealth()
      .then(h => (dbStatus.value = h))
      .catch(() => (dbStatus.value = null));
    // 图表实例就绪后绑定点击跳转（幂等）
    await nextTick();
    bindChartClick();
  } catch (err) {
    message("总览数据加载失败，请检查后端服务", { type: "error" });
    console.error("[welcome] loadAll failed:", err);
  } finally {
    loading.value = false;
  }
}

function goList() {
  router.push("/aftersale/list");
}
/** 带筛选跳列表（KPI 卡/图表点击） */
function goListWith(query?: Record<string, string>) {
  if (!query || Object.keys(query).length === 0) {
    router.push("/aftersale/list");
    return;
  }
  router.push({ path: "/aftersale/list", query });
}
function goStats() {
  router.push("/aftersale/stats");
}

/** 主题切换重绘 */
watch(theme, () => nextTick(loadChartsOnly));
async function loadChartsOnly() {
  try {
    const charts = await getCharts({});
    renderDaily(charts.daily ?? []);
    renderType(charts.issue_type_dist ?? []);
  } catch {
    /* 主题切换时的重绘失败不打扰用户 */
  }
}

/** resize 防抖重绘 */
let resizeTimer: number | undefined;
function onResize() {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    loadChartsOnly();
  }, 200);
}

onMounted(async () => {
  await nextTick();
  loadAll();
  window.addEventListener("resize", onResize);
});
onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  if (resizeTimer) clearTimeout(resizeTimer);
});
</script>

<template>
  <div>
    <!-- KPI 总览 -->
    <el-row :gutter="16">
      <re-col
        v-for="(card, index) in statCards"
        :key="card.label"
        class="mb-4"
        :value="6"
        :md="12"
        :sm="12"
        :xs="24"
      >
        <el-card
          shadow="never"
          class="kpi-card kpi-clickable"
          :title="`点击查看${card.label}明细`"
          @click="goListWith(card.query)"
        >
          <div class="kpi-body">
            <div class="kpi-icon" :style="{ color: card.color }">
              <IconifyIconOffline :icon="card.icon" width="20" height="20" />
            </div>
            <div class="kpi-meta">
              <div class="kpi-value">{{ card.value }}</div>
              <div class="kpi-label">{{ card.label }}</div>
            </div>
          </div>
        </el-card>
      </re-col>
    </el-row>

    <el-row :gutter="16">
      <!-- 90 天趋势 -->
      <re-col class="mb-4" :value="16" :lg="16" :md="24" :sm="24" :xs="24">
        <el-card shadow="never">
          <div class="flex justify-between items-center">
            <span class="card-title">近 90 天售后量</span>
            <div class="flex items-center gap-2">
              <span class="chart-hint">点击某天可筛选当天记录</span>
              <el-button link type="primary" @click="goStats">
                查看看板
              </el-button>
            </div>
          </div>
          <div ref="dailyRef" class="chart-box chart-clickable" />
        </el-card>
      </re-col>

      <!-- 类型分布 -->
      <re-col class="mb-4" :value="8" :lg="8" :md="24" :sm="24" :xs="24">
        <el-card shadow="never">
          <div class="flex justify-between items-center">
            <span class="card-title">问题类型分布</span>
            <span class="chart-hint">点击类型可筛选该类记录</span>
          </div>
          <div ref="typeRef" class="chart-box chart-clickable" />
        </el-card>
      </re-col>
    </el-row>

    <el-row :gutter="16">
      <!-- 最近记录 -->
      <re-col class="mb-4" :value="16" :lg="16" :md="24" :sm="24" :xs="24">
        <el-card shadow="never">
          <div class="flex justify-between items-center">
            <span class="card-title">最近记录</span>
            <el-button link type="primary" @click="goList">
              查看全部
            </el-button>
          </div>
          <pure-table
            row-key="id"
            table-layout="auto"
            :loading="loading"
            :data="recent"
            :columns="recentColumns"
            :pagination="undefined"
            :header-cell-style="{
              background: 'var(--el-fill-color-light)',
              color: 'var(--el-text-color-primary)'
            }"
          />
        </el-card>
      </re-col>

      <!-- 快捷入口 / 系统状态 -->
      <re-col class="mb-4" :value="8" :lg="8" :md="24" :sm="24" :xs="24">
        <el-card shadow="never" class="mb-4">
          <div class="card-title">快捷入口</div>
          <div class="quick-grid">
            <el-button type="primary" plain @click="goList">
              售后记录查询
            </el-button>
            <el-button type="success" plain @click="goStats">
              数据看板
            </el-button>
          </div>
        </el-card>
        <el-card shadow="never">
          <div class="card-title">系统状态</div>
          <div class="status-row">
            <span class="status-label">数据源</span>
            <el-tag
              :type="dbStatus?.ok ? 'success' : 'info'"
              effect="plain"
              size="small"
            >
              {{
                dbStatus?.ok ? `正常 · ${dbStatus.db}` : loading ? "检测中" : "不可用"
              }}
            </el-tag>
          </div>
          <div class="status-row">
            <span class="status-label">当前版本</span>
            <span class="status-value">v2（vue-pure-admin）</span>
          </div>
          <div class="status-row">
            <span class="status-label">老版入口</span>
            <a href="/v1/" target="_blank" class="status-link">/v1/</a>
          </div>
        </el-card>
      </re-col>
    </el-row>
  </div>
</template>

<style lang="scss" scoped>
.kpi-card {
  :deep(.el-card__body) {
    padding: 16px 18px;
  }
}

/* 图表/KPI 点击跳转：手型光标 + 悬停反馈 */
.kpi-clickable {
  cursor: pointer;
  transition: box-shadow 0.2s, transform 0.2s;

  &:hover {
    box-shadow: var(--el-box-shadow-light);
    transform: translateY(-2px);
  }
}

.chart-clickable {
  cursor: pointer;
}

.chart-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.kpi-body {
  display: flex;
  align-items: center;
  gap: 12px;
}

.kpi-icon {
  width: 42px;
  height: 42px;
  flex: none;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, currentColor 12%, transparent);
}

.kpi-value {
  font-size: 24px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--el-text-color-primary);
}

.kpi-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.chart-box {
  width: 100%;
  height: 300px;
  margin-top: 8px;
}

.quick-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 12px;

  .el-button {
    margin-left: 0;
  }
}

.status-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 0;

  & + .status-row {
    border-top: 1px dashed var(--el-border-color-lighter);
  }
}

.status-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.status-value {
  font-size: 13px;
  color: var(--el-text-color-primary);
}

.status-link {
  font-size: 13px;
  color: var(--el-color-primary);
  text-decoration: none;
}
</style>
