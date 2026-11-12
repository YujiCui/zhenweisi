#!/usr/bin/env python3
"""真维斯生成器的入口：python studio.py

在浏览器里填需求，生成你自己的桌面助手。只用 Python 标准库，不需要 pip install。
"""
import sys

if sys.version_info < (3, 9):
    sys.exit("Python 3.9+ is required, found " + sys.version.split()[0])

from zws.server import main

if __name__ == "__main__":
    main()
