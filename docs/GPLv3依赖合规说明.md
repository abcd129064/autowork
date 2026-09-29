# GPLv3 依赖合规说明

> 状态：规范（长期有效）。改了 `requirements.txt` 或新增随包分发的资产时，按本文件的
> 判定口径复核，并同步 `licenses/INDEX.md` 与根目录 `NOTICE`。
> 落地的文件：`LICENSE`（GPLv3 全文）、`NOTICE`、`licenses/`（各组件原文 + 索引）、
> `README.md`「许可证与第三方授权」段、`build_exe.py` 的随包复制、
> 主窗口「帮助 → 关于」弹窗。

## 一、一句话结论

界面组件库 `PySide6-Fluent-Widgets`（qfluentwidgets）是 GPLv3，它跟本程序作为一个整体
分发，所以整件作品必须以 GPLv3 授权下游。**不需要买商业授权**，也**不需要换掉这个库**
—— 需要做的只是把授权声明、源码可得性和第三方许可证原文补齐，本文件记录的就是这件事。

## 二、义务到底由什么触发：conveying，不是「商用」

常见的误解是「GPLv3 = 非商用项目必须开源」。这条不准确，两句话纠正：

1. **GPLv3 允许商用**。第 10 条反而禁止你附加「不得商用」这类限制。
2. 义务的真正触发点是 **conveying**（§0：把程序交给另一个法律主体 —— 分发、销售、
   放到公开仓库都算；公司内部同一法律主体之间复制不算）。

qfluentwidgets 作者对「商用」另卖的是 **QFluentWidgets Pro**（附加功能/商业支持），
那是独立产品线的定价策略，不是免费版的许可证条款。免费版 METADATA 写的就是 GPLv3。
所以「因为要商用所以必须买授权」不成立；反过来，「因为要 convey，所以必须按 GPLv3
带源码」是成立的。

## 三、本项目的三个分发面逐一判定

| 分发面 | 事实 | 是否 conveying | 后果 |
| --- | --- | --- | --- |
| GitHub 公开仓库 `abcd129064/autowork` | 源码公开可拉 | **是**（推送到公开仓库这一刻就已经发生） | 必须附许可证与声明；对应源码天然满足 |
| 桌面 exe（`dist/AutoWork/`、`aftersale.exe`） | 只在本公司同一法律主体的设备上用，球房为自营 | 否 | §6/§7 的对象交付义务不触发；exe 里照样带上声明（见第五节） |
| 售后面板 Web 端 | 仅内部访问 | **否** | GPLv3 是 plain GPL，**没有网络服务条款**（那是 AGPLv3 §13）。服务端自用不产生源码交付义务 |

判定要点：**公开仓库是本项目义务的实际来源**。既然已经选择公开并保持公开，剩下的活
就是把声明做齐全，而不是想办法回避。

## 四、已经落地的动作（清单即验收标准）

1. `LICENSE` —— GPLv3 全文，逐字取自 `pyside6_fluent_widgets-1.11.2.dist-info/LICENSE`，
   即 FSF 标准文本，没有附加条款。
2. `NOTICE` —— FSF 推荐的标准授权声明（英文原文 + 中文译文）、完整对应源码的 Offer、
   第三方组件要点清单、以及「内部使用与对外分发是两回事」这一条口径记录。
3. `licenses/` —— 17 个 Python 依赖的许可证原文（逐字复制，未改写）+
   `licenses/INDEX.md` 对照表；缺失原文的按「待补」处理（第四节）。
4. `README.md`「许可证与第三方授权」段 —— 面向读者的授权声明与源码指引。
5. 打包产物随附声明 —— `build_exe.py` 在构建收尾把 `LICENSE`、`NOTICE`、`licenses/`
   复制到两份产物目录（exe 旁边），装了程序就能看到授权。没走 `AutoWork.spec` 的
   `datas`，因为 onedir 下 datas 会落进 `_internal/`，不贴在 exe 旁边不好找。
6. 关于弹窗 —— `main_window/ui_mixin.py` 的 `AboutDialog` 里给出许可证链接与
   qfluentwidgets 的 GPLv3 归属；改动后用离屏冒烟复验：
   `QT_QPA_PLATFORM=offscreen C:\Users\shen_zhe\miniconda3\python.exe tools/smoke/smoke_about_license.py`
   （要求 rc=0，它同时校验弹窗文案与「不得残留旧库名 PyQt-Fluent-Widgets」）。

原始作者授权：本程序源自石睿轩的早期创作，作者已授权重写与再分发；`README.md` 与
`NOTICE` 保留其署名。

## 五、以后改这些东西时必须保持的约束

- **§10：不得给下游附加限制**。不能再加「禁止商用」「禁止再分发」「必须事先联系作者」
  这类条款，README/NOTICE 的措辞也不要出现等价表述。
- **§6：完整对应源码**。继续公开仓库即可满足。注意 §6(d) 只放宽网络分发场景下的
  **二进制**配对要求，源码 Offer 不能因此省掉 —— 我们的做法是把 `LICENSE`/`NOTICE`/
  `licenses/` 直接放进产物目录。
- **§11 聚合（aggregation）**：exe 旁边的非 GPL 资产（`frpc.exe`、图表 JS、配置、数据）
  不会因为「装在同一个目录」而被传染，保持独立许可即可。
- **不要往 `LICENSE` 里加自造条款**。它是逐字的标准文本，改一个字就偏离上游授权。
- **新增依赖的判据**：Apache / MIT / BSD 系都可以安全合并进 GPLv3 作品；
  要避开 GPL-2.0-only、AGPL、SSPL 这类与 GPLv3 不兼容的许可证（AGPL 组件会让整件
  作品变成 AGPL 义务）。`licenses/INDEX.md` 第五节写了原文怎么取。
- **Qt 的三重许可**（`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`）：本项目声明取
  GPL-3.0 这一支，跟整件作品的授权一致，好处是免掉 LGPL 那条路上的重链接要求。哪天
  要改走 LGPL 路线，必须重新评估 qfluentwidgets 的 GPL 约束，不能只改 Qt 的说法。

## 六、风险与待办

| 项 | 现状 | 处置 |
| --- | --- | --- |
| `licenses/` 缺 trafilatura 原文 | 打包环境未安装该包，无 dist-info 可复制 | 从上游取原文补进 `licenses/trafilatura/`，**不要凭记忆转录** |
| 逐文件 SPDX 头 | 全仓 Python 文件没有逐个版权/许可证头 | GPLv3 不强制；若要加，属独立机械任务，需单独规划与验证 |
| 自动更新分发链路 | 若将来把整包经 `tools/deploy/publish_update.py` 发给**外部**主体（非本公司），即构成 conveying | 那时必须同时给外部接收方可得的完整对应源码；第五节的约束同样适用 |
| 仓库里已入库的 `config/` 密文、业务数据 | AGENTS.md §7 既有待决项 | 与许可证无关，但公开仓库里放 DPAPI 密文与生产 IP 是独立风险，建议一并拍板 |

## 七、这份说明的依据

- `LICENSE`（本仓库根，FSF GPLv3 全文，674 行）
- 各依赖 `site-packages/<pkg>-<ver>.dist-info/METADATA` 的 `License` / `License-Expression`
  字段（实测版本与标识见 `licenses/INDEX.md` 表格）
- `frp-dev/LICENSE`（Apache-2.0）、`web/vue-pure-admin/LICENSE`（MIT）、
  `vendor/echarts.min.js` 头部 ASF 声明
- qfluentwidgets 上游仓库与 PyPI 页面的许可标注（免费版 GPL-3.0，Pro 为另售商业版）
