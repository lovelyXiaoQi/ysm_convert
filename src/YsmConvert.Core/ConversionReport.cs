using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace YsmConvert.Core;

public sealed record LogLine(string Pack, string Level, string Text);

public sealed record MolangItem(string Pack, string Kind, string Label, int Count, bool Attention);

public sealed record ValidationItem(string Level, string Text);

public sealed record CollectionWritten(string Dir, string Path, bool Written);

/// <summary>需要开发者过目的一条(告警/置零/体检错误), 带解释。</summary>
public sealed record AttentionItem(string Pack, string Severity, string Category, string Text, int Count, string Advice, string? Doc);

public sealed class PackResult
{
    public required string Name { get; init; }
    public string? JavaDir { get; set; }
    public string? Collection { get; set; }
    public int Index { get; set; }
    public int Total { get; set; }
    public bool? Ok { get; set; }
    public double Seconds { get; set; }
    public string? Error { get; set; }
    public int Errors { get; set; }
    public int Warnings { get; set; }
    public int Notices { get; set; }
    public List<LogLine> Lines { get; } = new();
    public List<MolangItem> Molang { get; } = new();

    public bool Finished => Ok.HasValue;
    public int AttentionCount => Errors + Warnings + Notices + Molang.Count(m => m.Attention) + (Ok == false ? 1 : 0);
}

/// <summary>一次内核运行(convert / validate / fix / baseline)的全部结果, 由事件流逐条喂出来。</summary>
public sealed class ConversionReport
{
    public string Action { get; set; } = "convert";
    public DateTimeOffset StartedAt { get; } = DateTimeOffset.Now;
    public OutputLayout? Layout { get; set; }
    public string? RefRp { get; set; }
    public bool? BaselinePresent { get; set; }
    public string? KernelVersion { get; set; }
    public string? PythonVersion { get; set; }
    public List<PackResult> Packs { get; } = new();
    public List<CollectionWritten> Collections { get; } = new();
    public List<ValidationItem> Validation { get; } = new();
    public int? ValidationErrors { get; set; }
    public int? ValidationWarnings { get; set; }
    public int? AnimationFiles { get; set; }
    public int? ControllerFiles { get; set; }
    public bool Done { get; set; }
    public bool Ok { get; set; }
    public int PacksOk { get; set; }
    public int PacksFailed { get; set; }
    public int ExitCode { get; set; }
    public string? FatalError { get; set; }
    public List<string> Stderr { get; } = new();

    public PackResult? PackByName(string? name) =>
        name is null ? null : Packs.FirstOrDefault(p => p.Name == name);

    /// <summary>喂一个事件; 返回受影响的包(没有则 null)。</summary>
    public PackResult? Apply(KernelEvent e)
    {
        switch (e.Event)
        {
            case KernelEvent.Start:
                Action = e.Action ?? Action;
                if (e.Extra is not null)
                {
                    if (e.Extra.TryGetValue("layout", out var layout) && layout.ValueKind == JsonValueKind.Object)
                    {
                        if (layout.TryGetProperty("refRp", out var refRp)) RefRp = refRp.GetString();
                        if (layout.TryGetProperty("baselinePresent", out var bp) && bp.ValueKind is JsonValueKind.True or JsonValueKind.False)
                            BaselinePresent = bp.GetBoolean();
                    }
                    if (e.Extra.TryGetValue("kernel", out var kernel) && kernel.ValueKind == JsonValueKind.Object)
                    {
                        if (kernel.TryGetProperty("version", out var v)) KernelVersion = v.ToString();
                        if (kernel.TryGetProperty("python", out var py)) PythonVersion = py.ToString();
                    }
                }
                return null;
            case KernelEvent.PackStart:
            {
                var pack = PackByName(e.PackName);
                if (pack is null)
                {
                    pack = new PackResult { Name = e.PackName ?? "?" };
                    Packs.Add(pack);
                }
                pack.JavaDir = e.JavaDir;
                pack.Collection = e.CollectionName;
                pack.Index = e.Index ?? 0;
                pack.Total = e.Total ?? 0;
                return pack;
            }
            case KernelEvent.Log:
            {
                var pack = EnsurePack(e.PackName);
                pack.Lines.Add(new LogLine(pack.Name, e.Level ?? "info", e.Text ?? ""));
                return pack;
            }
            case KernelEvent.Molang:
            {
                var pack = EnsurePack(e.PackName);
                pack.Molang.Add(new MolangItem(pack.Name, e.Kind ?? "other", e.Label ?? "", e.Count ?? 0, e.Attention ?? false));
                return pack;
            }
            case KernelEvent.PackDone:
            {
                var pack = EnsurePack(e.PackName);
                pack.Ok = e.Ok ?? false;
                pack.Seconds = e.Seconds ?? 0;
                pack.Error = e.Error;
                pack.Errors = e.Errors ?? 0;
                pack.Warnings = e.Warnings ?? 0;
                pack.Notices = e.Notices ?? 0;
                return pack;
            }
            case KernelEvent.Collection:
                Collections.Add(new CollectionWritten(e.Dir ?? "", e.Path ?? "", e.Written ?? false));
                return null;
            case KernelEvent.ValidateItem:
                Validation.Add(new ValidationItem(e.Level ?? "warn", e.Text ?? ""));
                return null;
            case KernelEvent.ValidateDone:
                ValidationErrors = e.Errors;
                ValidationWarnings = e.Warnings;
                AnimationFiles = e.Animations;
                ControllerFiles = e.Controllers;
                return null;
            case KernelEvent.Done:
                Done = true;
                Ok = e.Ok ?? false;
                PacksOk = e.PacksOk ?? 0;
                PacksFailed = e.PacksFailed ?? 0;
                ValidationErrors ??= e.ValidationErrors;
                return null;
            default:
                return null;
        }
    }

