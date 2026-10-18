# -*- coding: utf-8 -*-
"""workers 包惰性导出回归：导入无关子模块不应连带加载 network_workers。"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_python(code):
    """在仓库根启动子进程，避免当前测试进程已缓存的 workers 包干扰。"""
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_importing_table_worker_does_not_load_network_workers():
    proc = _run_python(
        "import sys; import workers.table_worker; "
        "print('workers.network_workers' in sys.modules)"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False"


def test_package_level_exports_still_work():
    from workers import DevicesFetchWorker, TableFetchWorker

    assert TableFetchWorker.__name__ == "TableFetchWorker"
    assert DevicesFetchWorker.__name__ == "DevicesFetchWorker"


def test_unknown_package_attribute_raises_attribute_error():
    import workers

    with pytest.raises(AttributeError):
        workers.NotAWorker
