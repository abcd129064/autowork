/**
 * 售后记录 新增 / 编辑 弹窗类型
 *
 * 字段与后端 `_WRITABLE` 白名单一一对应（见 web/aftersale_api/app.py:186）。
 * ⚠️ `resolved` / `is_initiative` / `is_our_problem` 后端存**中文字符串** `是` / `否`，
 *    不是布尔也不是 0/1，表单里必须用 `el-select` + `YES_NO_OPTIONS`，不能用 switch。
 * ⚠️ `occurred_at` 后端是 DATE 列，前端统一 `YYYY-MM-DD` 字符串。
 */

export interface AftersaleFormItem {
  /** 用于判断是「新增」还是「编辑」 */
  title: string;
  /** 编辑时携带，用于乐观锁（回传读取时的 updated_at） */
  id?: number;
  /** 乐观锁基准值：服务端返回的 updated_at 原样回传 */
  updated_at?: string;

  creator: string;
  issue_type: string;
  table_no: string;
  room_name: string;
  region: string;
  problem: string;
  cause: string;
  solution: string;
  /** `是` / `否` */
  resolved: string;
  resolver: string;
  response_time: string;
  snk_code: string;
  device_code: string;
  cycle_start: string;
  /** `是` / `否` */
  is_initiative: string;
  /** `是` / `否` */
  is_our_problem: string;
  /** YYYY-MM-DD */
  occurred_at: string;
  /** int 0 / 1 */
  is_important: number;

  /** 下拉候选：账期（由父页面注入，避免弹窗内再发请求） */
  cycleOptions?: string[];
  /** 下拉候选：问题类型（由父页面注入） */
  issueTypes?: string[];
  /** 下拉候选：地区（由父页面注入） */
  regions?: string[];
}

export interface AftersaleFormProps {
  formInline: AftersaleFormItem;
}
