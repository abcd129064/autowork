"""PyInstaller 打包包装脚本 - 将 stderr 重定向到 stdout 以避免 PowerShell NativeCommandError"""
import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

result = subprocess.run(
    [sys.executable, "-m", "PyInstaller",
     "--noconfirm", "--windowed", "--onedir",
     "--name", "AutoWork", "main.py"],
    stderr=subprocess.STDOUT
)
sys.exit(result.returncode)
