<script setup lang="ts">
import { ref, computed, watch } from "vue";
import ReCol from "@/components/ReCol";
import CloseIcon from "~icons/ep/close";
import { formRules } from "../utils/rule";
import { YES_NO_OPTIONS, searchTables } from "@/api/aftersale";
import type { TableRow } from "@/api/aftersale";
import type { AftersaleFormProps } from "../utils/types";
import {
  loadQuickPhrases,
  addQuickPhrase,
  removeQuickPhrase
} from "../utils/quickPhrases";

/**
 * 售后表单（对齐桌面端 windows/aftersale/form.py）：
 * - 字段集与默认值与桌面端一致（是否解决=是 / 主动发起=否 / 我方问题=是）
 * - 球房搜索带出：防抖查 billiard_tables，唯一命中静默带出；
 *   选中候选带出 桌号/SNK/城市→地区（关联提示条可视反馈）
 * - 填写人/解决人/发生日期默认「上次填写」（lastUsed，localStorage）
 * - 去掉桌面端没有的 账期/设备码 输入；SNK 码由带出获得（编辑回显在关联条）
 */

/** 桌面端预置（database/aftersale_db.py 同源） */
const ISSUE_TYPES_PRESET = [
  "硬件问题", "程序相关", "识别问题", "直播相关", "操作问题",
  "其他问题", "相机偏移", "新球助手", "安装调试", "不能扫码", "待查"
];
const REGIONS_PRESET = [
  "上海", "云南", "四川", "广东", "新疆", "江苏", "江西", "湖南", "西藏"
];
const RESPONSE_TIME_PRESET = ["1分钟内", "5分钟内", "30分钟内", "1小时内", "1小时以上"];

const props = withDefaults(defineProps<AftersaleFormProps>(), {
  formInline: () => ({
    title: "新增",
    creator: "",
    issue_type: "",
    table_no: "",
    room_name: "",
    region: "",
    problem: "",
    cause: "",
    solution: "",
    resolved: "是",
    resolver: "",
    response_time: "",
    snk_code: "",
    device_code: "",
    cycle_start: "",
    is_initiative: "否",
    is_our_problem: "是",
    occurred_at: "",
    is_important: 0,
    cycleOptions: [],
    issueTypes: [],
    regions: []
  })
});

const ruleFormRef = ref();
const newFormInline = ref(props.formInline);

const isEdit = computed(() => newFormInline.value.title === "编辑");

/** 类型/地区候选 = 预置 ∪ 历史（对齐桌面端 load_candidates 合并语义） */
const typeOptions = computed(() =>
  Array.from(
    new Set([...ISSUE_TYPES_PRESET, ...(newFormInline.value.issueTypes || [])])
  ).filter(Boolean)
);
const regionOptions = computed(() =>
  Array.from(
    new Set([...REGIONS_PRESET, ...(newFormInline.value.regions || [])])
  ).filter(Boolean)
);

// ---------- 球房搜索带出 ----------
const linked = ref<TableRow | null>(null);
let lastLinkedCity = "";

function applyTable(row: TableRow) {
  linked.value = row;
  lastLinkedCity = String(row.city || "");
  newFormInline.value.room_name = String(row.roomName || "");
  newFormInline.value.table_no = String(row.name || "");
  newFormInline.value.snk_code = String(row.snk_code || "");
  if (row.city) newFormInline.value.region = String(row.city);
}

/** 输入变动：旧关联失效（桌面端 _on_room_text_changed 同语义） */
function onRoomInput(v: string) {
  if (linked.value && v !== linked.value.roomName) {
    linked.value = null;
    newFormInline.value.table_no = "";
    newFormInline.value.snk_code = "";
    // 地区仅当仍是上次带出的城市时联动清空，手填/改过的地区保留
    if (lastLinkedCity && newFormInline.value.region === lastLinkedCity) {
      newFormInline.value.region = "";
      lastLinkedCity = "";
    }
  }
}

/** 防抖搜索（el-autocomplete 自带 debounce）；唯一命中静默带出 */
async function queryRoom(queryString: string, cb: (rows: any[]) => void) {
  const kw = String(queryString || "").trim();
  if (!kw) return cb([]);
  try {
    const res = await searchTables(kw);
    const rows = res.rows ?? [];
    if (rows.length === 1) {
      applyTable(rows[0]);
      return cb([]);
    }
    cb(rows);
  } catch {
    cb([]);
  }
}

function onTablePicked(row: TableRow) {
  if (row) applyTable(row);
}

// ---------- 记住上次填写 ----------
// 上一条由 hook 在新增成功后写 lastUsed；这里只做联动清空
watch(
  () => newFormInline.value.title,
  () => {
    linked.value = null;
    lastLinkedCity = "";
  }
);

