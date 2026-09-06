# -*- coding: utf-8 -*-
"""配置存储门面：settings.json 按域拆分为 config/*.json（2026-09-06）

背景：settings.json 单文件堆积 50+ 顶层键，46 处调用点内联 open+json.load，
读写竞态（worker 线程与主线程读-改-写）与单文件膨胀问题并存。本模块把
配置按业务域拆分到 ``config/`` 目录下的独立 JSON 文件，统一收口读写：

- aftersale.json    售后面板（周期/常用句/上次填写人/重算待办标志）
- perf.json         性能开关（亚克力/动画/表格平滑滚动 + 面板覆盖）
- database.json     数据库（mysql_sync / data_retention）
- credentials.json  凭据与连接配置（ssh/upload/ai/frpc，DPAPI 加密域）
- ui.json           主题/字体/高亮
- paths.json        各工具路径
- remote.json       远程会话列表
- misc.json         杂项（web_port 等）+ 未登记键兜底

核心 API（调用点迁移约定）：
- ``get(key, default)`` / ``set(key, value)``：**按键自动路由**到所属域，
  替代旧「读整个 settings.json → 取键」的内联模式；
- ``get_domain(domain)`` / ``update_domain(domain, data)``：整域读写
  （返回/传入的均为**明文** dict 副本）；
- ``get_merged()``：全域合并视图，语义等同旧 settings.json 整文件，
  供「读整文件后多处取键」的调用点（main.py 主题初始化等）零逻辑改动迁移。

加密下沉：credentials / database 两个域标记为 DPAPI 加密域——读盘后自动
decrypt_settings（调用方拿到的即明文），写盘前自动 encrypt_settings；
core/secrets.py 的键清单原样复用，调用点无需再手动加解密。

迁移：``migrate_legacy()``（幂等，惰性触发 + main.py 显式调用）检测到
旧 settings.json 存在 → 按键分拣合并写入各域文件（已有域文件只补缺键）
→ 旧文件原子改名为 settings.json.bak 兜底可回滚。明文敏感字段在分拣
写盘时经 encrypt_settings 自动加密（原 secrets.migrate_settings_file
职责由本迁移覆盖）。

线程安全：全局 RLock 保护「读盘-解密-缓存」与「读-改-写-落盘」复合操作，
worker 线程与主线程并发写配置不再有丢更新窗口（原 P2-1 竞态随之消除）。
"""
import copy
import json
import os
import threading

from core import app_paths

# 注意：路径统一经 app_paths.get_app_dir() 模块属性调用（不做 from 导入绑定），
# 保证测试 monkeypatch core.app_paths.get_app_dir 能生效（既有隔离约定）

# ==================== 域定义 ====================

# 域 → 文件名（config/ 目录下）
DOMAIN_FILES = {
    "aftersale": "aftersale.json",
    "perf": "perf.json",
    "database": "database.json",
    "credentials": "credentials.json",
    "ui": "ui.json",
    "paths": "paths.json",
    "remote": "remote.json",
    "misc": "misc.json",
}

# DPAPI 加密域：读盘自动解密、写盘自动加密（键清单复用 core/secrets.py）
ENCRYPTED_DOMAINS = frozenset({"credentials", "database"})

