using System.Text.Json;
using System.Text.Json.Serialization;

namespace YsmConvert.Core;

/// <summary>port_cli.py 事件流里的一行(JSON Lines)。字段按事件种类选填, 未知字段进 Extra。</summary>
public sealed class KernelEvent
{
    public const string Start = "start";
    public const string PackStart = "pack_start";
    public const string Log = "log";
    public const string Molang = "molang";
    public const string PackDone = "pack_done";
    public const string Collection = "collection";
    public const string ValidateItem = "validate_item";
    public const string ValidateDone = "validate_done";
    public const string Done = "done";
    public const string Pack = "pack";

    [JsonPropertyName("event")] public string Event { get; set; } = "";

    [JsonPropertyName("pack")] public string? PackName { get; set; }
    public string? Level { get; set; }
    public string? Text { get; set; }
    public string? Kind { get; set; }
    public string? Label { get; set; }
    public string? Error { get; set; }
    public string? JavaDir { get; set; }
    [JsonPropertyName("collection")] public string? CollectionName { get; set; }
    public string? Dir { get; set; }
    public string? Path { get; set; }
    public string? Action { get; set; }

    public int? Count { get; set; }
    public int? Index { get; set; }
    public int? Total { get; set; }
    public int? Errors { get; set; }
    public int? Warnings { get; set; }
    public int? Notices { get; set; }
    public int? Packs { get; set; }
    public int? PacksOk { get; set; }
    public int? PacksFailed { get; set; }
    public int? ValidationErrors { get; set; }
    public int? Animations { get; set; }
    public int? Controllers { get; set; }

    public bool? Attention { get; set; }
    public bool? Ok { get; set; }
    public bool? Written { get; set; }
    public double? Seconds { get; set; }

    [JsonExtensionData] public Dictionary<string, JsonElement>? Extra { get; set; }

    public static KernelEvent? TryParse(string line)
    {
        if (string.IsNullOrWhiteSpace(line) || line[0] != '{') return null;
        try
        {
            return JsonSerializer.Deserialize<KernelEvent>(line, KernelJson.Options);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    public string? ExtraString(string key)
    {
        if (Extra is null || !Extra.TryGetValue(key, out var el)) return null;
        return el.ValueKind == JsonValueKind.String ? el.GetString() : el.ToString();
    }

    public override string ToString() => Event switch
    {
        Log => $"[{PackName}] {Level}: {Text}",
        Molang => $"[{PackName}] molang/{Kind}: {Label} x{Count}",
        PackDone => $"[{PackName}] done ok={Ok} {Seconds}s",
        _ => Event,
    };
}
