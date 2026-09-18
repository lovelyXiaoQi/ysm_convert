namespace YsmConvert.Core;

/// <summary>一行要展示的 molang 提醒(小写改名已按包合并)。</summary>
public sealed record MolangRow(string Kind, string Text, int Count, string Severity);

/// <summary>
/// molang 留痕在提醒面板 / 日志 / 文本报告里怎么摆: 分级(notice = 要看, info = 知道一下即可)与
/// "动画名转小写"按包合并(官方包一个就能有几十条, 逐条列会把置零/退化项淹没)。
/// 类别与要不要提醒由内核宿主 port_cli.SplitMolangLabel 给出(kind / attention), 这里只决定呈现。
/// </summary>
public static class MolangPresentation
{
    private const int LowerNamesShown = 4;

    /// <summary>中性常量与动画名转小写只需知道一下(info); 置零/退化/未知函数要看(notice)。</summary>
    public static string Severity(string? kind) => kind is "const" or "lower" ? "info" : "notice";

    public static string Marker(string? kind) => Severity(kind) == "info" ? "[i]" : "[!]";

    /// <summary>一个包里要提醒的 molang 留痕 → 展示行: 小写改名合并成一行; notice 在前, info 在后(同级保持内核顺序)。</summary>
    public static List<MolangRow> Rows(IEnumerable<MolangItem> items)
    {
        var attention = items.Where(m => m.Attention).ToList();
        var rows = attention.Where(m => m.Kind != "lower")
            .Select(m => new MolangRow(m.Kind, m.Label, m.Count, Severity(m.Kind)))
            .ToList();
        var lower = attention.Where(m => m.Kind == "lower").Select(m => m.Label).ToList();
        if (lower.Count > 0)
            rows.Add(new MolangRow("lower", LowerSummary(lower), 1, "info"));
        return rows.OrderBy(r => r.Severity == "info" ? 1 : 0).ToList();
    }

    /// <summary>"Emotions_1 -> emotions_1" 若干条 → "N 个动画名改成小写: a→a, b→b …"。</summary>
    public static string LowerSummary(IReadOnlyCollection<string> labels)
    {
        var head = string.Join(", ", labels.Take(LowerNamesShown).Select(l => l.Replace(" -> ", "→", StringComparison.Ordinal)));
        return $"{labels.Count} 个动画名改成小写: {head}{(labels.Count > LowerNamesShown ? " …" : "")}";
    }

    /// <summary>
    /// 流式展示(CLI / 界面日志)用的按包缓冲: 小写改名先攒着, 包完成时 <see cref="Flush"/> 出一行汇总;
    /// 其余要提醒的事件立即返回展示行。调用方负责串行化。
    /// </summary>
    public sealed class StreamBuffer
    {
        private readonly Dictionary<string, List<string>> _lower = new(StringComparer.Ordinal);

        /// <summary>一个 molang 事件 → 立即展示的一行; 不提醒或被攒起来时返回 null。</summary>
        public string? Line(KernelEvent e)
        {
            if (e.Attention != true) return null;
            if (e.Kind == "lower")
            {
                var key = e.PackName ?? "";
                if (!_lower.TryGetValue(key, out var list))
                    _lower[key] = list = new List<string>();
                list.Add(e.Label ?? "");
                return null;
            }
            return $"{Marker(e.Kind)} molang/{e.Kind}: {e.Label} x{e.Count}";
        }

        /// <summary>包完成: 攒着的小写改名 → 一行汇总(没有则 null)。</summary>
        public string? Flush(string? pack)
        {
            var key = pack ?? "";
            if (!_lower.Remove(key, out var list) || list.Count == 0) return null;
            return $"[i] molang/lower: {LowerSummary(list)}";
        }
    }
}
