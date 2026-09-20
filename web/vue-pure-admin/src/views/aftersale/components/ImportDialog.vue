<script setup lang="ts">
import { ref, computed } from "vue";
import { message } from "@/utils/message";
import dayjs from "dayjs";
import * as XLSX from "xlsx";
import { batchImportRows } from "@/api/aftersale";

/**
 * Excel 批量导入（对齐桌面端 parse_excel_rows 语义）：
 * - 表头按中文名定位：类型/球房/桌号/地区/问题 必需；
 *   发生原因/是否解决/解决方案/解决人/响应时间 选填
 * - 「类型」列分组首行标记，逐行向下填充
 * - 跳过 球房/桌号/问题 全空的行；是否解决空值默认「否」
 * - created_at=导入时刻，creator 由后端写登录用户，账期按发生日期归属
 * - 先预览校验，确认后分批（500 行/批）提交
 */
defineOptions({ name: "AftersaleImportDialog" });

const visible = defineModel<boolean>({ default: false });
const emit = defineEmits<{ imported: [] }>();

/** Excel 中文表头 → 记录字段（与桌面端解析规则同源） */
const HEADER_MAP: Record<string, string> = {
  类型: "issue_type",
  球房: "room_name",
  桌号: "table_no",
  地区: "region",
  问题: "problem",
  发生原因: "cause",
  是否解决: "resolved",
  解决方案: "solution",
  解决人: "resolver",
  响应时间: "response_time",
  发生时间: "occurred_at"
};
const REQUIRED_HEADERS = ["类型", "球房", "桌号", "地区", "问题"];

type ImportRow = Record<string, string> & { _valid: boolean };

const parsing = ref(false);
const importing = ref(false);
const fileName = ref("");
const headerError = ref("");
const parsedRows = ref<ImportRow[]>([]);

const validCount = computed(() => parsedRows.value.filter(r => r._valid).length);
const invalidCount = computed(() => parsedRows.value.length - validCount.value);
const previewRows = computed(() => parsedRows.value.slice(0, 8));

function fmtDate(v: unknown): string {
  if (v == null || v === "") return "";
  if (v instanceof Date && !isNaN(v.getTime())) {
    return dayjs(v).format(v.getHours() || v.getMinutes() ? "YYYY-MM-DD HH:mm:ss" : "YYYY-MM-DD");
  }
  return String(v).trim();
}

async function onFileChange(uploadFile: { raw?: File; name: string }) {
  const raw = uploadFile.raw;
  if (!raw) return;
  fileName.value = uploadFile.name;
  headerError.value = "";
  parsedRows.value = [];
  parsing.value = true;
  try {
    const buf = await raw.arrayBuffer();
    const wb = XLSX.read(buf, { type: "array", cellDates: true });
    const ws = wb.Sheets[wb.SheetNames[0]];
    const matrix: unknown[][] = XLSX.utils.sheet_to_json(ws, {
      header: 1,
      blankrows: false,
      defval: ""
    });
    if (!matrix.length) throw new Error("表格为空");
    // 首行按中文名定位列号
    const headerIdx: Record<string, number> = {};
    (matrix[0] as unknown[]).forEach((h, i) => {
      const name = String(h ?? "").trim();
      if (name) headerIdx[name] = i;
    });
    const missing = REQUIRED_HEADERS.filter(h => !(h in headerIdx));
    if (missing.length) {
      headerError.value = `表头缺少必需列：${missing.join("、")}`;
      return;
    }
    const val = (row: unknown[], zh: string) => {
      const i = headerIdx[zh];
      return i == null ? "" : fmtDate(row[i]);
    };
    const out: ImportRow[] = [];
    let lastType = "";
    for (let r = 1; r < matrix.length; r++) {
      const row = matrix[r] as unknown[];
      const rec = {} as ImportRow;
      for (const [zh, field] of Object.entries(HEADER_MAP)) {
        rec[field] = val(row, zh);
      }
      // 类型列向下填充（桌面端分组首行语义）
      if (rec.issue_type) lastType = rec.issue_type;
      else rec.issue_type = lastType;
      const empty = !rec.room_name && !rec.table_no && !rec.problem;
      if (empty) continue; // 跳过全空行
      if (!rec.resolved) rec.resolved = "否"; // 默认未解决
      rec._valid =
        !!(rec.room_name || rec.table_no) &&
        !!rec.problem &&
        !!rec.issue_type;
      out.push(rec);
    }
    parsedRows.value = out;
    if (!out.length) headerError.value = "未解析到有效数据行";
  } catch (err) {
    headerError.value = `解析失败：${(err as Error).message}`;
    console.error("[aftersale] parse excel failed:", err);
  } finally {
    parsing.value = false;
  }
}