# 顶层键 → 域（全量登记，覆盖旧 settings.json 全部 51 键 + 代码中
# 已支持的动态键如 perf_table_smooth_management；未登记键迁移时归 misc）
KEY_DOMAIN = {
    # ---- aftersale ----
    "aftersale_cycle": "aftersale",
    "aftersale_cycle_recalc_pending": "aftersale",
    "aftersale_quick_phrases": "aftersale",
    "aftersale_last_creator": "aftersale",
    "aftersale_last_resolver": "aftersale",
    # ---- perf ----
    "perf_acrylic": "perf",
    "perf_animation": "perf",
    "perf_table_smooth": "perf",
    "performance_mode": "perf",          # 旧字段（迁移后移除，兼容登记）
    "perf_animation_aftersale": "perf",
    "perf_animation_video": "perf",
    "perf_table_smooth_aftersale": "perf",
    "perf_table_smooth_video": "perf",
    "perf_table_smooth_management": "perf",
    "perf_table_smooth_remote": "perf",
    # ---- database ----
    "mysql_sync": "database",
    "data_retention": "database",
    # ---- credentials（DPAPI）----
    "ssh_user": "credentials",
    "ssh_pass": "credentials",
    "upload_host": "credentials",
    "upload_port": "credentials",
    "upload_remote_dir": "credentials",
    "upload_user": "credentials",
    "upload_pass": "credentials",
    "sftp_default_remote_path": "credentials",
    "frpc_server": "credentials",
    "tcp_servers": "credentials",
    "api_credentials": "credentials",
    "ai_vendor": "credentials",
    "ai_model": "credentials",
    "ai_api_keys": "credentials",
    "forensic_ai_analysis": "credentials",
    "deepseek_api_key": "credentials",
    "xtcp_secret_key": "credentials",
    # ---- ui ----
    "dark_theme": "ui",
    "classic_layout": "ui",
    "theme_mode": "ui",
    "theme_color": "ui",
    "font_family": "ui",
    "font_size": "ui",
    "dpi_scale": "ui",
    "highlight_color": "ui",
    "log_highlight_rules": "ui",
    # ---- paths ----
    "exe_dir": "paths",
    "videos_dir": "paths",
    "cipher_tool": "paths",
    "front_exe": "paths",
    "backend_exe": "paths",
    "newlog_excel_dir": "paths",
    "newlog_out_dir": "paths",
    "last_exe": "paths",
    "single_pending_root": "paths",
    "single_videos_root": "paths",
    "newlog_target_name": "paths",
    # ---- remote ----
    "remote_sessions": "remote",
    "restore_remote_sessions": "remote",
    # ---- misc ----
    "web_port": "misc",
}

# ==================== 运行时状态 ====================

_lock = threading.RLock()
_cache: dict = {}          # domain -> 明文 dict（读盘解密后缓存；写后失效）
_migrated = False          # 惰性迁移只跑一次（防异常反复尝试拖慢启动）


# ==================== 路径 ====================

def config_dir() -> str:
    """config/ 目录（app dir 下，随 exe 分发/运行时生成）"""
    return os.path.join(app_paths.get_app_dir(), "config")


def domain_path(domain: str) -> str:
    """域配置文件绝对路径"""
    return os.path.join(config_dir(), DOMAIN_FILES.get(domain, f"{domain}.json"))


def legacy_settings_path() -> str:
    """旧 settings.json 路径（迁移源）"""
    return os.path.join(app_paths.get_app_dir(), "settings.json")


# ==================== 底层读写（无锁，仅供本模块内部使用） ====================

