//! ysmconv — YSM 模型转换器命令行(Java 版模型包 → 网易基岩版组件) + MCP 服务器。

mod args;
mod mcp;

use std::collections::HashMap;
use std::io::Write;
use std::process::ExitCode;
use std::sync::Mutex;

use serde_json::{Value, json};
use ysmconv_core::event::{self, KernelEvent};
use ysmconv_core::locator::KernelLocator;
use ysmconv_core::molang::StreamBuffer;
use ysmconv_core::service::{PerPack, PlanOptions, effective_parallelism};
use ysmconv_core::{
    CancelToken, CollectionSpec, ConversionReport, ConversionService, ConvertOptions, ConvertPlan, ConvertRequest,
    Error, OutputLayout, Result, links, material, output,
};

use args::ParsedArgs;

const USAGE: &str = "ysmconv — YSM 模型转换器命令行(Java 版模型包 → 网易基岩版组件)

用法:
  ysmconv info                                   内核与目录信息
  ysmconv discover <目录>... [--json]            发现 Java 模型包(单包 / 合集目录 / 上级目录)
  ysmconv convert <目录>... --out <组件根目录> [选项]
  ysmconv validate --out <组件根目录> [包名...] [--json]
  ysmconv fix --out <组件根目录> [包名...] [--no-validate] [--pretty] [--json]
  ysmconv baseline <Java default 模型目录> --out <组件根目录> [--with-mods]
  ysmconv mcp [--out <缺省组件根目录>]              以 MCP 服务器(stdio)方式运行, 供 AI 调用

convert 选项:
  --component <名>          新建/复用独立组件 <根>/<名>_bp 与 <名>_rp(缺省: 根目录必须已是含行为包+资源包的工程)
  --collection <目录名>     全部包归入这个合集文件夹(ysm_models/<目录名>/<包名>)
  --collection-name <名字>  文件夹在游戏里显示的名字(缺省沿用 Java 合集自带的名字)
  --collection-cover <PNG>  文件夹封面图(不超过 1MB, 最好是 Java 卡片 52x90 的比例; 缺省用合集自带的 ysm-pack.png)
  --prefix <前缀>           包名统一加前缀(缺省用内核建议名: 拼音化 + 合集前缀)
  --rename <文件夹>=<包名>  逐包指定包名(可多次)
  --with-mods               携带第三方模组联动动画(slashblade/parcool/...; 枪械 tacz 动画缺省就带)
  --java-functions          转换 Java 自定义函数(实验性, 缺省不转: fn.* 与音效调用按 0 处理)
  --material <材质>         全部包的模型材质(缺省自动: 按 Java 包的 all_cutout / PBR 贴图判定): 预设名
                            cutout / translucent / emissive / pbr / bloom(及 *_cull 剔除背面版)、自定义材质名,
                            或 \"default=<材质>,ysm_glow=<材质>\" 逐个材质键指定(default 本体, ysm_glow 发光组)
  --material-for <文件夹>=<材质>  逐包指定材质(可多次; 文件夹 = Java 包目录名)
  --cull <auto|on|off>      背面剔除(缺省 auto = 照 Java properties.all_cutout; on = 负尺寸描边要的剔除背面)
  --cull-for <文件夹>=<auto|on|off>  逐包指定背面剔除(可多次)
  --no-validate             转换后不跑资源包体检
  --pretty                  产物 JSON 按 2 空格缩进(缺省压成一行: 体积约省 70%, 转换也更快)
  --jobs <N>                同时转换的包数(缺省自动: 逻辑核数的一半、最多 8, 再按可用内存封顶);
                            多个包总是每包一个内核进程, 同一进程连续转大包会耗尽内存
  --ref-rp <资源包>         指定 java_default 基线所在资源包(缺省: 产物资源包自带则用它, 否则用内核快照)
  --json                    只输出 JSON 报告(不流式打印日志)
  --report <文件>           把 JSON 报告另存到文件
  --details                 文本模式下打印全部内核日志行(缺省只打印需要注意的项)

退出码: 0 成功, 1 有失败/体检错误, 2 参数或环境错误";

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.is_empty() || matches!(argv[0].as_str(), "-h" | "--help" | "help" | "/?") {
        println!("{USAGE}");
        return ExitCode::from(2);
    }
    if matches!(argv[0].as_str(), "-V" | "--version" | "version") {
        println!("ysmconv {}", ysmconv_core::VERSION);
        return ExitCode::SUCCESS;
    }
    let verb = argv[0].to_lowercase();
    let rest = &argv[1..];
    let result = match verb.as_str() {
        "info" => info(rest),
        "discover" => discover(rest),
        "convert" => convert(rest),
        "validate" => validate(rest),
        "fix" => fix(rest),
        "baseline" => baseline(rest),
        "mcp" => mcp::run(rest),
        _ => return fail(&format!("未知子命令: {verb}\n\n{USAGE}")),
    };
    match result {
        Ok(code) => ExitCode::from(code),
        Err(Error::Argument(message)) => fail(&format!("参数错误: {message}")),
        Err(error) => fail(&error.full_text("\n")),
    }
}

