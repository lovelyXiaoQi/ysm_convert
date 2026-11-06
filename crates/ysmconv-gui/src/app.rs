//! 主窗口: ① Java 模型包列表(左上) / ② 输出与选项(右上) / ③ 结果区(下方, 操作按钮在页签栏右侧) / 状态栏。
//! 转换、扫描、内核探测都在后台线程里跑, 结果经通道回到界面线程。

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
use std::sync::mpsc::{Receiver, Sender, channel};
use std::time::{Duration, Instant};

use eframe::egui::{
    self, Align, Color32, FontId, Layout, Rect, RichText, Sense, Stroke, TextEdit, TextStyle, Ui, pos2, vec2,
};
use egui_extras::{Column, TableBuilder};
use ysmconv_core::event::{self, KernelEvent};
use ysmconv_core::locator::KernelLocator;
use ysmconv_core::molang::{self, StreamBuffer};
use ysmconv_core::report::{AttentionItem, ValidationItem};
use ysmconv_core::time::LocalTime;
use ysmconv_core::{
    CancelToken, CollectionSpec, ConversionReport, ConversionService, ConvertOptions, ConvertRequest, DiscoveredPack,
    Error, OutputLayout, Result, links, material, names, output, win,
};

use crate::items::{LogEntry, PackItem};
use crate::settings::AppSettings;
use crate::virtual_list::VirtualList;

/// 命令行(把 Java 包目录拖到 exe 上 / "打开方式" / 脚本一键调用 GUI)。
#[derive(Default)]
pub struct StartupArgs {
    pub paths: Vec<String>,
    pub out: Option<String>,
    pub component: Option<String>,
    pub auto_run: bool,
}

impl StartupArgs {
    pub fn parse(args: &[String]) -> StartupArgs {
        let mut startup = StartupArgs::default();
        let mut i = 0;
        while i < args.len() {
            match args[i].as_str() {
                "--out" if i + 1 < args.len() => {
                    i += 1;
                    startup.out = Some(args[i].clone());
                }
                "--component" if i + 1 < args.len() => {
                    i += 1;
                    startup.component = Some(args[i].clone());
                }
                "--auto-run" => startup.auto_run = true,
                other => {
                    if Path::new(other).exists() {
                        startup.paths.push(other.to_string());
                    }
                }
            }
            i += 1;
        }
        startup
    }
}

enum Msg {
    KernelInit {
        service: Option<Arc<ConversionService>>,
        error: Option<String>,
        details: Option<String>,
        located: bool,
    },
    Discovered(Result<Vec<DiscoveredPack>>),
    Event(Box<KernelEvent>),
    Stderr(String),
    Finished(Box<Result<ConversionReport>>),
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tab {
    Logs,
    Attention,
    Validation,
}

/// 一次转换 / 体检 / 修复的进行中状态。多个包并行时各包事件交错到达: 日志按包缓冲, 该包完成时整块写入日志面板
/// (内核本来就是一个包转完才吐出它的全部日志行, 缓冲不损失实时性)。
struct RunState {
    verb: &'static str,
    cancel: CancelToken,
    by_name: HashMap<String, usize>,
    pending_logs: HashMap<String, Vec<LogEntry>>,
    molang: StreamBuffer,
    running_packs: Vec<String>,
    finished: i64,
    started: Instant,
    save_settings: bool,
}

struct CoverPreview {
    path: String,
    texture: Option<egui::TextureHandle>,
}

pub struct YsmApp {
    settings: AppSettings,
    service: Option<Arc<ConversionService>>,
    kernel_error: Option<String>,
    startup: Option<StartupArgs>,
    auto_run_pending: bool,
    tx: Sender<Msg>,
    rx: Receiver<Msg>,

    packs: Vec<PackItem>,
    last_clicked: Option<usize>,
    logs: Vec<LogEntry>,
    visible_logs: Vec<usize>,
    log_version: u64,
    log_list: VirtualList,
    attention: Vec<AttentionItem>,
    attention_version: u64,
    attention_list: VirtualList,
    validation: Vec<ValidationItem>,
    validation_version: u64,
    validation_list: VirtualList,

    status: String,
    progress: f32,
    indeterminate: bool,
    elapsed_text: String,
    run: Option<RunState>,
    scanning: bool,
    tab: Tab,
    batch_material: String,
    batch_cull: String,
    last_report: Option<ConversionReport>,
    last_layout: Option<OutputLayout>,
    cover: CoverPreview,
}

const ERROR_COLOR: Color32 = Color32::from_rgb(0xE5, 0x48, 0x4D);
const WARN_COLOR: Color32 = Color32::from_rgb(0xD9, 0x77, 0x06);
const DETAIL_COLOR: Color32 = Color32::from_rgb(0x8A, 0x8A, 0x8A);

fn notice_color(ui: &Ui) -> Color32 {
    if ui.visuals().dark_mode {
        Color32::from_rgb(0x60, 0xA5, 0xFA)
    } else {
        Color32::from_rgb(0x25, 0x63, 0xEB)
    }
}

/// 日志级别 / 提醒级别的颜色: 红 = 错误, 橙 = 警告, 蓝 = 提醒, 灰 = 明细 / 知道即可。
fn level_color(ui: &Ui, level: &str, info_is_gray: bool) -> Color32 {
    match level {
        "error" => ERROR_COLOR,
        "warn" => WARN_COLOR,
        "notice" => notice_color(ui),
        "detail" => DETAIL_COLOR,
        "info" if info_is_gray => DETAIL_COLOR,
        _ => ui.visuals().text_color(),
    }
}

fn first_line(text: &str) -> &str {
    text.lines().next().unwrap_or("")
}

fn format_elapsed(elapsed: Duration) -> String {
    let total = elapsed.as_millis();
    let hours = total / 3_600_000;
    let minutes = total / 60_000 % 60;
    let seconds = total / 1000 % 60;
    if hours >= 1 {
        format!("耗时 {hours}:{minutes:02}:{seconds:02}")
    } else {
        format!("耗时 {minutes}:{seconds:02}.{}", total % 1000 / 100)
    }
}

fn show_message(frame: &eframe::Frame, title: &str, text: &str, level: rfd::MessageLevel) {
    rfd::MessageDialog::new()
        .set_title(title)
        .set_description(text)
        .set_level(level)
        .set_buttons(rfd::MessageButtons::Ok)
        .set_parent(frame)
        .show();
}

/// PNG → (宽, 高, RGBA)。
pub fn decode_png(bytes: &[u8]) -> Option<(u32, u32, Vec<u8>)> {
    let mut decoder = png::Decoder::new(std::io::Cursor::new(bytes));
    decoder.set_transformations(png::Transformations::normalize_to_color8());
    let mut reader = decoder.read_info().ok()?;
    let mut buf = vec![0; reader.output_buffer_size()?];
    let info = reader.next_frame(&mut buf).ok()?;
    let data = &buf[..info.buffer_size()];
    let rgba = match info.color_type {
        png::ColorType::Rgba => data.to_vec(),
        png::ColorType::Rgb => data
            .as_chunks::<3>()
            .0
            .iter()
            .flat_map(|p| [p[0], p[1], p[2], 255])
            .collect(),
        png::ColorType::GrayscaleAlpha => data
            .as_chunks::<2>()
            .0
            .iter()
            .flat_map(|p| [p[0], p[0], p[0], p[1]])
            .collect(),
        png::ColorType::Grayscale => data.iter().flat_map(|&g| [g, g, g, 255]).collect(),
        png::ColorType::Indexed => return None,
    };
    Some((info.width, info.height, rgba))
}

impl YsmApp {
    pub fn new(cc: &eframe::CreationContext<'_>, startup: StartupArgs) -> Self {
        let (tx, rx) = channel();
        let app = YsmApp {
            settings: AppSettings::load(),
            service: None,
            kernel_error: None,
            startup: Some(startup),
            auto_run_pending: false,
            tx,
            rx,
            packs: Vec::new(),
            last_clicked: None,
            logs: Vec::new(),
            visible_logs: Vec::new(),
            log_version: 0,
            log_list: VirtualList::default(),
            attention: Vec::new(),
            attention_version: 0,
            attention_list: VirtualList::default(),
            validation: Vec::new(),
            validation_version: 0,
            validation_list: VirtualList::default(),
            status: "就绪".into(),
            progress: 0.0,
            indeterminate: false,
            elapsed_text: String::new(),
            run: None,
            scanning: false,
            tab: Tab::Logs,
            batch_material: material::AUTO.into(),
            batch_cull: material::AUTO.into(),
            last_report: None,
            last_layout: None,
            cover: CoverPreview {
                path: String::new(),
                texture: None,
            },
        };
        app.init_kernel(&cc.egui_ctx);
        app
    }

