<script setup lang="ts">
import { ref, reactive, watch } from "vue";
import { message } from "@/utils/message";
import { getAuditLog } from "@/api/aftersale";
import type { AuditLogRow } from "@/api/aftersale";

/**
 * 操作审计日志：谁在何时对哪条记录做了什么（create/update/delete/
 * batch_resolve/batch_delete/restore/purge/import）
 */
defineOptions({ name: "AftersaleAuditDialog" });

const visible = defineModel<boolean>({ default: false });

const loading = ref(false);
const rows = ref<AuditLogRow[]>([]);
const total = ref(0);
const action = ref("");
const pagination = reactive({ page: 1, pageSize: 20 });

const ACTION_LABELS: Record<string, string> = {
  create: "新增",
  update: "修改",
  delete: "删除",
  batch_resolve: "批量解决",
  batch_delete: "批量删除",
  restore: "回收站恢复",
  purge: "彻底删除",
  import: "Excel导入"
};

const ACTION_OPTIONS = Object.entries(ACTION_LABELS).map(([value, label]) => ({
  label,
  value
}));

function actionLabel(a: string) {
  return ACTION_LABELS[a] || a;
}

async function load() {
  loading.value = true;
  try {
    const res = await getAuditLog({
      page: pagination.page,
      page_size: pagination.pageSize,
      action: action.value || undefined
    });
    rows.value = res.rows ?? [];
    total.value = res.total ?? 0;
  } catch (err) {
    console.error("[aftersale] audit load failed:", err);
    message("审计日志加载失败", { type: "error" });
  } finally {
    loading.value = false;
  }
}

watch(visible, v => {
  if (v) {
    pagination.page = 1;
    action.value = "";
    load();
  }
});
</script>

<template>
  <el-dialog
    v-model="visible"
    title="操作日志"
    width="780px"
    destroy-on-close
  >
    <div class="audit-bar">
      <el-select
        v-model="action"
        placeholder="全部操作"
        clearable
        class="w-40!"
        @change="pagination.page = 1; load()"
      >
        <el-option
          v-for="o in ACTION_OPTIONS"
          :key="o.value"
          :label="o.label"
          :value="o.value"
        />
      </el-select>
      <span class="audit-tip">共 {{ total }} 条 · 保留最近写操作</span>
    </div>

    <el-table v-loading="loading" :data="rows" height="420" size="small">
      <el-table-column prop="id" label="#" width="70" />
      <el-table-column prop="ts" label="时间" width="160" />
      <el-table-column prop="user" label="操作人" width="100" />
      <el-table-column label="操作" width="110">
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="
              ['delete', 'batch_delete', 'purge'].includes(row.action)
                ? 'danger'
                : 'info'
            "
            effect="plain"
          >
            {{ actionLabel(row.action) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="record_id" label="记录ID" width="90">
        <template #default="{ row }">{{ row.record_id ?? "-" }}</template>
      </el-table-column>
      <el-table-column
        prop="detail"
        label="明细"
        min-width="200"
        show-overflow-tooltip
      />
    </el-table>

    <el-pagination
      v-model:current-page="pagination.page"
      class="mt-3"
      layout="total, prev, pager, next"
      :total="total"
      :page-size="pagination.pageSize"
      @current-change="load"
    />
  </el-dialog>
</template>

<style scoped>
.audit-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.audit-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: auto;
}
</style>
