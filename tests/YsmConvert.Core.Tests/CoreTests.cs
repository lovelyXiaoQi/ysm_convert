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
        // 网易按 entities 文件夹识别行为包, 没有就不挂载; 放 .gitkeep 让它进得了 git
        Assert.True(File.Exists(Path.Combine(layout.BpDir, "entities", ".gitkeep")));

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
    public void BehaviorPackMarkerLeavesExistingEntitiesAlone()
    {
        var bp = Path.Combine(_root, "ysm_bp");
        var entities = Path.Combine(bp, "entities");
        Directory.CreateDirectory(entities);
        File.WriteAllText(Path.Combine(entities, "__init__.py"), "");
        OutputTarget.EnsureBehaviorPackMarker(bp);
        Assert.Equal(new[] { "__init__.py" }, Directory.GetFiles(entities).Select(Path.GetFileName).ToArray());

        // 缺的补上, 再跑一次不变
        var bare = Path.Combine(_root, "bare_bp");
        Directory.CreateDirectory(bare);
        OutputTarget.EnsureBehaviorPackMarker(bare);
        OutputTarget.EnsureBehaviorPackMarker(bare);
        Assert.Equal(new[] { ".gitkeep" }, Directory.GetFiles(Path.Combine(bare, "entities")).Select(Path.GetFileName).ToArray());
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
            Collections = { new CollectionSpec { Dir = "c", Name = "酒狐", CoverImage = @"C:\art\cover.png" } },
            Options = new ConvertOptions { WithMods = true, Validate = false },
        };
        var node = JsonNode.Parse(job.ToJson())!;
        Assert.Equal("convert", node["action"]!.GetValue<string>());
        Assert.Equal(@"C:\out\x_bp\ysm_models", node["layout"]!["bpModels"]!.GetValue<string>());
        Assert.Equal(@"C:\ref", node["layout"]!["refRp"]!.GetValue<string>());
        Assert.Equal("c", node["packs"]![0]!["collection"]!.GetValue<string>());
        // 网易版文件夹只显示一个名字: 写 name, 不再分中英文 lang
        Assert.Equal("酒狐", node["collections"]!["c"]!["name"]!.GetValue<string>());
        Assert.Null(node["collections"]!["c"]!["lang"]);
        Assert.Equal(@"C:\art\cover.png", node["collections"]!["c"]!["coverImage"]!.GetValue<string>());
        Assert.True(node["options"]!["withMods"]!.GetValue<bool>());
        Assert.False(node["options"]!["validate"]!.GetValue<bool>());
        Assert.True(node["options"]!["compactJson"]!.GetValue<bool>());       // 缺省压一行
        Assert.True(node["options"]!["writeCollections"]!.GetValue<bool>());  // 只有按包拆出的子任务才关
    }

    [Fact]
    public void ParallelismStaysWithinBounds()
    {
        Assert.Equal(1, ConversionService.EffectiveParallelism(0, 1));
        Assert.Equal(3, ConversionService.EffectiveParallelism(3, 10));
        Assert.Equal(2, ConversionService.EffectiveParallelism(5, 2));       // 不超过包数
        Assert.InRange(ConversionService.AutoParallelism(), 1, 8);
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

public class CollectionCoverTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "ysmconv-cover-" + Guid.NewGuid().ToString("N"));

    public CollectionCoverTests() => Directory.CreateDirectory(_dir);

    public void Dispose()
    {
        try { Directory.Delete(_dir, true); } catch { }
    }

    private string Write(string name, int bytes)
    {
        var path = Path.Combine(_dir, name);
        File.WriteAllBytes(path, new byte[bytes]);
        return path;
    }

    [Fact]
    public void ValidatesCoverImage()
    {
        CollectionSpec Spec(string? cover) => new() { Dir = "fox", CoverImage = cover };
        Assert.Null(Spec(null).CoverProblem());
        Assert.Null(Spec(Write("ok.png", 1000)).CoverProblem());
        Assert.Contains("不存在", Spec(Path.Combine(_dir, "missing.png")).CoverProblem());
        Assert.Contains("PNG", Spec(Write("art.jpg", 1000)).CoverProblem());
        Assert.Contains("1MB", Spec(Write("huge.png", (int)CollectionSpec.MaxCoverBytes + 1)).CoverProblem());
    }

    [Fact]
    public void EmptySpecIsDetected()
    {
        Assert.True(new CollectionSpec { Dir = "fox", Name = "  " }.IsEmpty);
        Assert.False(new CollectionSpec { Dir = "fox", Name = "狐狸" }.IsEmpty);
        Assert.False(new CollectionSpec { Dir = "fox", CoverImage = "a.png" }.IsEmpty);
        var manifest = new CollectionSpec { Dir = "fox", Name = " 狐狸 ", CoverImage = Write("c.png", 10) }.ToManifest();
        Assert.Equal("狐狸", manifest["name"]!.GetValue<string>());
        Assert.True(Path.IsPathRooted(manifest["coverImage"]!.GetValue<string>()));
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

    [Theory]
    [InlineData("const", "is_maid -> 0.0", "molang 中性常量")]
    [InlineData("const", "ctrl.tac_hold_gun -> 0.0", "TACZ 联动未接入")]
    [InlineData("const", "ctrl.parcool_state -> ''", "模组联动按未安装处理")]
    [InlineData("const", "ysm.perlin_noise(置常量)", "molang 中性常量")]
    [InlineData("func", "ysm.stop_sound(置常量)", "molang 中性常量")]           // 旧内核的原始类别
    [InlineData("map", "entity_type -> 'player'", "molang 中性常量")]             // 手工传入的原始标签
    [InlineData("map", "weather -> query.mod.ysm_weather", "主组件运行层提供")]
    [InlineData("map", "ysm.bone_rot('HairB1').x -> 骨骼旋转回读变量", "主组件运行层提供")]
    [InlineData("map", "ysm.equipped_enchantment_level(...) -> variable.ysm_pb_enchant_level_fire_aspect_24445b73(主包运行层探针)", "主组件运行层提供")]
    [InlineData("map", "has_mainhand -> query.is_item_equipped(0)", "molang 映射")]
    [InlineData("map", "true/false 关键字 -> 1.0/0.0", "molang 映射")]
    [InlineData("func", "ysm.second_order(物理 → molang 状态积分)", "物理函数改写")]
    [InlineData("norm", "catmullrom 分段语义 Java(任一端) -> 基岩(出边)换算", "按 Java 语义规范化")]
    [InlineData("skip", "控制器 player.parallel_5(initial_state default 不存在, Java 侧永不动作)", "Java 同样不挂载")]
    [InlineData("tick", "每 tick 脚本动画(loop+显式 0 长度+timeline) -> 0.05s 循环", "每 tick 脚本动画")]
    public void ExplainsMolangByKernelWording(string kind, string label, string category) =>
        Assert.Equal(category, WarningCatalog.ExplainMolang(kind, label).Category);

    [Theory]
    [InlineData("基岩解析不了的表达式按 Java 口径置 0 / 删除(Java 同样解析失败): parallel0 timeline/0.0/1: 此处需要操作数, 遇到 ';' <- [;", "作者原文解析失败")]
    [InlineData("fn.move(自定义函数脚本, 基岩无对应)", "自定义函数置零")]
    [InlineData("tlm.is_sitting(无映射)", "车万女仆联动置零")]
    [InlineData("ysm.bone_pos(...).成员(结构体成员访问, 基岩无对应, 连同调用一起置零)", "molang 置零")]
    [InlineData("query.foo(引擎二进制无此名)", "基岩没有的查询")]
    [InlineData("残缺前缀 ysm./ctrl./fn.(点后无标识符, 直接删除)", "作者原文残句")]
    [InlineData("ctrl.hold(x,y)(槽位/分类无法转换)", "物品条件转换失败")]
    [InlineData("ctrl.wal(无映射)", "molang 置零")]
    public void ExplainsZeroByKernelWording(string label, string category) =>
        Assert.Equal(category, WarningCatalog.ExplainMolang("zero", label).Category);

    [Theory]
    [InlineData("sound_effects 原版音效 minecraft:item.trident.throw 两边命名不同, 定义名直通 item.trident.throw(基岩查无即无声, 需人工对照)", "原版音效命名差异")]
    [InlineData("ysm.particle 位置为表达式, 落到实体原点 locator", "粒子位置近似")]
    [InlineData("ysm.abs_particle 世界绝对偏移近似为随身 locator", "粒子位置近似")]
    [InlineData("ysm.particle(minecraft:foo) 无基岩对照, 原名直通(引擎查无即无效果)", "粒子 ID 无对照")]
    [InlineData("ysm.texture_name 比较的贴图 'skin_x' 不在本包贴图表里(== 恒假)", "贴图名比较恒不成立")]
    [InlineData("ysm.effect_level(参数不是常量, 无法登记成探针, 按旧策略置常量)", "运行层探针未登记")]
    [InlineData("query.foo(文档亦无, 证据缺失已保留 — 若文件作废先实机探针此名)", "引擎支持待确认")]
    [InlineData("math.foo(证据缺失, 已保留 — 需人工确认)", "引擎支持待确认")]
    [InlineData("math.clamp(参数不足, 保留原样)", "math 参数个数不对")]
    [InlineData("ysm.second_order(键非字面量或参数残缺, 退化为取输入)", "molang 退化")]
    public void ExplainsWarnByKernelWording(string label, string category) =>
        Assert.Equal(category, WarningCatalog.ExplainMolang("warn", label).Category);

    [Theory]
    [InlineData("a -> 0.0", true)]
    [InlineData("a -> -1", true)]
    [InlineData("a -> ''", true)]
    [InlineData("a -> 'player'", true)]
    [InlineData("a -> 1.0/0.0", false)]
    [InlineData("a -> 'x' + 'y'", false)]
    [InlineData("a -> query.is_item_equipped(0)", false)]
    [InlineData("no arrow 0.0", false)]
    public void DetectsConstantReplacement(string label, bool expected) =>
        Assert.Equal(expected, WarningCatalog.IsConstantReplacement(label));

    [Theory]
    [InlineData("a/b.json animations/x: 输入状态变量 variable.ysm_block_hold 裸读(不做初始化, 必须带 ?? 回落): x", "运行期变量裸读")]
    [InlineData("pack: 占用变量 variable.ysm_own_x 被读取但没有任何控制器状态 on_entry 写入(伴生动画永不让出通道)", "逐通道覆盖不完整")]
    [InlineData("pack: sound_effects 效果键 ysm_snd_x 未在 ysm.json 的 files.player.sound_effect 登记(无声)", "音频未登记")]
    [InlineData("a/b.json animations/x: 未转换的 Java 专有 token: ysm.foo", "Java 写法残留")]
    [InlineData("a/b.json animations/x: 基岩解析不了的 molang(整份文件拒载): x", "molang 语法红线")]
    [InlineData("行为包 my_models_bp: 缺 entities 文件夹, 网易按它识别行为包 —— 没有它 MC Studio 测试与正式游戏都不挂载这个行为包, 模型不会出现在选择界面(MCDK 按 manifest 建联接, 测不出来); 修复(fix)或重新转换会补上 entities/.gitkeep(D:\\out\\my_models_bp)", "行为包不会被挂载")]
    public void ExplainsValidationByKernelWording(string text, string category) =>
        Assert.Equal(category, WarningCatalog.ExplainValidation("error", text).Category);

    [Fact]
    public void RiptideIsNotTreatedAsModCompat() =>
        Assert.Contains("激流", WarningCatalog.ExplainMolang("const", "ctrl.riptide -> 0.0").Advice);

    [Fact]
    public void JavaStateLineIsExplained() =>
        Assert.Equal("主组件运行层", WarningCatalog.ExplainLog("info", "java_state(顶层) → 运行层状态 weather/open_air, roaming 变量 13 个(存档 + 多人同步)").Category);
}

