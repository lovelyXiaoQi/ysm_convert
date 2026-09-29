# -*- coding: utf-8 -*-
"""预编资源索引: 把资源包里运行期要查的信息写进行为包 ysm_models/_rp_index/<资源包 uuid>.json, 随行为包发布。

为什么: 正式服 / 手机上的组件文件除 manifest.json 外都加密落盘, 运行期扫不了资源包(联机时加入者本机也未必有资源包
目录); 服务端经 server_resource 能读行为包, 读到这份索引后连同模型包一起下发给客户端(ysmModelScripts/packLoader:
serverCollector → clientScanner → resourceIndex.LoadPrecompiled)。内容(动画 / 控制器 ID、文件归属、molang 概况、时长、
几何事实)与运行期扫描同一套读法(resourceIndex.BuildPackIndex), 从资源包**源文件**生成: 打包脚本把动画文件合并成
_merged_animations_NN.json 之后, ysm.json 里按原路径写的声明只能靠这份索引认。
文件名取资源包 manifest 的 header uuid: 服务端看到的是全部行为包合在一起的目录, 各组件的索引不能重名。

谁来调:
- 打包脚本 scripts/pack_scripts.py(RP_INDEX_CONFIGS): 写进 publish 产物, 本工程源码行为包里不放(开发测试靠运行期扫描兜底;
  放了又不跟着资源包改动重新生成, 会比资源包旧);
- 转换器 port_cli(convert / finalize / fix): 写进转换产物的行为包, 第三方组件自带。

用法(Python 2.7):
    python build_rp_index.py <资源包目录> <行为包目录>           # 生成并写入
    python build_rp_index.py <资源包目录> <行为包目录> --check   # 只核对: 缺失或与资源包不一致时退出码 1
"""
from __future__ import print_function

import io
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ysm_bp"))

from ysmModelScripts.packLoader import resourceIndex  # noqa: E402
from ysmModelScripts.packLoader.serverCollector import RP_INDEX_DIR  # noqa: E402

MODELS_DIR = "ysm_models"


def IndexName(rpDir):
    """索引文件名(不带扩展名): 资源包 manifest 的 header uuid; 读不到 manifest 退回资源包目录名"""
    uuid = None
    try:
        with io.open(os.path.join(rpDir, "manifest.json"), encoding="utf-8-sig") as handle:
            uuid = (json.load(handle).get("header") or {}).get("uuid")
    except (IOError, OSError, ValueError, AttributeError):
        uuid = None
    name = uuid or os.path.basename(os.path.normpath(rpDir))
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def IndexDir(bpDir):
    return os.path.join(bpDir, MODELS_DIR, RP_INDEX_DIR)


def IndexPath(rpDir, bpDir):
    return os.path.join(IndexDir(bpDir), IndexName(rpDir) + ".json")


def BuildDocument(rpDir):
    """资源包目录 → 预编索引文档(OrderedDict, 与运行期扫描同一套读法)"""
    return resourceIndex.BuildPackIndex(rpDir)


def _Dump(document):
    text = json.dumps(document, ensure_ascii=True, separators=(",", ":"))
    return text.decode("ascii") if isinstance(text, str) else text


def WriteIndex(rpDir, bpDir, replaceAll=False):
    """生成并写入; replaceAll 时先清空索引目录(发布产物里别留源码带进来的旧索引)。返回 (路径, 摘要 dict)"""
    document = BuildDocument(rpDir)
    folder = IndexDir(bpDir)
    if replaceAll and os.path.isdir(folder):
        shutil.rmtree(folder)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = IndexPath(rpDir, bpDir)
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(_Dump(document))
    summary = {
        "animations": len(document["anims"]),
        "controllers": len(document["controllers"]),
        "files": len(document["fileAnims"]) + len(document["fileControllers"]),
        "bytes": os.path.getsize(path),
    }
    return path, summary


def CheckIndex(rpDir, bpDir):
    """现有索引与资源包是否一致 → (一致?, 说明)"""
    path = IndexPath(rpDir, bpDir)
    if not os.path.isfile(path):
        return False, u"缺少预编资源索引: {}".format(path)
    with io.open(path, encoding="utf-8-sig") as handle:
        existing = json.load(handle)
    fresh = json.loads(_Dump(BuildDocument(rpDir)))
    if existing != fresh:
        return False, u"预编资源索引与资源包不一致(资源包改过之后没重新生成): {}".format(path)
    return True, path


def _Print(text):
    """输出重定向到管道时 py2 按 ascii 编码 unicode 会抛错: 先按控制台编码(没有就 utf-8)转成字节"""
    if not isinstance(text, str):
        text = text.encode(getattr(sys.stdout, "encoding", None) or "utf-8", "replace")
    print(text)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 2
    rpDir, bpDir = args
    if "--check" in argv:
        ok, message = CheckIndex(rpDir, bpDir)
        _Print((u"[OK] " if ok else u"[FAIL] ") + message)
        return 0 if ok else 1
    path, summary = WriteIndex(rpDir, bpDir)
    _Print(u"[OK] {}: 动画 {animations} 个, 控制器 {controllers} 个, 文件 {files} 个, {bytes} 字节".format(
        path.decode("utf-8") if isinstance(path, str) else path, **summary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