// ---------- 常用句库 ----------
// 对齐桌面端 QuickPhraseDialog：点击句填入问题框；localStorage 持久化（可增删）
const phrases = ref<string[]>(loadQuickPhrases());
const newPhrase = ref("");

/** 点击常用句：问题框为空直接填入；已有内容则以「；」追加 */
function applyPhrase(p: string) {
  const cur = String(newFormInline.value.problem || "").trim();
  newFormInline.value.problem = cur ? `${cur}；${p}` : p;
}

function onAddPhrase() {
  phrases.value = addQuickPhrase(newPhrase.value);
  newPhrase.value = "";
}

function onRemovePhrase(i: number) {
  phrases.value = removeQuickPhrase(i);
}

/** 连续录入：新增成功后由 hook 调用 —— 清空问题相关字段，
 *  保留 填写人/解决人/发生日期/判定默认值，等待录入下一条 */
function resetForContinue() {
  const f = newFormInline.value;
  f.issue_type = "";
  f.table_no = "";
  f.room_name = "";
  f.region = "";
  f.problem = "";
  f.cause = "";
  f.solution = "";
  f.snk_code = "";
  f.device_code = "";
  f.response_time = "";
  f.is_important = 0;
  f.resolved = "是";
  f.is_initiative = "否";
  f.is_our_problem = "是";
  linked.value = null;
  lastLinkedCity = "";
  // 清除上一条的校验残留提示
  ruleFormRef.value?.clearValidate?.();
}

function getRef() {
  return ruleFormRef.value;
}

defineExpose({ getRef, resetForContinue });
</script>

