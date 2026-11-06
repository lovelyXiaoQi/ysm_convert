//! 字体: 用系统自带的中文字体(%WINDIR%\Fonts), 不往发布包里塞字体文件。

use std::path::PathBuf;
use std::sync::Arc;

use eframe::egui::{self, FontData, FontDefinitions, FontFamily, FontId, TextStyle};

/// 中文界面字体的候选(文件名, 优先用集合里的第几个字体): 微软雅黑 UI → 等线 → 黑体 → 宋体。
const CJK_CANDIDATES: &[(&str, u32)] = &[
    ("msyh.ttc", 1),
    ("msyh.ttf", 0),
    ("Deng.ttf", 0),
    ("simhei.ttf", 0),
    ("simsun.ttc", 0),
];
const MONO_CANDIDATES: &[&str] = &["consola.ttf", "cour.ttf"];

fn fonts_dir() -> PathBuf {
    let windows = std::env::var_os("SystemRoot")
        .or_else(|| std::env::var_os("windir"))
        .unwrap_or_else(|| "C:\\Windows".into());
    PathBuf::from(windows).join("Fonts")
}

/// TrueType 集合(.ttc)里的字体数; 不是集合时为 1。
fn collection_size(bytes: &[u8]) -> u32 {
    if bytes.len() >= 12 && &bytes[0..4] == b"ttcf" {
        u32::from_be_bytes([bytes[8], bytes[9], bytes[10], bytes[11]])
    } else {
        1
    }
}

fn load(file: &str, preferred_index: u32) -> Option<FontData> {
    let bytes = std::fs::read(fonts_dir().join(file)).ok()?;
    let count = collection_size(&bytes);
    let mut data = FontData::from_owned(bytes);
    data.index = if preferred_index < count { preferred_index } else { 0 };
    Some(data)
}

pub fn install(ctx: &egui::Context) {
    let mut fonts = FontDefinitions::default();
    if let Some(cjk) = CJK_CANDIDATES.iter().find_map(|&(file, index)| load(file, index)) {
        fonts.font_data.insert("cjk".into(), Arc::new(cjk));
        // 中文字体放在最前: 拉丁字母也用它, 中英混排的基线与字重一致; egui 自带字体留作符号 / emoji 回落
        fonts
            .families
            .entry(FontFamily::Proportional)
            .or_default()
            .insert(0, "cjk".into());
        fonts
            .families
            .entry(FontFamily::Monospace)
            .or_default()
            .push("cjk".into());
    }
    if let Some(mono) = MONO_CANDIDATES.iter().find_map(|&file| load(file, 0)) {
        fonts.font_data.insert("mono".into(), Arc::new(mono));
        fonts
            .families
            .entry(FontFamily::Monospace)
            .or_default()
            .insert(0, "mono".into());
    }
    ctx.set_fonts(fonts);
    ctx.all_styles_mut(|style| {
        style.text_styles = [
            (TextStyle::Small, FontId::proportional(11.5)),
            (TextStyle::Body, FontId::proportional(13.5)),
            (TextStyle::Button, FontId::proportional(13.5)),
            (TextStyle::Heading, FontId::proportional(15.5)),
            (TextStyle::Monospace, FontId::monospace(12.5)),
        ]
        .into();
        // 常驻、占位的滚动条(桌面工具的习惯): 浮动细条要悬停才显形, 用户看不出选项区 / 包列表还能滚
        style.spacing.scroll = egui::style::ScrollStyle::solid();
    });
}
