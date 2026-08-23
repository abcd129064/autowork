# -*- coding: utf-8 -*-
import faulthandler
faulthandler.enable()
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
print("A1", flush=True)
from qfluentwidgets import PushButton, FluentIcon
print("A2", flush=True)
import inspect
print("A3", flush=True)
sig = inspect.signature(PushButton.__init__)
print("SIG:", sig, flush=True)
b = PushButton(FluentIcon.DOWNLOAD, "x")
print("OK", b.text(), flush=True)