def _read_domain_raw(domain: str) -> dict:
    """读域文件并解密（加密域返回明文 dict）；文件缺失/损坏返回 {}"""
    try:
        with open(domain_path(domain), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if domain in ENCRYPTED_DOMAINS:
        from core.secrets import decrypt_settings
        data = decrypt_settings(data)
    return data


def _write_domain_raw(domain: str, data: dict) -> bool:
    """整域落盘（加密域先加密）；返回是否成功"""
    from core.secrets import encrypt_settings
    payload = encrypt_settings(data) if domain in ENCRYPTED_DOMAINS else data
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(domain_path(domain), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


# ==================== 域级 API（加锁 + 缓存） ====================

def get_domain(domain: str) -> dict:
    """整域明文副本（深拷贝，调用方修改不影响缓存）"""
    _ensure_migrated()
    with _lock:
        if domain not in _cache:
            _cache[domain] = _read_domain_raw(domain)
        return copy.deepcopy(_cache[domain])


def update_domain(domain: str, data: dict) -> bool:
    """合并写整域：锁内 读现值 → 用 data 覆盖同键 → 落盘 → 失效缓存"""
    _ensure_migrated()
    with _lock:
        current = get_domain(domain)
        current.update(data)
        ok = _write_domain_raw(domain, current)
        if ok:
            _cache[domain] = current
        return ok


def replace_domain(domain: str, data: dict) -> bool:
    """整域覆写（不与现值合并；data 应包含该域全部键）"""
    _ensure_migrated()
    with _lock:
        ok = _write_domain_raw(domain, dict(data))
        if ok:
            _cache[domain] = copy.deepcopy(data)
        return ok


def invalidate_cache():
    """清空进程缓存（下次访问重读盘；外部手改配置文件后的刷新入口）"""
    with _lock:
        _cache.clear()


# ==================== 键级 API（按键自动路由） ====================

def domain_of(key: str) -> str:
    """键所属域（未登记键归 misc 兜底）"""
    return KEY_DOMAIN.get(key, "misc")


def get(key: str, default=None):
    """按键读配置（自动路由到所属域；容器值返回深拷贝防缓存污染）"""
    value = get_domain(domain_of(key)).get(key, default)
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    return value


def set(key: str, value) -> bool:
    """按键写配置（合并写，域内其他键不动）"""
    return update_domain(domain_of(key), {key: value})


def remove(key: str) -> bool:
    """按键删除（迁移清理旧字段等场景）"""
    d = domain_of(key)
    _ensure_migrated()
    with _lock:
        current = get_domain(d)
        if key not in current:
            return True
        current.pop(key, None)
        ok = _write_domain_raw(d, current)
        if ok:
            _cache[d] = current
        return ok


def get_merged() -> dict:
    """全域合并视图（语义等同旧 settings.json 整文件，明文）

    供「读整文件后多处取键」的调用点零逻辑改动迁移；加密域贡献明文值。
    """
    merged: dict = {}
    _ensure_migrated()
    with _lock:
        for domain in DOMAIN_FILES:
            merged.update(get_domain(domain))
    return merged


# ==================== 旧 settings.json 迁移 ====================

def migrate_legacy() -> bool:
    """旧 settings.json → config/*.json 按域分拣 + 改名 .bak 兜底

    - 幂等：settings.json 不存在（已迁移/全新安装）直接跳过；
    - 已存在的域文件只补缺键（不覆盖新值），可安全重跑；
    - 明文敏感字段随分拣写盘自动 DPAPI 加密（覆盖原
      secrets.migrate_settings_file 职责）；
    - 分拣完成后旧文件原子改名 settings.json.bak（可回滚）；
    - 全程异常不抛出（配置读写各调用点本就有缺省兜底）。
    """
    global _migrated
    with _lock:
        if _migrated:
            return False
        _migrated = True
        legacy = legacy_settings_path()
        if not os.path.isfile(legacy):
            return False
        try:
            with open(legacy, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict) or not data:
            return False

        # 按键分拣（未登记键归 misc）
        per_domain: dict = {}
        for k, v in data.items():
            per_domain.setdefault(KEY_DOMAIN.get(k, "misc"), {})[k] = v

        os.makedirs(config_dir(), exist_ok=True)
        for domain, section in per_domain.items():
            merged = dict(section)
            if os.path.isfile(domain_path(domain)):
                # 已有域文件（如上次迁移中断）→ 只补缺键
                existing = _read_domain_raw(domain)
                if existing:
                    for k, v in section.items():
                        existing.setdefault(k, v)
                    merged = existing
            _write_domain_raw(domain, merged)

        # 旧文件改名兜底（保留原值可回滚；失败则原文件留存——
        # 域文件已是权威数据，原文件仅成为无效副本，无害）
        try:
            os.replace(legacy,
                       os.path.join(app_paths.get_app_dir(), "settings.json.bak"))
        except OSError:
            pass
        _cache.clear()
        return True


def _ensure_migrated():
    """惰性迁移入口：任何读写前确保旧 settings.json 已分拣（幂等）"""
    if not _migrated:
        migrate_legacy()