    fn running(&self) -> bool {
        self.run.is_some()
    }

    fn can_run(&self) -> bool {
        !self.running() && self.service.is_some()
    }

    // ---------------------------------------------------------------- kernel
    fn init_kernel(&self, ctx: &egui::Context) {
        let tx = self.tx.clone();
        let ctx = ctx.clone();
        std::thread::spawn(move || {
            let msg = match KernelLocator::try_locate() {
                Err(error) => Msg::KernelInit {
                    service: None,
                    error: Some(error),
                    details: None,
                    located: false,
                },
                Ok(paths) => {
                    let service = ConversionService::new(paths);
                    match service.get_info(&CancelToken::new()) {
                        Ok(_) => Msg::KernelInit {
                            service: Some(Arc::new(service)),
                            error: None,
                            details: None,
                            located: true,
                        },
                        Err(Error::Kernel { message, details }) => Msg::KernelInit {
                            service: None,
                            error: Some(message),
                            details,
                            located: true,
                        },
                        Err(other) => Msg::KernelInit {
                            service: None,
                            error: Some(other.to_string()),
                            details: None,
                            located: true,
                        },
                    }
                }
            };
            let _ = tx.send(msg);
            ctx.request_repaint();
        });
    }

    fn on_kernel_init(
        &mut self,
        ctx: &egui::Context,
        frame: &eframe::Frame,
        service: Option<Arc<ConversionService>>,
        error: Option<String>,
        details: Option<String>,
        located: bool,
    ) {
        if let Some(error) = error {
            self.status = format!(
                "{}: {}",
                if located {
                    "内核启动失败"
                } else {
                    "内核不可用"
                },
                first_line(&error)
            );
            if let Some(details) = details {
                self.push_log(LogEntry::new("detail", details));
            }
            self.kernel_error = Some(error);
        }
        self.service = service;
        // 窗口显示出来再报错, 否则弹框会跑到主窗口后面
        if let Some(error) = &self.kernel_error {
            show_message(frame, "转换内核无法启动", error, rfd::MessageLevel::Error);
        }
        let Some(startup) = self.startup.take() else { return };
        if let Some(out) = startup.out {
            self.settings.output_root = out;
        }
        if let Some(component) = startup.component {
            self.settings.is_component_mode = true;
            self.settings.component_name = component;
        }
        if !startup.paths.is_empty() {
            self.auto_run_pending = startup.auto_run;
            self.add_paths(startup.paths, ctx);
        } else if startup.auto_run && self.can_run() {
            self.start_convert(ctx, frame);
        }
    }

    fn process_messages(&mut self, ctx: &egui::Context, frame: &eframe::Frame) {
        while let Ok(msg) = self.rx.try_recv() {
            match msg {
                Msg::KernelInit {
                    service,
                    error,
                    details,
                    located,
                } => self.on_kernel_init(ctx, frame, service, error, details, located),
                Msg::Discovered(result) => self.on_discovered(result, ctx, frame),
                Msg::Event(e) => self.on_kernel_event(*e),
                Msg::Stderr(line) => self.push_log(LogEntry::new("detail", format!("(stderr) {line}"))),
                Msg::Finished(result) => self.on_finished(*result, frame),
            }
        }
    }

    // ---------------------------------------------------------------- packs
    fn pick_and_add(&mut self, ctx: &egui::Context, frame: &eframe::Frame) {
        let mut dialog = rfd::FileDialog::new()
            .set_title("选择 Java 模型包目录 / 合集目录 / 装着若干包的上级目录(可多选)")
            .set_parent(frame);
        if !self.settings.last_input_dir.is_empty() && Path::new(&self.settings.last_input_dir).is_dir() {
            dialog = dialog.set_directory(&self.settings.last_input_dir);
        }
        let Some(folders) = dialog.pick_folders() else { return };
        if let Some(first) = folders.first() {
            self.settings.last_input_dir = first.parent().unwrap_or(first).to_string_lossy().into_owned();
        }
        self.add_paths(folders.iter().map(|p| p.to_string_lossy().into_owned()).collect(), ctx);
    }

    fn add_paths(&mut self, paths: Vec<String>, ctx: &egui::Context) {
        let Some(service) = self.service.clone() else {
            self.status = self.kernel_error.clone().unwrap_or_else(|| "内核不可用".into());
            return;
        };
        let mut dirs: Vec<String> = Vec::new();
        for p in paths {
            let dir = if Path::new(&p).is_file() {
                Path::new(&p)
                    .parent()
                    .map(|d| d.to_string_lossy().into_owned())
                    .unwrap_or(p)
            } else {
                p
            };
            if !dirs.contains(&dir) {
                dirs.push(dir);
            }
        }
        if dirs.is_empty() {
            return;
        }
        self.scanning = true;
        if !self.running() {
            self.indeterminate = true;
        }
        self.status = "正在扫描 Java 模型包…".into();
        let tx = self.tx.clone();
        let ctx = ctx.clone();
        std::thread::spawn(move || {
            let result = service.discover(&dirs, &CancelToken::new());
            let _ = tx.send(Msg::Discovered(result));
            ctx.request_repaint();
        });
    }

    fn on_discovered(&mut self, result: Result<Vec<DiscoveredPack>>, ctx: &egui::Context, frame: &eframe::Frame) {
        self.scanning = false;
        if !self.running() {
            self.indeterminate = false;
        }
        match result {
            Ok(found) => {
                let found_count = found.len();
                let mut added = 0;
                for pack in found {
                    let java_dir = pack.java_dir.to_lowercase();
                    if self.packs.iter().any(|p| p.java_dir.to_lowercase() == java_dir) {
                        continue;
                    }
                    let mut item = PackItem::new(pack);
                    if self.settings.use_collection && !self.settings.collection_dir.trim().is_empty() {
                        item.collection = self.settings.collection_dir.trim().to_string();
                    }
                    self.packs.push(item);
                    added += 1;
                }
                self.status = if found_count == 0 {
                    "所选目录里没有 Java 模型包(需要含 ysm.json 的目录)".into()
                } else {
                    format!("新增 {added} 个包(共 {} 个)", self.packs.len())
                };
            }
            Err(error) => self.status = format!("扫描失败: {error}"),
        }
        if self.auto_run_pending {
            self.auto_run_pending = false;
            if self.can_run() {
                self.start_convert(ctx, frame);
            }
        }
    }

    fn remove_selected(&mut self) {
        self.packs.retain(|p| !p.selected);
        self.last_clicked = None;
    }

    fn apply_collection(&mut self) {
        let dir = if self.settings.use_collection {
            self.settings.collection_dir.trim().to_string()
        } else {
            String::new()
        };
        for p in &mut self.packs {
            p.collection = dir.clone();
        }
        self.status = if self.settings.use_collection {
            format!("全部包归入合集 {dir}")
        } else {
            "全部包改为平铺(不归入合集)".into()
        };
    }

    fn apply_material(&mut self, frame: &eframe::Frame) {
        if let Some(problem) = material::problem(Some(&self.batch_material)) {
            show_message(frame, "材质写法有问题", &problem, rfd::MessageLevel::Warning);
            return;
        }
        for p in &mut self.packs {
            p.material = self.batch_material.clone();
            p.cull_mode = self.batch_cull.clone();
        }
        self.status = format!("全部包: 材质 {}, 背面剔除 {}", self.batch_material, self.batch_cull);
    }

    // ---------------------------------------------------------------- output
    fn browse_output(&mut self, frame: &eframe::Frame) {
        let title = if self.settings.is_component_mode {
            "选择放置新组件的目录"
        } else {
            "选择已有的组件工程根目录(含行为包与资源包)"
        };
        let mut dialog = rfd::FileDialog::new().set_title(title).set_parent(frame);
        if !self.settings.output_root.is_empty() && Path::new(&self.settings.output_root).is_dir() {
            dialog = dialog.set_directory(&self.settings.output_root);
        }
        if let Some(folder) = dialog.pick_folder() {
            self.settings.output_root = folder.to_string_lossy().into_owned();
        }
    }

