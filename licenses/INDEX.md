# 第三方组件许可证清单

> 本目录收纳 AutoWork 依赖的第三方项目的**许可证原文**（从各自 `*.dist-info/` 或
> 上游仓库逐字复制，未经任何改写）。表格里的版本号是打包环境
> `C:\Users\shen_zhe\miniconda3\python.exe` 的实测安装版本，不是 `requirements.txt`
> 里的下限。
> 结论口径见 [../docs/GPLv3依赖合规说明.md](../docs/GPLv3依赖合规说明.md)。

## 一、运行时 Python 依赖（已收录原文）

| 组件 | 实测版本 | 许可证（取自上游 METADATA） | 本地原文 | 在本项目中的角色 |
| --- | --- | --- | --- | --- |
| PySide6-Fluent-Widgets | 1.11.2 | GPL-3.0（上游 METADATA 标为 GPLv3） | `licenses/pyside6_fluent_widgets/pyside6_fluent_widgets.LICENSE` | GUI 组件库，**本项目唯一的强 copyleft 传染源** |
| paramiko | 5.0.0 | LGPL-2.1 | `licenses/paramiko/paramiko.LICENSE` | SSH / SFTP 客户端 |
| bcrypt | 5.0.0 | Apache-2.0 | `licenses/bcrypt/bcrypt.LICENSE` | paramiko 的密码哈希后端 |
| requests | 2.32.3 | Apache-2.0 | `licenses/requests/requests.LICENSE` | HTTP 调用 |
| urllib3 | 2.3.0 | MIT | `licenses/urllib3/urllib3.LICENSE` | requests 的传输层 |
| openai | 2.53.0 | Apache-2.0 | `licenses/openai/openai.LICENSE` | DeepSeek AI 分析（OpenAI 兼容协议） |
| trafilatura | 见下文 | 以上游为准 | — | 小说阅读器正文抽取（当前打包环境未安装） |
| pygwalker | 0.5.0.1 | Apache-2.0 | `licenses/pygwalker/pygwalker.LICENSE` | 售后面板可视化图表 |
| fastapi | 0.138.0 | MIT | `licenses/fastapi/fastapi.LICENSE` | 本地售后面板 Web 服务 |
| uvicorn | 0.52.4 | BSD-3-Clause | `licenses/uvicorn/uvicorn.LICENSE` | fastapi 的 ASGI 服务器 |
| pymysql | 1.1.1 | MIT | `licenses/pymysql/pymysql.LICENSE` | 远程 MySQL 同步 |
| numpy | 2.5.2 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | `licenses/numpy/numpy.LICENSE` | pandas / opencv 的数值底座 |
| pandas | 3.0.5 | BSD-3-Clause | `licenses/pandas/pandas.LICENSE` | 库存明细与统计 |
| darkdetect | 0.8.0 | BSD-3-Clause | `licenses/darkdetect/darkdetect.LICENSE` | qfluentwidgets 的系统深浅色探测 |
| Pillow | 11.1.0 | MIT-CMU | `licenses/pillow/pillow.LICENSE` | 亚克力模糊的位图处理 |
| opencv-python-headless | 4.13.0.92 | Apache-2.0 | `licenses/opencv_python_headless/opencv_python_headless.LICENSE` | 单杆视频渲染（`requirements.txt` 写的是 `opencv-python`，打包实际装 headless） |
| lxml | 6.1.1 | BSD-3-Clause | `licenses/lxml/lxml.LICENSE` | trafilatura 的解析后端 |
| openpyxl | 3.1.5 | MIT | `licenses/openpyxl/openpyxl.LICENCE.rst` | xlsx 读写（上游文件名就是 `LICENCE.rst`） |

## 二、以「可选项」方式取用的组件

### PySide6 / PySide6-Essentials / shiboken6 6.11.0

上游 METADATA 的许可证字段是 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`，
即三选一。本项目**选择 GPL-3.0 这条路**，理由是整件作品已经因为 qfluentwidgets
而按 GPLv3 分发，走 GPL 选项可以顺手免掉 LGPL 那条路上的「允许用户重新链接
Qt 库」义务（PyInstaller 打进 exe 的 Qt DLL 不需要单独可替换）。

所以 Qt 这边**不再单独放许可证文件**：wheel 里附带的那份
`LicenseRef-Qt-Commercial.txt` 讲的是商业授权，跟我们取用的许可证无关，
收进来只会误导。GPL-3.0 全文就是仓库根的 `LICENSE`。

### PyInstaller（仅构建期，不随产物分发）

GPL-2.0-or-later 配 Apache-2.0 例外条款（"Distribution Terms" 明确写明：用
PyInstaller 打包出来的产物不受 GPL 约束）。因此它只出现在开发/打包环节，
不在运行时依赖清单里。

## 三、非 Python 的随附资产

| 资产 | 许可证 | 原文位置 | 说明 |
| --- | --- | --- | --- |
| `frpc.exe` | Apache-2.0 | `frp-dev/LICENSE` | frp 官方 Go 源码构建出的客户端二进制 |
| `vendor/echarts.min.js` | Apache-2.0 | 文件头部 ASF 声明 | 管理面板图表 |
| `web/vue-pure-admin/` | MIT | `web/vue-pure-admin/LICENSE` | Web 管理端脚手架 |

## 四、待补原文

- **trafilatura**：打包环境里没有这个包（AGENTS.md §5.1 记录的解释器分裂现状），
  因此无 `*.dist-info` 可复制。补原文请从上游取
  `https://github.com/adbar/trafilatura/blob/master/LICENSE.txt`，
  落到 `licenses/trafilatura/`。**不要凭记忆转录**，许可证名称也以上游文件为准。

## 五、维护约定

1. 新增依赖要放许可证时，从 `site-packages/<包>-<版本>.dist-info/` 里的 LICENSE
   文件**逐字复制**，保持上游原文件名的大小写（openpyxl 就是 `LICENCE.rst`），
   不改一个字。第一、二节的许可证标识与版本号由
   `tools/probe/probe_license_meta.py` 从各包 METADATA 读出，改完依赖重跑它核对。
2. dist-info 里没有原文时（只有 SPDX 字段），宁可像第四那样标「待补」，
   也不要手写一份"看起来差不多"的授权文本。
3. 本表第一列的名称与 `requirements.txt` 对齐；改了依赖就要同步本文件与
   根目录 `NOTICE`。
