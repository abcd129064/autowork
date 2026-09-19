<script setup lang="ts">
import { ref, computed } from "vue";
import ReCol from "@/components/ReCol";
import { formRules } from "../utils/rule";
import { YES_NO_OPTIONS } from "@/api/aftersale";
import type { AftersaleFormProps } from "../utils/types";

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
    resolved: "否",
    resolver: "",
    response_time: "",
    snk_code: "",
    device_code: "",
    cycle_start: "",
    is_initiative: "否",
    is_our_problem: "否",
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

function getRef() {
  return ruleFormRef.value;
}

defineExpose({ getRef });
</script>

<template>
  <el-form
    ref="ruleFormRef"
    :model="newFormInline"
    :rules="formRules"
    label-width="92px"
  >
    <el-row :gutter="30">
      <!-- ===== 归类信息 ===== -->
      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="问题类型" prop="issue_type">
          <el-select
            v-model="newFormInline.issue_type"
            placeholder="请选择问题类型"
            class="w-full"
            clearable
            filterable
            allow-create
            default-first-option
          >
            <el-option
              v-for="item in newFormInline.issueTypes"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="账期" prop="cycle_start">
          <el-select
            v-model="newFormInline.cycle_start"
            placeholder="请选择账期"
            class="w-full"
            clearable
            filterable
            allow-create
            default-first-option
          >
            <el-option
              v-for="item in newFormInline.cycleOptions"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="地区" prop="region">
          <el-select
            v-model="newFormInline.region"
            placeholder="请选择/输入地区"
            class="w-full"
            clearable
            filterable
            allow-create
            default-first-option
          >
            <el-option
              v-for="item in newFormInline.regions"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="门店名称" prop="room_name">
          <el-input
            v-model="newFormInline.room_name"
            clearable
            placeholder="请输入门店名称"
          />
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="球桌号" prop="table_no">
          <el-input
            v-model="newFormInline.table_no"
            clearable
            placeholder="请输入球桌号"
          />
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="发生时间" prop="occurred_at">
          <el-date-picker
            v-model="newFormInline.occurred_at"
            type="date"
            class="w-full"
            placeholder="请选择发生日期"
            value-format="YYYY-MM-DD"
          />
        </el-form-item>
      </re-col>

      <!-- ===== 问题描述 ===== -->
      <re-col :value="24">
        <el-form-item label="问题描述" prop="problem">
          <el-input
            v-model="newFormInline.problem"
            type="textarea"
            :rows="2"
            clearable
            placeholder="请描述客户反馈的问题"
          />
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24" :sm="24">
        <el-form-item label="发生原因" prop="cause">
          <el-input
            v-model="newFormInline.cause"
            type="textarea"
            :rows="2"
            clearable
            placeholder="问题产生的原因"
          />
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24" :sm="24">
        <el-form-item label="解决方案" prop="solution">
          <el-input
            v-model="newFormInline.solution"
            type="textarea"
            :rows="2"
            clearable
            placeholder="采取的解决措施"
          />
        </el-form-item>
      </re-col>

      <!-- ===== 状态标记（⚠️ 后端是中文字符串 是/否，必须用 select） ===== -->
      <re-col :value="8" :xs="24" :sm="12">
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

      <re-col :value="8" :xs="24" :sm="12">
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

      <re-col :value="8" :xs="24" :sm="12">
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

      <re-col :value="8" :xs="24" :sm="12">
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

      <!-- ===== 处理信息 ===== -->
      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="处理人" prop="resolver">
          <el-input
            v-model="newFormInline.resolver"
            clearable
            placeholder="请输入处理人"
          />
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="响应时长" prop="response_time">
          <el-input
            v-model="newFormInline.response_time"
            clearable
            placeholder="如：2小时 / 当天"
          />
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="填写人" prop="creator">
          <el-input
            v-model="newFormInline.creator"
            clearable
            :placeholder="isEdit ? '可留空则不修改' : '留空则由后端写入当前登录用户'"
          />
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="SNK 码" prop="snk_code">
          <el-input
            v-model="newFormInline.snk_code"
            clearable
            placeholder="请输入 SNK 码"
          />
        </el-form-item>
      </re-col>

      <re-col :value="8" :xs="24" :sm="12">
        <el-form-item label="设备码" prop="device_code">
          <el-input
            v-model="newFormInline.device_code"
            clearable
            placeholder="请输入设备码"
          />
        </el-form-item>
      </re-col>
    </el-row>
  </el-form>
</template>