    fn browse_cover(&mut self, frame: &eframe::Frame) {
        let mut dialog = rfd::FileDialog::new()
            .set_title("选择文件夹封面图(PNG, 不超过 1MB, 最好是 Java 卡片 52x90 的比例)")
            .add_filter("PNG 图片", &["png"])
            .set_parent(frame);
        if let Some(dir) = Path::new(&self.settings.collection_cover)
            .parent()
            .filter(|d| d.is_dir())
        {
            dialog = dialog.set_directory(dir);
        }
        let Some(file) = dialog.pick_file() else { return };
        let file = file.to_string_lossy().into_owned();
        let spec = CollectionSpec {
            dir: "cover".into(),
            cover_image: Some(file.clone()),
            ..Default::default()
        };
        if let Some(problem) = spec.cover_problem() {
            show_message(frame, "文件夹封面", &problem, rfd::MessageLevel::Warning);
            return;
        }
        self.settings.collection_cover = file;
    }

    fn resolve_layout(&mut self, allow_create: bool, frame: &eframe::Frame) -> Option<OutputLayout> {
        let root = self.settings.output_root.trim().to_string();
        if root.is_empty() {
            self.status = "请先选择输出目录".into();
            return None;
        }
        let result = if self.settings.is_component_mode {
            let component = self.settings.component_name.trim().to_string();
            if !allow_create && !Path::new(&root).join(format!("{component}_bp")).is_dir() {
                self.status = format!("组件 {component} 还不存在, 先转换一次");
                return None;
            }
            output::ensure_component(&root, &component)
        } else {
            output::detect(&root)
        };
        match result {
            Ok(layout) => Some(layout),
            Err(error) => {
                let text = error.to_string();
                self.status = text.clone();
                show_message(frame, "输出目录", &text, rfd::MessageLevel::Warning);
                None
            }
        }
    }

    // ---------------------------------------------------------------- run
    fn enabled_by_name(&self) -> HashMap<String, usize> {
        self.packs
            .iter()
            .enumerate()
            .filter(|(_, p)| p.enabled)
            .map(|(i, p)| (p.name.trim().to_string(), i))
            .collect()
    }

    fn start_convert(&mut self, ctx: &egui::Context, frame: &eframe::Frame) {
        if self.service.is_none() {
            return;
        }
        let enabled: Vec<usize> = (0..self.packs.len()).filter(|&i| self.packs[i].enabled).collect();
        if enabled.is_empty() {
            self.status = "没有勾选任何 Java 包".into();
            return;
        }
        let Some(layout) = self.resolve_layout(true, frame) else {
            return;
        };
        let packs: Vec<_> = enabled.iter().map(|&i| self.packs[i].to_spec()).collect();
        let problems = names::validate(&packs);
        if !problems.is_empty() {
            show_message(frame, "包名有问题", &problems.join("\n"), rfd::MessageLevel::Warning);
            self.status = "请先修正包名".into();
            return;
        }
        let material_problems = material::validate(&packs);
        if !material_problems.is_empty() {
            show_message(
                frame,
                "材质写法有问题",
                &material_problems.join("\n"),
                rfd::MessageLevel::Warning,
            );
            self.status = "请先修正“材质”列".into();
            return;
        }
        let mut collections = Vec::new();
        let dir = self.settings.collection_dir.trim().to_string();
        let spec = CollectionSpec {
            dir: dir.clone(),
            name: Some(self.settings.collection_name.trim().to_string()),
            description: None,
            cover_image: Some(self.settings.collection_cover.trim().to_string()),
        };
        if self.settings.use_collection
            && !dir.is_empty()
            && packs.iter().any(|p| p.collection.as_deref() == Some(dir.as_str()))
            && !spec.is_empty()
        {
            if let Some(problem) = spec.cover_problem() {
                show_message(frame, "文件夹封面有问题", &problem, rfd::MessageLevel::Warning);
                self.status = problem;
                return;
            }
            collections.push(spec);
        }
        let request = ConvertRequest {
            layout,
            packs: packs.clone(),
            collections,
            options: ConvertOptions {
                with_mods: self.settings.with_mods,
                java_functions: self.settings.java_functions,
                validate: self.settings.validate_after,
                compact_json: self.settings.compact_json,
                ..ConvertOptions::default()
            },
            ref_rp: None,
        };
        for &i in &enabled {
            let item = &mut self.packs[i];
            item.status = "排队中".into();
            item.attention_count = 0;
            item.ok = None;
        }
        let by_name = self.enabled_by_name();
        self.execute(
            "转换",
            by_name,
            packs.len(),
            true,
            ctx,
            move |service, on_event, on_stderr, cancel| {
                service.convert(&request, Some(on_event), Some(on_stderr), cancel)
            },
        );
    }

    fn start_validate(&mut self, ctx: &egui::Context, frame: &eframe::Frame) {
        if self.service.is_none() {
            return;
        }
        let Some(layout) = self.resolve_layout(false, frame) else {
            return;
        };
        let names: Vec<String> = self
            .packs
            .iter()
            .filter(|p| p.enabled)
            .map(|p| p.name.trim().to_string())
            .collect();
        let by_name = self.enabled_by_name();
        self.execute(
            "体检",
            by_name,
            0,
            false,
            ctx,
            move |service, on_event, on_stderr, cancel| {
                service.validate(&layout, &names, Some(on_event), Some(on_stderr), cancel)
            },
        );
    }

    fn start_fix(&mut self, ctx: &egui::Context, frame: &eframe::Frame) {
        if self.service.is_none() {
            return;
        }
        let Some(layout) = self.resolve_layout(false, frame) else {
            return;
        };
        let names: Vec<String> = self
            .packs
            .iter()
            .filter(|p| p.enabled)
            .map(|p| p.name.trim().to_string())
            .collect();
        let by_name = self.enabled_by_name();
        let (validate, compact) = (self.settings.validate_after, self.settings.compact_json);
        let total = names.len();
        self.execute(
            "修复",
            by_name,
            total,
            false,
            ctx,
            move |service, on_event, on_stderr, cancel| {
                service.fix(
                    &layout,
                    &names,
                    validate,
                    compact,
                    Some(on_event),
                    Some(on_stderr),
                    cancel,
                )
            },
        );
    }

    #[allow(clippy::too_many_arguments)]
    fn execute(
        &mut self,
        verb: &'static str,
        by_name: HashMap<String, usize>,
        total: usize,
        save_settings: bool,
        ctx: &egui::Context,
        job: impl FnOnce(
            &ConversionService,
            &(dyn Fn(&KernelEvent) + Sync),
            &(dyn Fn(&str) + Sync),
            &CancelToken,
        ) -> Result<ConversionReport>
        + Send
        + 'static,
    ) {
        let Some(service) = self.service.clone() else { return };
        self.logs.clear();
        self.visible_logs.clear();
        self.log_version += 1;
        self.attention.clear();
        self.attention_version += 1;
        self.validation.clear();
        self.validation_version += 1;
        self.progress = 0.0;
        self.indeterminate = total == 0;
        self.status = format!("{verb}中…");
        self.elapsed_text = format_elapsed(Duration::ZERO);
        let cancel = CancelToken::new();
        self.run = Some(RunState {
            verb,
            cancel: cancel.clone(),
            by_name,
            pending_logs: HashMap::new(),
            molang: StreamBuffer::new(),
            running_packs: Vec::new(),
            finished: 0,
            started: Instant::now(),
            save_settings,
        });
        let tx = self.tx.clone();
        let ctx = ctx.clone();
        std::thread::spawn(move || {
            let (event_tx, event_ctx) = (tx.clone(), ctx.clone());
            let on_event = move |e: &KernelEvent| {
                let _ = event_tx.send(Msg::Event(Box::new(e.clone())));
                event_ctx.request_repaint();
            };
            let (stderr_tx, stderr_ctx) = (tx.clone(), ctx.clone());
            let on_stderr = move |line: &str| {
                let _ = stderr_tx.send(Msg::Stderr(line.to_string()));
                stderr_ctx.request_repaint();
            };
            let result = job(&service, &on_event, &on_stderr, &cancel);
            let _ = tx.send(Msg::Finished(Box::new(result)));
            ctx.request_repaint();
        });
    }

    fn push_log(&mut self, entry: LogEntry) {
        if self.settings.show_details || entry.level != "detail" {
            self.visible_logs.push(self.logs.len());
        }
        self.logs.push(entry);
    }

    fn refilter_logs(&mut self) {
        let show = self.settings.show_details;
        self.visible_logs = (0..self.logs.len())
            .filter(|&i| show || self.logs[i].level != "detail")
            .collect();
        self.log_version += 1;
    }

