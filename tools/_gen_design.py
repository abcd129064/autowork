# -*- coding: utf-8 -*-
"""生成售后总览图表设计稿 HTML（真实生产数据内嵌）"""
import json

charts = json.load(open(r'tools/_design_charts.json', encoding='utf-8'))
records = json.load(open(r'tools/_design_records.json', encoding='utf-8'))

payload = {
    "daily": charts.get("daily") or [],
    "issue_types": charts.get("issue_type_dist") or [],
    "regions": charts.get("region_dist") or [],
    "stats": records.get("stats") or {}
}
data_js = json.dumps(payload, ensure_ascii=False)

html = open(r'tools/_design_template.html', encoding='utf-8').read()
html = html.replace("__DATA__", data_js)
out = r"design/aftersale_dashboard_design.html"
open(out, "w", encoding="utf-8").write(html)
print("written:", out, len(html), "bytes")
