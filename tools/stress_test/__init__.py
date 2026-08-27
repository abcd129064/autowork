# -*- coding: utf-8 -*-
"""售后系统规模化压力测试套件

设计要点见 tools/stress_test/README.md（本套件文档）：
- 数据：内存级模拟（sqlite3 :memory: + 生成器分批装载），不落盘、不新建物理库
- 规模：1 万 / 5 万 / 10 万条三档
- 场景：售后工单 / 运维面板 / 视频业务
- 指标：响应时间分位数、QPS/TPS、RSS 与 CPU 占用、资源趋势
- 约束：RSS 护栏（默认 500MB）+ 分批生成 + 每场景回收，避免 1GB 内存耗尽

入口：
    python tools/stress_test/run_stress.py --scale 10k --scenarios aftersale
"""