    private PackResult EnsurePack(string? name)
    {
        var pack = PackByName(name);
        if (pack is null)
        {
            pack = new PackResult { Name = name ?? "?" };
            Packs.Add(pack);
        }
        return pack;
    }

    /// <summary>需要人工过目的全部项目(失败/错误/警告/提醒/置零 molang/体检错误与警告), 带解释。</summary>
    public List<AttentionItem> BuildAttention()
    {
        var items = new List<AttentionItem>();
        foreach (var pack in Packs)
        {
            if (pack.Ok == false)
                items.Add(new AttentionItem(pack.Name, "error", "移植失败",
                    FirstLine(pack.Error) ?? "内核异常", 1, "该包没有完整落盘; 展开日志看异常堆栈, 通常是 Java 包文件损坏或声明指向不存在的文件。", WarningCatalog.PortTutorialDoc));
            foreach (var line in pack.Lines)
            {
                if (line.Level is not ("error" or "warn" or "notice")) continue;
                var ex = WarningCatalog.ExplainLog(line.Level, line.Text);
                items.Add(new AttentionItem(pack.Name, line.Level, ex.Category, line.Text.Trim(), 1, ex.Advice, ex.Doc));
            }
            foreach (var m in pack.Molang.Where(m => m.Attention))
            {
                var ex = WarningCatalog.ExplainMolang(m.Kind, m.Label);
                items.Add(new AttentionItem(pack.Name, "notice", ex.Category, m.Label, m.Count, ex.Advice, ex.Doc));
            }
        }
        foreach (var v in Validation)
        {
            var ex = WarningCatalog.ExplainValidation(v.Level, v.Text);
            items.Add(new AttentionItem(PackOfValidation(v.Text), v.Level, ex.Category, v.Text, 1, ex.Advice, ex.Doc));
        }
        if (FatalError is not null)
            items.Add(new AttentionItem("", "error", "内核未能运行", FatalError, 1, "检查 core/ 目录与 Python 运行时是否完整。", null));
        return items;
    }

    private string PackOfValidation(string text)
    {
        foreach (var pack in Packs)
            if (text.Contains(pack.Name, StringComparison.Ordinal)) return pack.Name;
        var colon = text.IndexOf(':');
        return colon > 0 && colon < 80 ? text[..colon].Trim() : "";
    }