fn fail(message: &str) -> ExitCode {
    eprintln!("{message}");
    ExitCode::from(2)
}

fn create_service() -> Result<ConversionService> {
    Ok(ConversionService::new(KernelLocator::locate()?))
}

fn pretty(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_default()
}

/// 终端输出: 一行写不进(管道被关)就静默放弃, 不让 println! 的 panic 把进程带走。
fn out(line: &str) {
    let mut stdout = std::io::stdout().lock();
    let _ = writeln!(stdout, "{line}");
}

fn read_source_info(paths: &ysmconv_core::KernelPaths) -> Option<Value> {
    let text = std::fs::read_to_string(paths.source_info_file()).ok()?;
    ysmconv_core::jsonc::parse(&text).ok()
}

// ---------------------------------------------------------------- info
fn info(args: &[String]) -> Result<u8> {
    let a = ParsedArgs::parse(args, &["json"])?;
    let service = create_service()?;
    let paths = service.paths();
    // 内核起不来时(典型: 缺 VC++ 2008 运行库)先把定位到的路径打全, 再报错 —— 这正是 info 的用处
    let (info, probe_error) = match service.get_info(&CancelToken::new()) {
        Ok(info) => (Some(info), None),
        Err(error @ Error::Kernel { .. }) => (None, Some(error.full_text("\n"))),
        Err(error) => return Err(error),
    };
    let source = read_source_info(paths);
    if a.has("json") {
        out(&pretty(&json!({
            "coreDir": paths.core_dir.display().to_string(),
            "python": paths.python_exe.display().to_string(),
            "pythonSource": paths.python_source,
            "kernelRoot": paths.kernel_root.display().to_string(),
            "kernelFromProject": paths.from_project,
            "docsDir": paths.docs_dir.display().to_string(),
            "wiki": links::WIKI_URL,
            "repo": links::REPO_URL,
            "version": ysmconv_core::VERSION,
            "kernel": info.as_ref().map(|i| i.to_value()),
            "kernelError": probe_error,
            "kernelSource": source,
        })));
        return Ok(if probe_error.is_none() { 0 } else { 1 });
    }
    let kernel = info
        .as_ref()
        .and_then(|i| i.kernel.clone())
        .unwrap_or_else(|| "?".into());
    let python = info
        .as_ref()
        .and_then(|i| i.python.clone())
        .unwrap_or_else(|| "?".into());
    let pinyin = if info.as_ref().and_then(|i| i.pinyin) == Some(true) {
        "有"
    } else {
        "无"
    };
    out(&format!("ysmconv:     {}", ysmconv_core::VERSION));
    out(&format!("core 目录:   {}", paths.core_dir.display()));
    out(&format!(
        "Python:      {} ({}) 版本 {python}",
        paths.python_exe.display(),
        paths.python_source
    ));
    out(&format!(
        "内核入口:    {} (内核 {kernel}, 拼音库 {pinyin})",
        paths.port_cli().display()
    ));
    out(&format!("内核来源:    {}", paths.kernel_source_text()));
    out(&format!("基线资源包:  {}", paths.bundled_ref_rp().display()));
    out(&format!("在线文档:    {}", links::WIKI_URL));
    out(&format!("开源地址:    {}", links::REPO_URL));
    out(&format!(
        "本地文档:    {}(同一批文档的副本, 供 AI 经 MCP 的 ysm_docs 查阅)",
        paths.docs_dir.display()
    ));
    if let Some(Value::Object(src)) = &source {
        let text = |k: &str| match src.get(k) {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Null) | None => String::new(),
            Some(other) => other.to_string(),
        };
        let commit: String = text("commit").chars().take(12).collect();
        out(&format!(
            "内核来源:    {} @ {commit} 同步于 {}",
            text("source"),
            text("syncedAt")
        ));
    }
    if let Some(error) = probe_error {
        eprintln!();
        eprintln!("[内核无法运行] {error}");
        return Ok(1);
    }
    Ok(0)
}

