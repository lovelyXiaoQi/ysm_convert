using System.IO;
using System.Windows;

namespace YsmConvert.App;

public partial class App : Application
{
    /// <summary>命令行里给的目录(把 Java 包目录拖到 exe 上 / "打开方式"), 主窗口加载后直接加进任务列表。</summary>
    public string[] StartupPaths { get; private set; } = Array.Empty<string>();

    /// <summary>--out &lt;目录&gt;: 启动时填好目标根目录。</summary>
    public string? StartupOut { get; private set; }

    /// <summary>--component &lt;名&gt;: 启动时切到独立组件模式并填好组件名。</summary>
    public string? StartupComponent { get; private set; }

    /// <summary>--auto-run: 目录加载完立即开始转换(脚本一键调用 GUI 用)。</summary>
    public bool AutoRun { get; private set; }

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        var paths = new List<string>();
        for (var i = 0; i < e.Args.Length; i++)
        {
            var arg = e.Args[i];
            switch (arg)
            {
                case "--out" when i + 1 < e.Args.Length:
                    StartupOut = e.Args[++i];
                    break;
                case "--component" when i + 1 < e.Args.Length:
                    StartupComponent = e.Args[++i];
                    break;
                case "--auto-run":
                    AutoRun = true;
                    break;
                default:
                    if (Directory.Exists(arg) || File.Exists(arg)) paths.Add(arg);
                    break;
            }
        }
        StartupPaths = paths.ToArray();
        DispatcherUnhandledException += (_, args) =>
        {
            MessageBox.Show(args.Exception.ToString(), "YSM 转换器 - 未处理异常", MessageBoxButton.OK, MessageBoxImage.Error);
            args.Handled = true;
        };
    }
}
