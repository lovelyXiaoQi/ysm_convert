using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using YsmConvert.Cli.Mcp;
using YsmConvert.Core;

namespace YsmConvert.Cli;

internal static class Program
{
    private const string Usage = """
        ysmconv — YSM 模型转换器命令行(Java 版模型包 → 网易基岩版组件)

        用法:
          ysmconv info                                   内核与目录信息
          ysmconv discover <目录>... [--json]            发现 Java 模型包(单包 / 合集目录 / 上级目录)
          ysmconv convert <目录>... --out <组件根目录> [选项]
          ysmconv validate --out <组件根目录> [包名...] [--json]
          ysmconv fix --out <组件根目录> [包名...] [--no-validate] [--json]
          ysmconv baseline <Java default 模型目录> --out <组件根目录> [--with-mods]
          ysmconv mcp [--out <缺省组件根目录>]              以 MCP 服务器(stdio)方式运行, 供 AI 调用

        convert 选项:
          --component <名>          新建/复用独立组件 <根>/<名>_bp 与 <名>_rp(缺省: 根目录必须已是含行为包+资源包的工程)
          --collection <目录名>     全部包归入这个合集文件夹(ysm_models/<目录名>/<包名>)
          --collection-name <英文名> --collection-name-zh <中文名> --collection-desc <英文描述> --collection-desc-zh <中文描述>
          --prefix <前缀>           包名统一加前缀(缺省用内核建议名: 拼音化 + 合集前缀)
          --rename <文件夹>=<包名>  逐包指定包名(可多次)
          --with-mods               携带第三方模组联动动画(tacz/slashblade/...)
          --no-validate             转换后不跑资源包体检
          --ref-rp <资源包>         指定 java_default 基线所在资源包(缺省: 产物资源包自带则用它, 否则用内核快照)
          --json                    只输出 JSON 报告(不流式打印日志)
          --report <文件>           把 JSON 报告另存到文件
          --details                 文本模式下打印全部内核日志行(缺省只打印需要注意的项)

        退出码: 0 成功, 1 有失败/体检错误, 2 参数或环境错误
        """;

