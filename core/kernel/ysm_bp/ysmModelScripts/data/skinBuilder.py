# -*- coding: utf-8 -*-
"""皮肤资源组合器: 共享基线 + 模型差异声明 → 完整注册列表。

替代旧 modConfig 中每个模型内联复制 ~100 条共享动画的做法。
"""


def BuildEntries(baseEntries, overrideEntries):
    """按 key 合并 (key, value) 注册条目。

    - 基线条目按原顺序在前;
    - overrideEntries 中与基线同 key 的条目原位覆盖其 value;
    - 新 key 按声明顺序追加在后。
    每次调用返回全新列表, 各模型互不共享可变对象。
    """
    result = []
    indexByKey = {}
    for entry in baseEntries:
        indexByKey[entry[0]] = len(result)
        result.append((entry[0], entry[1]))
    for entry in overrideEntries:
        key = entry[0]
        if key in indexByKey:
            result[indexByKey[key]] = (key, entry[1])
        else:
            indexByKey[key] = len(result)
            result.append((key, entry[1]))
    return result
