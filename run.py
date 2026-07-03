#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
Sparrow 投资助手 - 启动文件
根据操作系统自动判断环境：
  - Linux（生产环境）：带 baseUrlPath=sparrow，配合 nginx 反向代理
  - macOS/Windows（开发环境）：不带前缀，直接 localhost:5007 访问
"""
import sys
import platform
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).parent


def main():
    system = platform.system().lower()
    
    cmd = [
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.port=5007",
        "--server.address=0.0.0.0",
        "--server.headless=true",
    ]
    
    if system == "linux":
        # 生产环境：配合 nginx 的 /sparrow/ 代理路径
        cmd.append("--server.baseUrlPath=sparrow")
        print("🐦 Sparrow 投资助手启动（生产模式）")
        print("🌐 通过 nginx 访问: https://moobc.cn/sparrow/")
    else:
        # 开发环境：直接访问，无前缀
        print("🐦 Sparrow 投资助手启动（开发模式）")
        print("🌐 访问地址: http://localhost:5007")
    
    subprocess.run(cmd, cwd=str(PROJECT_DIR))


if __name__ == "__main__":
    main()
