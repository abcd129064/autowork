import dayjs from "dayjs";
import { message } from "@/utils/message";
import editForm from "../form/index.vue";
import { addDialog } from "@/components/ReDialog";
import type { PaginationProps } from "@pureadmin/table";
import { ElMessageBox } from "element-plus";
import {
  getRecords,
  getCycleOptions,
  addRecord,
  updateRecord,
  removeRecord,
  batchResolve,
  batchRemove,
  YES,
  NO,
  YES_NO_OPTIONS,
  NEW_RECORD_TEMPLATE
} from "@/api/aftersale";
import type { AftersaleRecord, AftersaleStats } from "@/api/aftersale";
import type { AftersaleFormItem } from "./types";
import { type Ref, h, ref, reactive, computed, onMounted } from "vue";

import FileListIcon from "~icons/ri/file-list-3-line";
import ErrorIcon from "~icons/ri/error-warning-line";
import ThumbIcon from "~icons/ri/thumb-up-line";
// 注意：`ri/user-warning-line` 在 Remix Icon 里不存在（unplugin-icons 会 500），
// 用 `ri/alert-line` 替代表示「我方问题」告警语义
import UserWarnIcon from "~icons/ri/alert-line";

/** 从 axios 错误里安全取出 HTTP 状态码（响应拦截器直接把 AxiosError 抛出来） */
function statusOf(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status;
}

/** 后端 `_WRITABLE` 白名单：只有这些 key 允许出现在写请求体里 */
const WRITABLE_KEYS: Array<keyof AftersaleFormItem> = [
  "creator",
  "issue_type",
  "table_no",
  "room_name",
  "region",
  "problem",
  "cause",
  "resolved",
  "solution",
  "resolver",
  "response_time",
  "snk_code",
  "device_code",
  "cycle_start",
  "is_initiative",
  "is_our_problem",
  "occurred_at",
  "is_important"
];

