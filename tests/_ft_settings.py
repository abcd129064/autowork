# -*- coding: utf-8 -*-
import json
d = json.load(open(r"C:/Users/shen_zhe/Desktop/autowork/settings.json", encoding="utf-8"))
print("classic_layout =", d.get("classic_layout"))
print("dpi_scale =", d.get("dpi_scale"))
