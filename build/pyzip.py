# -*- coding: utf-8 -*-
"""(由 make-python.ps1 用本机 Python 2.7 调用) 把标准库与 pypinyin / enum34 预编译成 .pyc, 打进 python27.zip。

    python pyzip.py <Python 2.7 安装目录> <输出 zip>

- 只放 .pyc(不放 .py): zipimport 对只有 .pyc 的条目不比对源码时间戳, 解压到哪台机器上都直接可用; 内核进程启动时
  不用再现场编译标准库与 2MB 的拼音词典(实测 --version 从约 1.2 秒降到约 0.4 秒)。
- 剔除的目录与旧版 make-python.ps1 相同(测试 / GUI / 开发工具等内核用不到的大块), 模块集合不变。
- pypinyin 去掉没有任何模块导入的 phrases_dict_large(2.3MB)、命令行入口与类型存根。
- 条目按路径排序、时间戳固定, 同一份输入每次生成的 zip 逐字节相同。
"""
from __future__ import print_function

import imp
import marshal
import os
import struct
import sys
import zipfile

home = os.path.abspath(sys.argv[1])
target = os.path.abspath(sys.argv[2])
lib = os.path.join(home, "Lib")
site_packages = os.path.join(lib, "site-packages")

SKIP_DIRS = [
    "test", "site-packages", "lib-tk", "idlelib", "lib2to3", "ensurepip", "distutils", "bsddb", "sqlite3",
    "multiprocessing", "pydoc_data", "curses", "wsgiref", "hotshot", "compiler", "msilib", "lib-old",
    os.path.join("unittest", "test"), os.path.join("ctypes", "test"), os.path.join("email", "test"),
    os.path.join("json", "tests"),
]
PACKAGES = ["pypinyin", "enum"]
PACKAGE_SKIP = {
    os.path.join("pypinyin", "phrases_dict_large.py"),
    os.path.join("pypinyin", "runner.py"),
    os.path.join("pypinyin", "__main__.py"),
}
FIXED_DATE = (2020, 1, 1, 0, 0, 0)


def skipped(rel):
    rel = os.path.normcase(rel)
    for s in SKIP_DIRS:
        s = os.path.normcase(s)
        if rel == s or rel.startswith(s + os.sep):
            return True
    return False


def collect():
    entries = []
    for dirpath, dirnames, filenames in os.walk(lib):
        rel_dir = os.path.relpath(dirpath, lib)
        if rel_dir != "." and skipped(rel_dir):
            dirnames[:] = []
            continue
        for name in filenames:
            if name.endswith(".py"):
                rel = os.path.normpath(os.path.join(rel_dir, name))
                entries.append((os.path.join(dirpath, name), rel))
    for package in PACKAGES:
        root = os.path.join(site_packages, package)
        if not os.path.isdir(root):
            raise SystemExit("本机 Python 2.7 没装 %s (pip install %s)" % (package, package))
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.normpath(os.path.relpath(path, site_packages))
                if rel in PACKAGE_SKIP:
                    continue
                entries.append((path, rel))
    entries.sort(key=lambda e: e[1].lower())
    return entries


def compile_pyc(path, rel):
    with open(path, "rU") as f:
        source = f.read()
    if source and source[-1] not in "\r\n":
        source += "\n"
    # 回溯里显示相对路径, 不带构建机的绝对路径; dont_inherit: 不继承本脚本的 print_function 等 __future__ 开关
    code = compile(source, rel, "exec", 0, True)
    mtime = int(os.stat(path).st_mtime)
    return imp.get_magic() + struct.pack("<I", mtime) + marshal.dumps(code)


def main():
    entries = collect()
    if os.path.exists(target):
        os.remove(target)
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in entries:
            info = zipfile.ZipInfo(rel.replace(os.sep, "/") + "c", FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, compile_pyc(path, rel))
            count += 1
    print("python27.zip: %d 个模块, %.1f MB" % (count, os.path.getsize(target) / 1048576.0))


if __name__ == "__main__":
    main()