// ---------------------------------------------------------------- discover
fn discover(args: &[String]) -> Result<u8> {
    let a = ParsedArgs::parse(args, &["json"])?;
    if a.positional.is_empty() {
        return Err(Error::Argument("discover 需要至少一个目录".into()));
    }
    let service = create_service()?;
    let packs = service.discover(&a.positional, &CancelToken::new())?;
    if a.has("json") {
        out(&serde_json::to_string_pretty(&packs).unwrap_or_default());
        return Ok(0);
    }
    for p in &packs {
        if let Some(error) = &p.error {
            out(&format!("[跳过] {}: {error}", p.java_dir));
            continue;
        }
        let suggested = p.suggested_name.as_deref().unwrap_or("");
        out(&format!(
            "{} {}{}",
            pad_right(suggested, 40),
            p.display_name(),
            p.collection_folder
                .as_ref()
                .map(|c| format!("  [合集 {c}]"))
                .unwrap_or_default()
        ));
        out(&format!("    {}", p.java_dir));
        if let Some(profile) = &p.material_profile {
            let hint = material::negative_cube_hint(Some(profile), None)
                .map(|h| format!("; {h}"))
                .unwrap_or_default();
            out(&format!(
                "    材质(自动): {}{hint}",
                material::describe_auto(Some(profile))
            ));
        }
    }
    out(&format!(
        "共 {} 个 Java 包",
        packs.iter().filter(|p| p.error.is_none()).count()
    ));
    Ok(0)
}

fn pad_right(text: &str, width: usize) -> String {
    let len = text.chars().count();
    if len >= width {
        text.to_string()
    } else {
        format!("{text}{}", " ".repeat(width - len))
    }
}

// ---------------------------------------------------------------- convert
fn split_pair<'a>(text: &'a str, option: &str, shape: &str) -> Result<(&'a str, &'a str)> {
    match text.find('=') {
        Some(eq) if eq > 0 => Ok((&text[..eq], &text[eq + 1..])),
        _ => Err(Error::Argument(format!("--{option} 格式是 {shape}: {text}"))),
    }
}

fn convert(args: &[String]) -> Result<u8> {
    let a = ParsedArgs::parse(
        args,
        &[
            "json",
            "with-mods",
            "java-functions",
            "no-validate",
            "details",
            "pretty",
        ],
    )?;
    if a.positional.is_empty() {
        return Err(Error::Argument("convert 需要至少一个 Java 包目录".into()));
    }
    let jobs = match a.get("jobs") {
        None => 0,
        Some(text) => match text.trim().parse::<usize>() {
            Ok(n) if n >= 1 => n,
            _ => return Err(Error::Argument(format!("--jobs 要一个正整数: {text}"))),
        },
    };
    let service = create_service()?;
    let layout = resolve_layout(&a)?;
    let cancel = CancelToken::new();
    let discovered = service.discover(&a.positional, &cancel)?;
    for bad in discovered.iter().filter(|d| d.error.is_some()) {
        eprintln!("[跳过] {}: {}", bad.java_dir, bad.error.as_deref().unwrap_or(""));
    }

    let mut plan = PlanOptions {
        prefix: a.get("prefix").map(str::to_string),
        collection_override: a.get("collection").map(str::to_string),
        material: a.get("material").map(str::to_string),
        cull: material::parse_cull(a.get("cull"))?,
        renames: PerPack::new(),
        materials: PerPack::new(),
        culls: PerPack::new(),
    };
    for r in a.get_all("rename") {
        let (folder, name) = split_pair(r, "rename", "<文件夹>=<包名>")?;
        plan.renames.insert(folder, name.to_string());
    }
    for m in a.get_all("material-for") {
        let (folder, value) = split_pair(m, "material-for", "<文件夹>=<材质>")?;
        plan.materials.insert(folder, value.to_string());
    }
    for c in a.get_all("cull-for") {
        let (folder, value) = split_pair(c, "cull-for", "<文件夹>=<auto|on|off>")?;
        plan.culls.insert(folder, material::parse_cull(Some(value))?);
    }
    let packs = ConvertPlan::build_packs(&discovered, &plan);

    let mut collections = Vec::new();
    if let Some(dir) = a.get("collection") {
        let spec = CollectionSpec {
            dir: dir.to_string(),
            name: a.get("collection-name").map(str::to_string),
            description: a.get("collection-desc").map(str::to_string),
            cover_image: a.get("collection-cover").map(str::to_string),
        };
        if !spec.is_empty() {
            collections.push(spec);
        }
    } else if a.has("collection-name") || a.has("collection-cover") {
        return Err(Error::Argument(
            "--collection-name / --collection-cover 要和 --collection <目录名> 一起用".into(),
        ));
    }

    let request = ConvertRequest {
        layout,
        packs,
        collections,
        options: ConvertOptions {
            with_mods: a.has("with-mods"),
            java_functions: a.has("java-functions"),
            validate: !a.has("no-validate"),
            compact_json: !a.has("pretty"),
            max_parallel: jobs,
            ..ConvertOptions::default()
        },
        ref_rp: a.get("ref-rp").map(str::to_string),
    };
    let json_mode = a.has("json");
    if !json_mode && request.packs.len() > 1 {
        out(&format!(
            "{} 个包, 同时转换 {} 个{}",
            request.packs.len(),
            effective_parallelism(jobs, request.packs.len()),
            if request.options.compact_json {
                ", JSON 压成一行"
            } else {
                ""
            }
        ));
    }
    let printer = StreamingPrinter::new(a.has("details"));
    let on_event = |e: &KernelEvent| printer.on_event(e);
    let on_stderr = |line: &str| eprintln!("  (stderr) {line}");
    let report = if json_mode {
        service.convert(&request, None, None, &cancel)?
    } else {
        service.convert(&request, Some(&on_event), Some(&on_stderr), &cancel)?
    };
    finish(&report, &a, json_mode)
}

