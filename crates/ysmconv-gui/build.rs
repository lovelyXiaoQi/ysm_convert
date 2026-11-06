//! 嵌入 exe 资源: 图标(仓库根目录 icon.ico)、版本信息与应用程序清单(高 DPI、通用控件 6.0 让系统对话框是新样式)。
//! 版本号取环境变量 YSMCONV_VERSION(发版流水线按 tag 递增后传入), 没有时取 Cargo.toml。

const MANIFEST: &str = r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity type="win32" name="YSM.YsmConvert" version="1.0.0.0" processorArchitecture="*"/>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
</assembly>
"#;

fn main() {
    println!("cargo:rerun-if-env-changed=YSMCONV_VERSION");
    println!("cargo:rerun-if-changed=../../icon.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let version = std::env::var("YSMCONV_VERSION").unwrap_or_else(|_| std::env::var("CARGO_PKG_VERSION").unwrap());
    let mut res = winresource::WindowsResource::new();
    res.set_icon("../../icon.ico")
        .set_manifest(MANIFEST)
        .set("FileDescription", "YSM 模型转换器")
        .set("ProductName", "YSM Convert")
        .set("CompanyName", "YSM")
        .set("OriginalFilename", "YsmConvert.exe")
        .set("FileVersion", &version)
        .set("ProductVersion", &version)
        .set_version_info(winresource::VersionInfo::FILEVERSION, packed(&version))
        .set_version_info(winresource::VersionInfo::PRODUCTVERSION, packed(&version));
    if let Err(error) = res.compile() {
        panic!("嵌入 exe 资源失败(要 Windows SDK 的 rc.exe): {error}");
    }
}

/// "1.2.3" → 0x0001_0002_0003_0000
fn packed(version: &str) -> u64 {
    let mut parts = version
        .split(|c: char| !c.is_ascii_digit())
        .filter(|p| !p.is_empty())
        .map(|p| p.parse::<u64>().unwrap_or(0));
    let mut value = 0u64;
    for shift in [48, 32, 16, 0] {
        value |= (parts.next().unwrap_or(0) & 0xffff) << shift;
    }
    value
}
