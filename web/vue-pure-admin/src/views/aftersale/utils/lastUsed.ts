/**
 * 记住上次填写（localStorage + 云端同步）—— 对齐桌面端「记住上次」体验：
 * - 填写人 / 解决人：新增成功后记住，下次打开弹窗默认填入
 * - 发生日期：记住上次新增的日期（连续补录同一天免重复选）
 * - 云端同步：aftersale_user_prefs（后端 /api/user/prefs），换电脑/换浏览器不丢
 */
import { getUserPrefs, putUserPrefs } from "@/api/aftersale";
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
  const merged = { ...loadLastUsed(), ...patch };
  try {
    localStorage.setItem(KEY, JSON.stringify(merged));
  } catch {
    /* 存储不可用时静默（隐私模式等） */
  }
  // 云端推送（尽力而为）
  putUserPrefs({ last_used: merged }).catch(() => void 0);
}

/**
 * 拉取云端偏好覆盖本地缓存（登录后列表页挂载时调用一次）。
 * 服务端无数据时不覆盖本地。
 */
export async function pullLastUsedFromCloud(): Promise<LastUsed> {
  try {
    const res = await getUserPrefs();
    const remote = res?.prefs?.last_used;
    if (remote && typeof remote === "object") {
      const merged = { ...loadLastUsed(), ...(remote as LastUsed) };
      localStorage.setItem(KEY, JSON.stringify(merged));
      return merged;
    }
  } catch {
    /* 未登录/网络失败：沿用本地 */
  }
  return loadLastUsed();
}
