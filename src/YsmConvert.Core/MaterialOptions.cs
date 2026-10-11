using System.Text.RegularExpressions;

namespace YsmConvert.Core;

/// <summary>材质下拉框的一项: 值写进 ysm.json, 说明给人看。</summary>
public sealed record MaterialChoice(string Value, string Label)
{
    public override string ToString() => Value;
}

/// <summary>Java 包的材质画像(内核发现阶段 port_java_pack.DetectMaterialProfile 给出)。</summary>
public sealed class MaterialProfile
{
    /// <summary>Java properties.all_cutout: 无论贴图情况全部剔除背面, 给负尺寸方块做描边外壳用。</summary>
    public bool AllCutout { get; set; }
    /// <summary>带 PBR 贴图(normal / specular)的皮肤数。</summary>
    public int PbrSkins { get; set; }
    public int PbrVehicles { get; set; }
    /// <summary>玩家几何(main / arm)里尺寸带负分量的方块数。官方模型也常见, Java 缺省不剔除它们, 只有 all_cutout 打开才成描边外壳。</summary>
    public int NegativeCubes { get; set; }
}

/// <summary>
/// 模型材质与背面剔除的转换选项(逐包): 内核写进产物 ysm.json 的顶级 material / cull(port_java_pack.ApplyMaterialOptions)。
/// 缺省"自动"不写键, YSM 主组件按 Java 包判定 —— properties.all_cutout 打开 → 剔除背面(负尺寸描边), 皮肤带 PBR 贴图 → pbr,
/// 其余 cutout; 发光组(ysmGlow* 骨骼)缺省 emissive。背面剔除打开时这些预设一律换成剔除背面版(主组件 materialResolver.CULL_TWINS)。
/// </summary>
public static partial class MaterialOptions
{
    public const string Auto = "自动";
    public const string CullOn = "剔除";
    public const string CullOff = "不剔除";

    /// <summary>YSM 主组件的材质预设(materialResolver.MATERIAL_PRESETS); 剔除背面版通常不用直接选, 用"背面剔除"列。</summary>
    public static readonly IReadOnlyList<MaterialChoice> Choices =
    [
        new(Auto, "读 Java 包: all_cutout → 剔除背面, 带 PBR 贴图 → pbr, 其余 cutout"),
        new("cutout", "裁切: 按贴图透明裁掉(alpha < 0.1), 不剔除背面 —— Java 缺省"),
        new("translucent", "半透明: 按贴图 alpha 混合"),
        new("emissive", "全亮发光: 不受光照, 夜里也亮, 带泛光"),
        new("pbr", "PBR: 裁切 + LabPBR 法线 / 高光贴图"),
        new("bloom", "旧版泛光: 贴图 alpha < 0.3 的像素当发光(旧版基岩 YSM 约定)"),
        new("cutout_cull", "裁切 + 剔除背面(负尺寸描边)"),
        new("translucent_cull", "半透明 + 剔除背面"),
        new("emissive_cull", "全亮发光 + 剔除背面"),
        new("pbr_cull", "PBR + 剔除背面"),
    ];

    public static readonly IReadOnlyList<string> CullChoices = [Auto, CullOn, CullOff];

    private static readonly HashSet<string> PresetNames = new(
        ["cutout", "cutout_cull", "translucent", "translucent_cull", "emissive", "emissive_cull", "pbr", "pbr_cull",
         "bloom", "bloom_nocull"], StringComparer.Ordinal);

    private static readonly HashSet<string> AutoWords = new(["", "auto", Auto], StringComparer.OrdinalIgnoreCase);

    [GeneratedRegex("^[A-Za-z0-9_.]+$")]
    private static partial Regex NamePattern();

    [GeneratedRegex("[,，;；\r\n]+")]
    private static partial Regex PairSeparator();

    /// <summary>界面 / 命令行写的材质 → 交给内核的文本; "自动"与空白返回 null。</summary>
    public static string? Normalize(string? text)
    {
        var trimmed = text?.Trim() ?? "";
        return AutoWords.Contains(trimmed) ? null : trimmed;
    }

