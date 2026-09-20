/**
 * 记住上次填写（localStorage）—— 对齐桌面端「记住上次」体验：
 * - 填写人 / 解决人：新增成功后记住，下次打开弹窗默认填入
 * - 发生日期：记住上次新增的日期（连续补录同一天免重复选）
 */
import type { LastUsed } from "./types";

const KEY = "aftersale-last-used";

export function loadLastUsed(): LastUsed {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}") as LastUsed;
  } catch {
    return {};
  }
}

export function saveLastUsed(patch: LastUsed): void {
  try {
    localStorage.setItem(
      KEY,
      JSON.stringify({ ...loadLastUsed(), ...patch })
    );
  } catch {
    /* 存储不可用时静默（隐私模式等） */
  }
}
