# -*- coding: utf-8 -*-
"""材质注册决策的全项目唯一实现。

整合自旧代码中的 7 处复制粘贴块(ysmClientUtils x4 / ysmModelClientSystem x2 /
EntityRenderManager x1), 行为以玩家侧 6 处一致版本为准:
- material 为字符串时视为 [["default", material]];
- 当存在 default 项且任一材质名含 "nocull" 时, default 项自动切换为
  bloom_nocull / bloom_plus_nocull(按原 default 材质名推断) —— 只对旧式材质名生效,
  default 写新式预设(cutout 等)时不参与;
- 材质名 "light" 统一映射为 "tohru";
- 非法条目(非二元组)静默跳过; inherit 皮肤被 modelNormalizer 多包的一层拆回原样。

**材质预设**(作者在 ysm.json 顶级 material / skins.<皮肤名>.material 写预设名即可, 见
docs/ysm-json-pack-guide.md): 材质不只决定着色, 还是 YSM 后处理的遮罩协议 —— 帧缓冲 alpha
0.2 = 泛光(bloom_start), 0.3 = 描边(outline_post_end)。作者直接选原版材质会让开了描边的玩家
看不到这个模型的描边, 所以预设按玩家的描边开关自动换变体, 作者只管语义:

    cutout       裁切, 不剔除背面(Java entityCutoutNoCull, 丢 alpha < 0.1)  —— Java 模式包缺省
    cutout_cull  裁切, 剔除背面
    translucent  半透明混合, 不剔除背面(Java entityTranslucent), 不写帧缓冲 alpha
    emissive     全亮 + 泛光(Java 的 ysmGlow 发光组缺省用它)
    pbr          cutout + LabPBR 法线/高光贴图(皮肤带 pbr 字段时的缺省, 见 PbrTextures)
    bloom        贴图 alpha < 0.3 的像素当发光(基岩 YSM 旧约定) —— 旧版模型缺省
    bloom_nocull 同上, 不剔除背面
    translucent_cull / emissive_cull / pbr_cull  对应预设的剔除背面版

**剔除背面开关**(皮肤 / 替换实体数据的 cull 字段, 解析器取自 Java properties.all_cutout 或顶级扩展键 cull):
Java 的 all_cutout = "无论贴图情况全部剔除背面", 给负尺寸方块做描边外壳用(反向的面从外面看被剔掉, 只剩轮廓)。
缺省关时 Java 对负尺寸方块不剔除(官方酒狐 08/13/16/18/21/22 号带负尺寸方块, all_cutout 全是 false), 外观与
不剔除一致。开关打开时每个预设(含缺省回落与发光组)换成剔除背面的孪生(CULL_TWINS), 自定义材质名照原样。

非预设名(tohru / saury / 引擎或资源包里定义的任意材质名)原样注册。
"""

# 装载链路会把 JSON 字符串转成 str(ByteifyJson), 第三方经 EntityRenderManager 传进来的可能是 unicode
_STRING_TYPES = (str, type(u""))

# 预设名 → (描边关的材质, 描边开的材质); 材质定义在 ysm_rp/materials/entity.material
MATERIAL_PRESETS = {
    "cutout": ("ysm_cutout", "ysm_cutout_outline"),
    "cutout_cull": ("ysm_cutout_cull", "ysm_cutout_cull_outline"),
    "translucent": ("ysm_translucent", "ysm_translucent"),
    "translucent_cull": ("ysm_translucent_cull", "ysm_translucent_cull"),
    "emissive": ("ysm_emissive", "ysm_emissive"),
    "emissive_cull": ("ysm_emissive_cull", "ysm_emissive_cull"),
    "pbr": ("ysm_pbr", "ysm_pbr_outline"),
    "pbr_cull": ("ysm_pbr_cull", "ysm_pbr_cull_outline"),
    "bloom": ("bloom", "bloom_plus"),
    "bloom_nocull": ("bloom_nocull", "bloom_plus_nocull"),
}
# 剔除背面开关打开时预设换成的孪生(bloom 本身就剔除背面)
CULL_TWINS = {
    "cutout": "cutout_cull",
    "translucent": "translucent_cull",
    "emissive": "emissive_cull",
    "pbr": "pbr_cull",
    "bloom_nocull": "bloom",
}
# 皮肤 / 替换实体数据里的剔除背面开关(见模块注)
CULL_KEY = "cull"
# 新式预设: default 写它们(预设名或它们的具体材质名 ysm_cutout 等)时不走旧的"有 nocull 项就把 default 换成
# bloom_nocull"规则
_NEW_STYLE_PRESETS = frozenset(["cutout", "cutout_cull", "translucent", "translucent_cull", "emissive",
                                "emissive_cull", "pbr", "pbr_cull"])
