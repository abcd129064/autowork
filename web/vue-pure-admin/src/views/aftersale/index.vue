<script setup lang="ts">
import { ref, onBeforeUnmount } from "vue";
import { useAftersale } from "./utils/hook";
import { PureTableBar } from "@/components/RePureTableBar";
import { useRenderIcon } from "@/components/ReIcon/src/hooks";
import DetailDrawer from "./components/DetailDrawer.vue";
import RecycleDialog from "./components/RecycleDialog.vue";
import AuditDialog from "./components/AuditDialog.vue";
import ImportDialog from "./components/ImportDialog.vue";

import Refresh from "~icons/ep/refresh";
import Check from "~icons/ep/check";
import Delete from "~icons/ep/delete";
import AddFill from "~icons/ri/add-circle-line";
// ⚠️ `~icons/ep/edit-pen` 构建产物里没有对应 SVG（按钮会渲染成空白），
// 用 `~icons/ri/edit-line` 代替
import EditPen from "~icons/ri/edit-line";
import Eye from "~icons/ri/eye-line";
import Upload from "~icons/ri/upload-2-line";
import Download from "~icons/ri/download-2-line";
import RecycleBin from "~icons/ri/delete-bin-line";
import History from "~icons/ri/history-line";

defineOptions({
  name: "AftersaleList"
});

const formRef = ref();
const tableRef = ref();

const {
  form,
  loading,
  columns,
  dataList,
  statCards,
  pagination,
  cycleOptions,
  issueTypes,
  regions,
  yesNoOptions,
  selectedNum,
  detailRow,
  onSearch,
  resetForm,
  openDialog,
  openDetail,
  onExport,
  rowClassName,
  handleSortChange,
  handleDelete,
  onBatchResolve,
  onBatchDelete,
  onSelectionCancel,
  handleSizeChange,
  handleCurrentChange,
  handleSelectionChange
} = useAftersale(tableRef);

/** 清除「发生日期」筛选（总览页图表点击跳转带入） */
function clearOccurred() {
  form.occurred_at = "";
  onSearch();
}

/** 清除「球房/球桌」筛选（统计页排行榜点击跳转带入） */
function clearRoomTable() {
  form.room_name = "";
  form.table_no = "";
  onSearch();
}

// ---- 工具弹窗 ----
const detailVisible = ref(false);
const recycleVisible = ref(false);
const auditVisible = ref(false);
const importVisible = ref(false);

function showDetail(row) {
  openDetail(row);
  detailVisible.value = true;
}

// ---- 自动刷新（默认关；开关状态持久化 localStorage）----
const AUTO_KEY = "aftersale-auto-refresh";
const AUTO_INTERVAL = 60 * 1000;
const autoRefresh = ref(localStorage.getItem(AUTO_KEY) === "1");
let autoTimer: ReturnType<typeof setInterval> | null = null;

function stopAutoTimer() {
  if (autoTimer) {
    clearInterval(autoTimer);
    autoTimer = null;
  }
}

function onAutoRefreshChange(v: boolean) {
  localStorage.setItem(AUTO_KEY, v ? "1" : "0");
  stopAutoTimer();
  if (v) autoTimer = setInterval(() => onSearch(false), AUTO_INTERVAL);
}

// 记住上次的开关状态：进入页面即恢复
if (autoRefresh.value) onAutoRefreshChange(true);
onBeforeUnmount(stopAutoTimer);
</script>

