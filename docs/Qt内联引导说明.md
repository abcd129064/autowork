# Qt 内联引导说明（conda base 环境）

> 本文档说明：为什么需要在 `import PySide6` 之前执行一段"内联引导"代码、它做了什么、标准写法是什么，以及哪些入口已经内置、新增脚本时如何照抄。

## 一、问题背景

项目使用 conda base 环境运行。当在 **conda base 激活态**下启动任何依赖 `PySide6 / qfluentwidgets` 的入口时，`PATH` 会被注入 conda 自带的 Qt 二进制（`qtbase.dll / qtwebengine.dll` 等，位于 `<conda>\Library\bin`）。

这些 conda 自带的 Qt DLL 与 **PySide6 自带的 Qt 二进制**版本不一致，会相互冲突，导致 Qt 平台插件加载失败，典型报错：

```
qt.qpa.plugin: Could not find the Qt platform plugin "windows" in ""
QT_QPA_PLATFORM_PLUGIN_PATH ...
DLL load failed while importing QtWidgets: 找不到指定的模块。
```

**结论**：在 `import PySide6` / `import qfluentwidgets` **之前**，必须先用 `os.add_dll_directory` 把 PySide6 自带的 Qt 搜索路径"固定"下来，并让 Qt 使用 PySide6 自带的插件目录。这一段代码即为"内联引导"。

## 二、引导做了什么（三个阶段）

1. **固定 DLL 搜索路径**：通过 `importlib.util.find_spec('PySide6')` 定位 PySide6 安装目录，依次把以下三个目录加入 DLL 搜索（`os.add_dll_directory`），让 Qt 优先加载 PySide6 自带的 DLL：
   - PySide6 包目录本身；
   - PySide6 包目录的上一级（`site-packages`，通常也是其它 DLL 所在）；
   - `System32`（Windows 基础系统 DLL）。
   - 目录不存在则跳过，全部包在 `try/except` 中，失败不影响后续（仅降级）。
2. **设置插件路径**：把 `QT_PLUGIN_PATH` 指向 PySide6 的 `plugins` 目录，并把 `QT_QPA_PLATFORM_PLUGIN_PATH` 指向 `plugins/platforms`（用 `setdefault`，不覆盖已有值）。这确保 Qt 平台插件（`qwindows.dll`）从 PySide6 自带目录加载。
3. **注入亚克力补丁**：紧接着 `import core.acrylic_patch`。该模块在 numpy/scipy 不可用（如打包环境被 excludes 排除）时，向 `sys.modules` 注入基于 PIL 的 `qfluentwidgets.common.image_utils` 替代实现，使亚克力磨砂在打包后仍生效。**必须放在 `import qfluentwidgets` 之前。**

## 三、标准代码模板（可直接复制）

主入口 [`main.py`](../main.py) 与各面板入口（`windows/aftersale_panel.py`、`windows/ledger_panel.py`、`windows/management_panel.py`）采用同一模板：

```python
# ---- Qt 运行时引导 ----
# 场景：conda base 激活后，PATH 会注入 conda 自带的 Qt DLL（qtbase/qtwebengine 等，位于
# <conda>\Library\bin），它们与 PySide6 自带的 Qt 二进制冲突，导致 Qt 平台插件加载失败
# （qt.qpa.plugin / DLL load failed）。必须在 import PySide6 之前用 os.add_dll_directory
# 固定 PySide6 自身的 Qt 搜索路径，并让其使用自带的插件目录。
import os as _os
import importlib.util as _qt_iu
_qt_handles = []
try:
    _qt_spec = _qt_iu.find_spec('PySide6')
    if _qt_spec is not None:
        _qt_locs = list(getattr(_qt_spec, 'submodule_search_locations', None) or [])
        if _qt_locs:
            _qt_pkg = _qt_locs[0]
            for _d in (_qt_pkg, _os.path.dirname(_qt_pkg),
                       _os.path.join(_os.environ.get('SystemRoot', r'C:\Windows'), 'System32')):
                if _os.path.isdir(_d):
                    try:
                        _qt_handles.append(_os.add_dll_directory(_d))
                    except OSError:
                        pass
            _os.environ['QT_PLUGIN_PATH'] = _os.path.join(_qt_pkg, 'plugins')
            _os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH',
                                   _os.path.join(_qt_pkg, 'plugins', 'platforms'))
except Exception:
    pass

# 【关键】在 qfluentwidgets 导入前注入亚克力 PIL 补丁（打包环境无 numpy/scipy 时生效）
import core.acrylic_patch  # noqa: F401
```

## 四、位置约束（最关键）

> **`_qt_handles` 必须赋值（不要写 `_ = []`），否则 `os.add_dll_directory` 返回的句柄会被垃圾回收，刚才固定的 DLL 路径随即失效。**

1. **必须在任何 `PySide6` / `qfluentwidgets` 导入之前**。否则 PySide6 已被 conda 的 Qt DLL 污染，再固定路径也无力回天。
2. `import core.acrylic_patch` 紧随其后，且必须早于 `from qfluentwidgets import ...`。
3. 冒烟脚本/独立入口还需要**把项目根加入 `sys.path`**（引导放在 import 项目模块之前）：
   ```python
   import os, sys
   _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
   if _ROOT not in sys.path:
       sys.path.insert(0, _ROOT)
   ```
   这样 `import core.acrylic_patch`、`from windows.aftersale.records import ...` 等才能解析到项目根。

## 五、哪些入口已经内置

| 入口 | 是否内置引导 |
| --- | --- |
| 主程序 `main.py` | ✅（含引导 + acrylic_patch） |
| 售后面板 `windows/aftersale_panel.py` | ✅（含引导 + 打包 DB 重定向） |
| 跑视频面板 `windows/ledger_panel.py` | ✅ |
| 管理面板 `windows/management_panel.py` | ✅ |
| PyInstaller spec（`AutoWork.spec` / `AfterSale.spec`） | ✅（打包侧固定 Qt 搜索路径） |

## 六、新增入口/冒烟脚本时

照抄上面的模板，放在脚本最顶部（`import` 其它任何东西之前），流程为：

```
1. sys.path 加项目根
2. Qt 引导（add_dll_directory + QT_PLUGIN_PATH）
3. import core.acrylic_patch
4. 再 import PySide6 / qfluentwidgets / 项目模块
```

## 七、常见坑

- **不要用 `python -c "...多行..."` 写长引导**：引号与换行在 PowerShell 下极易转义错误。应通过临时 `.py` 脚本（如 `tests/_smoke_*.py`）承载，写完再删除。
- **冒烟脚本退出码非 0（0xC0000409）**：多因构造页面时启用的后台 `QThread worker` 尚未结束，`app.quit()` 优雅销毁时报错。属于脚本清理问题，与产品代码无关；如需强制清零退出，可在结尾 `os._exit(0)`。
- **`QCompleter.popup().complete()` 在无事件循环下会触发弹窗并崩溃（0xC0000005）**：冒烟测试只断言 `filterMode()` / 字符串包含语义，不要直接调 `complete()`。
- **后端为 MySQL 时，`PRAGMA` 会被适配器静默跳过**：冒烟检查表列应改用 `SHOW COLUMNS FROM <table>`（见 `database/backend.py` 的 `MysqlConnectionAdapter.execute`）。