// ---------------------------------------------------------------- validate / fix / baseline
fn validate(args: &[String]) -> Result<u8> {
    let a = ParsedArgs::parse(args, &["json"])?;
    let service = create_service()?;
    let layout = resolve_layout(&a)?;
    let json_mode = a.has("json");
    let printer = StreamingPrinter::new(true);
    let on_event = |e: &KernelEvent| printer.on_event(e);
    let report = service.validate(
        &layout,
        &a.positional,
        if json_mode { None } else { Some(&on_event) },
        None,
        &CancelToken::new(),
    )?;
    finish(&report, &a, json_mode)
}

fn fix(args: &[String]) -> Result<u8> {
    let a = ParsedArgs::parse(args, &["json", "no-validate", "details", "pretty"])?;
    let service = create_service()?;
    let layout = resolve_layout(&a)?;
    let json_mode = a.has("json");
    let printer = StreamingPrinter::new(a.has("details"));
    let on_event = |e: &KernelEvent| printer.on_event(e);
    let report = service.fix(
        &layout,
        &a.positional,
        !a.has("no-validate"),
        !a.has("pretty"),
        if json_mode { None } else { Some(&on_event) },
        None,
        &CancelToken::new(),
    )?;
    finish(&report, &a, json_mode)
}

fn baseline(args: &[String]) -> Result<u8> {
    let a = ParsedArgs::parse(args, &["json", "with-mods", "details"])?;
    if a.positional.len() != 1 {
        return Err(Error::Argument("baseline 需要一个 Java default 模型目录".into()));
    }
    let service = create_service()?;
    let layout = resolve_layout(&a)?;
    let json_mode = a.has("json");
    let printer = StreamingPrinter::new(a.has("details"));
    let on_event = |e: &KernelEvent| printer.on_event(e);
    let report = service.baseline(
        &layout,
        &a.positional[0],
        a.has("with-mods"),
        if json_mode { None } else { Some(&on_event) },
        None,
        &CancelToken::new(),
    )?;
    finish(&report, &a, json_mode)
}

// ---------------------------------------------------------------- helpers
fn resolve_layout(a: &ParsedArgs) -> Result<OutputLayout> {
    let root = a.require("out")?;
    if let Some(component) = a.get("component") {
        return output::ensure_component(root, component);
    }
    if !std::path::Path::new(root).is_dir() {
        return Err(Error::Argument(format!(
            "输出目录不存在: {root}(新建组件请加 --component <名>)"
        )));
    }
    output::detect(root)
}

/// 流式打印事件。多个包并行时各包事件交错到达, 每个包的行先攒着, 该包完成时整块打印
/// (内核本来就是一个包转完才吐出它的日志, 不损失实时性)。服务层已把事件串行化, 这里的锁只为满足 Sync。
struct StreamingPrinter {
    details: bool,
    state: Mutex<PrinterState>,
}

#[derive(Default)]
struct PrinterState {
    pending: HashMap<String, Vec<String>>,
    molang: StreamBuffer,
}

impl StreamingPrinter {
    fn new(details: bool) -> Self {
        StreamingPrinter {
            details,
            state: Mutex::new(PrinterState::default()),
        }
    }

