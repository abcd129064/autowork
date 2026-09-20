<script setup lang="ts">
import { ref, reactive, watch } from "vue";
import { message } from "@/utils/message";
import { ElMessageBox } from "element-plus";
import {
  getRecycleRecords,
  restoreRecords,
  purgeRecords
} from "@/api/aftersale";
import type { AftersaleRecord } from "@/api/aftersale";

/**
 * 回收站（软删除记录）：恢复 / 彻底删除。
 * 删除列表页的单删/批删均为软删（deleted=1），在此可找回；
 * 「彻底删除」为硬删不可恢复，需二次确认。
 */
defineOptions({ name: "AftersaleRecycleDialog" });

const visible = defineModel<boolean>({ default: false });
const emit = defineEmits<{ restored: [] }>();

const loading = ref(false);
const rows = ref<AftersaleRecord[]>([]);
const total = ref(0);
const keyword = ref("");
const selected = ref<AftersaleRecord[]>([]);
const pagination = reactive({ page: 1, pageSize: 20 });

async function load() {
  loading.value = true;
  try {
    const res = await getRecycleRecords({
      page: pagination.page,
      page_size: pagination.pageSize,
      keyword: keyword.value || undefined
    });
    rows.value = res.rows ?? [];
    total.value = res.total ?? 0;
  } catch (err) {
    console.error("[aftersale] recycle load failed:", err);
    message("回收站加载失败", { type: "error" });
  } finally {
    loading.value = false;
  }
}

watch(visible, v => {
  if (v) {
    pagination.page = 1;
    keyword.value = "";
    load();
  }
});

async function onRestore() {
  const ids = selected.value.map(r => r.id);
  if (!ids.length) {
    message("请先勾选要恢复的记录", { type: "warning" });
    return;
  }
  try {
    const res = await restoreRecords(ids);
    message(`已恢复 ${res.restored} 条记录`, { type: "success" });
    selected.value = [];
    emit("restored");
    load();
  } catch (err) {
    message("恢复失败", { type: "error" });
    console.error("[aftersale] restore failed:", err);
  }
}

async function onPurge() {
  const ids = selected.value.map(r => r.id);
  if (!ids.length) {
    message("请先勾选要彻底删除的记录", { type: "warning" });
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确认彻底删除选中的 ${ids.length} 条记录？该操作不可恢复！`,
      "危险操作",
      { confirmButtonText: "彻底删除", cancelButtonText: "取消", type: "error" }
    );
  } catch {
    return;
  }
  try {
    const res = await purgeRecords(ids);
    message(`已彻底删除 ${res.purged} 条记录`, { type: "success" });
    selected.value = [];
    load();
  } catch (err) {
    message("删除失败", { type: "error" });
    console.error("[aftersale] purge failed:", err);
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="回收站"
    width="860px"
    destroy-on-close
  >
    <div class="recycle-bar">
      <el-input
        v-model="keyword"
        placeholder="搜索 门店/球桌/问题/填写人"
        clearable
        class="w-55!"
        @keyup.enter="pagination.page = 1; load()"
      />
      <el-button
        type="success"
        :disabled="!selected.length"
        @click="onRestore"
      >
        恢复选中
      </el-button>
      <el-button type="danger" :disabled="!selected.length" @click="onPurge">
        彻底删除
      </el-button>
      <span class="recycle-tip">
        共 {{ total }} 条 · 已删除记录保留在回收站，桌面端不再显示
      </span>
    </div>

    <el-table
      v-loading="loading"
      :data="rows"
      height="420"
      @selection-change="selected = $event"
    >
      <el-table-column type="selection" width="42" />
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="deleted_at" label="删除时间" width="150" />
      <el-table-column prop="occurred_at" label="发生时间" width="110" />
      <el-table-column prop="issue_type" label="类型" width="100" />
      <el-table-column prop="room_name" label="门店" min-width="140" show-overflow-tooltip />
      <el-table-column prop="problem" label="问题" min-width="180" show-overflow-tooltip />
      <el-table-column prop="creator" label="填写人" width="90" />
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
.recycle-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.recycle-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: auto;
}
</style>