<template>
  <el-form
    ref="ruleFormRef"
    :model="newFormInline"
    :rules="formRules"
    label-width="88px"
  >
    <el-row :gutter="16">
      <!-- ===== 基本信息 ===== -->
      <re-col :value="12" :xs="24">
        <el-form-item label="问题类型" prop="issue_type">
          <el-select
            v-model="newFormInline.issue_type"
            placeholder="选择/输入问题类型"
            class="w-full"
            clearable
            filterable
            allow-create
            default-first-option
          >
            <el-option
              v-for="item in typeOptions"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24">
        <el-form-item label="发生时间" prop="occurred_at">
          <el-date-picker
            v-model="newFormInline.occurred_at"
            type="date"
            class="w-full"
            placeholder="发生日期"
            value-format="YYYY-MM-DD"
          />
        </el-form-item>
      </re-col>

      <!-- ===== 位置关联 ===== -->
      <re-col :value="24">
        <el-form-item label="球房" prop="room_name">
          <el-autocomplete
            v-model="newFormInline.room_name"
            class="w-full"
            :fetch-suggestions="queryRoom"
            :trigger-on-focus="false"
            :debounce="300"
            value-key="roomName"
            clearable
            placeholder="输入球房名搜索球桌：唯一命中自动带出，多条点选带出桌号/SNK/地区"
            @select="onTablePicked"
            @input="onRoomInput"
          >
            <template #default="{ item }">
              <div class="room-item">
                <span class="tbl">{{ item.name }}</span>
                <span class="room">{{ item.roomName }}</span>
                <span v-if="item.city" class="city">{{ item.city }}</span>
              </div>
            </template>
          </el-autocomplete>
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24">
        <el-form-item label="桌号" prop="table_no">
          <el-input
            v-model="newFormInline.table_no"
            clearable
            placeholder="选桌自动带出"
          />
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24">
        <el-form-item label="地区" prop="region">
          <el-select
            v-model="newFormInline.region"
            placeholder="选择/输入地区（选桌自动带出）"
            class="w-full"
            clearable
            filterable
            allow-create
            default-first-option
          >
            <el-option
              v-for="item in regionOptions"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
      </re-col>

      <re-col v-if="linked" :value="24">
        <el-alert
          type="success"
          :closable="false"
          show-icon
          class="link-bar"
        >
          <template #title>
            已关联球桌：{{ linked.roomName }} · {{ linked.name }} 号桌
            <template v-if="linked.snk_code">
              　|　SNK: {{ linked.snk_code }}
            </template>
            <template v-if="linked.city">　|　城市自动带出</template>
          </template>
        </el-alert>
      </re-col>

      <!-- ===== 问题描述 ===== -->
      <re-col :value="24">
        <el-form-item label="问题" prop="problem">
          <div class="problem-wrap">
            <div class="problem-toolbar">
              <el-popover
                placement="bottom-end"
                :width="320"
                trigger="click"
                popper-class="qp-popper"
              >
                <template #reference>
                  <el-button link type="primary" size="small">
                    常用句
                  </el-button>
                </template>
                <div class="qp-panel">
                  <div class="qp-list">
                    <div
                      v-for="(p, i) in phrases"
                      :key="p"
                      class="qp-item"
                      @click="applyPhrase(p)"
                    >
                      <span class="qp-text">{{ p }}</span>
                      <el-icon
                        class="qp-del"
                        title="删除该常用句"
                        @click.stop="onRemovePhrase(i)"
                      >
                        <component :is="CloseIcon" />
                      </el-icon>
                    </div>
                    <div v-if="!phrases.length" class="qp-empty">
                      暂无常用句，在下方输入添加
                    </div>
                  </div>
                  <div class="qp-add">
                    <el-input
                      v-model="newPhrase"
                      size="small"
                      placeholder="输入新的常用句，回车添加"
                      @keyup.enter="onAddPhrase"
                    />
                    <el-button size="small" type="primary" @click="onAddPhrase">
                      添加
                    </el-button>
                  </div>
                </div>
              </el-popover>
            </div>
            <el-input
              v-model="newFormInline.problem"
              type="textarea"
              :rows="2"
              clearable
              placeholder="请描述客户反馈的问题（可点右上「常用句」快速填入）"
            />
          </div>
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24">
        <el-form-item label="发生原因" prop="cause">
          <el-input
            v-model="newFormInline.cause"
            type="textarea"
            :rows="2"
            clearable
            placeholder="选填"
          />
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24">
        <el-form-item label="解决方案" prop="solution">
          <el-input
            v-model="newFormInline.solution"
            type="textarea"
            :rows="2"
            clearable
            placeholder="选填"
          />
        </el-form-item>
      </re-col>

      <!-- ===== 判定与处理 ===== -->
      <re-col :value="8" :xs="24">
        <el-form-item label="是否解决" prop="resolved">
          <el-radio-group v-model="newFormInline.resolved">
            <el-radio
              v-for="item in YES_NO_OPTIONS"
              :key="item.value"
              :value="item.value"
            >
              {{ item.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24">
        <el-form-item label="我方问题" prop="is_our_problem">
          <el-radio-group v-model="newFormInline.is_our_problem">
            <el-radio
              v-for="item in YES_NO_OPTIONS"
              :key="item.value"
              :value="item.value"
            >
              {{ item.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24">
        <el-form-item label="主动发起" prop="is_initiative">
          <el-radio-group v-model="newFormInline.is_initiative">
            <el-radio
              v-for="item in YES_NO_OPTIONS"
              :key="item.value"
              :value="item.value"
            >
              {{ item.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24">
        <el-form-item label="重要标记" prop="is_important">
          <el-switch
            v-model="newFormInline.is_important"
            inline-prompt
            :active-value="1"
            :inactive-value="0"
            active-text="重要"
            inactive-text="普通"
          />
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24">
        <el-form-item label="响应时间" prop="response_time">
          <el-select
            v-model="newFormInline.response_time"
            placeholder="选择/输入响应时间"
            class="w-full"
            clearable
            filterable
            allow-create
            default-first-option
          >
            <el-option
              v-for="item in RESPONSE_TIME_PRESET"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24">
        <el-form-item label="解决人" prop="resolver">
          <el-input
            v-model="newFormInline.resolver"
            clearable
            placeholder="默认上次填写"
          />
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24">
        <el-form-item label="填写人" prop="creator">
          <el-input
            v-model="newFormInline.creator"
            clearable
            :placeholder="isEdit ? '可留空则不修改' : '默认上次填写，留空由后端写登录用户'"
          />
        </el-form-item>
      </re-col>
    </el-row>
  </el-form>
</template>

<style scoped>
.link-bar {
  margin-bottom: 18px;
}
.problem-wrap {
  width: 100%;
}
.problem-toolbar {
  display: flex;
  justify-content: flex-end;
  line-height: 1;
  margin-bottom: 2px;
}
.room-item {
  display: flex;
  align-items: center;
  gap: 8px;
  line-height: 24px;
}
.room-item .tbl {
  font-weight: 600;
  min-width: 56px;
}
.room-item .room {
  flex: 1;
}
.room-item .city {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
/* 常用句面板（popper 渲染在 body 下，样式走 popper-class） */
.qp-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.qp-list {
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.qp-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  line-height: 20px;
}
.qp-item:hover {
  background: var(--el-fill-color-light);
}
.qp-text {
  flex: 1;
  word-break: break-all;
}
.qp-del {
  flex: none;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  border-radius: 4px;
  padding: 2px;
}
.qp-del:hover {
  color: var(--el-color-danger);
  background: var(--el-fill-color);
}
.qp-empty {
  padding: 12px 0;
  text-align: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.qp-add {
  display: flex;
  gap: 6px;
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 8px;
}
</style>
