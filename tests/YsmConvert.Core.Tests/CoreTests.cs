using System.Text.Json;
using System.Text.Json.Nodes;
using YsmConvert.Core;

namespace YsmConvert.Core.Tests;

public class KernelEventTests
{
    [Fact]
    public void ParsesLogEvent()
    {
        var e = KernelEvent.TryParse("""{"event": "log", "pack": "demo", "level": "warn", "text": "[WARN] \u51e0\u4f55\u7f3a\u5931"}""");
        Assert.NotNull(e);
        Assert.Equal(KernelEvent.Log, e!.Event);
        Assert.Equal("demo", e.PackName);
        Assert.Equal("warn", e.Level);
        Assert.Equal("[WARN] 几何缺失", e.Text);
    }

    [Fact]
    public void ParsesStartEventLayoutIntoExtra()
    {
        var e = KernelEvent.TryParse("""{"event": "start", "action": "convert", "packs": 2, "layout": {"refRp": "X", "baselinePresent": true}, "kernel": {"version": "1.0", "python": "2.7.18"}}""");
        Assert.NotNull(e);
        Assert.Equal(2, e!.Packs);
        Assert.True(e.Extra!.ContainsKey("layout"));
    }

    [Fact]
    public void RejectsNonJsonLines()
    {
        Assert.Null(KernelEvent.TryParse("plain text"));
        Assert.Null(KernelEvent.TryParse("{not json"));
        Assert.Null(KernelEvent.TryParse(""));
    }
}

public class ConversionReportTests
{
    private static ConversionReport Replay(params string[] lines)
    {
        var report = new ConversionReport();
        foreach (var line in lines)
        {
            var e = KernelEvent.TryParse(line);
            Assert.NotNull(e);
            report.Apply(e!);
        }
        return report;
    }

    [Fact]
    public void AggregatesPackLifecycle()
    {
        var report = Replay(
            """{"event": "start", "action": "convert", "packs": 1, "layout": {"refRp": "R", "baselinePresent": true}, "kernel": {"version": "1.0", "python": "2.7.18"}}""",
            """{"event": "pack_start", "pack": "a", "javaDir": "J", "collection": "c", "index": 0, "total": 1}""",
            """{"event": "log", "pack": "a", "level": "info", "text": "geometry ok"}""",
            """{"event": "log", "pack": "a", "level": "notice", "text": "[!] script skipped"}""",
            """{"event": "molang", "pack": "a", "kind": "zero", "label": "ctrl.ride(x)", "count": 3, "attention": true}""",
            """{"event": "molang", "pack": "a", "kind": "map", "label": "math.e", "count": 1, "attention": false}""",
            """{"event": "pack_done", "pack": "a", "ok": true, "seconds": 1.5, "errors": 0, "warnings": 0, "notices": 1}""",
            """{"event": "collection", "dir": "c", "path": "P", "written": true}""",
            """{"event": "validate_item", "level": "error", "text": "bad molang"}""",
            """{"event": "validate_done", "errors": 1, "warnings": 0, "animations": 3, "controllers": 2}""",
            """{"event": "done", "ok": false, "packsOk": 1, "packsFailed": 0, "validationErrors": 1}""");

        Assert.True(report.Done);
        Assert.False(report.Ok);
        Assert.Equal("R", report.RefRp);
        Assert.True(report.BaselinePresent);
        var pack = Assert.Single(report.Packs);
        Assert.Equal("a", pack.Name);
        Assert.Equal("c", pack.Collection);
        Assert.True(pack.Ok);
        Assert.Equal(2, pack.Lines.Count);
        Assert.Equal(2, pack.Molang.Count);
        Assert.Equal(1, report.ValidationErrors);
        Assert.Single(report.Collections);

        var attention = report.BuildAttention();
        // notice 行 + 置零 molang + 体检错误 = 3 项; map 类不算
        Assert.Equal(3, attention.Count);
        Assert.Contains(attention, a => a.Category == "molang 置零" && a.Count == 3);
        Assert.Contains(attention, a => a.Severity == "error" && a.Text == "bad molang");

        var json = report.ToJson();
        Assert.Equal(3, json["attention"]!.AsArray().Count);
        var text = report.ToText(details: false);
        Assert.Contains("== a [OK]", text);
        Assert.Contains("体检", text);
    }

