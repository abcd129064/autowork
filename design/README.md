# design/ 设计资产索引

> 本目录是**只读设计资产**：HTML 设计稿、logo、生成器、入库截图。
> 不放运行时资源（运行时资源在 `resource/`、`styles/`），
> 也不放脚本产物（产物落 `tools/_scratch/`，需长期留档的图落 `design/shots/`）。
> 作业规范见根目录 [AGENTS.md](../AGENTS.md)。

```
design/
├── *.html          # 界面设计稿（浏览器直接打开查看）
├── generator/      # 生成设计稿的脚本 + 数据源 + 模板
├── logo/           # logo 定稿（v4）+ 规范 + 各版本生成脚本
│   └── _archive/   # 落选候选图、缩放测试图、旧图标备份
└── shots/          # 入库截图（文档引用的稳定资产）
```

---

## 一、界面设计稿（HTML）

| 设计稿 | 内容 | 对应实现 |
| --- | --- | --- |
| [aftersale_columns_v2.html](aftersale_columns_v2.html) | 售后记录表 · 列合并设计稿 v2（P0-2） | `windows/aftersale/records.py`（列合并方案实测见 `tools/perf/column_merge_probe.py`） |
| [aftersale_dashboard_design.html](aftersale_dashboard_design.html) | 售后总览 · 图表设计稿（**由脚本生成**，内嵌真实生产数据） | `design/generator/gen_design.py` |
| [tools_page_v2.html](tools_page_v2.html) | 工具页 · 二期设计稿（ToolHub v2 · qfluentwidgets 控件还原） | `main_window/tool_hub.py` |
| [remote_page_v2.html](remote_page_v2.html) | 远程页 · 二期设计稿（RemoteHub v2 · 控件还原） | `main_window/remote_hub.py`（已被 v3 取代） |
| [remote_page_v3.html](remote_page_v3.html) | 远程页 · 二期设计稿（RemoteHub v3 · 连接感知） | `main_window/remote_hub.py` |
| [remote_page_v3_065.html](remote_page_v3_065.html) | 远程页 v3 变体 · 锁定 frps 0.65 口径 | `main_window/remote_hub.py`（**当前生效版本**） |
| [remote_session_v2.html](remote_session_v2.html) | 远程会话页 · 二期设计稿（Remote Session v2） | `windows/remote_session/` |

同一页面的多个版本**全部保留**，用于回溯设计演进；判断当前生效版本看上表最后一列。

## 二、生成器 `design/generator/`

| 文件 | 作用 |
| --- | --- |
| `gen_design.py` | 生成 `design/aftersale_dashboard_design.html` |
| `design_charts.json` | 图表配置数据源 |
| `design_records.json` | 内嵌的真实记录数据 |
| `design_template.html` | HTML 模板 |

```bash
python design/generator/gen_design.py     # 脚本内部按 __file__ 定位，任意 cwd 可跑
```

## 三、logo `design/logo/`

> **定稿结论：v4（`autowork_logo4_*`）为最终方案**，已于 2026-09-06 替换项目根目录
> `app_icon.ico`（16/32/48/256）与 `app_icon.png`（512 RGBA）。
> 完整设计规范、色板、构图理由见 [logo/spec.md](logo/spec.md)。**不要重新挑选版本。**

| 文件 | 说明 |
| --- | --- |
| `autowork_logo4.ico` + `autowork_logo4_{16,32,48,256,512,1024}.png` | **定稿产物**，根目录图标的来源 |
| `spec.md` | logo 设计规范（母题、构图、色板、尺寸档位、定稿记录） |
| `gen_logo.py`、`gen_logo_v2.py` ~ `gen_logo_v5.py` | v1~v5 各版本生成脚本，保留以便复现与再设计 |

各版本设计意图（摘自脚本 docstring）：

- **v1** 抽象字母 A + 圆角方形容器（Windows 11 图标壳）
- **v2** M365 双层叠构风格（参考 Excel 图标构图语言）
- **v3** v2 构图 + 青→蓝→紫罗兰炫彩渐变（Copilot 系气质）
- **v4** ✅ 三层矩形共享同一张全局色相场，拼缝色相连续 + Fluent 接触阴影 + 缘光
- **v5** v4 机制，色相场改为 Word 式横向（紫罗兰→蓝→青）

### `design/logo/_archive/`（42 个文件，只读归档）

落选候选图（v1/v2/v3/v5 全套 PNG + ICO）、`scale_test*_dark/light.png` 真实像素
缩放测试图、`junction_check4/5.png` 拼缝检查图、`scale_test.py` 测试图生成脚本、
`backup_old_icon/`（替换前的旧 `app_icon.ico` / `app_icon.png`）。

> ⚠️ **重跑 `gen_logo*.py` 会把候选图重新生成到 `design/logo/` 根目录**
> （脚本内输出路径未指向 `_archive/`）。跑完请把非 v4 产物移回 `_archive/`，
> 不要提交到定稿目录。

## 四、入库截图 `design/shots/`

| 文件 | 说明 |
| --- | --- |
| `shot_startup_setting.png` | 启动时设置页截图 |

真机截图脚本是 `tools/perf/shot_fluent_mainwindow.py`，其产物默认落
`tools/_scratch/` 下的 `shots/` 子目录（不入库）；确认需要长期留档时再手动拷进
`design/shots/` 并在本表登记。
