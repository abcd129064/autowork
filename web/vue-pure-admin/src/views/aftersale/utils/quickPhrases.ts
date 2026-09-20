/**
 * 常用句库（对齐桌面端 database/aftersale_db.py load_quick_phrases 语义）：
 * - localStorage 持久化（桌面端存 config/aftersale.json，web 端存浏览器本地）
 * - 云端同步：aftersale_user_prefs（后端 /api/user/prefs，多端共享）
 *   读：pullPhrasesFromCloud() 拉服务端覆盖本地缓存；写：本地立即生效 + 服务端异步推送
 * - 预置句与桌面端 _QPC_DEFAULT 同源
 * - add 去重 + 置顶（对齐桌面端 add_quick_phrase）
 */
import { getUserPrefs, putUserPrefs } from "@/api/aftersale";

const KEY = "aftersale-quick-phrases";

/** 桌面端预置（database/aftersale_db.py _QPC_DEFAULT 同源） */
export const QUICK_PHRASES_PRESET = [
  "主机没有开机",
  "遥控器没反应",
  "程序没了",
  "不能扫码",
  "识别不了",
  "记分牌显示不出来"
];

export function loadQuickPhrases(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw === null) return [...QUICK_PHRASES_PRESET];
    const v = JSON.parse(raw);
    if (Array.isArray(v)) {
      return v.map(x => String(x).trim()).filter(Boolean);
    }
  } catch {
    /* 损坏数据回退预置 */
  }
  return [...QUICK_PHRASES_PRESET];
}

function saveLocal(list: string[]) {
  localStorage.setItem(KEY, JSON.stringify(list.filter(Boolean)));
}

/** 服务端写回（尽力而为，失败不影响本地） */
function pushCloud(list: string[]) {
  putUserPrefs({ quick_phrases: list.filter(Boolean) }).catch(() => void 0);
}

/**
 * 拉取云端偏好覆盖本地缓存（登录后列表页挂载时调用一次）。
 * 服务端无数据时不覆盖（保留本地/预置）。
 */
export async function pullPhrasesFromCloud(): Promise<string[]> {
  try {
    const res = await getUserPrefs();
    const remote = res?.prefs?.quick_phrases;
    if (Array.isArray(remote) && remote.length) {
      const list = remote.map(x => String(x).trim()).filter(Boolean);
      saveLocal(list);
      return list;
    }
  } catch {
    /* 未登录/网络失败：沿用本地 */
  }
  return loadQuickPhrases();
}

/** 新增：去重 + 置顶，返回更新后的列表（对齐桌面端 add_quick_phrase） */
export function addQuickPhrase(text: string): string[] {
  const t = String(text || "").trim();
  const list = loadQuickPhrases();
  if (t) {
    const idx = list.indexOf(t);
    if (idx >= 0) list.splice(idx, 1);
    list.unshift(t);
    saveLocal(list);
    pushCloud(list);
  }
  return list;
}

/** 删除指定下标，返回更新后的列表 */
export function removeQuickPhrase(index: number): string[] {
  const list = loadQuickPhrases();
  list.splice(index, 1);
  saveLocal(list);
  pushCloud(list);
  return list;
}
