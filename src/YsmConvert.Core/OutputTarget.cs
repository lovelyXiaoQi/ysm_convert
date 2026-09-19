using System.Text.Json;
using System.Text.Json.Nodes;

namespace YsmConvert.Core;

/// <summary>
/// 产物落点的两种形态, 底层其实是同一件事 —— 一个装着行为包 + 资源包的目录:
/// <list type="bullet">
/// <item>工程形态: 已有的 YSM 组件工程(如主仓库 ysm_bp/ysm_rp), 按 manifest.json 的模块类型识别哪个是 BP 哪个是 RP;</item>
/// <item>组件形态: 新建 &lt;根&gt;/&lt;名&gt;_bp 与 &lt;根&gt;/&lt;名&gt;_rp(带 manifest.json), 运行时被 YSM 主包自动发现。</item>
/// </list>
/// </summary>
public static class OutputTarget
{
    public static OutputLayout Detect(string root)
    {
        if (!TryDetect(root, out var layout, out var error))
            throw new InvalidOperationException(error);
        return layout!;
    }

    public static bool TryDetect(string root, out OutputLayout? layout, out string? error)
    {
        layout = null;
        error = null;
        root = Path.GetFullPath(root);
        if (!Directory.Exists(root))
        {
            error = $"目录不存在: {root}";
            return false;
        }
        string? bp = null, rp = null;
        var seen = new List<string>();
        foreach (var dir in Directory.EnumerateDirectories(root).OrderBy(d => d, StringComparer.OrdinalIgnoreCase))
        {
            var manifest = Path.Combine(dir, "manifest.json");
            if (!File.Exists(manifest)) continue;
            var types = ReadModuleTypes(manifest);
            seen.Add($"{Path.GetFileName(dir)}[{string.Join(",", types)}]");
            if (bp is null && (types.Contains("data") || types.Contains("javascript")))
                bp = dir;
            else if (rp is null && types.Contains("resources"))
                rp = dir;
        }
        // 没有 manifest 的裸目录按名字兜底(ysm_bp/ysm_rp 之类)
        bp ??= Directory.EnumerateDirectories(root).FirstOrDefault(d => LooksLikeBp(Path.GetFileName(d)));
        rp ??= Directory.EnumerateDirectories(root).FirstOrDefault(d => LooksLikeRp(Path.GetFileName(d)));
        if (bp is null || rp is null)
        {
            error = $"在 {root} 下没找到成对的行为包与资源包(按 manifest.json 的模块类型 data / resources 识别)。" +
                    (seen.Count > 0 ? $" 找到: {string.Join("; ", seen)}" : " 该目录下没有任何带 manifest.json 的子目录。") +
                    " 要新建组件请用组件形态(指定组件名)。";
            return false;
        }
        layout = new OutputLayout(root, bp, rp);
        return true;
    }

    /// <summary>新建(或复用)组件: &lt;root&gt;/&lt;name&gt;_bp、&lt;root&gt;/&lt;name&gt;_rp, 缺 manifest.json 就补。</summary>
    public static OutputLayout EnsureComponent(string root, string componentName)
    {
        if (!IsValidComponentName(componentName))
            throw new ArgumentException($"组件名只能用英文、数字、下划线与连字符: {componentName}");
        root = Path.GetFullPath(root);
        var bp = Path.Combine(root, componentName + "_bp");
        var rp = Path.Combine(root, componentName + "_rp");
        Directory.CreateDirectory(Path.Combine(bp, "ysm_models"));
        Directory.CreateDirectory(rp);
        EnsureBehaviorPackMarker(bp);
        var bpManifest = Path.Combine(bp, "manifest.json");
        var rpManifest = Path.Combine(rp, "manifest.json");
        if (!File.Exists(bpManifest))
            File.WriteAllText(bpManifest, BuildManifestJson("data", Guid.NewGuid(), Guid.NewGuid()), new System.Text.UTF8Encoding(false));
        if (!File.Exists(rpManifest))
            File.WriteAllText(rpManifest, BuildManifestJson("resources", Guid.NewGuid(), Guid.NewGuid()), new System.Text.UTF8Encoding(false));
        return new OutputLayout(root, bp, rp);
    }

    /// <summary>
    /// 网易按有没有 <c>entities</c> 文件夹识别行为包: 没有就不挂载(MC Studio 测试世界与正式游戏都只启用它的资源包),
    /// ysm_models 里的 ysm.json 永远扫不到。MCDK 按 manifest 建目录联接, 不受这条限制, 在那边测不出来。
    /// 缺就补一个带 .gitkeep 的空文件夹(git 不跟踪空目录), 已有的不动。
    /// </summary>
    public static void EnsureBehaviorPackMarker(string bpDir)
    {
        var entities = Path.Combine(bpDir, "entities");
        if (Directory.Exists(entities)) return;
        Directory.CreateDirectory(entities);
        File.WriteAllBytes(Path.Combine(entities, ".gitkeep"), Array.Empty<byte>());
    }

    public static bool IsValidComponentName(string name) =>
        !string.IsNullOrWhiteSpace(name) && name.All(c => char.IsAsciiLetterOrDigit(c) || c == '_' || c == '-');

    /// <summary>与 YSM 主包 manifest 同形(网易形态: header 无 name/description, format_version 1, 最低引擎 1.18.0)。</summary>
    internal static string BuildManifestJson(string moduleType, Guid headerUuid, Guid moduleUuid)
    {
        var manifest = new JsonObject
        {
            ["modules"] = new JsonArray
            {
                new JsonObject
                {
                    ["type"] = moduleType,
                    ["uuid"] = moduleUuid.ToString(),
                    ["version"] = new JsonArray(1, 0, 0),
                },
            },
            ["header"] = new JsonObject
            {
                ["uuid"] = headerUuid.ToString(),
                ["version"] = new JsonArray(1, 0, 0),
                ["min_engine_version"] = new JsonArray(1, 18, 0),
            },
            ["format_version"] = 1,
        };
        return manifest.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + "\n";
    }

    private static HashSet<string> ReadModuleTypes(string manifestPath)
    {
        var types = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(manifestPath), new JsonDocumentOptions
            {
                CommentHandling = JsonCommentHandling.Skip,
                AllowTrailingCommas = true,
            });
            if (doc.RootElement.TryGetProperty("modules", out var modules) && modules.ValueKind == JsonValueKind.Array)
            {
                foreach (var m in modules.EnumerateArray())
                    if (m.TryGetProperty("type", out var t) && t.ValueKind == JsonValueKind.String)
                        types.Add(t.GetString()!);
            }
        }
        catch (Exception ex) when (ex is JsonException or IOException)
        {
            // 坏 manifest 当作没有模块
        }
        return types;
    }

    private static bool LooksLikeBp(string name)
    {
        var n = name.ToLowerInvariant();
        return n.EndsWith("_bp") || n == "bp" || n.Contains("behavior");
    }

    private static bool LooksLikeRp(string name)
    {
        var n = name.ToLowerInvariant();
        return n.EndsWith("_rp") || n == "rp" || n.Contains("resource");
    }
}
