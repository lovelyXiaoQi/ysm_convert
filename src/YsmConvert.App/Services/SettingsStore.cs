using System.IO;
using System.Text;
using System.Text.Json;

namespace YsmConvert.App.Services;

public sealed class AppSettings
{
    public string OutputRoot { get; set; } = "";
    public bool IsComponentMode { get; set; } = true;
    public string ComponentName { get; set; } = "my_ysm_models";
    public bool WithMods { get; set; }
    public bool ValidateAfter { get; set; } = true;
    public bool UseCollection { get; set; }
    public string CollectionDir { get; set; } = "";
    /// <summary>文件夹显示名(网易版只显示一个名字)。</summary>
    public string CollectionName { get; set; } = "";
    /// <summary>文件夹封面图(PNG)路径, 空 = 用 Java 合集自带的 ysm-pack.png 或默认封面。</summary>
    public string CollectionCover { get; set; } = "";
    /// <summary>产物 JSON 压成一行。</summary>
    public bool CompactJson { get; set; } = true;
    public string LastInputDir { get; set; } = "";
    public bool ShowDetails { get; set; }
}

/// <summary>%APPDATA%\YsmConvert\settings.json</summary>
public static class SettingsStore
{
    private static readonly string FilePath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "YsmConvert", "settings.json");

    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    public static AppSettings Load()
    {
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(FilePath), Options) ?? new AppSettings();
        }
        catch (Exception ex) when (ex is IOException or JsonException)
        {
            // 坏设置文件按缺省处理
        }
        return new AppSettings();
    }

    public static void Save(AppSettings settings)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
            File.WriteAllText(FilePath, JsonSerializer.Serialize(settings, Options), new UTF8Encoding(false));
        }
        catch (IOException)
        {
            // 保存失败不影响使用
        }
    }
}
