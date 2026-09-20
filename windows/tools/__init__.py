# -*- coding: utf-8 -*-
"""windows.tools 包：工具页（ToolHub）各工作区的功能后端

收纳「工具」页 4 个工作区里真正干活的功能模块，UI 只在 main_window/tool_hub.py
与 main_window/ui_mixin.py 里做入口，业务逻辑都在本包：
    single_shot_video / single_video_tool —— 单杆视频（json 生成 + 逐帧渲染）
    port_fake                             —— 端口占用（真实 bind+listen 模拟服务）
    newlog                                —— 视频/日志批量整理（Excel 驱动）

注意：本包与仓库根的 tools/（开发/运维脚本）无关，这里全是运行时功能代码。
"""
