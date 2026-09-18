namespace YsmConvert.Core;

/// <summary>一次转换请求(GUI / CLI / MCP 共用的输入形态)。</summary>
public sealed class ConvertRequest
{
    public required OutputLayout Layout { get; init; }
    public List<PackSpec> Packs { get; init; } = new();
    public List<CollectionSpec> Collections { get; init; } = new();
    public ConvertOptions Options { get; init; } = new();
    /// <summary>java_default 基线所在资源包; null = 产物资源包自带基线时用它, 否则用内核自带快照。</summary>
    public string? RefRp { get; init; }
}

/// <summary>门面: 发现 Java 包、移植、体检、修复、移植基线。所有入口都返回同一种报告。</summary>
public sealed class ConversionService
{
    public ConversionService(KernelPaths paths)
    {
        Paths = paths;
        Runner = new KernelRunner(paths);
    }

    public KernelPaths Paths { get; }
    public KernelRunner Runner { get; }

    public Task<IReadOnlyList<DiscoveredPack>> DiscoverAsync(IEnumerable<string> dirs, CancellationToken ct = default) =>
        Runner.DiscoverAsync(dirs, ct);

    public Task<KernelInfo?> GetInfoAsync(CancellationToken ct = default) => Runner.GetInfoAsync(ct);

    public Task<ConversionReport> ConvertAsync(ConvertRequest request, Action<KernelEvent>? onEvent = null,
        Action<string>? onStderr = null, CancellationToken ct = default)
    {
        var problems = PackNameRules.Validate(request.Packs);
        if (request.Packs.Count == 0) problems.Insert(0, "没有要转换的 Java 包");
        if (problems.Count > 0)
            throw new ArgumentException(string.Join("\n", problems));
        var job = new JobSpec
        {
            Action = "convert",
            Layout = request.Layout,
            RefRp = ResolveRefRp(request.Layout, request.RefRp),
            Packs = request.Packs,
            Collections = request.Collections,
            Options = request.Options,
        };
        return RunAsync(job, onEvent, onStderr, ct);
    }

    public Task<ConversionReport> ValidateAsync(OutputLayout layout, IEnumerable<string>? packs = null,
        Action<KernelEvent>? onEvent = null, Action<string>? onStderr = null, CancellationToken ct = default)
    {
        var job = new JobSpec
        {
            Action = "validate",
            Layout = layout,
            RefRp = ResolveRefRp(layout, null),
            Packs = (packs ?? Array.Empty<string>()).Select(n => new PackSpec { JavaDir = "", Name = n }).ToList(),
        };
        return RunAsync(job, onEvent, onStderr, ct);
    }

    public Task<ConversionReport> FixAsync(OutputLayout layout, IEnumerable<string>? packs = null, bool validate = true,
        Action<KernelEvent>? onEvent = null, Action<string>? onStderr = null, CancellationToken ct = default)
    {
        var job = new JobSpec
        {
            Action = "fix",
            Layout = layout,
            RefRp = ResolveRefRp(layout, null),
            Packs = (packs ?? Array.Empty<string>()).Select(n => new PackSpec { JavaDir = "", Name = n }).ToList(),
            Options = new ConvertOptions { Validate = validate },
        };
        return RunAsync(job, onEvent, onStderr, ct);
    }

    public Task<ConversionReport> BaselineAsync(OutputLayout layout, string javaDefaultDir, bool withMods = false,
        Action<KernelEvent>? onEvent = null, Action<string>? onStderr = null, CancellationToken ct = default)
    {
        var job = new JobSpec
        {
            Action = "baseline",
            Layout = layout,
            Packs = new List<PackSpec> { new() { JavaDir = Path.GetFullPath(javaDefaultDir), Name = "java_default" } },
            Options = new ConvertOptions { WithMods = withMods, Validate = false },
        };
        return RunAsync(job, onEvent, onStderr, ct);
    }

    public string? ResolveRefRp(OutputLayout layout, string? explicitRefRp)
    {
        if (!string.IsNullOrEmpty(explicitRefRp)) return Path.GetFullPath(explicitRefRp);
        if (layout.HasBaseline) return null;
        return Directory.Exists(Path.Combine(Paths.BundledRefRp, "animations", "java_default")) ? Paths.BundledRefRp : null;
    }

    private async Task<ConversionReport> RunAsync(JobSpec job, Action<KernelEvent>? onEvent, Action<string>? onStderr, CancellationToken ct)
    {
        var report = new ConversionReport { Action = job.Action, Layout = job.Layout };
        try
        {
            report.ExitCode = await Runner.RunJobAsync(job, e =>
            {
                report.Apply(e);
                onEvent?.Invoke(e);
            }, line =>
            {
                report.Stderr.Add(line);
                onStderr?.Invoke(line);
            }, ct).ConfigureAwait(false);
            if (!report.Done && report.FatalError is null)
                report.FatalError = $"内核未正常结束(退出码 {report.ExitCode})" +
                                    (report.Stderr.Count > 0 ? ": " + string.Join(" | ", report.Stderr.TakeLast(5)) : "");
        }
        catch (KernelException ex)
        {
            report.FatalError = ex.Details is null ? ex.Message : $"{ex.Message}: {ex.Details}";
            report.ExitCode = -1;
        }
        return report;
    }
}

/// <summary>把发现结果整理成转换任务: 包名(建议名 / 统一前缀 / 逐包改名)、合集归属。</summary>
public static class ConvertPlan
{
    public static List<PackSpec> BuildPacks(IEnumerable<DiscoveredPack> discovered, string? prefix = null,
        string? collectionOverride = null, IReadOnlyDictionary<string, string>? renames = null)
    {
        var packs = new List<PackSpec>();
        foreach (var d in discovered)
        {
            if (d.Error is not null) continue;
            var folder = d.Folder ?? Path.GetFileName(d.JavaDir);
            string name;
            if (renames is not null && (renames.TryGetValue(folder, out var renamed) || renames.TryGetValue(d.JavaDir, out renamed)))
                name = renamed;
            else if (!string.IsNullOrWhiteSpace(prefix))
                name = PackNameRules.Sanitize(prefix) + "_" + PackNameRules.Sanitize(d.SuggestedName ?? folder).TrimStart('_');
            else
                name = d.SuggestedName ?? PackNameRules.Sanitize(folder);
            packs.Add(new PackSpec
            {
                JavaDir = d.JavaDir,
                Name = name,
                Collection = collectionOverride ?? d.CollectionFolder,
            });
        }
        return packs;
    }

    /// <summary>合集的显示信息: 优先任务里给的, 否则从 Java 源旁的 ysm-pack.json 由内核照搬(这里不重复解析)。</summary>
    public static List<CollectionSpec> BuildCollections(IEnumerable<PackSpec> packs, CollectionSpec? given)
    {
        var dirs = packs.Select(p => p.Collection).Where(c => c is not null).Distinct().ToList();
        var specs = new List<CollectionSpec>();
        foreach (var dir in dirs)
        {
            if (given is not null && given.Dir == dir)
                specs.Add(given);
        }
        return specs;
    }
}
