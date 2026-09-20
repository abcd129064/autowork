<script setup lang="ts">
import { computed } from "vue";
import dayjs from "dayjs";
import type { AftersaleRecord } from "@/api/aftersale";

/**
 * 售后记录详情抽屉：只读展示全部字段（含表单不展示的
 * 账期/设备码/SNK 码/填写时间/更新时间），供长文本完整阅读
 */
defineOptions({ name: "AftersaleDetailDrawer" });

const props = defineProps<{
  row: AftersaleRecord | null;
}>();

const visible = defineModel<boolean>({ default: false });

const items = computed(() => {
  const r = props.row;
  if (!r) return [];
  const fmt = (v: string) =>
    v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "-";
  return [
    { label: "编号", value: String(r.id) },
    { label: "填写时间", value: fmt(r.created_at) },
    { label: "发生时间", value: r.occurred_at || "-" },
    { label: "账期", value: r.cycle_start || "-" },
    { label: "问题类型", value: r.issue_type || "-" },
    { label: "地区", value: r.region || "-" },
    { label: "门店", value: r.room_name || "-" },
    { label: "球桌号", value: r.table_no || "-" },
    { label: "SNK 码", value: r.snk_code || "-" },
    { label: "设备码", value: r.device_code || "-" },
    { label: "问题描述", value: r.problem || "-", wide: true },
    { label: "发生原因", value: r.cause || "-", wide: true },
    { label: "解决方案", value: r.solution || "-", wide: true },
    { label: "是否解决", value: r.resolved === "是" ? "已解决" : "未解决" },
    { label: "我方问题", value: r.is_our_problem || "-" },
    { label: "主动发起", value: r.is_initiative || "-" },
    { label: "重要标记", value: Number(r.is_important) ? "重要" : "普通" },
    { label: "响应时间", value: r.response_time || "-" },
    { label: "解决人", value: r.resolver || "-" },
    { label: "填写人", value: r.creator || "-" },
    { label: "更新时间", value: fmt(r.updated_at || "") }
  ];
});
</script>

<template>
  <el-drawer
    v-model="visible"
    title="售后记录详情"
    size="520px"
    destroy-on-close
  >
    <el-descriptions v-if="row" :column="2" border size="small">
      <template v-for="it in items" :key="it.label">
        <el-descriptions-item
          :label="it.label"
          :span="(it as any).wide ? 2 : 1"
        >
          <span
            :class="{
              'detail-pre': ['问题描述', '发生原因', '解决方案'].includes(
                it.label
              )
            }"
          >
            {{ it.value }}
          </span>
        </el-descriptions-item>
      </template>
    </el-descriptions>
  </el-drawer>
</template>

<style scoped>
.detail-pre {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