    private static async Task<int> Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        Console.InputEncoding = new UTF8Encoding(false);
        if (args.Length == 0 || args[0] is "-h" or "--help" or "help" or "/?")
        {
            Console.WriteLine(Usage);
            return 2;
        }
        var verb = args[0].ToLowerInvariant();
        var rest = args.Skip(1).ToArray();
        try
        {
            return verb switch
            {
                "info" => await InfoAsync(rest),
                "discover" => await DiscoverAsync(rest),
                "convert" => await ConvertAsync(rest),
                "validate" => await ValidateAsync(rest),
                "fix" => await FixAsync(rest),
                "baseline" => await BaselineAsync(rest),
                "mcp" => await McpAsync(rest),
                _ => Fail($"未知子命令: {verb}\n\n{Usage}"),
            };
        }
        catch (ArgumentException ex)
        {
            return Fail("参数错误: " + ex.Message);
        }
        catch (InvalidOperationException ex)
        {
            return Fail(ex.Message);
        }
        catch (KernelException ex)
        {
            return Fail(ex.Details is null ? ex.Message : $"{ex.Message}\n{ex.Details}");
        }
        catch (OperationCanceledException)
        {
            return Fail("已取消");
        }
    }

    private static int Fail(string message)
    {
        Console.Error.WriteLine(message);
        return 2;
    }

    private static ConversionService CreateService()
    {
        var paths = KernelLocator.Locate();
        return new ConversionService(paths);
    }

    private static readonly JsonSerializerOptions Pretty = new()
    {
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    // ---------------------------------------------------------------- info
    private static async Task<int> InfoAsync(string[] args)
    {
        var a = ParsedArgs.Parse(args, "json");
        var service = CreateService();
        // 内核起不来时(典型: 缺 VC++ 2008 运行库)先把定位到的路径打全, 再报错 —— 这正是 info 的用处
        KernelInfo? info = null;
        string? probeError = null;
        try
        {
            info = await service.GetInfoAsync();
        }
        catch (KernelException ex)
        {
            probeError = ex.Details is null ? ex.Message : $"{ex.Message}\n{ex.Details}";
        }
        var source = File.Exists(service.Paths.SourceInfoFile)
            ? JsonNode.Parse(await File.ReadAllTextAsync(service.Paths.SourceInfoFile))
            : null;
        if (a.Has("json"))
        {
            Console.WriteLine(new JsonObject
            {
                ["coreDir"] = service.Paths.CoreDir,
                ["python"] = service.Paths.PythonExe,
                ["pythonSource"] = service.Paths.PythonSource,
                ["kernelRoot"] = service.Paths.KernelRoot,
                ["docsDir"] = service.Paths.DocsDir,
                ["kernel"] = info is null ? null : JsonSerializer.SerializeToNode(info, Pretty),
                ["kernelError"] = probeError,
                ["kernelSource"] = source,
            }.ToJsonString(Pretty));
            return probeError is null ? 0 : 1;
        }
        Console.WriteLine($"core 目录:   {service.Paths.CoreDir}");
        Console.WriteLine($"Python:      {service.Paths.PythonExe} ({service.Paths.PythonSource}) 版本 {info?.Python ?? "?"}");
        Console.WriteLine($"内核入口:    {service.Paths.PortCli} (内核 {info?.Kernel ?? "?"}, 拼音库 {(info?.Pinyin == true ? "有" : "无")})");
        Console.WriteLine($"基线资源包:  {service.Paths.BundledRefRp}");
        Console.WriteLine($"文档目录:    {service.Paths.DocsDir}");
        if (source is JsonObject src)
            Console.WriteLine($"内核来源:    {src["source"]} @ {src["commit"]?.ToString()[..Math.Min(12, src["commit"]?.ToString().Length ?? 0)]} 同步于 {src["syncedAt"]}");
        if (probeError is not null)
        {
            Console.Error.WriteLine();
            Console.Error.WriteLine("[内核无法运行] " + probeError);
            return 1;
        }
        return 0;
    }

    // ---------------------------------------------------------------- discover
    private static async Task<int> DiscoverAsync(string[] args)
    {
        var a = ParsedArgs.Parse(args, "json");
        if (a.Positional.Count == 0) throw new ArgumentException("discover 需要至少一个目录");
        var service = CreateService();
        var packs = await service.DiscoverAsync(a.Positional);
        if (a.Has("json"))
        {
            Console.WriteLine(JsonSerializer.Serialize(packs, Pretty));
            return 0;
        }
        foreach (var p in packs)
        {
            if (p.Error is not null)
            {
                Console.WriteLine($"[跳过] {p.JavaDir}: {p.Error}");
                continue;
            }
            Console.WriteLine($"{p.SuggestedName,-40} {p.DisplayName}{(p.CollectionFolder is null ? "" : $"  [合集 {p.CollectionFolder}]")}");
            Console.WriteLine($"    {p.JavaDir}");
        }
        Console.WriteLine($"共 {packs.Count(p => p.Error is null)} 个 Java 包");
        return 0;
    }

    // ---------------------------------------------------------------- convert
    private static async Task<int> ConvertAsync(string[] args)
    {
        var a = ParsedArgs.Parse(args, "json", "with-mods", "no-validate", "details");
        if (a.Positional.Count == 0) throw new ArgumentException("convert 需要至少一个 Java 包目录");
        var service = CreateService();
        var layout = ResolveLayout(a);
        var discovered = await service.DiscoverAsync(a.Positional);
        foreach (var bad in discovered.Where(d => d.Error is not null))
            Console.Error.WriteLine($"[跳过] {bad.JavaDir}: {bad.Error}");

        var renames = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var r in a.GetAll("rename"))
        {
            var eq = r.IndexOf('=');
            if (eq <= 0) throw new ArgumentException($"--rename 格式是 <文件夹>=<包名>: {r}");
            renames[r[..eq]] = r[(eq + 1)..];
        }
        var packs = ConvertPlan.BuildPacks(discovered, a.Get("prefix"), a.Get("collection"), renames);
        var collections = new List<CollectionSpec>();
        if (a.Get("collection") is { } dir && (a.Has("collection-name") || a.Has("collection-name-zh") || a.Has("collection-desc") || a.Has("collection-desc-zh")))
        {
            collections.Add(new CollectionSpec
            {
                Dir = dir,
                Name = a.Get("collection-name"),
                NameZh = a.Get("collection-name-zh"),
                Description = a.Get("collection-desc"),
                DescriptionZh = a.Get("collection-desc-zh"),
            });
        }
        var request = new ConvertRequest
        {
            Layout = layout,
            Packs = packs,
            Collections = collections,
            Options = new ConvertOptions { WithMods = a.Has("with-mods"), Validate = !a.Has("no-validate") },
            RefRp = a.Get("ref-rp"),
        };
        var json = a.Has("json");
        var report = await service.ConvertAsync(request,
            json ? null : StreamingPrinter(a.Has("details")),
            json ? null : line => Console.Error.WriteLine("  (stderr) " + line));
        return Finish(report, a, json);
    }

    // ---------------------------------------------------------------- validate / fix / baseline
    private static async Task<int> ValidateAsync(string[] args)
    {
        var a = ParsedArgs.Parse(args, "json");
        var service = CreateService();
        var layout = ResolveLayout(a);
        var json = a.Has("json");
        var report = await service.ValidateAsync(layout, a.Positional, json ? null : StreamingPrinter(true));
        return Finish(report, a, json);
    }

    private static async Task<int> FixAsync(string[] args)
    {
        var a = ParsedArgs.Parse(args, "json", "no-validate", "details");
        var service = CreateService();
        var layout = ResolveLayout(a);
        var json = a.Has("json");
        var report = await service.FixAsync(layout, a.Positional, !a.Has("no-validate"), json ? null : StreamingPrinter(a.Has("details")));
        return Finish(report, a, json);
    }

    private static async Task<int> BaselineAsync(string[] args)
    {
        var a = ParsedArgs.Parse(args, "json", "with-mods", "details");
        if (a.Positional.Count != 1) throw new ArgumentException("baseline 需要一个 Java default 模型目录");
        var service = CreateService();
        var layout = ResolveLayout(a);
        var json = a.Has("json");
        var report = await service.BaselineAsync(layout, a.Positional[0], a.Has("with-mods"), json ? null : StreamingPrinter(a.Has("details")));
        return Finish(report, a, json);
    }

    // ---------------------------------------------------------------- mcp
    private static async Task<int> McpAsync(string[] args)
    {
        var a = ParsedArgs.Parse(args);
        if (!KernelLocator.TryLocate(out var paths, out var error))
        {
            Console.Error.WriteLine(error);
            // 仍然起服务器, 让 AI 能通过 ysm_info 看到问题
        }
        var service = paths is null ? null : new ConversionService(paths);
        var docs = new DocsLibrary(paths?.DocsDir ?? Path.Combine(AppContext.BaseDirectory, "core", "docs"));
        var server = new McpServer(service, docs, a.Get("out"), error);
        using var stdin = Console.OpenStandardInput();
        using var stdout = Console.OpenStandardOutput();
        await server.RunAsync(stdin, stdout, Console.Error, CancellationToken.None);
        return 0;
    }

    // ---------------------------------------------------------------- helpers
    private static OutputLayout ResolveLayout(ParsedArgs a)
    {
        var root = a.Require("out");
        if (a.Get("component") is { } component)
            return OutputTarget.EnsureComponent(root, component);
        if (!Directory.Exists(root))
            throw new ArgumentException($"输出目录不存在: {root}(新建组件请加 --component <名>)");
        return OutputTarget.Detect(root);
    }

    private static Action<KernelEvent> StreamingPrinter(bool details)
    {
        return e =>
        {
            switch (e.Event)
            {
                case KernelEvent.Start:
                    Console.WriteLine($"内核 {e.ExtraString("kernel")}");
                    break;
                case KernelEvent.PackStart:
                    Console.WriteLine($"== [{(e.Index ?? 0) + 1}/{e.Total}] {e.PackName}{(e.JavaDir is null ? "" : "  <- " + e.JavaDir)}");
                    break;
                case KernelEvent.Log:
                    if (details || e.Level is "error" or "warn" or "notice")
                        Console.WriteLine("   " + e.Text);
                    break;
                case KernelEvent.Molang:
                    if (e.Attention == true)
                        Console.WriteLine($"   [!] molang/{e.Kind}: {e.Label} x{e.Count}");
                    break;
                case KernelEvent.PackDone:
                    Console.WriteLine(e.Ok == true
                        ? $"   -> 完成 {e.Seconds:0.0}s  错误 {e.Errors} / 警告 {e.Warnings} / 提醒 {e.Notices}"
                        : $"   -> 失败: {e.Error?.Trim().Split('\n').LastOrDefault()}");
                    break;
                case KernelEvent.Collection:
                    Console.WriteLine($"合集清单 {e.Dir}: {(e.Written == true ? "已写入" : "沿用 Java 源")} {e.Path}");
                    break;
                case KernelEvent.ValidateItem:
                    Console.WriteLine($"体检 [{e.Level?.ToUpperInvariant()}] {e.Text}");
                    break;
                case KernelEvent.ValidateDone:
                    Console.WriteLine($"体检: 动画文件 {e.Animations} / 控制器文件 {e.Controllers}; 错误 {e.Errors} / 警告 {e.Warnings}");
                    break;
            }
        };
    }

    private static int Finish(ConversionReport report, ParsedArgs a, bool json)
    {
        if (a.Get("report") is { } file)
            File.WriteAllText(file, report.ToJsonString(), new UTF8Encoding(false));
        if (json)
        {
            Console.WriteLine(report.ToJsonString(includeLines: a.Has("details")));
        }
        else
        {
            var attention = report.BuildAttention();
            if (attention.Count > 0)
            {
                Console.WriteLine();
                Console.WriteLine($"== 需要开发者过目的 {attention.Count} 项:");
                foreach (var item in attention)
                    Console.WriteLine($"   [{item.Severity}] ({item.Pack}) {item.Category}: {item.Text}{(item.Count > 1 ? $" x{item.Count}" : "")}\n        → {item.Advice}{(item.Doc is null ? "" : $" [{item.Doc}]")}");
            }
            if (report.FatalError is not null)
                Console.Error.WriteLine("[FATAL] " + report.FatalError);
            Console.WriteLine(report.Done
                ? $"== {(report.Ok ? "全部成功" : "有失败或体检错误")}: 成功 {report.PacksOk} / 失败 {report.PacksFailed} / 体检错误 {report.ValidationErrors ?? 0}"
                : "== 内核未正常结束");
        }
        return report.Done && report.Ok && report.FatalError is null ? 0 : 1;
    }
}
