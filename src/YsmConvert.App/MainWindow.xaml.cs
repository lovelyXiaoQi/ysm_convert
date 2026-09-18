using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using YsmConvert.App.ViewModels;
using YsmConvert.Core;

namespace YsmConvert.App;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        Loaded += async (_, _) =>
        {
            // 内核探测在构造期就启动了; 窗口显示出来再报错, 否则弹框会跑到主窗口后面
            await ViewModel.KernelReady;
            if (ViewModel.KernelError is not null)
                MessageBox.Show(this, ViewModel.KernelError, "转换内核无法启动", MessageBoxButton.OK, MessageBoxImage.Error);
            if (Application.Current is not App app) return;
            if (app.StartupOut is not null) ViewModel.OutputRoot = app.StartupOut;
            if (app.StartupComponent is not null)
            {
                ViewModel.IsComponentMode = true;
                ViewModel.ComponentName = app.StartupComponent;
            }
            if (app.StartupPaths.Length > 0)
                await ViewModel.AddPathsAsync(app.StartupPaths);
            if (app.AutoRun && ViewModel.RunCommand.CanExecute(null))
                ViewModel.RunCommand.Execute(null);
        };
    }

    private MainViewModel ViewModel => (MainViewModel)DataContext;

    private void Window_PreviewDragOver(object sender, DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None;
        e.Handled = true;
    }

    private async void Window_PreviewDrop(object sender, DragEventArgs e)
    {
        if (!e.Data.GetDataPresent(DataFormats.FileDrop)) return;
        e.Handled = true;
        if (e.Data.GetData(DataFormats.FileDrop) is string[] paths && paths.Length > 0)
            await ViewModel.AddPathsAsync(paths);
    }

    /// <summary>"文档"页签当按钮用: 点一下就在浏览器打开线上 Wiki, 不切换页签。</summary>
    private void DocsTab_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        e.Handled = true;
        ViewModel.OpenWikiCommand.Execute(null);
    }

    /// <summary>"开源地址"页签同样当按钮用: 在浏览器打开转换器的 GitHub 仓库。</summary>
    private void RepoTab_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        e.Handled = true;
        ViewModel.OpenRepoCommand.Execute(null);
    }

    protected override void OnClosing(CancelEventArgs e)
    {
        ViewModel.SaveSettings();
        base.OnClosing(e);
    }
}