_NEW_STYLE_MATERIALS = _NEW_STYLE_PRESETS | frozenset(
    name for preset in _NEW_STYLE_PRESETS for name in MATERIAL_PRESETS[preset])

JAVA_DEFAULT_PRESET = "cutout"
LEGACY_DEFAULT_PRESET = "bloom"
PBR_PRESET = "pbr"

# PBR 贴图(Java 版 {uv, normal, specular}, LabPBR 编码; 解析器把它们写成皮肤 / 替换实体数据的 pbr 字段):
# 渲染控制器 ysm_main / 第一人称手臂 / 载具的 textures 数组是 [Texture.default, Texture.ysm_pbr_n, Texture.ysm_pbr_s],
# 对应着色器的 TEXTURE_0/1/2(前向渲染只取前 3 张)。没声明的一张用中性占位图(平法线 / 无高光), 结果与 cutout 一致
PBR_NORMAL_TEXTURE_KEY = "ysm_pbr_n"
PBR_SPECULAR_TEXTURE_KEY = "ysm_pbr_s"
PBR_FLAT_NORMAL_TEXTURE = "textures/ysm/pbr_flat_n"
PBR_NO_SPECULAR_TEXTURE = "textures/ysm/pbr_none_s"

# 渲染控制器 ysm_main / 第一人称手臂 / 载具按骨骼名通配 {"ysmGlow*": "Material.ysm_glow"} 引用的材质键:
# Java 版骨骼名以 ysmGlow 开头的是发光组。作者可在 material 列表里写 ["ysm_glow", 预设] 改掉它
GLOW_MATERIAL_KEY = "ysm_glow"
GLOW_DEFAULT_PRESET = "emissive"


def ResolvePreset(name, outlineEnabled=False, cull=False):
    """预设名 → 具体材质名(按描边开关取变体); cull = 剔除背面开关, 先换成剔除背面的孪生。非预设名原样返回"""
    if not isinstance(name, _STRING_TYPES):
        return name
    if cull:
        name = CULL_TWINS.get(name, name)
    variants = MATERIAL_PRESETS.get(name)
    if variants is None:
        return name
    return variants[1] if outlineEnabled else variants[0]


def _MaterialEntries(material):
    """material 列表 → [(键, 材质名), ...], 非法条目(非二元 / 非字符串)静默跳过。

    modelNormalizer 的包裹怪癖(刻意保留, 见其模块注): inherit 皮肤继承到的二维 material 会被再包一层
    [["default", [键, 名]], ...] —— 名字本身是二元组时拆回作者原意。旧实现遇到它在 set.add 上抛
    TypeError(作者写了二维 material 的模型, 继承皮肤整个应用失败)。
    """
    entries = []
    for mat in material:
        if not (isinstance(mat, (list, tuple)) and len(mat) == 2):
            continue
        key, name = mat[0], mat[1]
        if isinstance(name, (list, tuple)):
            if len(name) != 2:
                continue
            key, name = name[0], name[1]
        if isinstance(key, _STRING_TYPES) and isinstance(name, _STRING_TYPES):
            entries.append((key, name))
    return entries