public class MolangPresentationTests
{
    private static ConversionReport Replay(params string[] lines)
    {
        var report = new ConversionReport();
        foreach (var line in lines)
            report.Apply(KernelEvent.TryParse(line)!);
        return report;
    }

    [Fact]
    public void ConstantsAreInfoAndLowercaseRenamesMergePerPack()
    {
        var report = Replay(
            """{"event": "pack_start", "pack": "a", "javaDir": "J", "index": 0, "total": 1}""",
            """{"event": "molang", "pack": "a", "kind": "lower", "label": "Mouth_1 -> mouth_1", "count": 1, "attention": true}""",
            """{"event": "molang", "pack": "a", "kind": "const", "label": "ctrl.tac_hold_gun -> 0.0", "count": 6, "attention": true}""",
            """{"event": "molang", "pack": "a", "kind": "lower", "label": "Mouth_2 -> mouth_2", "count": 1, "attention": true}""",
            """{"event": "molang", "pack": "a", "kind": "zero", "label": "fn.move(自定义函数脚本, 基岩无对应)", "count": 4, "attention": true}""",
            """{"event": "molang", "pack": "a", "kind": "lower", "label": "Sit_1 -> sit_1", "count": 1, "attention": true}""",
            """{"event": "molang", "pack": "a", "kind": "map", "label": "weather -> query.mod.ysm_weather", "count": 2, "attention": false}""",
            """{"event": "pack_done", "pack": "a", "ok": true, "seconds": 1, "errors": 0, "warnings": 0, "notices": 0}""");

        var attention = report.BuildAttention();
        Assert.Equal(3, attention.Count);                          // 置零 + 中性常量 + 小写汇总一行
        Assert.Equal("notice", attention[0].Severity);              // 要看的排在前面
        Assert.Equal("自定义函数置零", attention[0].Category);
        Assert.Contains(attention, a => a is { Severity: "info", Category: "TACZ 联动未接入", Count: 6 });
        var lower = Assert.Single(attention, a => a.Category == "动画名转小写");
        Assert.Equal("info", lower.Severity);
        Assert.StartsWith("3 个动画名改成小写: Mouth_1→mouth_1", lower.Text);
        Assert.Equal(3, report.Packs[0].AttentionCount);

        var text = report.ToText(details: false);
        Assert.Contains("[i] molang/lower: 3 个动画名改成小写", text);
        Assert.Contains("[i] molang/const: ctrl.tac_hold_gun -> 0.0 x6", text);
        Assert.Contains("[!] molang/zero: fn.move", text);
        var raw = report.ToJson()["packs"]![0]!["molangAttention"]!.AsArray();
        Assert.Equal(5, raw.Count);                                  // 机读形态保留逐条
        Assert.Equal("info", raw[1]!["severity"]!.GetValue<string>());
    }

    [Fact]
    public void StreamBufferHoldsLowercaseRenamesUntilPackDone()
    {
        var buffer = new MolangPresentation.StreamBuffer();
        KernelEvent Event(string pack, string kind, string label, bool attention = true) =>
            new() { Event = KernelEvent.Molang, PackName = pack, Kind = kind, Label = label, Count = 2, Attention = attention };

        Assert.Equal("[!] molang/zero: fn.x(y) x2", buffer.Line(Event("a", "zero", "fn.x(y)")));
        Assert.Equal("[i] molang/const: is_maid -> 0.0 x2", buffer.Line(Event("a", "const", "is_maid -> 0.0")));
        Assert.Null(buffer.Line(Event("a", "map", "head_yaw -> x", attention: false)));
        Assert.Null(buffer.Line(Event("a", "lower", "A -> a")));
        Assert.Null(buffer.Line(Event("b", "lower", "B -> b")));
        Assert.Equal("[i] molang/lower: 1 个动画名改成小写: A→a", buffer.Flush("a"));
        Assert.Null(buffer.Flush("a"));
        Assert.Equal("[i] molang/lower: 1 个动画名改成小写: B→b", buffer.Flush("b"));
    }
}