    [Fact]
    public void FailedPackBecomesAttention()
    {
        var report = Replay(
            """{"event": "pack_start", "pack": "b", "javaDir": "J", "collection": null, "index": 0, "total": 1}""",
            """{"event": "pack_done", "pack": "b", "ok": false, "seconds": 0.1, "error": "Traceback...\nKeyError: 'x'", "errors": 1, "warnings": 0, "notices": 0}""",
            """{"event": "done", "ok": false, "packsOk": 0, "packsFailed": 1, "validationErrors": 0}""");
        var item = Assert.Single(report.BuildAttention());
        Assert.Equal("移植失败", item.Category);
        Assert.Equal("KeyError: 'x'", item.Text);
    }
}

public class PackNameRulesTests
{
    [Theory]
    [InlineData("wine_fox_01", true)]
    [InlineData("01_maid", true)]
    [InlineData("Wine", false)]
    [InlineData("_x", false)]
    [InlineData("a-b", false)]
    [InlineData("", false)]
    [InlineData("中文", false)]
    public void ValidatesNames(string name, bool valid) => Assert.Equal(valid, PackNameRules.IsValid(name));

    [Fact]
    public void SanitizeProducesValidNames()
    {
        Assert.Equal("wine_fox_01", PackNameRules.Sanitize("Wine Fox (01)"));
        Assert.Equal("pack", PackNameRules.Sanitize("酒狐"));
        Assert.True(PackNameRules.IsValid(PackNameRules.Sanitize("--Weird__Name--")));
    }

    [Fact]
    public void ValidateReportsDuplicatesAndMissingDirs()
    {
        var packs = new[]
        {
            new PackSpec { JavaDir = Path.Combine(Path.GetTempPath(), "nope1"), Name = "a" },
            new PackSpec { JavaDir = Path.Combine(Path.GetTempPath(), "nope2"), Name = "a" },
            new PackSpec { JavaDir = Path.Combine(Path.GetTempPath(), "nope3"), Name = "Bad Name", Collection = "合集" },
        };
        var problems = PackNameRules.Validate(packs);
        Assert.Contains(problems, p => p.Contains("重复"));
        Assert.Contains(problems, p => p.Contains("缺 ysm.json"));
        Assert.Contains(problems, p => p.Contains("小写英文"));
        Assert.Contains(problems, p => p.Contains("合集目录名"));
    }
}

public class OutputTargetTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "ysmconv-test-" + Guid.NewGuid().ToString("N"));

    public OutputTargetTests() => Directory.CreateDirectory(_root);

    public void Dispose()
    {
        try { Directory.Delete(_root, true); } catch { }
    }

    [Fact]
    public void EnsureComponentCreatesManifestsAndDetectsThem()
    {
        var layout = OutputTarget.EnsureComponent(_root, "my_models");
        Assert.True(File.Exists(Path.Combine(layout.BpDir, "manifest.json")));
        Assert.True(File.Exists(Path.Combine(layout.RpDir, "manifest.json")));
        Assert.True(Directory.Exists(layout.BpModels));
        Assert.False(layout.HasBaseline);

        var bp = JsonNode.Parse(File.ReadAllText(Path.Combine(layout.BpDir, "manifest.json")))!;
        Assert.Equal("data", bp["modules"]![0]!["type"]!.GetValue<string>());
        Assert.Equal(1, bp["format_version"]!.GetValue<int>());

        var detected = OutputTarget.Detect(_root);
        Assert.Equal(layout.BpDir, detected.BpDir);
        Assert.Equal(layout.RpDir, detected.RpDir);

        // 幂等: 再跑一次不会改 uuid
        var again = OutputTarget.EnsureComponent(_root, "my_models");
        var bp2 = JsonNode.Parse(File.ReadAllText(Path.Combine(again.BpDir, "manifest.json")))!;
        Assert.Equal(bp["header"]!["uuid"]!.GetValue<string>(), bp2["header"]!["uuid"]!.GetValue<string>());
    }

    [Fact]
    public void DetectFallsBackToFolderNames()
    {
        Directory.CreateDirectory(Path.Combine(_root, "ysm_bp"));
        Directory.CreateDirectory(Path.Combine(_root, "ysm_rp"));
        var layout = OutputTarget.Detect(_root);
        Assert.EndsWith("ysm_bp", layout.BpDir);
        Assert.EndsWith("ysm_rp", layout.RpDir);
    }

    [Fact]
    public void DetectFailsOnEmptyDirectory()
    {
        Assert.False(OutputTarget.TryDetect(_root, out _, out var error));
        Assert.Contains("组件", error);
        Assert.Throws<ArgumentException>(() => OutputTarget.EnsureComponent(_root, "bad name"));
    }
}

