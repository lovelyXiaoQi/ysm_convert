//! YsmConvert.exe — YSM 模型转换器图形界面(Java 版模型包 → 网易基岩版组件)。
#![windows_subsystem = "windows"]
#![allow(non_snake_case)]

mod app;
mod fonts;
mod items;
mod settings;
mod virtual_list;

use eframe::egui;

const TITLE: &str = "YSM模型网易基岩版转换器";
const ICON_ICO: &[u8] = include_bytes!("../../../icon.ico");

/// icon.ico 里最大的那张 PNG 条目 → (宽, 高, RGBA)。
pub fn icon_rgba() -> Option<(u32, u32, Vec<u8>)> {
    let data = ICON_ICO;
    let count = u16::from_le_bytes([*data.get(4)?, *data.get(5)?]) as usize;
    let mut best: Option<(u32, &[u8])> = None;
    for i in 0..count {
        let entry = data.get(6 + 16 * i..22 + 16 * i)?;
        let side = if entry[0] == 0 { 256 } else { entry[0] as u32 };
        let size = u32::from_le_bytes([entry[8], entry[9], entry[10], entry[11]]) as usize;
        let offset = u32::from_le_bytes([entry[12], entry[13], entry[14], entry[15]]) as usize;
        let bytes = data.get(offset..offset + size)?;
        if bytes.starts_with(b"\x89PNG") && best.is_none_or(|(s, _)| side > s) {
            best = Some((side, bytes));
        }
    }
    app::decode_png(best?.1)
}

fn message_box(title: &str, text: &str) {
    #[cfg(windows)]
    {
        use windows_sys::Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW};
        let wide = |s: &str| s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
        let (t, m) = (wide(title), wide(text));
        unsafe { MessageBoxW(std::ptr::null_mut(), m.as_ptr(), t.as_ptr(), MB_OK | MB_ICONERROR) };
    }
    #[cfg(not(windows))]
    eprintln!("{title}: {text}");
}

fn main() {
    // 没有控制台的窗口程序崩了要让人看见原因(旧版 WPF 同样弹"未处理异常"框)
    std::panic::set_hook(Box::new(|info| {
        let message = info
            .payload()
            .downcast_ref::<&str>()
            .map(|s| s.to_string())
            .or_else(|| info.payload().downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "未知错误".into());
        let location = info
            .location()
            .map(|l| format!("\n\n位置: {}:{}", l.file(), l.line()))
            .unwrap_or_default();
        message_box("YSM 转换器 - 未处理异常", &format!("{message}{location}"));
    }));

    let args: Vec<String> = std::env::args().skip(1).collect();
    let startup = app::StartupArgs::parse(&args);
    let mut viewport = egui::ViewportBuilder::default()
        .with_title(TITLE)
        .with_inner_size([1280.0, 820.0])
        .with_min_inner_size([1000.0, 640.0])
        .with_drag_and_drop(true);
    if let Some((width, height, rgba)) = icon_rgba() {
        viewport = viewport.with_icon(egui::IconData { rgba, width, height });
    }
    let options = eframe::NativeOptions {
        viewport,
        centered: true,
        ..Default::default()
    };
    let result = eframe::run_native(
        TITLE,
        options,
        Box::new(move |cc| {
            fonts::install(&cc.egui_ctx);
            Ok(Box::new(app::YsmApp::new(cc, startup)))
        }),
    );
    if let Err(error) = result {
        message_box(
            "YSM 转换器无法启动",
            &format!(
                "图形界面初始化失败: {error}\n\n多半是显卡驱动不支持 OpenGL 2.0(远程桌面 / 虚拟机里常见)。更新显卡驱动后重试; \
                 同目录下的命令行 ysmconv.exe 不受影响, 功能相同(ysmconv --help 查看用法)。"
            ),
        );
    }
}
