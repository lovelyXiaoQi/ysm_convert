using System.Text;
using System.Text.RegularExpressions;

namespace YsmConvert.Core;

/// <summary>
/// 包名 = ysm_models/ 下的目录名 = 模型 ID(ysm_pack:&lt;包名&gt;)与全部资源 ID(geometry./animation./controller.animation.)的词根。
/// 基岩资源 ID 只认 [a-z0-9_.-], 含其他字符整份文件拒载; 这里进一步收紧到 [a-z0-9_](点与连字符留给 ID 分段)。
/// </summary>
public static partial class PackNameRules
{
    public const int MaxLength = 64;

    [GeneratedRegex("^[a-z0-9][a-z0-9_]*$")]
    private static partial Regex ValidPattern();

    [GeneratedRegex("[^a-z0-9_]+")]
    private static partial Regex UnsafeRun();

    public static bool IsValid(string? name) =>
        !string.IsNullOrEmpty(name) && name.Length <= MaxLength && ValidPattern().IsMatch(name);

    /// <summary>ASCII 化 + 小写 + 非法段换下划线(拼音化由内核 --discover 的 suggestedName 负责, 这里只兜底)。</summary>
    public static string Sanitize(string text)
    {
        var ascii = new StringBuilder(text.Length);
        foreach (var ch in text.ToLowerInvariant())
            ascii.Append(ch < 128 ? ch : '_');
        var slug = UnsafeRun().Replace(ascii.ToString(), "_").Trim('_');
        while (slug.Contains("__")) slug = slug.Replace("__", "_");
        return slug.Length == 0 ? "pack" : slug;
    }

    public static string? Problem(string? name)
    {
        if (string.IsNullOrWhiteSpace(name)) return "包名为空";
        if (name.Length > MaxLength) return $"包名超过 {MaxLength} 字符";
        if (!ValidPattern().IsMatch(name)) return "包名只能用小写英文、数字、下划线, 且不能以下划线开头";
        return null;
    }

    /// <summary>整批校验: 单个包名合法性、重名、Java 目录存在、合集目录名合法。返回问题列表(空 = 通过)。</summary>
    public static List<string> Validate(IEnumerable<PackSpec> packs)
    {
        var problems = new List<string>();
        var seen = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var p in packs)
        {
            var problem = Problem(p.Name);
            if (problem is not null)
                problems.Add($"{p.JavaDir}: {problem}(当前 \"{p.Name}\")");
            if (seen.TryGetValue(p.Name ?? "", out var other))
                problems.Add($"包名重复: {p.Name}({other} 与 {p.JavaDir})");
            else if (p.Name is not null)
                seen[p.Name] = p.JavaDir;
            if (!File.Exists(Path.Combine(p.JavaDir, "ysm.json")))
                problems.Add($"不是 Java 模型包目录(缺 ysm.json): {p.JavaDir}");
            if (p.Collection is not null && !OutputTarget.IsValidComponentName(p.Collection))
                problems.Add($"合集目录名只能用英文、数字、下划线与连字符: {p.Collection}");
        }
        return problems;
    }
}