async function onConfirm() {
  const rows = parsedRows.value.filter(r => r._valid).map(({ _valid, ...r }) => r);
  if (!rows.length) return;
  importing.value = true;
  let imported = 0;
  let skipped = 0;
  try {
    for (let i = 0; i < rows.length; i += 500) {
      const res = await batchImportRows(rows.slice(i, i + 500));
      imported += res.imported ?? 0;
      skipped += res.skipped ?? 0;
    }
    message(`导入完成：成功 ${imported} 条，跳过 ${skipped} 条`, {
      type: "success"
    });
    visible.value = false;
    parsedRows.value = [];
    fileName.value = "";
    emit("imported");
  } catch (err) {
    message("导入失败，请检查后端服务", { type: "error" });
    console.error("[aftersale] batch import failed:", err);
  } finally {
    importing.value = false;
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="Excel 批量导入"
    width="820px"
    destroy-on-close
  >
    <el-upload
      drag
      accept=".xlsx,.xls"
      :auto-upload="false"
      :show-file-list="false"
      :on-change="onFileChange"
    >
      <div class="upload-hint">
        <p>将 Excel 文件拖到此处，或点击选择文件</p>
        <p class="sub">
          表头需包含：类型 / 球房 / 桌号 / 地区 / 问题（选填：发生原因 / 是否解决 /
          解决方案 / 解决人 / 响应时间 / 发生时间）
        </p>
      </div>
    </el-upload>

    <el-alert
      v-if="headerError"
      :title="headerError"
      type="error"
      :closable="false"
      class="mt-3"
    />

    <template v-if="parsedRows.length">
      <div class="import-summary">
        <span>
          <b>{{ fileName }}</b> 解析到 {{ parsedRows.length }} 行：
        </span>
        <el-tag type="success" size="small">可导入 {{ validCount }}</el-tag>
        <el-tag v-if="invalidCount" type="danger" size="small">
          无效跳过 {{ invalidCount }}
        </el-tag>
      </div>
      <el-table :data="previewRows" height="260" size="small" border>
        <el-table-column type="index" label="#" width="46" />
        <el-table-column prop="issue_type" label="类型" width="100" />
        <el-table-column prop="room_name" label="球房" min-width="140" show-overflow-tooltip />
        <el-table-column prop="table_no" label="桌号" width="80" />
        <el-table-column prop="region" label="地区" width="80" />
        <el-table-column prop="problem" label="问题" min-width="160" show-overflow-tooltip />
        <el-table-column prop="resolved" label="是否解决" width="86" />
        <el-table-column label="校验" width="76">
          <template #default="{ row }">
            <el-tag :type="row._valid ? 'success' : 'danger'" size="small">
              {{ row._valid ? "通过" : "无效" }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="parsedRows.length > 8" class="preview-more">
        仅预览前 8 行，共 {{ parsedRows.length }} 行
      </p>
    </template>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="primary"
        :disabled="!validCount"
        :loading="importing"
        @click="onConfirm"
      >
        导入 {{ validCount ? `（${validCount} 条）` : "" }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.upload-hint p {
  margin: 6px 0;
}
.upload-hint .sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.import-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 0 8px;
  font-size: 13px;
}
.preview-more {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 6px;
}
</style>