export function useAftersale(tableRef: Ref) {
  const form = reactive({
    keyword: "",
    cycle_start: "",
    issue_type: "",
    resolved: "",
    is_initiative: "",
    is_our_problem: ""
  });

  const loading = ref(true);
  const dataList = ref<AftersaleRecord[]>([]);
  const cycleOptions = ref<string[]>([]);
  const issueTypes = ref<string[]>([]);
  const regions = ref<string[]>([]);
  const selectedNum = ref(0);
  const stats = ref<AftersaleStats>({
    total: 0,
    unresolved: 0,
    initiative: 0,
    our_problem: 0
  });

  const pagination = reactive<PaginationProps>({
    total: 0,
    pageSize: 20,
    currentPage: 1,
    background: true
  });

  const yesNoOptions = YES_NO_OPTIONS;
  /** 与后端存储值对齐：`是` / `否` */
  const isYes = (v: unknown) => String(v) === YES;

  /** 表格列：对齐后端字段（三个是/否为中文字符串） */
  const columns: TableColumnList = [
    {
      label: "勾选列", // 多选必须设置 label
      type: "selection",
      fixed: "left",
      reserveSelection: true // 数据刷新后保留选项
    },
    {
      label: "ID",
      prop: "id",
      width: 70,
      fixed: "left"
    },
    {
      label: "填写时间",
      prop: "created_at",
      minWidth: 150,
      formatter: ({ created_at }) =>
        created_at ? dayjs(created_at).format("YYYY-MM-DD HH:mm") : "-"
    },
    {
      label: "发生时间",
      prop: "occurred_at",
      minWidth: 110
    },
    {
      label: "账期",
      prop: "cycle_start",
      minWidth: 110,
      cellRenderer: ({ row, props }) =>
        row.cycle_start ? (
          <el-tag size={props.size} effect="plain">
            {row.cycle_start}
          </el-tag>
        ) : (
          <span>-</span>
        )
    },
    {
      label: "类型",
      prop: "issue_type",
      minWidth: 100,
      cellRenderer: ({ row, props }) =>
        row.issue_type ? (
          <el-tag size={props.size} type="info" effect="plain">
            {row.issue_type}
          </el-tag>
        ) : (
          <span>-</span>
        )
    },
    {
      label: "地区",
      prop: "region",
      minWidth: 90
    },
    {
      label: "门店",
      prop: "room_name",
      minWidth: 140
    },
    {
      label: "球桌号",
      prop: "table_no",
      minWidth: 90
    },
    {
      label: "问题",
      prop: "problem",
      minWidth: 200,
      showOverflowTooltip: true
    },
    {
      label: "发生原因",
      prop: "cause",
      minWidth: 160,
      showOverflowTooltip: true
    },
    {
      label: "解决方案",
      prop: "solution",
      minWidth: 180,
      showOverflowTooltip: true
    },
    {
      label: "解决",
      prop: "resolved",
      minWidth: 90,
      align: "center",
      cellRenderer: ({ row, props }) => (
        <el-tag
          size={props.size}
          type={isYes(row.resolved) ? "success" : "danger"}
          effect="plain"
        >
          {isYes(row.resolved) ? "已解决" : "未解决"}
        </el-tag>
      )
    },
    {
      label: "我们问题",
      prop: "is_our_problem",
      minWidth: 100,
      align: "center",
      cellRenderer: ({ row, props }) => (
        <el-tag
          size={props.size}
          type={isYes(row.is_our_problem) ? "warning" : "info"}
          effect="plain"
        >
          {isYes(row.is_our_problem) ? "是" : "否"}
        </el-tag>
      )
    },
    {
      label: "主动发起",
      prop: "is_initiative",
      minWidth: 100,
      align: "center",
      cellRenderer: ({ row, props }) => (
        <el-tag
          size={props.size}
          type={isYes(row.is_initiative) ? "primary" : "info"}
          effect="plain"
        >
          {isYes(row.is_initiative) ? "是" : "否"}
        </el-tag>
      )
    },
    {
      label: "响应",
      prop: "response_time",
      minWidth: 100
    },
    {
      label: "处理人",
      prop: "resolver",
      minWidth: 100
    },
    {
      label: "填写人",
      prop: "creator",
      minWidth: 100
    },
    {
      label: "操作",
      fixed: "right",
      // 200px 才放得下「编辑 + 删除」两个带图标的 link 按钮；
      // 之前 150px 会把「编辑」挤到视口外，且表格 overflow-x:hidden 无法横向滚出，
      // 表现为编辑按钮完全不可见（只有删除的红色图标能露出来）
      width: 200,
      slot: "operation"
    }
  ];

  /** KPI 统计卡（stats 由后端按同一筛选口径返回） */
  const statCards = computed(() => [
    {
      key: "total",
      label: "记录总数",
      value: stats.value.total,
      icon: FileListIcon,
      color: "#409eff"
    },
    {
      key: "unresolved",
      label: "未解决",
      value: stats.value.unresolved,
      icon: ErrorIcon,
      color: "#f56c6c"
    },
    {
      key: "initiative",
      label: "主动发起",
      value: stats.value.initiative,
      icon: ThumbIcon,
      color: "#67c23a"
    },
    {
      key: "our_problem",
      label: "我方问题",
      value: stats.value.our_problem,
      icon: UserWarnIcon,
      color: "#e6a23c"
    }
  ]);

  /** 过滤空值，避免把空串当有效筛选传给后端 */
  function buildParams() {
    const params: Record<string, any> = {
      page: pagination.currentPage,
      page_size: pagination.pageSize
    };
    for (const [k, v] of Object.entries(form)) {
      if (v !== "" && v !== null && v !== undefined) params[k] = v;
    }
    return params;
  }

  /**
   * 抽掉 `_WRITABLE` 之外的 key，并把空串规整为 `null`（后端只跳过 `None`，
   * 空串会被当成合法值写库，导致把已有内容覆盖成空）。
   */
  function toWritablePayload(src: Record<string, any>) {
    const payload: Record<string, any> = {};
    for (const k of WRITABLE_KEYS) {
      const v = src[k];
      if (v === "" || v === undefined || v === null) continue;
      payload[k] = v;
    }
    return payload;
  }

  async function onSearch(resetPage = true) {
    if (resetPage) pagination.currentPage = 1;
    loading.value = true;
    try {
      const res = await getRecords(buildParams());
      dataList.value = res.rows ?? [];
      pagination.total = res.total ?? 0;
      if (res.stats) stats.value = res.stats;
    } catch (err) {
      message("售后记录加载失败，请检查后端服务", { type: "error" });
      console.error("[aftersale] getRecords failed:", err);
    } finally {
      loading.value = false;
    }
  }

  /** 账期候选（后端按当前模式给出合法起点） */
  async function loadCycleOptions() {
    try {
      const res = await getCycleOptions();
      cycleOptions.value = res.options ?? [];
    } catch (err) {
      console.error("[aftersale] cycle-options failed:", err);
    }
  }

  /**
   * 问题类型 / 地区候选：用一次不分页的大页拉取后去重，
   * 保证下拉里是真实的完整分类而不是首页碰巧出现的子集
   */
  async function loadFacets() {
    try {
      const res = await getRecords({ page: 1, page_size: 200 });
      const rows = res.rows ?? [];
      issueTypes.value = Array.from(
        new Set(rows.map(r => r.issue_type).filter(Boolean))
      ).sort();
      regions.value = Array.from(
        new Set(rows.map(r => r.region).filter(Boolean))
      ).sort();
    } catch (err) {
      console.error("[aftersale] loadFacets failed:", err);
    }
  }

  function handleSizeChange(val: number) {
    pagination.pageSize = val;
    onSearch(false);
  }

  function handleCurrentChange(val: number) {
    pagination.currentPage = val;
    onSearch(false);
  }

  function handleSelectionChange(val: AftersaleRecord[]) {
    selectedNum.value = val.length;
    tableRef.value?.setAdaptive?.();
  }

  function onSelectionCancel() {
    selectedNum.value = 0;
    tableRef.value?.getTableRef().clearSelection();
  }

  function clearSelection() {
    selectedNum.value = 0;
    tableRef.value?.getTableRef().clearSelection();
  }

  /** 批量标记已解决 */
  function onBatchResolve() {
    const rows = tableRef.value.getTableRef().getSelectionRows();
    const ids = rows.map(r => r.id);
    if (!ids.length) return;
    ElMessageBox.confirm(
      `确认将选中的 ${ids.length} 条记录标记为已解决？`,
      "系统提示",
      {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        type: "warning",
        draggable: true
      }
    )
      .then(async () => {
        try {
          await batchResolve(ids);
          message(`已标记 ${ids.length} 条记录为已解决`, { type: "success" });
          clearSelection();
          onSearch(false);
        } catch (err) {
          message(writeErrorMessage(err, "批量标记失败"), { type: "error" });
          console.error("[aftersale] batchResolve failed:", err);
        }
      })
      .catch(() => void 0);
  }

  /** 批量删除（二次确认 + 明确列出数量） */
  function onBatchDelete() {
    const rows = tableRef.value.getTableRef().getSelectionRows();
    const ids = rows.map(r => r.id);
    if (!ids.length) return;
    ElMessageBox.confirm(
      `确认删除选中的 ${ids.length} 条记录？该操作不可撤销。`,
      "系统提示",
      {
        confirmButtonText: "确定删除",
        cancelButtonText: "取消",
        type: "error",
        draggable: true
      }
    )
      .then(async () => {
        try {
          await batchRemove(ids);
          message(`已删除 ${ids.length} 条记录`, { type: "success" });
          clearSelection();
          onSearch(false);
        } catch (err) {
          message(writeErrorMessage(err, "批量删除失败"), { type: "error" });
          console.error("[aftersale] batchRemove failed:", err);
        }
      })
      .catch(() => void 0);
  }

  /**
   * 写接口错误的统一人话映射
   *
   * - 503 → 服务端没开 WRITE_ENABLED（离线联调时的常态，不是 bug）
   * - 409 → 乐观锁冲突，必须提示刷新
   * - 401 → AUTH_ENABLED 打开但 token 缺失/过期
   */
  function writeErrorMessage(err: unknown, prefix: string) {
    const s = statusOf(err);
    if (s === 503) return `${prefix}：服务端未开启写入（WRITE_ENABLED=false）`;
    if (s === 401) return `${prefix}：登录已失效，请重新登录`;
    if (s === 409) return `${prefix}：记录已被他人修改，请刷新后重试`;
    return prefix;
  }

  /** 单条删除 */
  async function handleDelete(row: AftersaleRecord) {
    try {
      await removeRecord(row.id);
      message(`已删除编号为 ${row.id} 的记录`, { type: "success" });
      onSearch(false);
    } catch (err) {
      message(writeErrorMessage(err, "删除失败"), { type: "error" });
      console.error("[aftersale] removeRecord failed:", err);
    }
  }

  /**
   * 新增 / 编辑 弹窗
   *
   * 新增：把 `NEW_RECORD_TEMPLATE` 铺开成表单初始值，`creator` 留空交给后端写登录用户
   * 编辑：行数据整体回填，并把 `updated_at` 一起带上做乐观锁
   */
  function openDialog(title: "新增" | "编辑", row?: AftersaleRecord) {
    const initForm: AftersaleFormItem = {
      title,
      // 新增时用模板，编辑时用行数据覆盖
      ...(NEW_RECORD_TEMPLATE as unknown as AftersaleFormItem),
      ...(row
        ? {
            id: row.id,
            updated_at: row.updated_at,
            creator: row.creator ?? "",
            issue_type: row.issue_type ?? "",
            table_no: row.table_no ?? "",
            room_name: row.room_name ?? "",
            region: row.region ?? "",
            problem: row.problem ?? "",
            cause: row.cause ?? "",
            solution: row.solution ?? "",
            resolved: row.resolved ?? NO,
            resolver: row.resolver ?? "",
            response_time: row.response_time ?? "",
            snk_code: row.snk_code ?? "",
            device_code: row.device_code ?? "",
            cycle_start: row.cycle_start ?? "",
            is_initiative: row.is_initiative ?? NO,
            is_our_problem: row.is_our_problem ?? NO,
            // 后端 DATE 列，统一成 YYYY-MM-DD
            occurred_at: row.occurred_at
              ? dayjs(row.occurred_at).format("YYYY-MM-DD")
              : "",
            is_important: Number(row.is_important ?? 0)
          }
        : {}),
      cycleOptions: cycleOptions.value,
      issueTypes: issueTypes.value,
      regions: regions.value
    };

    addDialog({
      title: `${title}售后记录`,
      props: { formInline: initForm },
      width: "62%",
      draggable: true,
      fullscreenIcon: true,
      closeOnClickModal: false,
      contentRenderer: () => h(editForm, { ref: formRef, formInline: null }),
      beforeSure: (done, { options }) => {
        const FormRef = formRef.value.getRef();
        const curData = options.props.formInline as AftersaleFormItem;
        FormRef.validate(async (valid: boolean) => {
          if (!valid) return;
          const payload = toWritablePayload(curData);
          try {
            if (title === "新增") {
              const res = await addRecord(payload);
              message(`已新增售后记录（编号 ${res?.id ?? "-"}）`, {
                type: "success"
              });
            } else {
              await updateRecord(curData.id, {
                ...payload,
                // 乐观锁：原样回传读取时的 updated_at，后端比对失败会返回 409
                updated_at: curData.updated_at
              });
              message(`已更新编号为 ${curData.id} 的记录`, { type: "success" });
            }
            done(); // 关闭弹框
            onSearch(false); // 保持当前页码刷新
          } catch (err) {
            const s = statusOf(err);
            if (s === 409) {
              // 冲突时**不关弹窗**丢弃用户输入，但必须刷新列表让用户重新拉取最新值
              message("该记录已被其他客户端修改，请刷新后重试", {
                type: "warning"
              });
              onSearch(false);
            } else {
              message(
                writeErrorMessage(err, title === "新增" ? "新增失败" : "保存失败"),
                { type: "error" }
              );
            }
            console.error(`[aftersale] ${title} failed:`, err);
          }
        });
      }
    });
  }

  const formRef = ref();

  function resetForm(formEl) {
    if (!formEl) return;
    formEl.resetFields();
    onSearch();
  }

  onMounted(() => {
    onSearch();
    loadCycleOptions();
    loadFacets();
  });

  return {
    form,
    loading,
    columns,
    dataList,
    stats,
    statCards,
    pagination,
    cycleOptions,
    issueTypes,
    regions,
    yesNoOptions,
    selectedNum,
    onSearch,
    resetForm,
    openDialog,
    handleDelete,
    onBatchResolve,
    onBatchDelete,
    onSelectionCancel,
    handleSizeChange,
    handleCurrentChange,
    handleSelectionChange
  };
}
