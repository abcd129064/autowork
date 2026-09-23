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
  /** 未解决时长分布（当日/1-3天/4-7天/8-15天/15天以上） */
  aging: Array<{ name: string; value: number }>;
  /** 球桌售后排行 TOP10（name="球房·桌号"，联合分组避免跨球房同名桌合并） */
  table_top: Array<{
    name: string;
    value: number;
    room_name: string;
    table_no: string;
  }>;
  /** 球房售后排行 TOP10 */
  room_top: Array<{ name: string; value: number }>;
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
 * 默认值与桌面端 form.py 对齐：是否解决=是 / 主动发起=否 / 我方问题=是
 */
export const NEW_RECORD_TEMPLATE: Partial<AftersaleRecord> = {
  issue_type: "",
  table_no: "",
  room_name: "",
  region: "",
  problem: "",
  cause: "",
  solution: "",
  resolved: YES,
  resolver: "",
  response_time: "",
  snk_code: "",
  device_code: "",
  cycle_start: "",
  is_initiative: NO,
  is_our_problem: YES,
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

/** ===== 售后排行页（/aftersale/rank） ===== */

/** 排行行（level=room 时 table_no 为空串） */
export type RankRow = {
  rank: number;
  room_name: string;
  table_no: string;
  /** 展示名：球桌全局榜="球房·桌号"，下钻榜=桌号，球房榜=球房名 */
  name: string;
  total: number;
  /** 占同口径记录总数的百分比（后端已算好，1 位小数） */
  share: number;
  unresolved: number;
  our_problem: number;
  initiative: number;
  /** 最近一次发生日期 yyyy-MM-dd */
  last_occurred: string;
};

export type RankResult = {
  level: "room" | "table";
  sort: string;
  limit: number;
  rows: RankRow[];
  summary: {
    rooms: number;
    tables: number;
    total: number;
    unresolved: number;
  };
};

/** 排行查询入参（时间三选一：cycle_start 优先，start/end 次之，全空=后端兜底近90天） */
export type RankQuery = {
  level?: "room" | "table";
  limit?: number;
  cycle_start?: string;
  start?: string;
  end?: string;
  resolved?: string;
  is_initiative?: string;
  is_our_problem?: string;
  region?: string;
  room_name?: string;
  keyword?: string;
  sort?: string;
};

/** 球房/球桌售后排行 */
export const getRank = (params?: RankQuery) => {
  return http.request<RankResult>("get", "/api/stats/rank", { params });
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

/** 球桌候选（球房带出桌号/SNK/城市，与桌面端同源 billiard_tables） */
export interface TableRow {
  name: string;
  roomName: string;
  snk_code: string;
  city: string;
}

/** 按球房名模糊搜索球桌（后端排除公司测试与手动设备 @s） */
export const searchTables = (room: string, limit = 30) => {
  return http.request<{ rows: TableRow[] }>("get", "/api/tables/search", {
    params: { room, limit }
  });
};

/** ===== 回收站（软删除） ===== */

export const getRecycleRecords = (params?: {
  page?: number;
  page_size?: number;
  keyword?: string;
}) => {
  return http.request<{ total: number; rows: AftersaleRecord[] }>(
    "get",
    "/api/records/recycle",
    { params }
  );
};

/** 从回收站恢复 */
export const restoreRecords = (ids: number[]) => {
  return http.request<{ restored: number }>("post", "/api/records/restore", {
    data: { ids }
  });
};

/** 彻底删除（硬删，不可恢复） */
export const purgeRecords = (ids: number[]) => {
  return http.request<{ purged: number }>("post", "/api/records/purge", {
    data: { ids }
  });
};

/** ===== 批量导入（Excel） ===== */

export const batchImportRows = (rows: object[]) => {
  return http.request<{ imported: number; skipped: number }>(
    "post",
    "/api/records/batch-import",
    { data: { rows } }
  );
};

/** ===== 操作审计 ===== */

export type AuditLogRow = {
  id: number;
  ts: string;
  user: string;
  action: string;
  record_id: number | null;
  detail: string;
};

export const getAuditLog = (params?: {
  page?: number;
  page_size?: number;
  action?: string;
}) => {
  return http.request<{
    total: number;
    rows: AuditLogRow[];
    page: number;
    page_size: number;
  }>("get", "/api/audit", { params });
};

/** ===== 用户偏好（常用句库 / 上次填写，多端共享） ===== */

export const getUserPrefs = () => {
  return http.request<{
    prefs: Record<string, any>;
    updated_at: string | null;
  }>("get", "/api/user/prefs");
};

export const putUserPrefs = (patch: Record<string, any>) => {
  return http.request<{ ok: boolean }>("put", "/api/user/prefs", {
    data: { prefs: patch }
  });
};
