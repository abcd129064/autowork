# design/ 设计资产索引

> 本目录是**只读设计资产**：HTML 设计稿、logo、生成器、入库截图。
> 不放运行时资源（运行时资源在 `resource/`、`styles/`），
> 也不放脚本产物（产物落 `tools/_scratch/`，需长期留档的图落 `design/shots/`）。
> **视觉规范/设计令牌/组件选型/UI 陷阱**的正典在 [docs/DESIGN.md](../docs/DESIGN.md)；
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
| [aftersale_columns_v2.html](aftersale_columns_v2.html) | 售后记录表 · 列合并设计稿 v2（P0-2） | `windows/aftersale/records/page.py`（列合并方案实测见 `tools/perf/column_merge_probe.py`） |
| [aftersale_dashboard_design.html](aftersale_dashboard_design.html) | 售后总览 · 图表设计稿（**由脚本生成**，内嵌真实生产数据） | `design/generator/gen_design.py` |
| [tools_page_v2.html](tools_page_v2.html) | 工具页 · 二期设计稿（ToolHub v2 · qfluentwidgets 控件还原） | `main_window/tool_hub.py` |
| [remote_page_v2.html](remote_page_v2.html) | 远程页 · 二期设计稿（RemoteHub v2 · 控件还原） | `windows/remote_session/remote_hub.py`（已被 v3 取代） |
| [remote_page_v3.html](remote_page_v3.html) | 远程页 · 二期设计稿（RemoteHub v3 · 连接感知） | `windows/remote_session/remote_hub.py` |
| [remote_page_v3_065.html](remote_page_v3_065.html) | 远程页 v3 变体 · 锁定 frps 0.65 口径 | `windows/remote_session/remote_hub.py`（**当前生效版本**） |
| [remote_session_v2.html](remote_session_v2.html) | 远程会话页 · 二期设计稿（Remote Session v2） | `windows/remote_session/` |
| [dahua_camera_tool.html](dahua_camera_tool.html) | 工具页第 5 子页「相机工具」设计稿 v1（云台预览 + OSD 叠加 + 批量下发，控件↔SDK API 标注齐全） | 已升级为面板级「相机」导航（`windows/camera/`） |
| [video_repair_tool.html](video_repair_tool.html) | 工具页「视频修复」工作区设计稿 v1（掉电截断无 moov 的 mp4 无损修复：借参考文件 VOL 头 + 按 GOP 边界裁剪 + ffmpeg 流拷贝重封装；含内嵌 Pivot 与弹出窗口左侧子导航双形态、四态、后端契约与 7 处落点清单） | `main_window/tool_hub.py`（工作区）+ `windows/tools/video_repair.py`（后端）+ `workers/video_repair_worker.py`（线程） |
| [video_enhance_tool.html](video_enhance_tool.html) | 工具页「画质提升」工作区设计稿 v1（**纯 ffmpeg 滤镜链**，不接 Real-ESRGAN：去噪→放大→锐化→色彩→补帧→编码；内嵌页签 + 弹出工作台双形态、5 子页〔增强任务/滤镜调参/编码输出/环境检测/质量对比〕、ffmpeg 路径设置项+PATH 兜底、VMAF/PSNR/SSIM 客观评分、**8 处注册点清单**与滤镜预设表。**已落地并按实现回填**：五张画布保留原样，画布与实现的差异写在紧邻的黄色 note 与文末「已识别的风险 / 本次明确不做」里） | `windows/tools/video_enhance.py`（引擎）+ `windows/tools/video_enhance_hub.py`（5 子页 UI）+ `workers/video_enhance_worker.py`（6 stage 线程）+ `main_window/tool_hub.py` 注册（工具页第 6 项）；单测 `tests/test_video_enhance.py`、冒烟 `tools/smoke/smoke_video_enhance_hub.py` |
| [worklog_hub_v1.html](worklog_hub_v1.html) | 工作留痕面板 · 设计稿 v1（五页签：记录量化/时间轴/思维导图/统计汇总/成员与分类，多成员量化，SP 概念映射 + 控件映射总表，**Pivot 页签可点击切换预览**） | **已实施**：`windows/worklog/`（hub + 五页签 + common/controller/delegate/dialogs）+ `database/worklog_db.py` + `workers/worklog_worker.py`；主窗口四处集成见 `docs/API.md` 的 windows.worklog 段；冒烟 `tools/smoke/smoke_worklog_hub.py`、单测 `tests/test_worklog_db.py` |

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
| `shot_worklog_records.png` | 工作留痕 · 页签1 记录量化（`design/worklog_hub_v1.html` 308-410）：四卡 + 流式工具栏 + 按天分组表 |
| `shot_worklog_timeline.png` | 工作留痕 · 页签2 时间轴（稿 412-455）：日分组头 + 竖线圆点 + 条目卡 |
| `shot_worklog_mindmap.png` | 工作留痕 · 页签3 思维导图（稿 459-518）：根/分支/叶子三级 + 贝塞尔连线 + 18px 点阵画布 |
| `shot_worklog_stats.png` | 工作留痕 · 页签4 统计汇总（稿 522-595）：四卡 + 按成员堆叠柱 + 分类环图 + 成员汇总表 |
| `shot_worklog_members.png` | 工作留痕 · 页签5 成员与分类（稿 598-628）：成员表 + 分类标签流 |

真机截图脚本是 `tools/perf/shot_fluent_mainwindow.py`，其产物默认落
`tools/_scratch/` 下的 `shots/` 子目录（不入库）；确认需要长期留档时再手动拷进
`design/shots/` 并在本表登记。

工作留痕五张图由 `tools/perf/shot_worklog_hub.py` 产出：它把库换成临时目录里的
独立 SQLite 并灌种子数据，**不碰生产 `database/tables.db`**（worklog 两张表是懒建
的，真库截屏会把 DDL 写进去）；offscreen 平台无中文字体回退，图里的方框是渲染环境
限制，非界面缺陷（与 `shot_startup_setting.png` 同状态）。
