using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using YsmConvert.Core;

namespace YsmConvert.Cli.Mcp;

/// <summary>
/// MCP 服务器(stdio, JSON-RPC 2.0, 每行一个消息), 不依赖 SDK —— 协议面只有 initialize / ping / tools/list / tools/call。
/// 让 AI(如 Claude Code)能一键转换、体检、修复、查文档、解释告警, 形成"转换 → 看告警 → 改产物 → 再体检"的闭环。
/// 所有诊断只写 stderr, stdout 只有协议消息。
/// </summary>
internal sealed class McpServer
{
    private const string ServerName = "ysm-convert";
    // 跟随程序集版本(Directory.Build.props 的 <Version>, 发版流水线按 tag 递增后经 -p:Version 覆盖), 去掉 SDK 附加的 "+提交号"
    private static readonly string ServerVersion =
        typeof(McpServer).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.Split('+')[0] ?? "0.0.0";
    private static readonly string[] KnownProtocolVersions = { "2025-06-18", "2025-03-26", "2024-11-05" };

    private readonly ConversionService? _service;
    private readonly DocsLibrary _docs;
    private readonly string? _defaultOut;
    private readonly string? _kernelError;
    private ConversionReport? _lastReport;

    private static readonly JsonSerializerOptions JsonOut = new()
    {
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    private static readonly JsonSerializerOptions JsonPretty = new(JsonOut) { WriteIndented = true };

    public McpServer(ConversionService? service, DocsLibrary docs, string? defaultOut, string? kernelError)
    {
        _service = service;
        _docs = docs;
        _defaultOut = defaultOut;
        _kernelError = kernelError;
    }

    public async Task RunAsync(Stream stdin, Stream stdout, TextWriter log, CancellationToken ct)
    {
        using var reader = new StreamReader(stdin, new UTF8Encoding(false));
        using var writer = new StreamWriter(stdout, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
        log.WriteLine($"[{ServerName}] MCP 服务器就绪 (stdio){(_kernelError is null ? "" : "; 内核不可用: " + _kernelError)}");
        while (!ct.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(ct).ConfigureAwait(false);
            if (line is null) break;
            if (string.IsNullOrWhiteSpace(line)) continue;
            JsonNode? message;
            try
            {
                message = JsonNode.Parse(line);
            }
            catch (JsonException ex)
            {
                await WriteAsync(writer, Error(null, -32700, "Parse error: " + ex.Message)).ConfigureAwait(false);
                continue;
            }
            if (message is not JsonObject request)
            {
                await WriteAsync(writer, Error(null, -32600, "Invalid Request")).ConfigureAwait(false);
                continue;
            }
            JsonObject? response;
            try
            {
                response = await HandleAsync(request, log, ct).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                log.WriteLine($"[{ServerName}] 处理 {request["method"]} 失败: {ex}");
                response = Error(request["id"], -32603, ex.Message);
            }
            if (response is not null)
                await WriteAsync(writer, response).ConfigureAwait(false);
        }
    }

    private static async Task WriteAsync(StreamWriter writer, JsonObject message)
    {
        await writer.WriteLineAsync(message.ToJsonString(JsonOut)).ConfigureAwait(false);
    }

    private async Task<JsonObject?> HandleAsync(JsonObject request, TextWriter log, CancellationToken ct)
    {
        var method = request["method"]?.GetValue<string>() ?? "";
        var id = request["id"];
        var isNotification = id is null;
        var @params = request["params"] as JsonObject;

        switch (method)
        {
            case "initialize":
            {
                var requested = @params?["protocolVersion"]?.GetValue<string>();
                var version = requested is not null && KnownProtocolVersions.Contains(requested) ? requested : KnownProtocolVersions[0];
                return Result(id, new JsonObject
                {
                    ["protocolVersion"] = version,
                    ["capabilities"] = new JsonObject { ["tools"] = new JsonObject { ["listChanged"] = false } },
                    ["serverInfo"] = new JsonObject { ["name"] = ServerName, ["version"] = ServerVersion },
                    ["instructions"] = Instructions(),
                });
            }
            case "notifications/initialized":
            case "notifications/cancelled":
            case "notifications/progress":
            case "notifications/roots/list_changed":
                return null;
            case "ping":
                return Result(id, new JsonObject());
            case "logging/setLevel":
                return Result(id, new JsonObject());
            case "tools/list":
                return Result(id, new JsonObject { ["tools"] = ToolDefinitions() });
            case "tools/call":
            {
                var name = @params?["name"]?.GetValue<string>() ?? "";
                var args = @params?["arguments"] as JsonObject ?? new JsonObject();
                if (!ToolNames.Contains(name))
                    return Error(id, -32602, $"Unknown tool: {name}");
                string text;
                var isError = false;
                try
                {
                    text = await CallToolAsync(name, args, ct).ConfigureAwait(false);
                }
                catch (Exception ex) when (ex is ArgumentException or InvalidOperationException or KernelException or IOException)
                {
                    text = ex is KernelException ke && ke.Details is not null ? $"{ke.Message}\n{ke.Details}" : ex.Message;
                    isError = true;
                }
                return Result(id, new JsonObject
                {
                    ["content"] = new JsonArray(new JsonObject { ["type"] = "text", ["text"] = text }),
                    ["isError"] = isError,
                });
            }
            default:
                return isNotification ? null : Error(id, -32601, $"Method not found: {method}");
        }
    }

    private static JsonObject Result(JsonNode? id, JsonNode result) => new()
    {
        ["jsonrpc"] = "2.0",
        ["id"] = id?.DeepClone(),
        ["result"] = result,
    };

    private static JsonObject Error(JsonNode? id, int code, string message) => new()
    {
        ["jsonrpc"] = "2.0",
        ["id"] = id?.DeepClone(),
        ["error"] = new JsonObject { ["code"] = code, ["message"] = message },
    };

    private string Instructions() =>
        "YSM 模型转换器: 把 Java 版 YSM 模型包(未加密目录, 含 ysm.json)转换成网易基岩版组件。" +
        "典型流程: ysm_discover 找包 → ysm_convert 转换(返回需要开发者过目的告警清单与产物路径) → 按告警读产物文件手工修改 → " +
        "ysm_validate 复检(0 错误才能进游戏) → 需要时 ysm_fix 对已落盘产物补跑修复规则。告警看不懂用 ysm_explain, 规则细节用 ysm_docs 检索随附文档。" +
        " 产物 JSON 缺省压成一行; 要逐行阅读或手工改产物时, ysm_convert / ysm_fix 传 pretty=true 按缩进落盘。" +
        " attention 的 severity: error/warn 要修, notice 要看(molang 置零/退化/未知函数), info 知道即可(中性常量 = 基岩没有的 Java 量按常量处理、动画名转小写按包汇总)。" +
        " 天气/血量/roaming 存档等 Java 专有量由 YSM 主组件运行层提供(ysm.json 顶级 java_state 声明), 产物要搭配同期或更新的主组件。" +
        $" 给人看的同一批文档在线上 Wiki: {YsmLinks.WikiUrl}(引导用户去那里, 自己查用 ysm_docs)。" +
        (_defaultOut is null ? "" : $" 缺省输出目录: {_defaultOut}。") +
        (_kernelError is null ? "" : $" 注意: 内核当前不可用({_kernelError}), 只有文档类工具能用。");

    // ---------------------------------------------------------------- tools
    private static readonly string[] ToolNames =
    {
        "ysm_info", "ysm_discover", "ysm_convert", "ysm_validate", "ysm_fix", "ysm_baseline",
        "ysm_explain", "ysm_docs", "ysm_last_report", "ysm_pack_files",
    };

    private static JsonObject Schema(params (string name, string type, string desc, bool required)[] props)
    {
        var properties = new JsonObject();
        var required = new JsonArray();
        foreach (var (name, type, desc, req) in props)
        {
            JsonObject prop = type switch
            {
                "string[]" => new JsonObject { ["type"] = "array", ["items"] = new JsonObject { ["type"] = "string" }, ["description"] = desc },
                "object" => new JsonObject { ["type"] = "object", ["additionalProperties"] = new JsonObject { ["type"] = "string" }, ["description"] = desc },
                _ => new JsonObject { ["type"] = type, ["description"] = desc },
            };
            properties[name] = prop;
            if (req) required.Add(name);
        }
        var schema = new JsonObject { ["type"] = "object", ["properties"] = properties };
        if (required.Count > 0) schema["required"] = required;
        return schema;
    }

    private static JsonObject Tool(string name, string description, JsonObject schema) => new()
    {
        ["name"] = name,
        ["description"] = description,
        ["inputSchema"] = schema,
    };

    private JsonArray ToolDefinitions() => new(
        Tool("ysm_info", "转换器与内核信息: core 目录、Python、内核版本与来源提交、随附文档列表、缺省输出目录。", Schema()),
        Tool("ysm_discover", "在目录里发现 Java 版 YSM 模型包(单包目录 / 带 ysm-pack.json 的合集目录 / 装着若干包的上级目录, 深 2 层)。返回每个包的目录、建议包名、所属合集、metadata。",
            Schema(("dirs", "string[]", "要扫描的目录列表", true))),
        Tool("ysm_convert", "一键把 Java 模型包转换成网易基岩版组件并跑体检。inputs 可以是包目录或合集目录; out 是组件根目录: 已有工程(含行为包+资源包)直接用, 传 component 则新建 <out>/<component>_bp 与 _rp。返回文本报告 + JSON(attention = 需要开发者过目的项, outputs = 每个包的产物路径)。",
            Schema(("inputs", "string[]", "Java 包目录或合集目录", true),
                   ("out", "string", "组件根目录(缺省用服务器启动时的 --out)", false),
                   ("component", "string", "新建/复用独立组件名(英文数字下划线)", false),
                   ("collection", "string", "把全部包归入这个合集文件夹(目录名)", false),
                   ("collectionName", "string", "文件夹在游戏里显示的名字(缺省沿用 Java 合集自带的名字)", false),
                   ("collectionCover", "string", "文件夹封面图 PNG 的路径(不超过 1MB, 最好是 Java 卡片 52x90 的比例; 缺省用合集自带的 ysm-pack.png)", false),
                   ("prefix", "string", "包名统一前缀(缺省用内核建议名: 拼音化 + 合集前缀)", false),
                   ("renames", "object", "逐包指定包名: {\"<Java 文件夹名>\": \"<包名>\"}", false),
                   ("withMods", "boolean", "携带第三方模组联动动画, 缺省 false", false),
                   ("validate", "boolean", "转换后跑资源包体检, 缺省 true", false),
                   ("pretty", "boolean", "产物 JSON 按 2 空格缩进(缺省 false = 压成一行, 体积约省 70%; 要逐行读产物、手工改 bug 时设 true)", false),
                   ("jobs", "integer", "同时转换的包数(缺省自动; 多个包总是每包一个内核进程)", false),
                   ("refRp", "string", "java_default 基线所在资源包(缺省自动)", false),
                   ("details", "boolean", "报告里带全部内核日志行, 缺省 false", false))),
        Tool("ysm_validate", "对已落盘的产物跑引擎红线体检(molang 语法/资源 ID/空节点/音频登记...)。错误项意味着整份文件会被引擎拒载, 必须修到 0。",
            Schema(("out", "string", "组件根目录", false), ("packs", "string[]", "只体检这些包名(缺省全部)", false))),
        Tool("ysm_fix", "对已落盘的产物就地补跑修复规则(幂等): 控制器死引用剪枝、ysm.json 精简、通道表达式规范化、预览实体变量回填等。Java 源不在手边时用它; 有源的话重新 ysm_convert 更完整。",
            Schema(("out", "string", "组件根目录", false), ("packs", "string[]", "只修这些包名(缺省全部)", false), ("validate", "boolean", "修完体检, 缺省 true", false),
                   ("pretty", "boolean", "写回的 JSON 按 2 空格缩进(缺省 false = 压成一行)", false))),
        Tool("ysm_baseline", "把 Java 版内置 default 模型移植成 java_default 基线动画(只产出资源包动画, 不出现在模型列表)。Java 包缺的拉弓/举盾/游泳等动画靠它回落; 只有 YSM 主组件工程需要跑一次。",
            Schema(("javaDefaultDir", "string", "Java 版 assets/ysm/builtin/default 目录", true), ("out", "string", "YSM 主组件工程根目录", false), ("withMods", "boolean", "携带模组联动动画", false))),
        Tool("ysm_explain", "解释一条内核告警/体检文本或 molang 留痕标签: 它意味着什么、要不要处理、该查哪份文档。",
            Schema(("text", "string", "告警文本或 molang 标签", true), ("kind", "string", "molang 类别(map/const/zero/func/warn/lower/norm/skip/tick)或 log/validation, 缺省自动", false))),
        Tool("ysm_docs", "随附的 YSM 移植文档(格式速查/移植教程/molang 映射清单/动画机制对照)。不带参数列出文档; name 读整篇; name+section 读某个标题下的章节; query 全文检索。",
            Schema(("name", "string", "文档文件名(如 ysm-java-molang-mapping.md)", false), ("section", "string", "标题包含的文字", false), ("query", "string", "关键字检索", false))),
        Tool("ysm_last_report", "上一次 convert/validate/fix 的完整报告(文本 + JSON)。",
            Schema(("details", "boolean", "带全部日志行", false))),
        Tool("ysm_pack_files", "列出某个包在组件里的全部产物文件(声明 ysm.json、几何、动画、控制器、预览实体、贴图), 供逐个打开修改。",
            Schema(("out", "string", "组件根目录", false), ("pack", "string", "包名", true))));

    private async Task<string> CallToolAsync(string name, JsonObject args, CancellationToken ct)
    {
        switch (name)
        {
            case "ysm_info":
                return await InfoAsync(ct).ConfigureAwait(false);
            case "ysm_discover":
            {
                var dirs = StringList(args["dirs"]);
                if (dirs.Count == 0) throw new ArgumentException("dirs 不能为空");
                var packs = await RequireService().DiscoverAsync(dirs, ct).ConfigureAwait(false);
                return JsonSerializer.Serialize(packs, JsonPretty);
            }
            case "ysm_convert":
                return await ConvertAsync(args, ct).ConfigureAwait(false);
            case "ysm_validate":
            {
                var layout = ResolveLayout(args, allowCreate: false);
                var report = await RequireService().ValidateAsync(layout, StringList(args["packs"]), ct: ct).ConfigureAwait(false);
                _lastReport = report;
                return ReportText(report, false);
            }
            case "ysm_fix":
            {
                var layout = ResolveLayout(args, allowCreate: false);
                var report = await RequireService().FixAsync(layout, StringList(args["packs"]), Bool(args["validate"]) ?? true, ct: ct,
                    compactJson: !(Bool(args["pretty"]) ?? false)).ConfigureAwait(false);
                _lastReport = report;
                return ReportText(report, Bool(args["details"]) ?? false);
            }
            case "ysm_baseline":
            {
                var layout = ResolveLayout(args, allowCreate: false);
                var dir = args["javaDefaultDir"]?.GetValue<string>() ?? throw new ArgumentException("缺 javaDefaultDir");
                var report = await RequireService().BaselineAsync(layout, dir, Bool(args["withMods"]) ?? false, ct: ct).ConfigureAwait(false);
                _lastReport = report;
                return ReportText(report, true);
            }
            case "ysm_explain":
            {
                var text = args["text"]?.GetValue<string>() ?? throw new ArgumentException("缺 text");
                var kind = args["kind"]?.GetValue<string>();
                var explanation = ExplainAny(text, kind);
                var section = explanation.Doc is null ? null : _docs.ReadSection(explanation.Doc, explanation.Category);
                return JsonSerializer.Serialize(new
                {
                    explanation.Category, explanation.Advice, explanation.Doc,
                    docSection = section,
                    hint = "用 ysm_docs 带 query 检索文档里的具体条目",
                }, JsonPretty);
            }
            case "ysm_docs":
                return DocsTool(args);
            case "ysm_last_report":
                return _lastReport is null ? "还没有跑过 convert / validate / fix" : ReportText(_lastReport, Bool(args["details"]) ?? false);
            case "ysm_pack_files":
            {
                var layout = ResolveLayout(args, allowCreate: false);
                var pack = args["pack"]?.GetValue<string>() ?? throw new ArgumentException("缺 pack");
                return JsonSerializer.Serialize(PackFiles(layout, pack), JsonPretty);
            }
            default:
                throw new ArgumentException($"Unknown tool: {name}");
        }
    }

    private ConversionService RequireService() =>
        _service ?? throw new InvalidOperationException("内核不可用: " + (_kernelError ?? "未知原因"));

    private async Task<string> InfoAsync(CancellationToken ct)
    {
        // 内核跑不起来时(典型: 目标机器缺 VC++ 2008 运行库)这个工具仍要能回话, 把错误当数据返回
        KernelInfo? info = null;
        string? probeError = null;
        if (_service is not null)
        {
            try
            {
                info = await _service.GetInfoAsync(ct).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is KernelException or IOException)
            {
                probeError = ex is KernelException { Details: not null } ke ? $"{ke.Message}\n{ke.Details}" : ex.Message;
            }
        }
        JsonNode? source = null;
        if (_service is not null && File.Exists(_service.Paths.SourceInfoFile))
            source = JsonNode.Parse(await File.ReadAllTextAsync(_service.Paths.SourceInfoFile, ct).ConfigureAwait(false));
        var obj = new JsonObject
        {
            ["server"] = $"{ServerName} {ServerVersion}",
            ["kernelAvailable"] = _service is not null && probeError is null,
            ["kernelError"] = _kernelError ?? probeError,
            ["coreDir"] = _service?.Paths.CoreDir,
            ["python"] = _service?.Paths.PythonExe,
            ["pythonSource"] = _service?.Paths.PythonSource,
            ["kernel"] = info is null ? null : JsonSerializer.SerializeToNode(info, JsonOut),
            ["kernelSource"] = source,
            ["bundledRefRp"] = _service?.Paths.BundledRefRp,
            ["defaultOut"] = _defaultOut,
            ["docs"] = new JsonArray(_docs.List().Select(d => (JsonNode)new JsonObject { ["name"] = d.Name, ["title"] = d.Title, ["bytes"] = d.Size }).ToArray()),
            ["wiki"] = YsmLinks.WikiUrl,
            ["repo"] = YsmLinks.RepoUrl,
            ["autoParallelism"] = ConversionService.AutoParallelism(),
        };
        return obj.ToJsonString(JsonPretty);
    }

    private async Task<string> ConvertAsync(JsonObject args, CancellationToken ct)
    {
        var service = RequireService();
        var inputs = StringList(args["inputs"]);
        if (inputs.Count == 0) throw new ArgumentException("inputs 不能为空");
        var layout = ResolveLayout(args, allowCreate: true);
        var discovered = await service.DiscoverAsync(inputs, ct).ConfigureAwait(false);
        var renames = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (args["renames"] is JsonObject renameObj)
            foreach (var kv in renameObj)
                if (kv.Value is not null) renames[kv.Key] = kv.Value.GetValue<string>();
        var packs = ConvertPlan.BuildPacks(discovered, args["prefix"]?.GetValue<string>(), args["collection"]?.GetValue<string>(), renames);
        var collections = new List<CollectionSpec>();
        if (args["collection"]?.GetValue<string>() is { } dir)
        {
            var spec = new CollectionSpec
            {
                Dir = dir,
                Name = args["collectionName"]?.GetValue<string>(),
                CoverImage = args["collectionCover"]?.GetValue<string>(),
            };
            if (!spec.IsEmpty) collections.Add(spec);
        }
        var request = new ConvertRequest
        {
            Layout = layout,
            Packs = packs,
            Collections = collections,
            Options = new ConvertOptions
            {
                WithMods = Bool(args["withMods"]) ?? false,
                Validate = Bool(args["validate"]) ?? true,
                CompactJson = !(Bool(args["pretty"]) ?? false),
                MaxParallel = Int(args["jobs"]) ?? 0,
            },
            RefRp = args["refRp"]?.GetValue<string>(),
        };
        var report = await service.ConvertAsync(request, ct: ct).ConfigureAwait(false);
        _lastReport = report;
        var skipped = discovered.Where(d => d.Error is not null).Select(d => $"{d.JavaDir}: {d.Error}").ToList();
        var sb = new StringBuilder();
        if (skipped.Count > 0)
            sb.AppendLine("跳过的输入: " + string.Join("; ", skipped));
        sb.Append(ReportText(report, Bool(args["details"]) ?? false, packs.Select(p => p.Name)));
        return sb.ToString();
    }

    private string ReportText(ConversionReport report, bool details, IEnumerable<string>? packNames = null)
    {
        var json = report.ToJson(includeLines: details);
        if (report.Layout is not null)
        {
            var names = (packNames ?? report.Packs.Select(p => p.Name)).ToList();
            var outputs = new JsonObject();
            foreach (var name in names)
            {
                var pack = report.PackByName(name);
                outputs[name] = JsonSerializer.SerializeToNode(PackFiles(report.Layout, name, pack?.Collection), JsonOut);
            }
            json["outputs"] = outputs;
        }
        return report.ToText(details) + "\n---- JSON ----\n" + json.ToJsonString(JsonPretty);
    }

    private static object PackFiles(OutputLayout layout, string pack, string? collection = null)
    {
        string? manifest = null;
        var direct = Path.Combine(layout.BpModels, pack, "ysm.json");
        if (File.Exists(direct)) manifest = direct;
        else if (collection is not null && File.Exists(Path.Combine(layout.BpModels, collection, pack, "ysm.json")))
            manifest = Path.Combine(layout.BpModels, collection, pack, "ysm.json");
        else if (Directory.Exists(layout.BpModels))
            manifest = Directory.EnumerateDirectories(layout.BpModels)
                .Select(d => Path.Combine(d, pack, "ysm.json")).FirstOrDefault(File.Exists);

        static List<string> Files(string dir) =>
            Directory.Exists(dir) ? Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories).OrderBy(f => f).ToList() : new List<string>();

        var entity = Path.Combine(layout.RpDir, "entity", pack + ".entity.json");
        return new
        {
            pack,
            manifest,
            geometry = Files(Path.Combine(layout.RpDir, "models", "entity", pack)),
            animations = Files(Path.Combine(layout.RpDir, "animations", pack)),
            animationControllers = Files(Path.Combine(layout.RpDir, "animation_controllers", pack)),
            textures = Files(Path.Combine(layout.RpDir, "textures", "entity", pack)),
            sounds = Files(Path.Combine(layout.RpDir, "sounds", "ysm", pack)),
            previewEntity = File.Exists(entity) ? entity : null,
        };
    }

    private OutputLayout ResolveLayout(JsonObject args, bool allowCreate)
    {
        var root = args["out"]?.GetValue<string>() ?? _defaultOut ?? throw new ArgumentException("缺 out(组件根目录), 服务器启动时也没给 --out");
        var component = args["component"]?.GetValue<string>();
        if (component is not null)
        {
            if (!allowCreate) throw new ArgumentException("该工具不新建组件, 去掉 component");
            return OutputTarget.EnsureComponent(root, component);
        }
        if (!Directory.Exists(root)) throw new ArgumentException($"输出目录不存在: {root}(新建组件请传 component)");
        return OutputTarget.Detect(root);
    }

    private string DocsTool(JsonObject args)
    {
        var name = args["name"]?.GetValue<string>();
        var section = args["section"]?.GetValue<string>();
        var query = args["query"]?.GetValue<string>();
        if (!string.IsNullOrWhiteSpace(query))
        {
            var hits = _docs.Search(query);
            return hits.Count == 0
                ? $"文档里没有 \"{query}\""
                : string.Join("\n", hits.Select(h => $"{h.Name}:{h.Line}: {h.Text}"));
        }
        if (!string.IsNullOrWhiteSpace(name))
        {
            if (!string.IsNullOrWhiteSpace(section))
                return _docs.ReadSection(name, section) ?? $"{name} 里没有包含 \"{section}\" 的标题";
            return _docs.Read(name) ?? $"没有这份文档: {name}(用不带参数的 ysm_docs 看列表)";
        }
        var list = _docs.List();
        return list.Count == 0
            ? $"文档目录为空: {_docs.Dir}"
            : string.Join("\n", list.Select(d => $"{d.Name}  —  {d.Title} ({d.Size / 1024} KB)"));
    }

    private static Explanation ExplainAny(string text, string? kind)
    {
        if (WarningCatalog.IsMolangKind(kind))
            return WarningCatalog.ExplainMolang(kind!, StripMolangPrefix(text, kind!));
        if (kind == "validation")
            return WarningCatalog.ExplainValidation("error", text);
        var colon = text.IndexOf(':');
        if (kind is null && colon > 0 && colon < 6 && WarningCatalog.IsMolangKind(text[..colon]))
            return WarningCatalog.ExplainMolang(text[..colon], text[(colon + 1)..]);
        // 日志/报告里的展示形态: "[!] molang/zero: 标签 xN" / "[i] molang/const: ..."
        var shown = System.Text.RegularExpressions.Regex.Match(text, @"molang/([a-z]+):\s*(.*?)(?:\s+x\d+)?\s*$");
        if (shown.Success && WarningCatalog.IsMolangKind(shown.Groups[1].Value))
            return WarningCatalog.ExplainMolang(shown.Groups[1].Value, shown.Groups[2].Value);
        var level = text.TrimStart().StartsWith("[ERROR]") ? "error" : text.TrimStart().StartsWith("[WARN]") ? "warn" : "notice";
        return WarningCatalog.ExplainLog(level, text);
    }

    /// <summary>"map:is_maid -> 0.0" 这类带类别前缀的原始标签 → 正文(不带前缀原样返回)。</summary>
    private static string StripMolangPrefix(string text, string kind)
    {
        var colon = text.IndexOf(':');
        return colon > 0 && colon < 6 && WarningCatalog.IsMolangKind(text[..colon]) ? text[(colon + 1)..] : text;
    }

    private static List<string> StringList(JsonNode? node)
    {
        var list = new List<string>();
        if (node is JsonArray arr)
            foreach (var item in arr)
                if (item is not null) list.Add(item.GetValue<string>());
        else if (node is JsonValue value && value.TryGetValue<string>(out var single))
            list.Add(single);
        return list;
    }

    private static int? Int(JsonNode? node)
    {
        if (node is JsonValue value)
        {
            if (value.TryGetValue<int>(out var i)) return i;
            if (value.TryGetValue<double>(out var d)) return (int)d;
            if (value.TryGetValue<string>(out var s) && int.TryParse(s, out var parsed)) return parsed;
        }
        return null;
    }

    private static bool? Bool(JsonNode? node)
    {
        if (node is JsonValue value)
        {
            if (value.TryGetValue<bool>(out var b)) return b;
            if (value.TryGetValue<string>(out var s) && bool.TryParse(s, out var parsed)) return parsed;
        }
        return null;
    }
}