    fn update_running_status(&mut self, total: i64) {
        let Some(run) = &self.run else { return };
        let running = if run.running_packs.is_empty() {
            String::new()
        } else {
            let head: Vec<&str> = run.running_packs.iter().take(3).map(String::as_str).collect();
            format!(
                ", 正在转换: {}{}",
                head.join("、"),
                if run.running_packs.len() > 3 {
                    format!(" 等 {} 个", run.running_packs.len())
                } else {
                    String::new()
                }
            )
        };
        self.status = format!("已完成 {}/{total}{running}", run.finished);
    }

    fn on_kernel_event(&mut self, e: KernelEvent) {
        let Some(run) = self.run.as_mut() else { return };
        let key = e.pack.clone().unwrap_or_default();
        let fallback_total = run.by_name.len() as i64;
        match e.event.as_str() {
            event::PACK_START => {
                if let Some(&i) = run.by_name.get(&key)
                    && let Some(item) = self.packs.get_mut(i)
                {
                    item.status = "转换中".into();
                }
                if e.pack.is_some() {
                    run.running_packs.push(key.clone());
                }
                let line = format!(
                    "== {key}{}",
                    e.java_dir.as_ref().map(|d| format!("  <- {d}")).unwrap_or_default()
                );
                run.pending_logs
                    .entry(key)
                    .or_default()
                    .push(LogEntry::new("info", line));
                self.update_running_status(e.total.unwrap_or(fallback_total));
            }
            event::LOG => {
                let entry = LogEntry::new(e.level.as_deref().unwrap_or("info"), e.text.clone().unwrap_or_default());
                run.pending_logs.entry(key).or_default().push(entry);
            }
            event::MOLANG => {
                if let Some(line) = run.molang.line(&e) {
                    let level = if molang::severity(e.kind.as_deref()) == "info" {
                        "info"
                    } else {
                        "notice"
                    };
                    run.pending_logs
                        .entry(key)
                        .or_default()
                        .push(LogEntry::new(level, line));
                }
            }
            event::PACK_DONE => {
                let ok = e.ok == Some(true);
                if let Some(&i) = run.by_name.get(&key)
                    && let Some(item) = self.packs.get_mut(i)
                {
                    item.status = if ok { "完成" } else { "失败" }.into();
                }
                let mut block = run.pending_logs.remove(&key).unwrap_or_default();
                if let Some(lower) = run.molang.flush(e.pack.as_deref()) {
                    block.push(LogEntry::new("info", lower));
                }
                block.push(if ok {
                    LogEntry::new(
                        "info",
                        format!(
                            "-> 完成 {:.1}s  错误 {} / 警告 {} / 提醒 {}",
                            e.seconds.unwrap_or(0.0),
                            e.errors.unwrap_or(0),
                            e.warnings.unwrap_or(0),
                            e.notices.unwrap_or(0)
                        ),
                    )
                } else {
                    LogEntry::new("error", format!("-> 失败: {}", e.error.as_deref().unwrap_or("")))
                });
                run.running_packs.retain(|p| *p != key);
                run.finished += 1;
                if let Some(total) = e.total.filter(|&t| t > 0) {
                    self.progress = run.finished as f32 / total as f32;
                }
                for entry in block {
                    self.push_log(entry);
                }
                self.update_running_status(e.total.unwrap_or(fallback_total));
            }
            event::COLLECTION => {
                self.push_log(LogEntry::new(
                    "info",
                    format!(
                        "合集清单 {}: {} {}",
                        e.dir.as_deref().unwrap_or(""),
                        if e.written == Some(true) {
                            "已写入"
                        } else {
                            "沿用 Java 源"
                        },
                        e.path.as_deref().unwrap_or("")
                    ),
                ));
                if let Some(cover) = e.extra_string("cover").filter(|s| !s.is_empty()) {
                    self.push_log(LogEntry::new("info", format!("   文件夹封面 → {cover}.png")));
                }
                if let Some(warning) = e.extra_string("warning").filter(|s| !s.is_empty()) {
                    self.push_log(LogEntry::new("warn", format!("   [WARN] {warning}")));
                }
            }
            event::VALIDATE_ITEM => {
                let level = if e.level.as_deref() == Some("error") {
                    "error"
                } else {
                    "warn"
                };
                self.push_log(LogEntry::new(
                    level,
                    format!("体检: {}", e.text.as_deref().unwrap_or("")),
                ));
            }
            event::VALIDATE_DONE => {
                let n = |v: Option<i64>| v.map(|x| x.to_string()).unwrap_or_default();
                self.push_log(LogEntry::new(
                    "info",
                    format!(
                        "体检: 动画文件 {} / 控制器文件 {}; 错误 {} / 警告 {}",
                        n(e.animations),
                        n(e.controllers),
                        n(e.errors),
                        n(e.warnings)
                    ),
                ));
            }
            _ => {}
        }
    }

    fn on_finished(&mut self, result: Result<ConversionReport>, frame: &eframe::Frame) {
        let Some(run) = self.run.take() else { return };
        let verb = run.verb;
        match result {
            Ok(report) => {
                self.last_layout = report.layout.clone();
                self.attention = report.build_attention();
                self.attention_version += 1;
                self.validation = report.validation.clone();
                self.validation_version += 1;
                for pack in &report.packs {
                    let Some(&i) = run.by_name.get(&pack.name) else {
                        continue;
                    };
                    let count = self.attention.iter().filter(|a| a.pack == pack.name).count() as i64;
                    let Some(item) = self.packs.get_mut(i) else { continue };
                    item.attention_count = count;
                    item.ok = pack.ok;
                    match pack.ok {
                        Some(true) => {
                            item.status = if count > 0 {
                                format!("完成, {count} 项待过目")
                            } else {
                                "完成".into()
                            }
                        }
                        Some(false) => item.status = "失败".into(),
                        None => {}
                    }
                }
                if let Some(fatal) = report.fatal_error.clone() {
                    self.status = fatal.replace('\n', " ");
                    self.push_log(LogEntry::new("error", fatal.clone()));
                    show_message(frame, "内核未能运行", &fatal, rfd::MessageLevel::Error);
                } else {
                    let validation_errors = report.validation_errors.unwrap_or(0);
                    self.status = if report.ok {
                        format!(
                            "{verb}完成: 成功 {}, 体检错误 {validation_errors}, 待过目 {} 项 (资源包改动需重启游戏)",
                            report.packs_ok,
                            self.attention.len()
                        )
                    } else {
                        format!(
                            "{verb}结束但有问题: 失败 {}, 体检错误 {validation_errors}, 待过目 {} 项",
                            report.packs_failed,
                            self.attention.len()
                        )
                    };
                    if !self.attention.is_empty() {
                        self.tab = Tab::Attention;
                    }
                }
                self.progress = 1.0;
                self.last_report = Some(report);
            }
            Err(Error::Cancelled) => {
                self.status = format!("{verb}已取消");
                for &i in run.by_name.values() {
                    if let Some(item) = self.packs.get_mut(i)
                        && (item.status == "排队中" || item.status == "转换中")
                    {
                        item.status = "已取消".into();
                    }
                }
            }
            Err(Error::Argument(message)) => {
                self.status = message.replace('\n', " ");
                show_message(frame, "无法开始", &message, rfd::MessageLevel::Warning);
            }
            Err(other) => {
                let text = other.full_text("\n");
                self.status = format!("{verb}失败: {}", first_line(&text));
                self.push_log(LogEntry::new("error", text.clone()));
                show_message(frame, &format!("{verb}失败"), &text, rfd::MessageLevel::Error);
            }
        }
        self.elapsed_text = format_elapsed(run.started.elapsed());
        self.indeterminate = false;
        if run.save_settings {
            self.settings.save();
        }
    }

    // ---------------------------------------------------------------- misc commands
    fn open_output(&mut self) {
        let target = self
            .last_layout
            .as_ref()
            .map(|l| l.root.clone())
            .or_else(|| Some(self.settings.output_root.clone()).filter(|r| Path::new(r).is_dir()));
        if let Some(target) = target.filter(|t| Path::new(t).exists())
            && let Err(error) = win::shell_open(&target)
        {
            self.status = format!("打不开目录: {error}");
        }
    }

    fn open_url(&mut self, url: &str) {
        if let Err(error) = win::shell_open(url) {
            self.status = format!("打不开浏览器: {error}(网址 {url})");
        }
    }