    /// <summary>材质写法的问题(与内核 port_java_pack.NormalizeMaterialOption 同一规则), 没问题返回 null。
    /// 写法: 预设名 / 材质名(英文字母、数字、_ .), 或 "键=材质, 键=材质" 按材质键分别指定(default = 模型本体, ysm_glow = 发光组)。</summary>
    public static string? Problem(string? text)
    {
        var value = Normalize(text);
        if (value is null) return null;
        if (!value.Contains('='))
            return NamePattern().IsMatch(value) ? null : $"材质名只能用英文字母、数字、_ 和 .: {value}";
        var keys = new HashSet<string>(StringComparer.Ordinal);
        foreach (var part in PairSeparator().Split(value))
        {
            if (part.Trim().Length == 0) continue;
            var eq = part.IndexOf('=');
            if (eq < 0) return $"按材质键指定要写成 键=材质: {part.Trim()}";
            var key = part[..eq].Trim();
            var name = part[(eq + 1)..].Trim();
            foreach (var piece in new[] { key, name })
                if (!NamePattern().IsMatch(piece)) return $"材质键 / 材质名只能用英文字母、数字、_ 和 .: {piece}";
            if (!keys.Add(key)) return $"材质键重复: {key}";
        }
        return keys.Count == 0 ? "材质为空" : null;
    }

    /// <summary>写的材质里有没有不是 YSM 预设的名字(自定义材质: 要在资源包 materials 里有定义, 不参与描边 / 泛光)。</summary>
    public static bool IsCustom(string? text)
    {
        var value = Normalize(text);
        if (value is null || Problem(value) is not null) return false;
        var names = value.Contains('=')
            ? PairSeparator().Split(value).Where(p => p.Trim().Length > 0).Select(p => p[(p.IndexOf('=') + 1)..].Trim())
            : new[] { value };
        return names.Any(name => !PresetNames.Contains(name));
    }

    /// <summary>"自动 / 剔除 / 不剔除"(也认 auto / on / off) → null / true / false; 认不出抛 ArgumentException。</summary>
    public static bool? ParseCull(string? text)
    {
        var value = text?.Trim() ?? "";
        if (AutoWords.Contains(value)) return null;
        return value.ToLowerInvariant() switch
        {
            CullOn or "on" or "true" or "1" or "yes" or "cull" => true,
            CullOff or "off" or "false" or "0" or "no" or "nocull" => false,
            _ => throw new ArgumentException($"背面剔除只认 auto / on / off(自动 / 剔除 / 不剔除): {text}"),
        };
    }

    public static string CullText(bool? cull) => cull switch { true => CullOn, false => CullOff, null => Auto };

    /// <summary>"自动"会选什么(给界面的概况 / 提示): 普通 / PBR / 负尺寸描边。</summary>
    public static string DescribeAuto(MaterialProfile? profile)
    {
        if (profile is null) return "自动";
        var kind = profile.AllCutout ? "负尺寸描边·剔除背面" : "普通";
        if (profile.PbrSkins > 0) kind = "PBR · " + kind;
        return kind;
    }

    /// <summary>负尺寸方块的提醒文字(没有负尺寸方块时 null)。</summary>
    public static string? NegativeCubeHint(MaterialProfile? profile, bool? cull)
    {
        if (profile is not { NegativeCubes: > 0 }) return null;
        var culled = cull ?? profile.AllCutout;
        return culled
            ? $"几何有负尺寸方块 {profile.NegativeCubes} 个, 背面剔除打开: 它们从外面看只剩背面(描边外壳)"
            : $"几何有负尺寸方块 {profile.NegativeCubes} 个, 不剔除背面: 按普通方块显示(与 Java 缺省一致); 它们是描边外壳的话把“背面剔除”设为剔除";
    }

    /// <summary>整批校验材质写法, 返回问题列表(空 = 通过)。</summary>
    public static List<string> Validate(IEnumerable<PackSpec> packs)
    {
        var problems = new List<string>();
        foreach (var p in packs)
            if (Problem(p.Material) is { } problem)
                problems.Add($"{p.Name}: {problem}");
        return problems;
    }
}