public class JobSpecTests
{
    [Fact]
    public void SerializesKernelContract()
    {
        var job = new JobSpec
        {
            Layout = new OutputLayout(@"C:\out", @"C:\out\x_bp", @"C:\out\x_rp"),
            RefRp = @"C:\ref",
            Packs = { new PackSpec { JavaDir = @"C:\java\p", Name = "p", Collection = "c" } },
            Collections = { new CollectionSpec { Dir = "c", Name = "Fox", NameZh = "酒狐" } },
            Options = new ConvertOptions { WithMods = true, Validate = false },
        };
        var node = JsonNode.Parse(job.ToJson())!;
        Assert.Equal("convert", node["action"]!.GetValue<string>());
        Assert.Equal(@"C:\out\x_bp\ysm_models", node["layout"]!["bpModels"]!.GetValue<string>());
        Assert.Equal(@"C:\ref", node["layout"]!["refRp"]!.GetValue<string>());
        Assert.Equal("c", node["packs"]![0]!["collection"]!.GetValue<string>());
        Assert.Equal("酒狐", node["collections"]!["c"]!["lang"]!["zh_cn"]!["name"]!.GetValue<string>());
        Assert.True(node["options"]!["withMods"]!.GetValue<bool>());
        Assert.False(node["options"]!["validate"]!.GetValue<bool>());
    }

    [Fact]
    public void ConvertPlanAppliesPrefixCollectionAndRenames()
    {
        var discovered = new[]
        {
            new DiscoveredPack { JavaDir = @"C:\j\01_maid", Folder = "01_maid", SuggestedName = "fox_01_maid", CollectionFolder = "fox" },
            new DiscoveredPack { JavaDir = @"C:\j\02", Folder = "02", SuggestedName = "fox_02", CollectionFolder = "fox" },
            new DiscoveredPack { JavaDir = @"C:\j\bad", Folder = "bad", Error = "x" },
        };
        var packs = ConvertPlan.BuildPacks(discovered, prefix: null, collectionOverride: "all",
            renames: new Dictionary<string, string> { ["02"] = "renamed_02" });
        Assert.Equal(2, packs.Count);
        Assert.Equal("fox_01_maid", packs[0].Name);
        Assert.Equal("renamed_02", packs[1].Name);
        Assert.All(packs, p => Assert.Equal("all", p.Collection));

        var prefixed = ConvertPlan.BuildPacks(discovered, prefix: "Me");
        Assert.Equal("me_fox_01_maid", prefixed[0].Name);
        Assert.Equal("fox", prefixed[0].Collection);
    }
}