    fn export_report(&mut self, frame: &eframe::Frame) {
        let Some(report) = &self.last_report else { return };
        let file = rfd::FileDialog::new()
            .set_title("导出转换报告")
            .add_filter("文本报告", &["txt"])
            .add_filter("JSON 报告", &["json"])
            .set_file_name(format!("ysm-convert-{}.txt", LocalTime::now().format_compact()))
            .set_parent(frame)
            .save_file();
        let Some(file) = file else { return };
        let is_json = file.extension().is_some_and(|x| x.eq_ignore_ascii_case("json"));
        let text = if is_json {
            report.to_json_string(true)
        } else {
            report.to_text(true)
        };
        match std::fs::write(&file, text) {
            Ok(()) => self.status = format!("报告已导出: {}", file.display()),
            Err(error) => show_message(
                frame,
                "导出报告",
                &format!("写入失败: {error}"),
                rfd::MessageLevel::Error,
            ),
        }
    }

    fn copy_attention(&mut self, ctx: &egui::Context) {
        let mut text = String::new();
        for a in &self.attention {
            text.push_str(&format!(
                "[{}] ({}) {}: {} {}\n    → {}{}\n",
                a.severity,
                a.pack,
                a.category,
                a.text,
                if a.count > 1 {
                    format!("x{}", a.count)
                } else {
                    String::new()
                },
                a.advice,
                a.doc.as_ref().map(|d| format!(" [{d}]")).unwrap_or_default()
            ));
        }
        ctx.copy_text(text);
        self.status = format!("已复制 {} 项到剪贴板", self.attention.len());
    }

    fn handle_dropped_files(&mut self, ctx: &egui::Context) {
        let dropped: Vec<String> = ctx.input(|i| {
            i.raw
                .dropped_files
                .iter()
                .map(|f| f.path().to_string_lossy().into_owned())
                .collect()
        });
        if !dropped.is_empty() {
            self.add_paths(dropped, ctx);
        }
    }

    fn drop_overlay(&self, ctx: &egui::Context) {
        if !ctx.input(|i| !i.raw.hovered_files.is_empty()) {
            return;
        }
        let painter = ctx.layer_painter(egui::LayerId::new(
            egui::Order::Foreground,
            egui::Id::unique("drop_overlay"),
        ));
        let rect = ctx.content_rect();
        painter.rect_filled(rect, 0.0, Color32::from_black_alpha(150));
        painter.text(
            rect.center(),
            egui::Align2::CENTER_CENTER,
            "松开鼠标, 把这些目录里的 Java 模型包加进任务列表",
            FontId::proportional(20.0),
            Color32::WHITE,
        );
    }

    fn refresh_cover(&mut self, ctx: &egui::Context) {
        let path = self.settings.collection_cover.trim().to_string();
        if path == self.cover.path {
            return;
        }
        self.cover.path = path.clone();
        self.cover.texture = std::fs::read(&path)
            .ok()
            .and_then(|bytes| decode_png(&bytes))
            .map(|(w, h, rgba)| {
                let image = egui::ColorImage::from_rgba_unmultiplied([w as usize, h as usize], &rgba);
                ctx.load_texture("collection_cover", image, egui::TextureOptions::LINEAR)
            });
    }