def ResolveMaterials(material, outlineEnabled=False, cull=False):
    """把模型配置中的 material 字段解析为有序注册操作列表 [(key, name), ...]。

    material 为空时返回空列表, 兜底策略由调用方通过
    ResolveMaterialsWithFallback 选择。outlineEnabled / cull 只影响预设名的换算(描边变体 / 剔除背面孪生)。
    """
    if not material:
        return []
    if isinstance(material, _STRING_TYPES):
        material = [["default", material]]
    entries = _MaterialEntries(material)
    defaultNames = [name for key, name in entries if key == "default"]

    result = []
    legacyNoCull = (
        defaultNames
        and defaultNames[0] not in _NEW_STYLE_MATERIALS
        and any("nocull" in name for _key, name in entries)
    )
    if legacyNoCull:
        bloom_type = "bloom_nocull"
        for name in defaultNames:
            if "bloom_plus" in name:
                bloom_type = "bloom_plus_nocull"
                break
            elif "bloom" in name:
                break
        result.append(("default", bloom_type))
        entries = [(key, name) for key, name in entries if key != "default"]
    for key, name in entries:
        result.append((key, "tohru" if name == "light" else name))
    return [(key, ResolvePreset(name, outlineEnabled, cull)) for key, name in result]


def ResolveMaterialsWithFallback(material, outlineEnabled, preferNoCull=False, pbr=False, cull=False):
    """material 为空时按模型类别取缺省预设, 再按描边开关取变体。

    pbr: 皮肤带 PBR 贴图(HasPbr) —— 缺省换成 pbr 预设; 作者显式写了 material 时照作者的。
    cull: 剔除背面开关(Java all_cutout, 见模块注) —— 缺省回落与作者写的预设都换成剔除背面的孪生。

    preferNoCull: JSON 模型包(Java 模式)专用 —— Java 版 YSM 的模型渲染走
    RenderType.entityCutoutNoCull(geckolib3 IGeoRenderer.getRenderType), **不剔除背面**,
    作者据此建模(火焰/飘带/裙摆这类单面片从两侧都该看得见)。基岩的 entity 材质默认剔除背面,
    于是这些部件只有从正面看才显示, 侧背面透明(2026-09-04 实机: 凋灵娘骷髅头的火焰)。
    2026-09-23 起缺省 cutout 预设(裁切阈值 0.1, 与 Java 一致): 早先回退 bloom_nocull, 它把贴图
    alpha < 0.3 的像素当发光 —— Java 包里这些像素是半透明或普通像素, 基岩 YSM 的"alpha 0.2 = 发光"
    是官方 Java→网易转换工具给 ysmGlow 部件刷出来的约定; 发光组现在按骨骼名走 ysm_glow 材质键。
    旧版模型缺省仍是 bloom(行为不变)。
    Java 模式包的 material 只给别的材质键写了预设(格式手册教的 ["ysm_glow", 预设])时, 本体照样按缺省补一条
    default: 否则 default 键没注册, 本体落回玩家的原版材质(没有描边变体), PBR 缺省也丢了。旧版模型不补(行为不变)。
    """
    if pbr:
        preset = PBR_PRESET
    else:
        preset = JAVA_DEFAULT_PRESET if preferNoCull else LEGACY_DEFAULT_PRESET
    if not material:
        return [("default", ResolvePreset(preset, outlineEnabled, cull))]
    entries = ResolveMaterials(material, outlineEnabled, cull)
    if (preferNoCull or pbr) and not any(key == "default" for key, _name in entries):
        entries.append(("default", ResolvePreset(preset, outlineEnabled, cull)))
    return entries


def HasPbr(pbr):
    """皮肤 / 替换实体数据的 pbr 字段是否声明了至少一张 PBR 贴图"""
    return isinstance(pbr, dict) and bool(pbr.get("normal") or pbr.get("specular"))


def PbrTextures(pbr):
    """pbr 字段 → 两个贴图键的注册条目 [(键, 路径)]; 没声明的用中性占位图。

    渲染控制器恒引用这两个键, 没有 PBR 的模型也要注册(占位图下 pbr 着色器等同 cutout, 别的材质不采样它们)
    """
    pbr = pbr if isinstance(pbr, dict) else {}
    return [(PBR_NORMAL_TEXTURE_KEY, pbr.get("normal") or PBR_FLAT_NORMAL_TEXTURE),
            (PBR_SPECULAR_TEXTURE_KEY, pbr.get("specular") or PBR_NO_SPECULAR_TEXTURE)]


