using System.Runtime.InteropServices;

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
    /// <summary>自动并发时每个内核进程预留的内存(GB)。实测大包峰值: 凋灵娘约 0.7GB, 普通包几百 MB。</summary>
    public const double MemoryBudgetPerProcessGb = 1.5;

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

    /// <summary>自动并发数: 逻辑核数的一半(最多 8), 再按可用内存 / 每进程预算封顶, 至少 1。</summary>
    public static int AutoParallelism()
    {
        var byCpu = Math.Clamp(Environment.ProcessorCount / 2, 1, 8);
        var availableGb = MemoryStatus.AvailableGb();
        var byMemory = availableGb > 0 ? Math.Max(1, (int)(availableGb / MemoryBudgetPerProcessGb)) : byCpu;
        return Math.Min(byCpu, byMemory);
    }

    public static int EffectiveParallelism(int requested, int packCount) =>
        Math.Clamp(requested > 0 ? requested : AutoParallelism(), 1, Math.Max(1, packCount));

    public async Task<ConversionReport> ConvertAsync(ConvertRequest request, Action<KernelEvent>? onEvent = null,
        Action<string>? onStderr = null, CancellationToken ct = default)
    {
        var problems = PackNameRules.Validate(request.Packs);
        if (request.Packs.Count == 0) problems.Insert(0, "没有要转换的 Java 包");
        foreach (var collection in request.Collections)
            if (collection.CoverProblem() is { } coverProblem) problems.Add(coverProblem);
        if (problems.Count > 0)
            throw new ArgumentException(string.Join("\n", problems));
        // 工程形态的已有行为包同样要能被网易挂载(见 EnsureBehaviorPackMarker 注)
        OutputTarget.EnsureBehaviorPackMarker(request.Layout.BpDir);
        var refRp = ResolveRefRp(request.Layout, request.RefRp);
        if (request.Packs.Count == 1)
        {
            var job = new JobSpec
            {
                Action = "convert",
                Layout = request.Layout,
                RefRp = refRp,
                Packs = request.Packs,
                Collections = request.Collections,
                Options = request.Options,
            };
            return await RunAsync(job, onEvent, onStderr, ct).ConfigureAwait(false);
        }
        return await ConvertPerPackAsync(request, refRp, onEvent, onStderr, ct).ConfigureAwait(false);
    }

    /// <summary>
    /// 多个包: 每包一个内核进程, 最多同时跑 <see cref="EffectiveParallelism"/> 个, 全部结束后跑一次收尾任务(finalize)
    /// 统一写合集清单与体检。并行之外还有一个理由必须拆进程: 同一个 Python 进程连续转多个大包时内存只涨不回,
    /// 2026-09-18 实测 30 个包串行跑到第 28 个时 MemoryError, 而那个包单独跑峰值只有 0.7GB。
    /// 共享文件(sound_definitions.json、合集清单与封面)由内核的跨进程锁保护(port_java_pack.SharedFileLock)。
    /// </summary>
    private async Task<ConversionReport> ConvertPerPackAsync(ConvertRequest request, string? refRp,
        Action<KernelEvent>? onEvent, Action<string>? onStderr, CancellationToken ct)
    {
        var report = new ConversionReport { Action = "convert", Layout = request.Layout };
        var gate = new object();
        var total = request.Packs.Count;
        var started = 0;
        var finished = 0;
        var startSent = false;
        var outcome = new Dictionary<string, bool>(StringComparer.Ordinal);
        string? fatal = null;

        // 子进程的事件来自多个读取线程: 串行化后再交给报告与调用方(GUI 投递到界面线程, CLI 直接打印)
        void Deliver(KernelEvent e)
        {
            lock (gate)
            {
                report.Apply(e);
                onEvent?.Invoke(e);
            }
        }

        void Stderr(string line)
        {
            lock (gate)
            {
                report.Stderr.Add(line);
                onStderr?.Invoke(line);
            }
        }

        using var throttle = new SemaphoreSlim(EffectiveParallelism(request.Options.MaxParallel, total));
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);

        async Task RunOne(PackSpec pack)
        {
            await throttle.WaitAsync(linked.Token).ConfigureAwait(false);
            var sawDone = false;
            try
            {
                var job = new JobSpec
                {
                    Action = "convert",
                    Layout = request.Layout,
                    RefRp = refRp,
                    Packs = { pack },
                    Options = new ConvertOptions
                    {
                        WithMods = request.Options.WithMods,
                        Validate = false,
                        CompactJson = request.Options.CompactJson,
                        WriteCollections = false,
                    },
                };
                await Runner.RunJobAsync(job, e =>
                {
                    switch (e.Event)
                    {
                        case KernelEvent.Start:
                            lock (gate)
                            {
                                if (startSent) return;
                                startSent = true;
                            }
                            e.Packs = total;
                            Deliver(e);
                            return;
                        case KernelEvent.PackStart:
                            e.Index = Interlocked.Increment(ref started) - 1;
                            e.Total = total;
                            Deliver(e);
                            return;
                        case KernelEvent.PackDone:
                            sawDone = true;
                            lock (gate) outcome[pack.Name] = e.Ok == true;
                            e.Index = Interlocked.Increment(ref finished) - 1;
                            e.Total = total;
                            Deliver(e);
                            return;
                        case KernelEvent.Done:
                            return;     // 子任务各自的 done 不转发, 最后合成一个
                        default:
                            Deliver(e);
                            return;
                    }
                }, Stderr, linked.Token).ConfigureAwait(false);
            }
            catch (KernelException ex)
            {
                // 解释器起不来(缺 VC++ 2008 运行库、内核文件缺失)对每个包都一样: 记一次, 其余子任务不再启动
                lock (gate) fatal ??= ex.Details is null ? ex.Message : $"{ex.Message}\n{ex.Details}";
                linked.Cancel();
            }
            finally
            {
                throttle.Release();
            }
            if (!sawDone && !linked.IsCancellationRequested)
            {
                // 子进程没发 pack_done 就退出(被杀 / 崩溃): 补一条失败, 报告与进度都要算上它
                lock (gate) outcome[pack.Name] = false;
                Deliver(new KernelEvent
                {
                    Event = KernelEvent.PackDone,
                    PackName = pack.Name,
                    Ok = false,
                    Error = "内核进程意外退出, 没有报告结果(完整输出见报告的 stderr 段)",
                    Errors = 1,
                    Warnings = 0,
                    Notices = 0,
                    Seconds = 0,
                    Index = Interlocked.Increment(ref finished) - 1,
                    Total = total,
                });
            }
        }

        try
        {
            await Task.WhenAll(request.Packs.Select(RunOne)).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested && fatal is not null)
        {
            // 内核起不来引发的连锁取消, 不是用户点了取消
        }
        if (fatal is not null)
        {
            report.FatalError = fatal;
            report.ExitCode = -1;
            return report;
        }
        ct.ThrowIfCancellationRequested();

        var succeeded = request.Packs.Where(p => outcome.TryGetValue(p.Name, out var ok) && ok).ToList();
        var failedCount = total - succeeded.Count;
        var validationErrors = 0;
        var needFinalize = succeeded.Count > 0 && (request.Options.Validate || succeeded.Any(p => p.Collection is not null));
        if (needFinalize)
        {
            var finalize = new JobSpec
            {
                Action = "finalize",
                Layout = request.Layout,
                RefRp = refRp,
                Packs = succeeded,
                Collections = request.Collections,
                Options = new ConvertOptions { Validate = request.Options.Validate, CompactJson = request.Options.CompactJson },
            };
            try
            {
                await Runner.RunJobAsync(finalize, e =>
                {
                    if (e.Event == KernelEvent.Done)
                    {
                        validationErrors = e.ValidationErrors ?? 0;
                        return;
                    }
                    if (e.Event != KernelEvent.Start)
                        Deliver(e);
                }, Stderr, ct).ConfigureAwait(false);
            }
            catch (KernelException ex)
            {
                report.FatalError = ex.Details is null ? ex.Message : $"{ex.Message}\n{ex.Details}";
            }
        }
        Deliver(new KernelEvent
        {
            Event = KernelEvent.Done,
            Ok = failedCount == 0 && validationErrors == 0 && report.FatalError is null,
            PacksOk = succeeded.Count,
            PacksFailed = failedCount,
            ValidationErrors = validationErrors,
        });
        report.ExitCode = failedCount == 0 ? 0 : 1;
        return report;
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
        Action<KernelEvent>? onEvent = null, Action<string>? onStderr = null, CancellationToken ct = default,
        bool compactJson = true)
    {
        // 旧版转换器产出的组件行为包可能没有 entities, 就地修复一并补上(见 EnsureBehaviorPackMarker 注)
        OutputTarget.EnsureBehaviorPackMarker(layout.BpDir);
        var job = new JobSpec
        {
            Action = "fix",
            Layout = layout,
            RefRp = ResolveRefRp(layout, null),
            Packs = (packs ?? Array.Empty<string>()).Select(n => new PackSpec { JavaDir = "", Name = n }).ToList(),
            Options = new ConvertOptions { Validate = validate, CompactJson = compactJson },
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
            // 基线在 YSM 主工程里入库, 保持缩进
            Options = new ConvertOptions { WithMods = withMods, Validate = false, CompactJson = false },
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
}

/// <summary>系统可用内存(GB): 可用物理内存与可用提交额度取小; 取不到时返回 0。</summary>
internal static class MemoryStatus
{
    [StructLayout(LayoutKind.Sequential)]
    private struct MemoryStatusEx
    {
        public uint Length;
        public uint MemoryLoad;
        public ulong TotalPhys;
        public ulong AvailPhys;
        public ulong TotalPageFile;
        public ulong AvailPageFile;
        public ulong TotalVirtual;
        public ulong AvailVirtual;
        public ulong AvailExtendedVirtual;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx(ref MemoryStatusEx buffer);

    public static double AvailableGb()
    {
        try
        {
            var status = new MemoryStatusEx { Length = (uint)Marshal.SizeOf<MemoryStatusEx>() };
            if (!GlobalMemoryStatusEx(ref status)) return 0;
            return Math.Min(status.AvailPhys, status.AvailPageFile) / (1024.0 * 1024 * 1024);
        }
        catch (Exception ex) when (ex is DllNotFoundException or EntryPointNotFoundException)
        {
            return 0;
        }
    }
}