<template>
  <div class="main-content">
    <!-- KPI 统计卡 -->
    <div class="stats-row">
      <el-card
        v-for="card in statCards"
        :key="card.key"
        shadow="never"
        class="stat-card"
      >
        <div class="stat-card__body">
          <div class="stat-card__icon" :style="{ color: card.color }">
            <component :is="useRenderIcon(card.icon)" />
          </div>
          <div class="stat-card__meta">
            <div class="stat-card__value">{{ card.value }}</div>
            <div class="stat-card__label">{{ card.label }}</div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 筛选栏 -->
    <el-form
      ref="formRef"
      :inline="true"
      :model="form"
      class="search-form bg-bg_color w-full pl-8 pt-3 overflow-auto"
    >
      <el-form-item label="关键词：" prop="keyword">
        <el-input
          v-model="form.keyword"
          placeholder="问题/门店/球桌/处理人"
          clearable
          class="w-45!"
          @keyup.enter="onSearch()"
        />
      </el-form-item>
      <el-form-item v-if="form.occurred_at" label="发生日期：">
        <el-tag closable type="primary" @close="clearOccurred">
          {{ form.occurred_at }}
        </el-tag>
      </el-form-item>
      <!-- 排行榜点击跳转带入的球房/球桌筛选（可单独关闭回退到不筛） -->
      <el-form-item v-if="form.room_name || form.table_no" label="球房/球桌：">
        <el-tag closable type="warning" @close="clearRoomTable">
          {{ form.room_name }}{{ form.table_no ? `·${form.table_no}` : "" }}
        </el-tag>
      </el-form-item>
      <el-form-item label="账期：" prop="cycle_start">
        <el-select
          v-model="form.cycle_start"
          placeholder="全部账期"
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
      <el-form-item label="问题类型：" prop="issue_type">
        <el-select
          v-model="form.issue_type"
          placeholder="全部类型"
          clearable
          filterable
          class="w-36!"
        >
          <el-option
            v-for="item in issueTypes"
            :key="item"
            :label="item"
            :value="item"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="地区：" prop="region">
        <el-select
          v-model="form.region"
          placeholder="全部地区"
          clearable
          filterable
          class="w-32!"
        >
          <el-option
            v-for="item in regions"
            :key="item"
            :label="item"
            :value="item"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="是否解决：" prop="resolved">
        <el-select
          v-model="form.resolved"
          placeholder="全部"
          clearable
          class="w-28!"
        >
          <el-option
            v-for="item in yesNoOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="我方问题：" prop="is_our_problem">
        <el-select
          v-model="form.is_our_problem"
          placeholder="全部"
          clearable
          class="w-28!"
        >
          <el-option
            v-for="item in yesNoOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="主动发起：" prop="is_initiative">
        <el-select
          v-model="form.is_initiative"
          placeholder="全部"
          clearable
          class="w-28!"
        >
          <el-option
            v-for="item in yesNoOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button
          type="primary"
          :icon="useRenderIcon('ri/search-line')"
          :loading="loading"
          @click="onSearch()"
        >
          搜索
        </el-button>
        <el-button :icon="useRenderIcon(Refresh)" @click="resetForm(formRef)">
          重置
        </el-button>
      </el-form-item>
    </el-form>

    <PureTableBar title="售后记录查询" :columns="columns" @refresh="onSearch()">
      <template #buttons>
        <el-button
          type="primary"
          :icon="useRenderIcon(AddFill)"
          @click="openDialog('新增')"
        >
          新增
        </el-button>
        <el-button :icon="useRenderIcon(Upload)" @click="importVisible = true">
          导入
        </el-button>
        <el-button
          :icon="useRenderIcon(Download)"
          :loading="loading"
          @click="onExport"
        >
          导出
        </el-button>
        <el-button :icon="useRenderIcon(RecycleBin)" @click="recycleVisible = true">
          回收站
        </el-button>
        <el-button :icon="useRenderIcon(History)" @click="auditVisible = true">
          操作日志
        </el-button>
        <el-tooltip content="开启后每 60 秒自动刷新当前列表" placement="top">
          <span class="auto-refresh">
            自动刷新
            <el-switch
              v-model="autoRefresh"
              size="small"
              @change="onAutoRefreshChange"
            />
          </span>
        </el-tooltip>
      </template>
      <template v-slot="{ size, dynamicColumns }">
        <div
          v-if="selectedNum > 0"
          v-motion-fade
          class="bg-(--el-fill-color-light) w-full h-11.5 mb-2 pl-4 flex items-center"
        >
          <div class="flex-auto">
            <span
              style="font-size: var(--el-font-size-base)"
              class="text-[rgba(42,46,54,0.5)] dark:text-[rgba(220,220,242,0.5)]"
            >
              已选 {{ selectedNum }} 项
            </span>
            <el-button type="primary" text @click="onSelectionCancel">
              取消选择
            </el-button>
          </div>
          <el-button
            type="success"
            text
            :icon="useRenderIcon(Check)"
            @click="onBatchResolve"
          >
            批量标记已解决
          </el-button>
          <el-button
            type="danger"
            text
            :icon="useRenderIcon(Delete)"
            @click="onBatchDelete"
          >
            批量删除
          </el-button>
        </div>
        <pure-table
          ref="tableRef"
          row-key="id"
          adaptive
          :adaptiveConfig="{ offsetBottom: 108 }"
          table-layout="auto"
          :loading="loading"
          :size="size"
          :data="dataList"
          :columns="dynamicColumns"
          :pagination="{ ...pagination, size }"
          :row-class-name="rowClassName"
          :header-cell-style="{
            background: 'var(--el-fill-color-light)',
            color: 'var(--el-text-color-primary)'
          }"
          @selection-change="handleSelectionChange"
          @page-size-change="handleSizeChange"
          @page-current-change="handleCurrentChange"
          @sort-change="handleSortChange"
        >
          <template #operation="{ row }">
            <el-button
              class="reset-margin"
              link
              type="info"
              :size="size"
              :icon="useRenderIcon(Eye)"
              @click="showDetail(row)"
            >
              详情
            </el-button>
            <el-button
              class="reset-margin"
              link
              type="primary"
              :size="size"
              :icon="useRenderIcon(EditPen)"
              @click="openDialog('编辑', row)"
            >
              编辑
            </el-button>
            <el-popconfirm
              :title="`是否确认删除编号为 ${row.id} 的售后记录`"
              @confirm="handleDelete(row)"
            >
              <template #reference>
                <el-button
                  class="reset-margin"
                  link
                  type="danger"
                  :size="size"
                  :icon="useRenderIcon(Delete)"
                >
                  删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </pure-table>
      </template>
    </PureTableBar>

    <!-- 详情抽屉 / 工具弹窗 -->
    <DetailDrawer v-model="detailVisible" :row="detailRow" />
    <RecycleDialog v-model="recycleVisible" @restored="onSearch(false)" />
    <AuditDialog v-model="auditVisible" />
    <ImportDialog v-model="importVisible" @imported="onSearch(false)" />
  </div>
</template>

<style lang="scss" scoped>
.main-content {
  margin: 24px 24px 0 !important;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.stat-card {
  :deep(.el-card__body) {
    padding: 14px 16px;
  }
}

.stat-card__body {
  display: flex;
  align-items: center;
  gap: 12px;
}

.stat-card__icon {
  width: 40px;
  height: 40px;
  flex: none;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  // 用 color-mix 由图标主色推导淡色底
  background: color-mix(in srgb, currentColor 12%, transparent);
}

.stat-card__value {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--el-text-color-primary);
}

.stat-card__label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.search-form {
  :deep(.el-form-item) {
    margin-bottom: 12px;
  }
}

.auto-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin-left: 8px;
  cursor: default;
}

/* 未解决超 7 天的记录整行淡红底色（减损视角：久拖未决优先处理） */
:deep(.pure-table .el-table__row.overdue-row),
:deep(.el-table__row.overdue-row) {
  --el-table-tr-bg-color: var(--el-color-danger-light-9);
}
:deep(.el-table__row.overdue-row td.el-table__cell) {
  background: var(--el-color-danger-light-9) !important;
}

@media (max-width: 1200px) {
  .stats-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .stats-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