    private static string? FirstLine(string? text)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;
        var lines = text.Replace("\r", "").Split('\n', StringSplitOptions.RemoveEmptyEntries);
        return lines.LastOrDefault(l => l.Trim().Length > 0)?.Trim();
    }

    public string ToText(bool details = true)
    {
        var sb = new StringBuilder();
        sb.AppendLine($"== YSM 转换报告 ({Action}) {StartedAt:yyyy-MM-dd HH:mm:ss}");
        if (Layout is not null)
        {
            sb.AppendLine($"   行为包: {Layout.BpDir}");
            sb.AppendLine($"   资源包: {Layout.RpDir}");
        }
        if (RefRp is not null) sb.AppendLine($"   基线资源包: {RefRp}{(BaselinePresent == false ? " (缺 java_default 基线! 拉弓/举盾等回落动画将缺失)" : "")}");
        if (KernelVersion is not null) sb.AppendLine($"   内核 {KernelVersion} / Python {PythonVersion}");
        foreach (var pack in Packs)
        {
            var status = pack.Ok switch { true => "OK", false => "FAILED", null => "未完成" };
            sb.AppendLine();
            sb.AppendLine($"== {pack.Name} [{status}] {pack.Seconds:0.0}s  错误 {pack.Errors} / 警告 {pack.Warnings} / 提醒 {pack.Notices}{(pack.Collection is null ? "" : $"  合集 {pack.Collection}")}");
            if (pack.JavaDir is not null) sb.AppendLine($"   来源: {pack.JavaDir}");
            if (details)
            {
                foreach (var line in pack.Lines)
                    sb.AppendLine("   " + line.Text);
            }
            else
            {
                foreach (var line in pack.Lines.Where(l => l.Level is "error" or "warn" or "notice"))
                    sb.AppendLine("   " + line.Text.Trim());
            }
            foreach (var m in pack.Molang.Where(m => m.Attention))
                sb.AppendLine($"   [!] molang/{m.Kind}: {m.Label} x{m.Count}");
            if (pack.Error is not null)
            {
                sb.AppendLine("   ---- 异常 ----");
                sb.AppendLine(pack.Error.TrimEnd());
            }
        }
        foreach (var c in Collections)
            sb.AppendLine($"合集清单 {c.Dir}: {(c.Written ? "已写入" : "沿用 Java 源")} {c.Path}");
        if (ValidationErrors.HasValue || Validation.Count > 0)
        {
            sb.AppendLine();
            sb.AppendLine($"== 体检: 动画文件 {AnimationFiles ?? 0} / 控制器文件 {ControllerFiles ?? 0}; 错误 {ValidationErrors ?? 0} / 警告 {ValidationWarnings ?? Validation.Count(v => v.Level == "warn")}");
            foreach (var v in Validation)
                sb.AppendLine($"   [{v.Level.ToUpperInvariant()}] {v.Text}");
        }
        if (FatalError is not null)
            sb.AppendLine($"[FATAL] {FatalError}");
        sb.AppendLine();
        sb.AppendLine(Done
            ? $"== 结果: {(Ok ? "全部成功" : "有失败或体检错误")} 成功 {PacksOk} / 失败 {PacksFailed} / 体检错误 {ValidationErrors ?? 0}"
            : $"== 结果: 未正常结束(退出码 {ExitCode})");
        return sb.ToString();
    }

    public JsonObject ToJson(bool includeLines = true)
    {
        var packs = new JsonArray();
        foreach (var p in Packs)
        {
            var obj = new JsonObject
            {
                ["name"] = p.Name,
                ["javaDir"] = p.JavaDir,
                ["collection"] = p.Collection,
                ["ok"] = p.Ok,
                ["seconds"] = p.Seconds,
                ["errors"] = p.Errors,
                ["warnings"] = p.Warnings,
                ["notices"] = p.Notices,
                ["error"] = p.Error,
                ["molangAttention"] = new JsonArray(p.Molang.Where(m => m.Attention)
                    .Select(m => (JsonNode)new JsonObject { ["kind"] = m.Kind, ["label"] = m.Label, ["count"] = m.Count }).ToArray()),
            };
            if (includeLines)
                obj["lines"] = new JsonArray(p.Lines.Select(l => (JsonNode)new JsonObject { ["level"] = l.Level, ["text"] = l.Text }).ToArray());
            packs.Add(obj);
        }
        var attention = new JsonArray(BuildAttention().Select(a => (JsonNode)new JsonObject
        {
            ["pack"] = a.Pack, ["severity"] = a.Severity, ["category"] = a.Category, ["text"] = a.Text,
            ["count"] = a.Count, ["advice"] = a.Advice, ["doc"] = a.Doc,
        }).ToArray());
        return new JsonObject
        {
            ["action"] = Action,
            ["startedAt"] = StartedAt.ToString("O"),
            ["layout"] = Layout is null ? null : new JsonObject { ["root"] = Layout.Root, ["bp"] = Layout.BpDir, ["rp"] = Layout.RpDir },
            ["refRp"] = RefRp,
            ["baselinePresent"] = BaselinePresent,
            ["done"] = Done,
            ["ok"] = Ok,
            ["packsOk"] = PacksOk,
            ["packsFailed"] = PacksFailed,
            ["exitCode"] = ExitCode,
            ["fatalError"] = FatalError,
            ["validation"] = new JsonObject
            {
                ["errors"] = ValidationErrors,
                ["warnings"] = ValidationWarnings,
                ["animationFiles"] = AnimationFiles,
                ["controllerFiles"] = ControllerFiles,
                ["items"] = new JsonArray(Validation.Select(v => (JsonNode)new JsonObject { ["level"] = v.Level, ["text"] = v.Text }).ToArray()),
            },
            ["collections"] = new JsonArray(Collections.Select(c => (JsonNode)new JsonObject { ["dir"] = c.Dir, ["path"] = c.Path, ["written"] = c.Written }).ToArray()),
            ["packs"] = packs,
            ["attention"] = attention,
            ["stderr"] = new JsonArray(Stderr.Select(s => (JsonNode)JsonValue.Create(s)).ToArray()),
        };
    }

    public string ToJsonString(bool includeLines = true) =>
        ToJson(includeLines).ToJsonString(new JsonSerializerOptions
        {
            WriteIndented = true,
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        });
}