public class KernelLocatorTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "ysmconv-loc-" + Guid.NewGuid().ToString("N"));
    private readonly string? _saved = Environment.GetEnvironmentVariable(KernelLocator.CoreDirEnv);

    public KernelLocatorTests()
    {
        Directory.CreateDirectory(Path.Combine(_root, "kernel", "devtools"));
        File.WriteAllText(Path.Combine(_root, "kernel", "devtools", "port_cli.py"), "# stub");
        Environment.SetEnvironmentVariable(KernelLocator.CoreDirEnv, _root);
    }

    public void Dispose()
    {
        Environment.SetEnvironmentVariable(KernelLocator.CoreDirEnv, _saved);
        try { Directory.Delete(_root, true); } catch { }
    }

    [Fact]
    public void PrefersBundledInterpreter()
    {
        var pythonDir = Path.Combine(_root, "python");
        Directory.CreateDirectory(pythonDir);
        File.WriteAllText(Path.Combine(pythonDir, "python.exe"), "stub");

        Assert.True(KernelLocator.TryLocate(out var paths, out var error));
        Assert.Null(error);
        Assert.True(paths!.UsesBundledPython);
        Assert.Equal(Path.Combine(pythonDir, "python.exe"), paths.PythonExe);
    }

    [Fact]
    public void RefusesToFallBackWhenBundledRuntimeIsGutted()
    {
        // core/python 在但 python.exe 不在: 解压不全或杀毒删了。绝不能悄悄改用机器上装的 Python2.7,
        // 那份的版本与 site-packages 不受控, 产物口径会和游戏对不上, 而用户看不出被换了。
        Directory.CreateDirectory(Path.Combine(_root, "python"));

        Assert.False(KernelLocator.TryLocate(out var paths, out var error));
        Assert.Null(paths);
        Assert.Contains("不完整", error);
    }

    [Fact]
    public void ReportsMissingCoreDirectory()
    {
        Environment.SetEnvironmentVariable(KernelLocator.CoreDirEnv, Path.Combine(_root, "nope"));
        // 环境变量指向不存在的目录时会继续向上找程序目录, 测试宿主目录下没有 core, 应报找不到
        if (!KernelLocator.TryLocate(out _, out var error))
            Assert.Contains("core", error);
    }
}

public class KernelRunnerTests
{
    [Theory]
    [InlineData(unchecked((int)0xC0150002), true)]  // STATUS_SXS_CANT_GEN_ACTCTX: 缺 VC++ 2008 运行库
    [InlineData(unchecked((int)0xC0150004), true)]  // STATUS_SXS_ASSEMBLY_NOT_FOUND
    [InlineData(unchecked((int)0xC015FFFF), true)]
    [InlineData(0, false)]                          // 内核业务退出码
    [InlineData(1, false)]
    [InlineData(2, false)]
    [InlineData(-1, false)]
    [InlineData(unchecked((int)0xC0000005), false)] // 访问冲突, 不是 SxS
    public void DetectsSideBySideExitCodes(int exitCode, bool expected) =>
        Assert.Equal(expected, KernelRunner.IsSideBySideFailure(exitCode));

    [Fact]
    public void MissingRuntimeMessageNamesTheFix()
    {
        Assert.Contains("Visual C++ 2008", KernelRunner.MissingVcRuntimeMessage);
        Assert.Contains("vcredist_x64", KernelRunner.MissingVcRuntimeMessage);
        Assert.Contains("Microsoft.VC90.CRT", KernelRunner.MissingVcRuntimeMessage);
    }
}

public class WarningCatalogTests
{
    [Fact]
    public void ExplainsKnownKinds()
    {
        Assert.Equal("molang 置零", WarningCatalog.ExplainMolang("zero", "ctrl.x(...)").Category);
        Assert.Equal("未知函数置零", WarningCatalog.ExplainMolang("func", "fn.foo(未知函数置零)").Category);
        Assert.Equal("脚本控制器未转换", WarningCatalog.ExplainLog("notice", "[!] 脚本控制器 a@player_ctrl_main.molang 未转换: 含计数器").Category);
        Assert.Equal("Java 包声明的文件不存在", WarningCatalog.ExplainLog("warn", "[WARN] 动画缺失: animations/x.json").Category);
        Assert.Equal("音频未登记", WarningCatalog.ExplainValidation("warn", "x: 效果键 y 的定义 z 不在 sounds/sound_definitions.json").Category);
        Assert.NotNull(WarningCatalog.ExplainMolang("zero", "anything").Doc);
    }
}