    fn on_event(&self, e: &KernelEvent) {
        let mut state = self.state.lock().unwrap_or_else(|p| p.into_inner());
        let key = e.pack.clone().unwrap_or_default();
        match e.event.as_str() {
            event::START => out(&format!("内核 {}", e.extra_string("kernel").unwrap_or_default())),
            event::PACK_START => {
                let line = format!(
                    "== {}{}",
                    e.pack.as_deref().unwrap_or(""),
                    e.java_dir.as_ref().map(|d| format!("  <- {d}")).unwrap_or_default()
                );
                state.pending.entry(key).or_default().push(line);
            }
            event::LOG => {
                if self.details || matches!(e.level.as_deref(), Some("error" | "warn" | "notice")) {
                    let line = format!("   {}", e.text.as_deref().unwrap_or(""));
                    state.pending.entry(key).or_default().push(line);
                }
            }
            event::MOLANG => {
                if let Some(line) = state.molang.line(e) {
                    state.pending.entry(key).or_default().push(format!("   {line}"));
                }
            }
            event::PACK_DONE => {
                let lower = state.molang.flush(e.pack.as_deref());
                let mut block = state.pending.remove(&key).unwrap_or_default();
                if let Some(lower) = lower {
                    block.push(format!("   {lower}"));
                }
                let index = e.index.unwrap_or(0) + 1;
                let total = e.total.map(|t| t.to_string()).unwrap_or_default();
                block.push(if e.ok == Some(true) {
                    format!(
                        "   -> [{index}/{total}] 完成 {:.1}s  错误 {} / 警告 {} / 提醒 {}",
                        e.seconds.unwrap_or(0.0),
                        e.errors.unwrap_or(0),
                        e.warnings.unwrap_or(0),
                        e.notices.unwrap_or(0)
                    )
                } else {
                    let last = e
                        .error
                        .as_deref()
                        .map(|err| err.trim().split('\n').next_back().unwrap_or("").to_string());
                    format!("   -> [{index}/{total}] 失败: {}", last.unwrap_or_default())
                });
                for line in block {
                    out(&line);
                }
            }
            event::COLLECTION => {
                out(&format!(
                    "合集清单 {}: {} {}",
                    e.dir.as_deref().unwrap_or(""),
                    if e.written == Some(true) {
                        "已写入"
                    } else {
                        "沿用 Java 源"
                    },
                    e.path.as_deref().unwrap_or("")
                ));
                if let Some(cover) = e.extra_string("cover").filter(|s| !s.is_empty()) {
                    out(&format!("   文件夹封面 → {cover}.png"));
                }
                if let Some(warning) = e.extra_string("warning").filter(|s| !s.is_empty()) {
                    out(&format!("   [WARN] {warning}"));
                }
            }
            event::VALIDATE_ITEM => out(&format!(
                "体检 [{}] {}",
                e.level.as_deref().unwrap_or("").to_uppercase(),
                e.text.as_deref().unwrap_or("")
            )),
            event::VALIDATE_DONE => out(&format!(
                "体检: 动画文件 {} / 控制器文件 {}; 错误 {} / 警告 {}",
                opt(e.animations),
                opt(e.controllers),
                opt(e.errors),
                opt(e.warnings)
            )),
            _ => {}
        }
    }
}

fn opt(value: Option<i64>) -> String {
    value.map(|v| v.to_string()).unwrap_or_default()
}

fn finish(report: &ConversionReport, a: &ParsedArgs, json_mode: bool) -> Result<u8> {
    if let Some(file) = a.get("report") {
        std::fs::write(file, report.to_json_string(true))?;
    }
    if json_mode {
        out(&report.to_json_string(a.has("details")));
    } else {
        let attention = report.build_attention();
        if !attention.is_empty() {
            out("");
            out(&format!("== 需要开发者过目的 {} 项:", attention.len()));
            for item in &attention {
                out(&format!(
                    "   [{}] ({}) {}: {}{}\n        → {}{}",
                    item.severity,
                    item.pack,
                    item.category,
                    item.text,
                    if item.count > 1 {
                        format!(" x{}", item.count)
                    } else {
                        String::new()
                    },
                    item.advice,
                    item.doc.as_ref().map(|d| format!(" [{d}]")).unwrap_or_default()
                ));
            }
        }
        if let Some(fatal) = &report.fatal_error {
            eprintln!("[FATAL] {fatal}");
        }
        out(&if report.done {
            format!(
                "== {}: 成功 {} / 失败 {} / 体检错误 {}",
                if report.ok {
                    "全部成功"
                } else {
                    "有失败或体检错误"
                },
                report.packs_ok,
                report.packs_failed,
                report.validation_errors.unwrap_or(0)
            )
        } else {
            "== 内核未正常结束".to_string()
        });
    }
    Ok(if report.done && report.ok && report.fatal_error.is_none() {
        0
    } else {
        1
    })
}