    // ---------------------------------------------------------------- 界面
    fn status_bar(&mut self, ui: &mut Ui) {
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                self.progress_bar(ui);
                ui.add_space(12.0);
                ui.label(RichText::new(&self.elapsed_text).monospace().strong());
                ui.add_space(12.0);
                ui.with_layout(Layout::left_to_right(Align::Center), |ui| {
                    let status = self.status.replace('\n', " ");
                    ui.add(egui::Label::new(status.as_str()).truncate())
                        .on_hover_text(&self.status);
                });
            });
        });
        ui.add_space(2.0);
    }

    fn progress_bar(&self, ui: &mut Ui) {
        let (rect, _) = ui.allocate_exact_size(vec2(220.0, 14.0), Sense::hover());
        let visuals = ui.visuals();
        let painter = ui.painter();
        painter.rect_filled(rect, 3.0, visuals.extreme_bg_color);
        painter.rect_stroke(
            rect,
            3.0,
            Stroke::new(1.0, visuals.widgets.noninteractive.bg_stroke.color),
            egui::StrokeKind::Inside,
        );
        let fill = visuals.selection.bg_fill;
        if self.indeterminate {
            // 不确定进度: 一段色块来回跑
            let t = ui.input(|i| i.time) as f32;
            let phase = (t / 1.4).fract();
            let block = rect.width() * 0.3;
            let x = rect.left() - block + (rect.width() + block) * phase;
            let bar = Rect::from_min_max(
                pos2(x.max(rect.left()), rect.top()),
                pos2((x + block).min(rect.right()), rect.bottom()),
            );
            painter.rect_filled(bar, 3.0, fill);
            ui.ctx().request_repaint_after(Duration::from_millis(16));
        } else if self.progress > 0.0 {
            let bar = Rect::from_min_size(
                rect.min,
                vec2(rect.width() * self.progress.clamp(0.0, 1.0), rect.height()),
            );
            painter.rect_filled(bar, 3.0, fill);
        }
    }

    fn pack_list(&mut self, ui: &mut Ui, ctx: &egui::Context, frame: &eframe::Frame) {
        ui.label(
            RichText::new("① Java 模型包 (未加密目录, 含 ysm.json; 可直接把目录拖进来)")
                .strong()
                .size(14.5),
        );
        ui.add_space(4.0);
        let running = self.running();
        ui.horizontal_wrapped(|ui| {
            if ui
                .add_enabled(!running, egui::Button::new("添加目录…"))
                .on_hover_text("可选单个模型包目录、带 ysm-pack.json 的合集目录, 或装着若干包的上级目录(可多选)")
                .clicked()
            {
                self.pick_and_add(ctx, frame);
            }
            let any_selected = self.packs.iter().any(|p| p.selected);
            if ui
                .add_enabled(!running && any_selected, egui::Button::new("移除选中"))
                .on_hover_text("点行选中(Ctrl 多选、Shift 连选), 也可以按 Delete")
                .clicked()
            {
                self.remove_selected();
            }
            if ui
                .add_enabled(!running && !self.packs.is_empty(), egui::Button::new("清空"))
                .clicked()
            {
                self.packs.clear();
                self.last_clicked = None;
            }
        });
        ui.add(
            egui::Label::new(
                RichText::new("包名 = 模型 ID 与全部资源 ID 的词根: 小写英文/数字/下划线, 建议带作者前缀防冲突; 合集 = 模型选择界面里的文件夹")
                    .small()
                    .weak(),
            )
            .wrap(),
        );
        ui.add_space(4.0);
        if !running && ctx.input(|i| i.key_pressed(egui::Key::Delete)) && ctx.memory(|m| m.focused().is_none()) {
            self.remove_selected();
        }
        if self.packs.is_empty() {
            ui.add_space(40.0);
            ui.vertical_centered(|ui| {
                ui.label(
                    RichText::new("把 Java 模型包目录拖到这里, 或点“添加目录…”")
                        .weak()
                        .size(15.0),
                );
            });
            return;
        }
        self.pack_table(ui, running);
    }

    fn pack_table(&mut self, ui: &mut Ui, running: bool) {
        let mut clicked: Option<(usize, egui::Modifiers)> = None;
        let notice = notice_color(ui);
        let ok_color = if ui.visuals().dark_mode {
            Color32::from_rgb(0x4A, 0xDE, 0x80)
        } else {
            Color32::from_rgb(0x16, 0xA3, 0x4A)
        };
        let modifiers = ui.input(|i| i.modifiers);
        egui::ScrollArea::horizontal()
            .id_salt("packs_h")
            .auto_shrink([false, false])
            .scroll_bar_visibility(egui::scroll_area::ScrollBarVisibility::AlwaysVisible)
            .show(ui, |ui| {
                TableBuilder::new(ui)
                    .id_salt("packs")
                    .striped(true)
                    .resizable(true)
                    .sense(Sense::click())
                    .cell_layout(Layout::left_to_right(Align::Center))
                    .column(Column::exact(40.0))
                    .column(Column::initial(130.0).at_least(60.0).clip(true))
                    .column(Column::initial(150.0).at_least(60.0).clip(true))
                    .column(Column::initial(190.0).at_least(80.0).clip(true))
                    .column(Column::initial(96.0).at_least(60.0).clip(true))
                    .column(Column::initial(136.0).at_least(100.0).clip(true))
                    .column(Column::initial(86.0).at_least(80.0))
                    .column(Column::initial(136.0).at_least(60.0).clip(true))
                    .column(Column::initial(52.0).at_least(40.0))
                    .column(Column::remainder().at_least(200.0).clip(true))
                    .header(24.0, |mut header| {
                        for title in [
                            "启用",
                            "Java 文件夹",
                            "模型名",
                            "包名 (可改)",
                            "合集 (可改)",
                            "材质",
                            "背面剔除",
                            "状态",
                            "待过目",
                            "概况",
                        ] {
                            header.col(|ui| {
                                ui.strong(title);
                            });
                        }
                    })
                    .body(|body| {
                        body.rows(26.0, self.packs.len(), |mut row| {
                            let i = row.index();
                            let item = &mut self.packs[i];
                            row.set_selected(item.selected);
                            row.col(|ui| {
                                ui.add_enabled(
                                    item.source.error.is_none(),
                                    egui::Checkbox::without_text(&mut item.enabled),
                                );
                            });
                            row.col(|ui| {
                                ui.add(egui::Label::new(item.folder.as_str()).truncate().selectable(false))
                                    .on_hover_text(&item.java_dir);
                            });
                            row.col(|ui| {
                                ui.add(
                                    egui::Label::new(item.display_name.as_str())
                                        .truncate()
                                        .selectable(false),
                                );
                            });
                            row.col(|ui| {
                                let problem = names::problem(item.name.trim());
                                let edit = TextEdit::singleline(&mut item.name)
                                    .desired_width(ui.available_width())
                                    .text_color_opt(problem.as_ref().map(|_| ERROR_COLOR));
                                let response = ui.add_enabled(!running, edit);
                                if let Some(problem) = problem {
                                    response.on_hover_text(problem);
                                }
                            });
                            row.col(|ui| {
                                ui.add_enabled(
                                    !running,
                                    TextEdit::singleline(&mut item.collection).desired_width(ui.available_width()),
                                );
                            });
                            row.col(|ui| {
                                let tip = item.material_tip();
                                material_editor(ui, ("material", i), &mut item.material, !running, &tip);
                            });
                            row.col(|ui| {
                                ui.add_enabled_ui(!running, |ui| {
                                    egui::ComboBox::from_id_salt(("cull", i))
                                        .width(ui.available_width() - 4.0)
                                        .selected_text(item.cull_mode.as_str())
                                        .show_ui(ui, |ui| {
                                            for choice in material::CULL_CHOICES {
                                                ui.selectable_value(&mut item.cull_mode, choice.to_string(), *choice);
                                            }
                                        })
                                        .response
                                        .on_hover_text(item.material_tip());
                                });
                            });
                            row.col(|ui| {
                                let color = match item.ok {
                                    Some(true) => ok_color,
                                    Some(false) => ERROR_COLOR,
                                    None if item.status == "转换中" => notice,
                                    None => ui.visuals().text_color(),
                                };
                                ui.add(
                                    egui::Label::new(RichText::new(&item.status).color(color))
                                        .truncate()
                                        .selectable(false),
                                );
                            });
                            row.col(|ui| {
                                let text = if item.attention_count > 0 {
                                    item.attention_count.to_string()
                                } else {
                                    String::new()
                                };
                                ui.add(egui::Label::new(text).selectable(false));
                            });
                            row.col(|ui| {
                                ui.add(
                                    egui::Label::new(RichText::new(&item.summary).weak())
                                        .truncate()
                                        .selectable(false),
                                )
                                .on_hover_text(&item.summary);
                            });
                            if row.response().clicked() {
                                clicked = Some((i, modifiers));
                            }
                        });
                    });
            });
        if let Some((i, modifiers)) = clicked {
            if modifiers.shift {
                let anchor = self.last_clicked.unwrap_or(i);
                let (lo, hi) = (anchor.min(i), anchor.max(i));
                for (j, p) in self.packs.iter_mut().enumerate() {
                    p.selected = (lo..=hi).contains(&j) || (modifiers.command && p.selected);
                }
            } else if modifiers.command {
                self.packs[i].selected = !self.packs[i].selected;
                self.last_clicked = Some(i);
            } else {
                for (j, p) in self.packs.iter_mut().enumerate() {
                    p.selected = j == i;
                }
                self.last_clicked = Some(i);
            }
        }
    }

    fn options(&mut self, ui: &mut Ui, ctx: &egui::Context, frame: &eframe::Frame) {
        ui.label(RichText::new("② 输出与选项").strong().size(14.5));
        ui.add_space(4.0);
        let running = self.running();
        egui::ScrollArea::vertical().id_salt("options").auto_shrink([false, false]).show(ui, |ui| {
            ui.set_width(ui.available_width() - 8.0);
            let s = &mut self.settings;
            ui.radio_value(
                &mut s.is_component_mode,
                true,
                "新建 / 复用独立模型组件 (<名>_bp 与 <名>_rp, 与 YSM 主组件一起启用即被发现)",
            );
            ui.radio_value(&mut s.is_component_mode, false, "写入已有的资源包 / 行为包 (选它们所在的根目录)");
            ui.add_space(6.0);
            ui.label("目标根目录");
            let mut browse_output = false;
            ui.horizontal(|ui| {
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    browse_output = ui.add_enabled(!running, egui::Button::new("浏览…")).clicked();
                    ui.add(TextEdit::singleline(&mut s.output_root).desired_width(ui.available_width()));
                });
            });
            ui.add_space(6.0);
            ui.label("组件名 (仅新建组件时用; 英文/数字/下划线)");
            let component_mode = s.is_component_mode;
            ui.add_enabled(component_mode, TextEdit::singleline(&mut s.component_name).desired_width(f32::INFINITY));
            if browse_output {
                self.browse_output(frame);
            }

            ui.add_space(8.0);
            ui.separator();
            ui.checkbox(&mut self.settings.use_collection, "把列表里的包归入同一个合集文件夹");
            let use_collection = self.settings.use_collection;
            let mut browse_cover = false;
            let mut apply_collection = false;
            self.refresh_cover(ctx);
            ui.add_enabled_ui(use_collection, |ui| {
                let s = &mut self.settings;
                egui::Grid::new("collection").num_columns(2).spacing([8.0, 4.0]).show(ui, |ui| {
                    ui.label("合集目录名");
                    ui.add(TextEdit::singleline(&mut s.collection_dir).desired_width(f32::INFINITY))
                        .on_hover_text("ysm_models 下的目录名(英文/数字/下划线); 同一目录名的包在游戏里归进同一个文件夹");
                    ui.end_row();
                    ui.label("文件夹名");
                    ui.add(TextEdit::singleline(&mut s.collection_name).desired_width(f32::INFINITY))
                        .on_hover_text("游戏里文件夹卡片上显示的名字; 不填则沿用 Java 合集自带的名字, 都没有就显示目录名");
                    ui.end_row();
                    ui.label("文件夹封面");
                    ui.horizontal(|ui| {
                        ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                            if ui.add_enabled(!running, egui::Button::new("清除")).clicked() {
                                s.collection_cover.clear();
                            }
                            browse_cover = ui.add_enabled(!running, egui::Button::new("浏览…")).clicked();
                            ui.add(TextEdit::singleline(&mut s.collection_cover).desired_width(ui.available_width()));
                        });
                    });
                    ui.end_row();
                });
                ui.horizontal(|ui| {
                    let (rect, _) = ui.allocate_exact_size(vec2(58.0, 100.0), Sense::hover());
                    ui.painter().rect_stroke(
                        rect,
                        2.0,
                        Stroke::new(1.0, Color32::from_rgba_unmultiplied(0x88, 0x88, 0x88, 0x66)),
                        egui::StrokeKind::Inside,
                    );
                    if let Some(texture) = &self.cover.texture {
                        let size = texture.size_vec2();
                        let scale = (rect.width() / size.x).min(rect.height() / size.y);
                        let shown = Rect::from_center_size(rect.center(), size * scale);
                        ui.painter().image(texture.id(), shown, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
                    }
                    ui.add(
                        egui::Label::new(
                            RichText::new("PNG, 不超过 1MB。游戏里按 Java 文件夹卡片 52x90 的比例等比铺满, 底部一截是名字区。不选则用 Java 合集自带的 ysm-pack.png, 都没有就是默认封面。贴图改动要重启游戏才会加载。")
                                .small()
                                .weak(),
                        )
                        .wrap(),
                    );
                });
                apply_collection =
                    ui.add_enabled(!running, egui::Button::new("应用到列表里全部包的“合集”列")).clicked();
            });
            if browse_cover {
                self.browse_cover(frame);
            }
            if apply_collection {
                self.apply_collection();
            }

            ui.add_space(8.0);
            ui.separator();
            ui.label("模型材质 (每个包在列表的“材质”“背面剔除”两列里单独改)");
            let mut apply_material = false;
            egui::Grid::new("batch_material").num_columns(2).spacing([8.0, 4.0]).show(ui, |ui| {
                ui.label("材质");
                material_editor(
                    ui,
                    "batch_material",
                    &mut self.batch_material,
                    true,
                    "选预设或直接输入材质名; 也可以写 default=材质, ysm_glow=材质 分别指定模型本体和发光组(ysmGlow 开头的骨骼)",
                );
                ui.end_row();
                ui.label("背面剔除");
                egui::ComboBox::from_id_salt("batch_cull").selected_text(self.batch_cull.as_str()).show_ui(ui, |ui| {
                    for choice in material::CULL_CHOICES {
                        ui.selectable_value(&mut self.batch_cull, choice.to_string(), *choice);
                    }
                });
                ui.end_row();
            });
            ui.add(
                egui::Label::new(
                    RichText::new("自动 = 读 Java 包: properties.all_cutout 打开(负尺寸描边模型)就剔除背面, 皮肤带 PBR 贴图用 pbr, 其余 cutout(和 Java 缺省一样不剔除背面, 单面的火焰、飘带两面都看得见)。用负尺寸方块做描边外壳、Java 包却没开 all_cutout 时, 把背面剔除设为“剔除”。自定义材质直接输入材质名(资源包 materials 里定义的或原版材质名), 它不参与描边 / 泛光后处理。")
                        .small()
                        .weak(),
                )
                .wrap(),
            );
            apply_material =
                ui.add_enabled(!running, egui::Button::new("应用到列表里全部包的“材质”“背面剔除”列")).clicked() || apply_material;
            if apply_material {
                self.apply_material(frame);
            }

            ui.add_space(8.0);
            ui.separator();
            let s = &mut self.settings;
            ui.checkbox(&mut s.compact_json, "产物 JSON 压缩为一行 (体积约省 70%, 转换更快; 要手工查看或用 git 对比时关掉)");
            ui.checkbox(&mut s.validate_after, "转换后运行资源包红线体检 (推荐)");
            ui.checkbox(
                &mut s.with_mods,
                "携带第三方模组联动动画 (slashblade / parcool …, 基岩无对应物品, 一般不勾; 枪械动画总会带上)",
            );
            ui.checkbox(&mut s.java_functions, "转换自定义函数 (实验性: functions/*.molang 的 fn.* / 音效 / 事件脚本; 不勾则按 0 处理)")
                .on_hover_text("functions/*.molang 里的 fn.* 调用内联, ysm.play_sound / ysm.sync / 键鼠改成主组件宿主请求, @player_init / @player_update / @sync 与脚本控制器编进逐帧执行体。仍在完善(音效随动画停止等与 Java 有出入), 不勾时与之前一样把这些调用置零。");
        });
    }

    fn results(&mut self, ui: &mut Ui, ctx: &egui::Context, frame: &eframe::Frame) {
        ui.add_space(4.0);
        let running = self.running();
        ui.horizontal(|ui| {
            ui.selectable_value(&mut self.tab, Tab::Logs, "日志");
            ui.selectable_value(
                &mut self.tab,
                Tab::Attention,
                format!("开发者提醒 ({})", self.attention.len()),
            );
            ui.selectable_value(&mut self.tab, Tab::Validation, "体检");
            // 这两个页签当按钮用: 点一下在浏览器打开线上 Wiki / 开源仓库
            if ui
                .selectable_label(false, "文档 ↗")
                .on_hover_text(links::WIKI_URL)
                .clicked()
            {
                self.open_url(links::WIKI_URL);
            }
            if ui
                .selectable_label(false, "开源地址 ↗")
                .on_hover_text(links::REPO_URL)
                .clicked()
            {
                self.open_url(links::REPO_URL);
            }
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                if ui
                    .add_enabled(self.last_report.is_some(), egui::Button::new("导出报告…"))
                    .clicked()
                {
                    self.export_report(frame);
                }
                if ui.button("打开输出目录").clicked() {
                    self.open_output();
                }
                if ui
                    .add_enabled(self.can_run(), egui::Button::new("修复产物"))
                    .on_hover_text("对已落盘产物就地补跑修复规则(幂等); 有 Java 源时重新转换更完整")
                    .clicked()
                {
                    self.start_fix(ctx, frame);
                }
                if ui
                    .add_enabled(self.can_run(), egui::Button::new("只体检"))
                    .on_hover_text("对已落盘的产物跑引擎红线体检(不重新转换)")
                    .clicked()
                {
                    self.start_validate(ctx, frame);
                }
                if ui.add_enabled(running, egui::Button::new("取消")).clicked()
                    && let Some(run) = &self.run
                {
                    run.cancel.cancel();
                }
                if ui
                    .add_enabled(
                        self.can_run(),
                        egui::Button::new(RichText::new("开始转换").strong()).min_size(vec2(96.0, 0.0)),
                    )
                    .on_hover_text("资源包改动要重启游戏才会重新加载; 独立组件必须与 YSM 主组件一起启用。")
                    .clicked()
                {
                    self.start_convert(ctx, frame);
                }
            });
        });
        ui.separator();
        match self.tab {
            Tab::Logs => self.logs_tab(ui),
            Tab::Attention => self.attention_tab(ui, ctx),
            Tab::Validation => self.validation_tab(ui),
        }
    }

    fn logs_tab(&mut self, ui: &mut Ui) {
        ui.horizontal(|ui| {
            if ui
                .checkbox(&mut self.settings.show_details, "显示全部内核明细行")
                .changed()
            {
                self.refilter_logs();
            }
            ui.add_space(12.0);
            ui.label(
                RichText::new("红 = 错误, 橙 = 警告, 蓝 = 提醒 (内核做了降级/近似, 建议进游戏核对), 灰 = 明细").weak(),
            );
        });
        let font = TextStyle::Monospace.resolve(ui.style());
        let small = TextStyle::Small.resolve(ui.style());
        let logs = &self.logs;
        let visible = &self.visible_logs;
        self.log_list.show(
            ui,
            "logs",
            visible.len(),
            self.log_version,
            true,
            |ui, i, width| {
                let entry = &logs[visible[i]];
                let galley = ui.painter().layout(
                    entry.text.clone(),
                    font.clone(),
                    Color32::WHITE,
                    (width - 70.0).max(40.0),
                );
                galley.size().y + 3.0
            },
            |ui, i, rect| {
                let entry = &logs[visible[i]];
                let color = level_color(ui, &entry.level, false);
                let painter = ui.painter();
                painter.text(
                    rect.min + vec2(0.0, 1.0),
                    egui::Align2::LEFT_TOP,
                    &entry.time,
                    small.clone(),
                    DETAIL_COLOR,
                );
                let galley = painter.layout(entry.text.clone(), font.clone(), color, (rect.width() - 70.0).max(40.0));
                painter.galley(rect.min + vec2(66.0, 1.0), galley, color);
            },
        );
    }

    fn attention_tab(&mut self, ui: &mut Ui, ctx: &egui::Context) {
        ui.horizontal(|ui| {
            if ui.add_enabled(!self.attention.is_empty(), egui::Button::new("复制全部")).clicked() {
                self.copy_attention(ctx);
            }
            ui.add(
                egui::Label::new(
                    RichText::new("这里汇总需要人工过目的项: 基岩没有的 molang(置 0 / 按中性常量处理)、未转换的脚本控制器、缺失文件、体检错误。每项附处理建议与文档; 级别 info(灰)知道即可。")
                        .weak(),
                )
                .wrap(),
            );
        });
        let body = TextStyle::Body.resolve(ui.style());
        let small = TextStyle::Small.resolve(ui.style());
        // 列: 包 | 级别 | 类别 | 内容 | 建议 | 文档
        let columns = |width: f32| {
            let fixed = [150.0, 60.0, 130.0, 200.0];
            let gap = 8.0;
            let flex = ((width - fixed.iter().sum::<f32>() - gap * 5.0) / 2.0).max(160.0);
            [fixed[0], fixed[1], fixed[2], flex, flex, fixed[3]]
        };
        let header_cols = columns(ui.available_width() - 14.0);
        ui.horizontal(|ui| {
            for (title, w) in ["包", "级别", "类别", "内容", "建议", "文档"].iter().zip(header_cols) {
                ui.allocate_ui_with_layout(vec2(w, 18.0), Layout::left_to_right(Align::Center), |ui| {
                    ui.set_min_width(w);
                    ui.strong(*title);
                });
            }
        });
        let attention = &self.attention;
        let mut open_wiki = false;
        let measure_text = |ui: &Ui, text: &str, font: &FontId, w: f32| {
            ui.painter()
                .layout(text.to_string(), font.clone(), Color32::WHITE, w)
                .size()
                .y
        };
        self.attention_list.show(
            ui,
            "attention",
            attention.len(),
            self.attention_version,
            false,
            |ui, i, width| {
                let a = &attention[i];
                let c = columns(width);
                let content = if a.count > 1 {
                    format!("{} x{}", a.text, a.count)
                } else {
                    a.text.clone()
                };
                let h = [
                    measure_text(ui, &a.pack, &body, c[0]),
                    measure_text(ui, &a.category, &body, c[2]),
                    measure_text(ui, &content, &body, c[3]),
                    measure_text(ui, &a.advice, &body, c[4]),
                    20.0,
                ]
                .into_iter()
                .fold(0.0, f32::max);
                h + 10.0
            },
            |ui, i, rect| {
                let a = &attention[i];
                let c = columns(rect.width());
                let mut x = rect.left();
                let top = rect.top() + 4.0;
                let severity_color = level_color(ui, &a.severity, true);
                let text_color = ui.visuals().text_color();
                let weak = ui.visuals().weak_text_color();
                let painter = ui.painter().clone();
                let put = |text: &str, color: Color32, w: f32, font: &FontId, x: &mut f32| {
                    let galley = painter.layout(text.to_string(), font.clone(), color, w);
                    painter.galley(pos2(*x, top), galley, color);
                    *x += w + 8.0;
                };
                put(&a.pack, text_color, c[0], &body, &mut x);
                put(&a.severity, severity_color, c[1], &body, &mut x);
                put(&a.category, text_color, c[2], &body, &mut x);
                let mut job = egui::text::LayoutJob::default();
                job.wrap.max_width = c[3];
                job.append(&a.text, 0.0, egui::TextFormat::simple(body.clone(), text_color));
                if a.count > 1 {
                    job.append(
                        &format!(" x{}", a.count),
                        0.0,
                        egui::TextFormat::simple(body.clone(), weak),
                    );
                }
                painter.galley(pos2(x, top), painter.layout_job(job), text_color);
                x += c[3] + 8.0;
                put(&a.advice, text_color, c[4], &body, &mut x);
                if let Some(doc) = &a.doc {
                    let button_rect = Rect::from_min_size(pos2(x, top - 2.0), vec2(c[5].min(rect.right() - x), 20.0));
                    if ui
                        .put(button_rect, egui::Button::new(RichText::new(doc).font(small.clone())))
                        .on_hover_text("在浏览器打开 YSM Wiki, 按这个文档名找对应章节")
                        .clicked()
                    {
                        open_wiki = true;
                    }
                }
                ui.painter().hline(
                    rect.x_range(),
                    rect.bottom() - 0.5,
                    Stroke::new(1.0, ui.visuals().widgets.noninteractive.bg_stroke.color),
                );
            },
        );
        if open_wiki {
            self.open_url(links::WIKI_URL);
        }
    }

    fn validation_tab(&mut self, ui: &mut Ui) {
        ui.add(
            egui::Label::new(
                RichText::new("资源包红线体检: 错误 = 整份动画/控制器文件会被引擎拒载(游戏里模型停在绑定姿态), 必须修到 0; 警告 = 功能可能缺失(如音效未登记)。")
                    .weak(),
            )
            .wrap(),
        );
        let font = TextStyle::Monospace.resolve(ui.style());
        let items = &self.validation;
        self.validation_list.show(
            ui,
            "validation",
            items.len(),
            self.validation_version,
            false,
            |ui, i, width| {
                let v = &items[i];
                ui.painter()
                    .layout(format!("{} {}", v.level, v.text), font.clone(), Color32::WHITE, width)
                    .size()
                    .y
                    + 3.0
            },
            |ui, i, rect| {
                let v = &items[i];
                let color = level_color(ui, &v.level, false);
                let mut job = egui::text::LayoutJob::default();
                job.wrap.max_width = rect.width();
                let mut strong = egui::TextFormat::simple(font.clone(), color);
                strong.underline = Stroke::NONE;
                job.append(&v.level, 0.0, strong);
                job.append(
                    &format!(" {}", v.text),
                    0.0,
                    egui::TextFormat::simple(font.clone(), color),
                );
                let painter = ui.painter();
                painter.galley(rect.min + vec2(0.0, 1.0), painter.layout_job(job), color);
            },
        );
    }
}

