import { http } from "@/utils/http";

/**
 * autowork 售后记录接口层
 *
 * 后端契约见 web/aftersale_api/app.py
 * 只读接口默认开放；写入接口受 WRITE_ENABLED / AUTH_ENABLED 环境开关控制
 */

/**
 * 售后记录行（后端 `_WRITABLE` + 只读字段并集）
 *
 * ⚠️ 字段类型以后端 app.py 为准：
 * - `resolved` / `is_initiative` / `is_our_problem` 是**中文字符串** `"是"` / `"否"`，
 *   不是 0/1（后端 SQL 直接 `WHERE resolved = '是'`，批量解决也是 `SET resolved='是'`）
 * - `is_important` 是 int 0/1
 */
export type AftersaleRecord = {
  id: number;
  created_at: string;
  occurred_at: string;
  issue_type: string;
  table_no: string;
  room_name: string;
  region: string;
  problem: string;
  cause: string;
  solution: string;
  /** 中文字符串：`是` / `否` */
  resolved: string;
  resolver: string;
  response_time: string;
  /** 中文字符串：`是` / `否` */
  is_our_problem: string;
  /** 中文字符串：`是` / `否` */
  is_initiative: string;
  /** int 0 / 1 */
  is_important: number;
  snk_code: string;
  device_code: string;
  cycle_start: string;
  creator: string;
  updated_at?: string;
};

/** 列表统计卡数据 */
export type AftersaleStats = {
  total: number;
  unresolved: number;
  initiative: number;
  our_problem: number;
};

/** 列表查询入参（⚠️ 三个是/否字段传中文字符串 `是` / `否`） */
export type RecordsQuery = {
  page?: number;
  page_size?: number;
  keyword?: string;
  cycle_start?: string;
  issue_type?: string;
  resolved?: string;
  is_initiative?: string;
  is_our_problem?: string;
};

export type RecordsResult = {
  total: number;
  rows: AftersaleRecord[];
  stats: AftersaleStats;
  page: number;
  page_size: number;
};

export type CycleOptionsResult = {
  options: string[];
  type: string;
  current: string;
};

export type TableColumnDef = {
  label: string;
  prop: string;
};

export type ChartsResult = {
  region_dist: Array<{ name: string; value: number }>;
  /** 注意：键名是 `count` 不是 `value` */
  daily: Array<{ date: string; count: number }>;
  our_problem: { yes: number; no: number };
  issue_type_dist: Array<{ name: string; value: number }>;
  total: number;
};

/** 是/否中文字符串常量（与后端存储值一致，勿改成布尔） */
export const YES = "是";
export const NO = "否";
/** 是/否下拉选项 */
export const YES_NO_OPTIONS = [
  { label: "是", value: YES },
  { label: "否", value: NO }
];

/**
 * 判断后端「是/否」字符串字段是否为「是」
 *
 * ⚠️ 后端这三个字段（resolved/is_initiative/is_our_problem）存的是中文字符串，
 * 不是 0/1 —— 判断必须走这个助手，不能 `=== 1` 或 `=== true`
 */
export const isYes = (v: unknown) => String(v) === YES;

/**
 * 新增记录时，后端白名单字段的初始化模板
 *
 * 注意 `resolved` 默认 `否`（新报的问题默认没解决），
 * 其余是/否字段默认 `否`，由表单让用户显式改。
 */
export const NEW_RECORD_TEMPLATE: Partial<AftersaleRecord> = {
  issue_type: "",
  table_no: "",
  room_name: "",
  region: "",
  problem: "",
  cause: "",
  solution: "",
  resolved: NO,
  resolver: "",
  response_time: "",
  snk_code: "",
  device_code: "",
  cycle_start: "",
  is_initiative: NO,
  is_our_problem: NO,
  occurred_at: "",
  is_important: 0
};

/** 健康检查 */
export const getHealth = () => {
  return http.request<{ ok: boolean; db: string }>("get", "/api/health");
};

/** 售后记录分页列表 */
export const getRecords = (params?: RecordsQuery) => {
  return http.request<RecordsResult>("get", "/api/records", { params });
};

/** 账期选项 */
export const getCycleOptions = () => {
  return http.request<CycleOptionsResult>("get", "/api/cycle-options");
};

/** 表格列定义（后端下发） */
export const getTableColumns = () => {
  return http.request<TableColumnDef[]>("get", "/api/table-columns");
};

/** 图表聚合数据 */
export const getCharts = (params?: { cycle_start?: string }) => {
  return http.request<ChartsResult>("get", "/api/stats/charts", { params });
};

/** 自定义维度聚合查询 */
export const postStatsQuery = (data?: object) => {
  return http.request<any>("post", "/api/stats/query", { data });
};

/** 新增售后记录（需 WRITE_ENABLED） */
export const addRecord = (data?: object) => {
  return http.request<any>("post", "/api/records", { data });
};

/** 修改售后记录（乐观锁，冲突返回 409） */
export const updateRecord = (id: number, data?: object) => {
  return http.request<any>("put", `/api/records/${id}`, { data });
};

/** 删除售后记录 */
export const removeRecord = (id: number) => {
  return http.request<any>("delete", `/api/records/${id}`);
};

/** 批量标记已解决 */
export const batchResolve = (ids: number[]) => {
  return http.request<any>("post", "/api/records/batch-resolve", {
    data: { ids }
  });
};

/** 批量删除 */
export const batchRemove = (ids: number[]) => {
  return http.request<any>("post", "/api/records/batch-delete", {
    data: { ids }
  });
};
