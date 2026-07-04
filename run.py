#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
Sparrow 投资助手 - 启动文件
根据操作系统自动判断环境：
  - Linux（生产环境）：带 baseUrlPath=sparrow，配合 nginx 反向代理
  - macOS/Windows（开发环境）：不带前缀，直接 localhost:5007 访问

生产环境用法：
  nohup python3 run.py > logs/streamlit.log 2>&1 &
"""
import os
import sys
import platform
from pathlib import Path

PROJECT_DIR = Path(__file__).parent


def main():
    system = platform.system().lower()

    args = [
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.port=5007",
        "--server.address=0.0.0.0",
        "--server.headless=true",
    ]

    if system == "linux":
        args.append("--server.baseUrlPath=sparrow")
        print("🐦 Sparrow 投资助手启动（生产模式）")
        print("🌐 通过 nginx 访问: https://moobc.cn/sparrow/")
    else:
        print("🐦 Sparrow 投资助手启动（开发模式）")
        print("🌐 访问地址: http://localhost:5007")

    # 用 exec 替换当前进程，不再有父子进程链
    os.chdir(str(PROJECT_DIR))
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
