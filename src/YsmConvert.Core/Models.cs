using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace YsmConvert.Core;

/// <summary>产物落点: 组件根目录下的行为包与资源包。</summary>
public sealed record OutputLayout(string Root, string BpDir, string RpDir)
{
    public string BpModels => Path.Combine(BpDir, "ysm_models");

    /// <summary>资源包里已经有 java_default 基线(YSM 主组件工程形态)。</summary>
    public bool HasBaseline => Directory.Exists(Path.Combine(RpDir, "animations", "java_default"));
}

/// <summary>一个待移植的 Java 包。</summary>
public sealed class PackSpec
{
    public required string JavaDir { get; init; }
    public required string Name { get; set; }
    /// <summary>合集目录名(ysm_models/&lt;合集&gt;/&lt;包名&gt;); null = 平铺。</summary>
    public string? Collection { get; set; }
}

/// <summary>合集(模型选择界面里的文件夹)的显示信息 → ysm-pack.json。</summary>
public sealed class CollectionSpec
{
    public required string Dir { get; init; }
    public string? Name { get; set; }
    public string? Description { get; set; }
    public string? NameZh { get; set; }
    public string? DescriptionZh { get; set; }

    public JsonObject ToManifest()
    {
        var obj = new JsonObject();
        if (!string.IsNullOrWhiteSpace(Name)) obj["name"] = Name;
        if (!string.IsNullOrWhiteSpace(Description)) obj["description"] = Description;
        if (!string.IsNullOrWhiteSpace(NameZh) || !string.IsNullOrWhiteSpace(DescriptionZh))
        {
            var zh = new JsonObject();
            if (!string.IsNullOrWhiteSpace(NameZh)) zh["name"] = NameZh;
            if (!string.IsNullOrWhiteSpace(DescriptionZh)) zh["description"] = DescriptionZh;
            obj["lang"] = new JsonObject { ["zh_cn"] = zh };
        }
        return obj;
    }
}

public sealed class ConvertOptions
{
    /// <summary>携带第三方模组联动动画(tacz/slashblade/...), 缺省不带。</summary>
    public bool WithMods { get; set; }
    /// <summary>转换后跑资源包红线体检。</summary>
    public bool Validate { get; set; } = true;
}

/// <summary>交给 port_cli.py --job 的任务单(字段名与它的约定一一对应)。</summary>
public sealed class JobSpec
{
    public string Action { get; init; } = "convert";
    public required OutputLayout Layout { get; init; }
    public string? RefRp { get; init; }
    public List<PackSpec> Packs { get; init; } = new();
    public List<CollectionSpec> Collections { get; init; } = new();
    public ConvertOptions Options { get; init; } = new();

    public string ToJson()
    {
        var layout = new JsonObject
        {
            ["root"] = Layout.Root,
            ["rp"] = Layout.RpDir,
            ["bpModels"] = Layout.BpModels,
        };
        if (!string.IsNullOrEmpty(RefRp)) layout["refRp"] = RefRp;

        var packs = new JsonArray();
        foreach (var p in Packs)
        {
            packs.Add(new JsonObject
            {
                ["javaDir"] = p.JavaDir,
                ["name"] = p.Name,
                ["collection"] = p.Collection is null ? null : JsonValue.Create(p.Collection),
            });
        }
        var collections = new JsonObject();
        foreach (var c in Collections)
            collections[c.Dir] = c.ToManifest();

        var job = new JsonObject
        {
            ["action"] = Action,
            ["layout"] = layout,
            ["packs"] = packs,
            ["collections"] = collections,
            ["options"] = new JsonObject
            {
                ["withMods"] = Options.WithMods,
                ["validate"] = Options.Validate,
            },
        };
        return job.ToJsonString(new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
    }
}

/// <summary>port_cli.py --discover 发现的一个 Java 包。</summary>
public sealed class DiscoveredPack
{
    public string JavaDir { get; set; } = "";
    public string? Folder { get; set; }
    public string? SuggestedName { get; set; }
    public string? CollectionDir { get; set; }
    public string? CollectionFolder { get; set; }
    public string? Error { get; set; }
    public JsonElement? Spec { get; set; }
    public JsonElement? Metadata { get; set; }
    public bool? HasArm { get; set; }
    public int? AnimationFiles { get; set; }
    public int? Projectiles { get; set; }
    public int? Vehicles { get; set; }

    [JsonIgnore]
    public string DisplayName => MetadataString("name") ?? Folder ?? Path.GetFileName(JavaDir);

    [JsonIgnore]
    public string? Author => MetadataString("author") switch
    {
        null => null,
        var s => s,
    };

    public string? MetadataString(string key)
    {
        if (Metadata is not { ValueKind: JsonValueKind.Object } meta) return null;
        if (!meta.TryGetProperty(key, out var value)) return null;
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString(),
            JsonValueKind.Array => string.Join(", ", value.EnumerateArray().Select(v => v.ToString())),
            JsonValueKind.Null or JsonValueKind.Undefined => null,
            _ => value.ToString(),
        };
    }
}

public sealed class KernelInfo
{
    public string? Kernel { get; set; }
    public string? Python { get; set; }
    public string? PortTool { get; set; }
    public bool? Pinyin { get; set; }
}

public static class KernelJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };
}