# 存档里的 ysmModelData(及服务端据此写在载具上的替换标记)是选模型那一刻的整份快照: 解析器后来加的字段(如 pbr)
# 旧存档里没有, 要等玩家重新选模型。PBR 贴图因此以注册表里当前的解析结果为准, 查不到模型/皮肤时才用快照自带的。
# modelDict = 注册表里该模型的当前配置(查不到传 None); 业务包经 dataBridge 同名包装调用。
def SkinPbrFromRegistry(modelDict, modelData):
    """玩家模型数据的当前 PBR 贴图: 按贴图路径认出注册表里的皮肤, 取它的 pbr(没有 → {}); 认不出 → 快照的 pbr"""
    texture = modelData.get("texture")
    if isinstance(modelDict, dict) and texture:
        for skinData in (modelDict.get("skin_switch") or {}).values():
            if isinstance(skinData, dict) and skinData.get("texture") == texture:
                return skinData.get("pbr") or {}
    return modelData.get("pbr")


def _RegistryReplaceEntry(modelDict, entityType):
    """注册表里该模型对这个实体类型的替换表; 查不到 → None"""
    if isinstance(modelDict, dict):
        replaceEntities = ((modelDict.get("skin_switch") or {}).get("default") or {}).get("replace_entities") or {}
        replace = replaceEntities.get(entityType)
        if isinstance(replace, dict):
            return replace
    return None


def ReplacePbrFromRegistry(modelDict, replaceData, entityType):
    """载具替换数据的当前 PBR 贴图: 取注册表里该模型对这个实体类型的替换表(没有 pbr → {}); 查不到 → 标记自带的"""
    replace = _RegistryReplaceEntry(modelDict, entityType)
    if replace is not None:
        return replace.get("pbr") or {}
    return replaceData.get("pbr")


def ReplaceMaterialFromRegistry(modelDict, replaceData, entityType):
    """载具替换数据的当前材质声明 (material, 剔除背面开关): 取注册表里该模型对这个实体类型的替换表; 查不到 → 标记自带的。
    重新转换改了材质、或标记早于剔除背面开关, 都不用等主人重选模型"""
    replace = _RegistryReplaceEntry(modelDict, entityType)
    source = replace if replace is not None else replaceData
    return source.get("material"), bool(source.get(CULL_KEY))


def ResolveModelMaterials(modelData, outlineEnabled, pbr=None, skin=None):
    """玩家模型数据(选中皮肤的数据 / ModAttr ysmModelData) → 材质注册条目(含 ysm_glow 键)。

    playerRender.SetYsmModelRes 与 settingsApply(切描边开关)共用, 两处必须同一组条目。
    pbr: 调用方按注册表取到的当前 PBR 贴图(存档里的 ysmModelData 是选模型那一刻的快照, 可能没有); 缺省读快照自带的。
    skin: 注册表里这个皮肤的当前配置(业务包 dataBridge.CurrentSkinConfig), material 与剔除背面开关以它为准 —— 理由同 pbr:
    重新转换改了材质、或是剔除背面开关这种后来加的字段, 快照里都没有; 缺省(None)读快照自带的
    """
    if pbr is None:
        pbr = modelData.get("pbr")
    source = skin if isinstance(skin, dict) else modelData
    cull = bool(source.get(CULL_KEY))
    return WithGlowMaterial(ResolveMaterialsWithFallback(
        source.get("material"), outlineEnabled, bool(modelData.get("_ysmJavaMode")), HasPbr(pbr), cull),
        outlineEnabled, cull)


def WithGlowMaterial(entries, outlineEnabled=False, cull=False):
    """补 ysmGlow* 发光组的材质键(渲染控制器引用 Material.ysm_glow, 缺了引擎找不到材质)。

    作者在 material 列表里自己声明了 ysm_glow 就用作者的; 否则补缺省发光预设(剔除背面开关打开时用它的孪生)。
    """
    if any(key == GLOW_MATERIAL_KEY for key, _name in entries):
        return list(entries)
    return list(entries) + [(GLOW_MATERIAL_KEY, ResolvePreset(GLOW_DEFAULT_PRESET, outlineEnabled, cull))]
