namespace YsmConvert.Core;

/// <summary>转换内核的落点: 便携 Python 解释器 + devtools(项目源码或随包快照)。</summary>
public sealed record KernelPaths(
    string CoreDir,
    string PythonExe,
    IReadOnlyList<string> PythonArgsPrefix,
    string PythonSource,
    string KernelRoot,
    string DocsDir,
    bool FromProject = false)
{
    public string PortCli => Path.Combine(KernelRoot, "devtools", "port_cli.py");

    /// <summary>内核自带的 java_default 基线所在资源包(独立组件模式下的参考资源包)。</summary>
    public string BundledRefRp => Path.Combine(KernelRoot, "ysm_rp");

    public string SourceInfoFile => Path.Combine(KernelRoot, "KERNEL_SOURCE.json");

    /// <summary>用的是随包的便携解释器(发布形态), 而不是开发机上装的 Python。</summary>
    public bool UsesBundledPython => PythonSource == KernelLocator.BundledPythonSource;

    /// <summary>内核来源的人话说明(info / MCP 里显示)。</summary>
    public string KernelSourceText => FromProject ? "项目 devtools(实时)" : "随包快照 core/kernel";
}

/// <summary>
/// 定内核根: 环境变量 YSMCONV_CORE > 程序目录旁的 core(发布包形态) > 向上找到的 YSM 工程根(开发形态, 直接用
/// 工程的 devtools/ 与 ysm_rp/) > 向上找到的 core/kernel(旧开发形态)。
///
/// 转换器就住在 YSM 工程里(&lt;工程&gt;/ysm_convert), 开发时直接跑工程的 devtools —— 改了规则不用先同步快照,
/// 也就不存在"两份内核各自演化"。发布包里没有工程, 用 build/sync-core.ps1 拷进去的 core/kernel 快照;
/// 程序目录旁有 core 时优先用它, 免得把解压到工程里的发布包"测成"实时内核。
///
/// Python 解释器只在 core/python 整个不存在时(= 开发机还没跑 make-python.ps1)才回落到系统安装;
/// core/python 在、python.exe 不在, 说明发布包被削过, 直接报错 —— 悄悄改用系统 Python 会让产物口径失控。
/// </summary>
public static class KernelLocator
{
    public const string CoreDirEnv = "YSMCONV_CORE";
    public const string BundledPythonSource = "bundled";
    private const int MaxWalkUp = 8;

    public static KernelPaths Locate()
    {
        if (!TryLocate(out var paths, out var error))
            throw new InvalidOperationException(error);
        return paths!;
    }

    public static bool TryLocate(out KernelPaths? paths, out string? error) =>
        TryLocateFrom(AppContext.BaseDirectory, out paths, out error);

    /// <summary>从指定目录起解析(单元测试用; 正式入口是 <see cref="TryLocate"/>)。</summary>
    public static bool TryLocateFrom(string startDir, out KernelPaths? paths, out string? error)
    {
        paths = null;
        var fromEnv = Environment.GetEnvironmentVariable(CoreDirEnv);
        if (!string.IsNullOrWhiteSpace(fromEnv) && IsCoreDir(fromEnv))
            return Build(Path.GetFullPath(fromEnv), null, out paths, out error);

        // 发布包: 程序目录旁就是 core/(解压到工程里也按发布包算, 不去用工程的实时内核)
        var besideExe = Path.Combine(startDir, "core");
        if (IsCoreDir(besideExe))
            return Build(besideExe, null, out paths, out error);

        string? core = null, project = null;
        var dir = new DirectoryInfo(startDir);
        for (var depth = 0; dir is not null && depth < MaxWalkUp; depth++, dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "core");
            if (core is null && IsCoreDir(candidate))
                core = candidate;
            if (project is null && IsProjectRoot(dir.FullName))
                project = dir.FullName;
            // core/python 与工程各在一层: 两个都要接着往上找, 不能找到一个就停
        }
        if (project is not null)
            return Build(core ?? Path.Combine(Path.GetDirectoryName(project) ?? project, "core"), project, out paths, out error);
        if (core is not null)
            return Build(core, null, out paths, out error);

        error = "找不到转换内核: 程序目录旁没有 core/(kernel/devtools/port_cli.py), 也没找到 YSM 工程根" +
                $"(含 devtools/port_cli.py 与 ysm_rp/animations/java_default)。把程序放在带 core/ 的目录运行, " +
                $"或设置环境变量 {CoreDirEnv} 指向 core 目录。";
        return false;
    }

    private static bool Build(string coreDir, string? projectRoot, out KernelPaths? paths, out string? error)
    {
        paths = null;
        if (!TryFindPython(coreDir, out var exe, out var prefix, out var source, out error))
            return false;
        var kernelRoot = projectRoot ?? Path.Combine(coreDir, "kernel");
        var docsDir = projectRoot is null ? Path.Combine(coreDir, "docs") : Path.Combine(projectRoot, "docs");
        paths = new KernelPaths(coreDir, exe!, prefix!, source!, kernelRoot, docsDir, projectRoot is not null);
        return true;
    }

    private static bool IsCoreDir(string dir) =>
        File.Exists(Path.Combine(dir, "kernel", "devtools", "port_cli.py"));

    /// <summary>YSM 工程根: devtools 的内核入口 + 带 java_default 基线的资源包(两样都在才是工程, 不会误认 core/kernel)。</summary>
    private static bool IsProjectRoot(string dir) =>
        File.Exists(Path.Combine(dir, "devtools", "port_cli.py"))
        && Directory.Exists(Path.Combine(dir, "ysm_rp", "animations", "java_default"));

    private static bool TryFindPython(string coreDir, out string? exe, out IReadOnlyList<string>? prefix,
        out string? source, out string? error)
    {
        exe = null;
        prefix = null;
        source = null;
        error = null;

        var pythonDir = Path.Combine(coreDir, "python");
        var bundled = Path.Combine(pythonDir, "python.exe");
        if (File.Exists(bundled))
        {
            exe = bundled;
            prefix = Array.Empty<string>();
            source = BundledPythonSource;
            return true;
        }
        if (Directory.Exists(pythonDir))
        {
            // 发布包里 core/python 应当是完整的一份解释器。它在、python.exe 却不在, 多半是解压不全或被杀毒删了。
            // 这时绝不能回落到机器上装的 Python: 那份的版本与 site-packages 都不受控, 转换产物要和游戏内
            // 同一个 2.7 解释器对齐才作数, 而且悄悄换掉用户根本看不出来。
            error = $"随包的 Python 运行时不完整, 缺 {bundled}。\n" +
                    "请重新完整解压发布包; 若是杀毒软件删的, 把整个目录加进白名单后重新解压。";
            return false;
        }

        // core/python 整个不在 = 开发机上还没跑过 build/make-python.ps1, 用本机的 Python 2.7 顶上
        var system = @"C:\Python27\python.exe";
        if (File.Exists(system))
        {
            exe = system;
            prefix = Array.Empty<string>();
            source = @"C:\Python27";
            return true;
        }
        var launcher = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "py.exe");
        if (File.Exists(launcher))
        {
            exe = launcher;
            prefix = new[] { "-2.7" };
            source = "py -2.7";
            return true;
        }
        error = $"找不到 Python 2.7 解释器: 发布包应带 {bundled}(由 build/make-python.ps1 生成); " +
                "开发环境下也可以装一份 C:\\Python27, 或让 py 启动器带 2.7。";
        return false;
    }
}
