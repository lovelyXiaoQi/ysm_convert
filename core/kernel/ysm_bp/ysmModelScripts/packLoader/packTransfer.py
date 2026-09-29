# -*- coding: utf-8 -*-
"""服务端 → 客户端的模型包数据下发: 编码、分段、校验与重组(纯逻辑, 双端共用)。

载荷(见 serverCollector)→ JSON(ensure_ascii, 全 ASCII)→ zlib → base64, 得到纯 ASCII 的 str: 与解释器默认编码
无关(专用服务器 / 租赁服是 ascii, 普通客户端与本机开服是 utf-8), 经事件传输也不会被转码。
整份按 SEGMENT_SIZE 分段, 每段带 token / 序号 / 段数 / 校验和; 客户端按序号收齐、校验和对上才解码, 任何一段对不上
整份丢掉重来。分段大小与每 tick 段数照抄机械动力蓝图的正文分段通道(已在正式服、手机、联机下用过这组参数)。
"""
import base64
import json
import zlib

FORMAT_VERSION = 1
SEGMENT_SIZE = 16000
SEGMENTS_PER_TICK = 8


def _Ascii(text):
    """base64 串在事件里可能被转成 unicode: 统一回 ASCII str"""
    if isinstance(text, unicode):  # noqa: F821 — py2 运行时
        return text.encode("ascii")
    return text


def EncodePayload(payload):
    """载荷 dict → 纯 ASCII 的 str(文本字段可以是 unicode 或 UTF-8 str)"""
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return base64.b64encode(zlib.compress(raw, 6))


def DecodePayload(text):
    """EncodePayload 的逆; 坏数据统一抛 ValueError"""
    try:
        return json.loads(zlib.decompress(base64.b64decode(_Ascii(text))))
    except (TypeError, ValueError, zlib.error, UnicodeError) as error:
        raise ValueError("模型包数据解码失败: {!r}".format(error))


def Checksum(text):
    return zlib.crc32(_Ascii(text)) & 0xFFFFFFFF


def MakeToken(text):
    """同一份数据的 token 恒定(重新要时客户端能沿用已收到的段)"""
    return "{:08x}-{}".format(Checksum(text), len(text))


def SplitSegments(text, size=SEGMENT_SIZE):
    """整份文本 → 段列表(至少一段)"""
    text = _Ascii(text)
    if not text:
        return [""]
    return [text[start:start + size] for start in range(0, len(text), size)]


class SegmentAssembler(object):
    """按 token 收段: 收齐且校验和对上返回整份文本。换了 token / 段数 / 校验和就作废已收的, 从头收。"""

    def __init__(self):
        self.token = None
        self.total = 0
        self.crc = None
        self.parts = {}
        self.failures = 0

    def Reset(self):
        self.token = None
        self.total = 0
        self.crc = None
        self.parts = {}

    def Progress(self):
        return len(self.parts), self.total

    def Feed(self, token, index, total, crc, segment):
        """收一段; 收齐且校验通过 → 整份文本, 否则 None(校验失败时清空, failures 加一)"""
        try:
            index, total, crc = int(index), int(total), int(crc) & 0xFFFFFFFF
        except (TypeError, ValueError):
            return None
        token = _Ascii(token) if token is not None else None
        if token != self.token or total != self.total or crc != self.crc:
            self.Reset()
            self.token, self.total, self.crc = token, total, crc
        if total <= 0 or not 0 <= index < total or segment is None:
            return None
        self.parts[index] = _Ascii(segment)
        if len(self.parts) < total:
            return None
        text = "".join(self.parts[i] for i in range(total))
        self.Reset()
        if Checksum(text) != crc:
            self.failures += 1
            return None
        return text