/// 可编辑的材质下拉框: 输入框 + "▾" 预设菜单(值 + 说明)。失去焦点时把空白规整成"自动"。
fn material_editor(
    ui: &mut Ui,
    id: impl std::hash::Hash + std::fmt::Debug,
    value: &mut String,
    enabled: bool,
    tip: &str,
) {
    ui.add_enabled_ui(enabled, |ui| {
        ui.horizontal(|ui| {
            ui.spacing_mut().item_spacing.x = 2.0;
            let problem = material::problem(Some(value));
            let response = ui.add(
                TextEdit::singleline(value)
                    .id_salt(&id)
                    .desired_width((ui.available_width() - 24.0).max(40.0))
                    .text_color_opt(problem.as_ref().map(|_| ERROR_COLOR)),
            );
            if response.lost_focus() {
                let trimmed = value.trim();
                *value = if trimmed.is_empty() {
                    material::AUTO.to_string()
                } else {
                    trimmed.to_string()
                };
            }
            response.on_hover_text(tip);
            ui.menu_button(RichText::new("▼").size(10.0), |ui| {
                for choice in material::CHOICES {
                    let mut job = egui::text::LayoutJob::default();
                    let body = TextStyle::Body.resolve(ui.style());
                    job.append(
                        choice.value,
                        0.0,
                        egui::TextFormat::simple(body.clone(), ui.visuals().strong_text_color()),
                    );
                    job.append(
                        &format!("   {}", choice.label),
                        0.0,
                        egui::TextFormat::simple(body, ui.visuals().weak_text_color()),
                    );
                    if ui.add(egui::Button::new(job).frame(false)).clicked() {
                        *value = choice.value.to_string();
                        ui.close();
                    }
                }
            });
        });
    });
}

