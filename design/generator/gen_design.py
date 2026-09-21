# -*- coding: utf-8 -*-
"""生成售后总览图表设计稿 HTML（真实生产数据内嵌）

用法：python design/generator/gen_design.py
产物：design/aftersale_dashboard_design.html（数据源与模板均在本目录）
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DESIGN = os.path.dirname(HERE)

charts = json.load(open(os.path.join(HERE, 'design_charts.json'),
                        encoding='utf-8'))
records = json.load(open(os.path.join(HERE, 'design_records.json'),
                         encoding='utf-8'))

payload = {
    "daily": charts.get("daily") or [],
    "issue_types": charts.get("issue_type_dist") or [],
    "regions": charts.get("region_dist") or [],
    "stats": records.get("stats") or {}
}
data_js = json.dumps(payload, ensure_ascii=False)

html = open(os.path.join(HERE, 'design_template.html'), encoding='utf-8').read()
html = html.replace("__DATA__", data_js)
out = os.path.join(DESIGN, "aftersale_dashboard_design.html")
open(out, "w", encoding="utf-8").write(html)
print("written:", out, len(html), "bytes")
