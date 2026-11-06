//! 嵌入 exe 资源: 图标(仓库根目录 icon.ico)与版本信息。版本号取环境变量 YSMCONV_VERSION(发版流水线按 tag 递增后
//! 传入), 没有时取 Cargo.toml。

fn main() {
    println!("cargo:rerun-if-env-changed=YSMCONV_VERSION");
    println!("cargo:rerun-if-changed=../../icon.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let version = std::env::var("YSMCONV_VERSION").unwrap_or_else(|_| std::env::var("CARGO_PKG_VERSION").unwrap());
    let mut res = winresource::WindowsResource::new();
    res.set_icon("../../icon.ico")
        .set("FileDescription", "YSM 模型转换器命令行与 MCP 服务器")
        .set("ProductName", "YSM Convert")
        .set("CompanyName", "YSM")
        .set("OriginalFilename", "ysmconv.exe")
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