impl eframe::App for YsmApp {
    fn logic(&mut self, ctx: &egui::Context, frame: &mut eframe::Frame) {
        self.process_messages(ctx, frame);
        if let Some(run) = &self.run {
            self.elapsed_text = format_elapsed(run.started.elapsed());
            ctx.request_repaint_after(Duration::from_millis(100));
        }
    }

    fn ui(&mut self, ui: &mut Ui, frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        self.handle_dropped_files(&ctx);
        egui::Panel::bottom("status").show(ui, |ui| self.status_bar(ui));
        egui::Panel::bottom("results")
            .resizable(true)
            .default_size(320.0)
            .min_size(180.0)
            .show(ui, |ui| self.results(ui, &ctx, frame));
        egui::Panel::right("options")
            .resizable(true)
            .default_size(440.0)
            .min_size(320.0)
            .show(ui, |ui| self.options(ui, &ctx, frame));
        egui::CentralPanel::default().show(ui, |ui| self.pack_list(ui, &ctx, frame));
        self.drop_overlay(&ctx);
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        if let Some(run) = &self.run {
            run.cancel.cancel();
        }
        self.settings.save();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_startup_args() {
        let dir = std::env::temp_dir().to_string_lossy().into_owned();
        let args: Vec<String> = [
            dir.as_str(),
            "--out",
            "D:\\out",
            "--component",
            "fox",
            "--auto-run",
            "Z:\\no\\such\\dir",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let s = StartupArgs::parse(&args);
        assert_eq!(s.paths, vec![dir]);
        assert_eq!(s.out.as_deref(), Some("D:\\out"));
        assert_eq!(s.component.as_deref(), Some("fox"));
        assert!(s.auto_run);
    }

    #[test]
    fn formats_elapsed_like_before() {
        assert_eq!(format_elapsed(Duration::from_millis(83_456)), "耗时 1:23.4");
        assert_eq!(format_elapsed(Duration::from_secs(3_725)), "耗时 1:02:05");
    }

    #[test]
    fn decodes_the_app_icon() {
        let (w, h, rgba) = crate::icon_rgba().expect("icon.ico 里有 PNG 条目");
        assert_eq!((w, h), (256, 256));
        assert_eq!(rgba.len(), 256 * 256 * 4);
    }
}
