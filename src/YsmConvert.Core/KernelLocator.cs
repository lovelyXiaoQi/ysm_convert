namespace YsmConvert.Core;

/// <summary>转换内核的落点: 便携 Python 解释器 + 同步自 YSM 仓库的 devtools 快照。</summary>
public sealed record KernelPaths(
    string CoreDir,
    string PythonExe,
    IReadOnlyList<string> PythonArgsPrefix,
    string PythonSource,
    string KernelRoot,
    string DocsDir)
{
    public string PortCli => Path.Combine(KernelRoot, "devtools", "port_cli.py");

    /// <summary>内核自带的 java_default 基线所在资源包(独立组件模式下的参考资源包)。</summary>
    public string BundledRefRp => Path.Combine(KernelRoot, "ysm_rp");

    public string SourceInfoFile => Path.Combine(KernelRoot, "KERNEL_SOURCE.json");

    /// <summary>用的是随包的便携解释器(发布形态), 而不是开发机上装的 Python。</summary>
    public bool UsesBundledPython => PythonSource == KernelLocator.BundledPythonSource;
}

/// <summary>
/// 找 core/ 目录: 环境变量 YSMCONV_CORE > 程序目录下的 core > 向上逐级找 core/kernel/devtools/port_cli.py(开发期 bin/Debug 布局)。
/// Python 解释器只在 core/python 整个不存在时(= 开发机还没跑 make-python.ps1)才回落到系统安装;
/// core/python 在、python.exe 不在, 说明发布包被削过, 直接报错 —— 悄悄改用系统 Python 会让产物口径失控。
/// </summary>
public static class KernelLocator
{
    public const string CoreDirEnv = "YSMCONV_CORE";
    public const string BundledPythonSource = "bundled";

    public static KernelPaths Locate()
    {
        if (!TryLocate(out var paths, out var error))
            throw new InvalidOperationException(error);
        return paths!;
    }

    public static bool TryLocate(out KernelPaths? paths, out string? error)
    {
        paths = null;
        var coreDir = FindCoreDir();
        if (coreDir is null)
        {
            error = "找不到转换内核目录 core/(含 kernel/devtools/port_cli.py)。" +
                    $"请把程序放在带 core/ 的目录运行, 或设置环境变量 {CoreDirEnv} 指向 core 目录。";
            return false;
        }
        var kernelRoot = Path.Combine(coreDir, "kernel");
        var docsDir = Path.Combine(coreDir, "docs");
        if (!TryFindPython(coreDir, out var exe, out var prefix, out var source, out error))
            return false;
        paths = new KernelPaths(coreDir, exe!, prefix!, source!, kernelRoot, docsDir);
        return true;
    }

    private static string? FindCoreDir()
    {
        var fromEnv = Environment.GetEnvironmentVariable(CoreDirEnv);
        if (!string.IsNullOrWhiteSpace(fromEnv) && IsCoreDir(fromEnv))
            return Path.GetFullPath(fromEnv);

        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (var depth = 0; dir is not null && depth < 8; depth++, dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "core");
            if (IsCoreDir(candidate))
                return candidate;
        }
        return null;
    }

    private static bool IsCoreDir(string dir) =>
        File.Exists(Path.Combine(dir, "kernel", "devtools", "port_cli.py"));

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
