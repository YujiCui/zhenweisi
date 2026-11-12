"""浮窗的入口：pythonw assistant.pyw --home <助手的文件夹>

一般不用手动运行：生成器里点「启动助手」，或者双击桌面快捷方式。
"""
import sys

if sys.version_info < (3, 9):
    sys.exit("Python 3.9+ is required, found " + sys.version.split()[0])

from zws.widget import main

if __name__ == "__main__":
    main()
