//! MCP 服务器(stdio, JSON-RPC 2.0, 每行一个消息), 不依赖 SDK —— 协议面只有 initialize / ping / tools/list / tools/call。
//! 让 AI(如 Claude Code)能一键转换、体检、修复、查文档、解释告警, 形成"转换 → 看告警 → 改产物 → 再体检"的闭环。
//! 所有诊断只写 stderr, stdout 只有协议消息。

use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};
use ysmconv_core::catalog::{self, Explanation};
use ysmconv_core::docs::DocsLibrary;
use ysmconv_core::locator::KernelLocator;
use ysmconv_core::service::{PerPack, PlanOptions, auto_parallelism};
use ysmconv_core::{
    CancelToken, CollectionSpec, ConversionReport, ConversionService, ConvertOptions, ConvertPlan, ConvertRequest,
    Error, OutputLayout, Result, links, material, output,
};

use crate::args::ParsedArgs;

const SERVER_NAME: &str = "ysm-convert";
/// 认得的协议版本(新的在前); 客户端要的版本不在表里时回最新的一个, 由客户端决定能不能接受。
const KNOWN_PROTOCOL_VERSIONS: &[&str] = &["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];

const TOOL_NAMES: &[&str] = &[
    "ysm_info",
    "ysm_discover",
    "ysm_convert",
    "ysm_validate",
    "ysm_fix",
    "ysm_baseline",
    "ysm_explain",
    "ysm_docs",
    "ysm_last_report",
    "ysm_pack_files",
];

pub fn run(args: &[String]) -> Result<u8> {
    let a = ParsedArgs::parse(args, &[])?;
    let (service, kernel_error) = match KernelLocator::try_locate() {
        Ok(paths) => (Some(ConversionService::new(paths)), None),
        Err(error) => {
            // 仍然起服务器, 让 AI 能通过 ysm_info 看到问题
            eprintln!("{error}");
            (None, Some(error))
        }
    };
    let docs_dir = match &service {
        Some(s) => s.paths().docs_dir.clone(),
        None => exe_dir().join("core").join("docs"),
    };
    let mut server = McpServer {
        service,
        docs: DocsLibrary::new(docs_dir),
        default_out: a.get("out").map(str::to_string),
        kernel_error,
        last_report: None,
    };
    server.serve(std::io::stdin().lock(), std::io::stdout());
    Ok(0)
}

fn exe_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

pub struct McpServer {
    service: Option<ConversionService>,
    docs: DocsLibrary,
    default_out: Option<String>,
    kernel_error: Option<String>,
    last_report: Option<ConversionReport>,
}

impl McpServer {
    pub fn serve(&mut self, input: impl BufRead, mut output: impl Write) {
        eprintln!(
            "[{SERVER_NAME}] MCP 服务器就绪 (stdio){}",
            self.kernel_error
                .as_ref()
                .map(|e| format!("; 内核不可用: {e}"))
                .unwrap_or_default()
        );
        let mut input = input;
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match input.read_until(b'\n', &mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let line = String::from_utf8_lossy(&buf);
            let line = line.trim_end_matches(['\r', '\n']);
            if line.trim().is_empty() {
                continue;
            }
            if let Some(response) = self.handle_line(line) {
                let text = serde_json::to_string(&response).unwrap_or_default();
                if writeln!(output, "{text}").and_then(|_| output.flush()).is_err() {
                    break;
                }
            }
        }
    }

    /// 一行请求 → 回包(通知没有回包)。
    pub fn handle_line(&mut self, line: &str) -> Option<Value> {
        let message: Value = match serde_json::from_str(line) {
            Ok(m) => m,
            Err(error) => return Some(error_response(&Value::Null, -32700, &format!("Parse error: {error}"))),
        };
        let Value::Object(request) = message else {
            return Some(error_response(&Value::Null, -32600, "Invalid Request"));
        };
        let id = request.get("id").cloned().unwrap_or(Value::Null);
        match self.handle(&request) {
            Ok(response) => response,
            Err(error) => {
                eprintln!(
                    "[{SERVER_NAME}] 处理 {} 失败: {error}",
                    request.get("method").unwrap_or(&Value::Null)
                );
                Some(error_response(&id, -32603, &error.to_string()))
            }
        }
    }

    fn handle(&mut self, request: &Map<String, Value>) -> Result<Option<Value>> {
        let method = match request.get("method") {
            None | Some(Value::Null) => "",
            Some(Value::String(s)) => s.as_str(),
            Some(_) => return Err(Error::Argument("method 必须是字符串".into())),
        };
        let id = request.get("id").cloned().unwrap_or(Value::Null);
        let is_notification = id.is_null();
        let params = request.get("params").and_then(Value::as_object);

        Ok(match method {
            "initialize" => {
                let requested = params.and_then(|p| p.get("protocolVersion")).and_then(Value::as_str);
                let version = requested
                    .filter(|r| KNOWN_PROTOCOL_VERSIONS.contains(r))
                    .unwrap_or(KNOWN_PROTOCOL_VERSIONS[0]);
                Some(result_response(
                    &id,
                    json!({
                        "protocolVersion": version,
                        "capabilities": { "tools": { "listChanged": false } },
                        "serverInfo": { "name": SERVER_NAME, "version": ysmconv_core::VERSION },
                        "instructions": self.instructions(),
                    }),
                ))
            }
            "notifications/initialized"
            | "notifications/cancelled"
            | "notifications/progress"
            | "notifications/roots/list_changed" => None,
            "ping" | "logging/setLevel" => Some(result_response(&id, json!({}))),
            "tools/list" => Some(result_response(&id, json!({ "tools": tool_definitions() }))),
            "tools/call" => {
                let name = params.and_then(|p| p.get("name")).and_then(Value::as_str).unwrap_or("");
                let args = params
                    .and_then(|p| p.get("arguments"))
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default();
                if !TOOL_NAMES.contains(&name) {
                    return Ok(Some(error_response(&id, -32602, &format!("Unknown tool: {name}"))));
                }
                let (text, is_error) = match self.call_tool(name, &args) {
                    Ok(text) => (text, false),
                    Err(error) => (error.full_text("\n"), true),
                };
                Some(result_response(
                    &id,
                    json!({
                        "content": [{ "type": "text", "text": text }],
                        "isError": is_error,
                    }),
                ))
            }
            _ => {
                if is_notification {
                    None
                } else {
                    Some(error_response(&id, -32601, &format!("Method not found: {method}")))
                }
            }
        })
    }

    fn instructions(&self) -> String {
        let mut text = String::from(
            "YSM 模型转换器: 把 Java 版 YSM 模型包(未加密目录, 含 ysm.json)转换成网易基岩版组件。\
             典型流程: ysm_discover 找包 → ysm_convert 转换(返回需要开发者过目的告警清单与产物路径) → 按告警读产物文件手工修改 → \
             ysm_validate 复检(0 错误才能进游戏) → 需要时 ysm_fix 对已落盘产物补跑修复规则。告警看不懂用 ysm_explain, 规则细节用 ysm_docs 检索随附文档。\
             产物 JSON 缺省压成一行; 要逐行阅读或手工改产物时, ysm_convert / ysm_fix 传 pretty=true 按缩进落盘。\
             attention 的 severity: error/warn 要修, notice 要看(molang 置零/退化/未知函数), info 知道即可(中性常量 = 基岩没有的 Java 量按常量处理、动画名转小写按包汇总)。\
             天气/血量/roaming 存档等 Java 专有量由 YSM 主组件运行层提供(ysm.json 顶级 java_state 声明), 产物要搭配同期或更新的主组件。",
        );
        text.push_str(&format!(
            " 给人看的同一批文档在线上 Wiki: {}(引导用户去那里, 自己查用 ysm_docs)。",
            links::WIKI_URL
        ));
        if let Some(out) = &self.default_out {
            text.push_str(&format!(" 缺省输出目录: {out}。"));
        }
        if let Some(error) = &self.kernel_error {
            text.push_str(&format!(" 注意: 内核当前不可用({error}), 只有文档类工具能用。"));
        }
        text
    }

    fn require_service(&self) -> Result<&ConversionService> {
        self.service.as_ref().ok_or_else(|| {
            Error::Invalid(format!(
                "内核不可用: {}",
                self.kernel_error.as_deref().unwrap_or("未知原因")
            ))
        })
    }

    fn call_tool(&mut self, name: &str, args: &Map<String, Value>) -> Result<String> {
        match name {
            "ysm_info" => self.info(),
            "ysm_discover" => {
                let dirs = string_list(args, "dirs")?;
                if dirs.is_empty() {
                    return Err(Error::Argument("dirs 不能为空".into()));
                }
                let packs = self.require_service()?.discover(&dirs, &CancelToken::new())?;
                Ok(serde_json::to_string_pretty(&packs).unwrap_or_default())
            }
            "ysm_convert" => self.convert(args),
            "ysm_validate" => {
                let layout = self.resolve_layout(args, false)?;
                let packs = string_list(args, "packs")?;
                let report = self
                    .require_service()?
                    .validate(&layout, &packs, None, None, &CancelToken::new())?;
                let text = report_text(&report, false, None);
                self.last_report = Some(report);
                Ok(text)
            }
            "ysm_fix" => {
                let layout = self.resolve_layout(args, false)?;
                let packs = string_list(args, "packs")?;
                let report = self.require_service()?.fix(
                    &layout,
                    &packs,
                    bool_arg(args, "validate").unwrap_or(true),
                    !bool_arg(args, "pretty").unwrap_or(false),
                    None,
                    None,
                    &CancelToken::new(),
                )?;
                let text = report_text(&report, bool_arg(args, "details").unwrap_or(false), None);
                self.last_report = Some(report);
                Ok(text)
            }
            "ysm_baseline" => {
                let layout = self.resolve_layout(args, false)?;
                let dir =
                    opt_str(args, "javaDefaultDir")?.ok_or_else(|| Error::Argument("缺 javaDefaultDir".into()))?;
                let report = self.require_service()?.baseline(
                    &layout,
                    &dir,
                    bool_arg(args, "withMods").unwrap_or(false),
                    None,
                    None,
                    &CancelToken::new(),
                )?;
                let text = report_text(&report, true, None);
                self.last_report = Some(report);
                Ok(text)
            }
            "ysm_explain" => {
                let text = opt_str(args, "text")?.ok_or_else(|| Error::Argument("缺 text".into()))?;
                let kind = opt_str(args, "kind")?;
                let explanation = explain_any(&text, kind.as_deref());
                let section = explanation
                    .doc
                    .and_then(|doc| self.docs.read_section(doc, explanation.category));
                let mut obj = Map::new();
                obj.insert("category".into(), json!(explanation.category));
                obj.insert("advice".into(), json!(explanation.advice));
                if let Some(doc) = explanation.doc {
                    obj.insert("doc".into(), json!(doc));
                }
                if let Some(section) = section {
                    obj.insert("docSection".into(), json!(section));
                }
                obj.insert("hint".into(), json!("用 ysm_docs 带 query 检索文档里的具体条目"));
                Ok(serde_json::to_string_pretty(&Value::Object(obj)).unwrap_or_default())
            }
            "ysm_docs" => self.docs_tool(args),
            "ysm_last_report" => Ok(match &self.last_report {
                None => "还没有跑过 convert / validate / fix".to_string(),
                Some(report) => report_text(report, bool_arg(args, "details").unwrap_or(false), None),
            }),
            "ysm_pack_files" => {
                let layout = self.resolve_layout(args, false)?;
                let pack = opt_str(args, "pack")?.ok_or_else(|| Error::Argument("缺 pack".into()))?;
                Ok(serde_json::to_string_pretty(&pack_files(&layout, &pack, None)).unwrap_or_default())
            }
            other => Err(Error::Argument(format!("Unknown tool: {other}"))),
        }
    }

    fn info(&self) -> Result<String> {
        // 内核跑不起来时(典型: 目标机器缺 VC++ 2008 运行库)这个工具仍要能回话, 把错误当数据返回
        let mut info = None;
        let mut probe_error = None;
        if let Some(service) = &self.service {
            match service.get_info(&CancelToken::new()) {
                Ok(i) => info = Some(i),
                Err(error) => probe_error = Some(error.full_text("\n")),
            }
        }
        let paths = self.service.as_ref().map(|s| s.paths());
        let source = paths
            .map(|p| p.source_info_file())
            .filter(|f| f.is_file())
            .and_then(|f| std::fs::read_to_string(f).ok())
            .and_then(|t| ysmconv_core::jsonc::parse(&t).ok());
        let docs: Vec<Value> = self
            .docs
            .list()
            .into_iter()
            .map(|d| json!({ "name": d.name, "title": d.title, "bytes": d.size }))
            .collect();
        let path_text = |f: fn(&ysmconv_core::KernelPaths) -> PathBuf| paths.map(|p| f(p).display().to_string());
        let obj = json!({
            "server": format!("{SERVER_NAME} {}", ysmconv_core::VERSION),
            "kernelAvailable": self.service.is_some() && probe_error.is_none(),
            "kernelError": self.kernel_error.clone().or(probe_error),
            "coreDir": path_text(|p| p.core_dir.clone()),
            "python": path_text(|p| p.python_exe.clone()),
            "pythonSource": paths.map(|p| p.python_source.clone()),
            "kernel": info.map(|i| i.to_value()),
            "kernelSource": source,
            "kernelRoot": path_text(|p| p.kernel_root.clone()),
            "kernelFromProject": paths.map(|p| p.from_project),
            "bundledRefRp": path_text(|p| p.bundled_ref_rp()),
            "defaultOut": self.default_out,
            "docs": docs,
            "wiki": links::WIKI_URL,
            "repo": links::REPO_URL,
            "autoParallelism": auto_parallelism(),
        });
        Ok(serde_json::to_string_pretty(&obj).unwrap_or_default())
    }

    fn convert(&mut self, args: &Map<String, Value>) -> Result<String> {
        let service = self.require_service()?;
        let inputs = string_list(args, "inputs")?;
        if inputs.is_empty() {
            return Err(Error::Argument("inputs 不能为空".into()));
        }
        let layout = self.resolve_layout(args, true)?;
        let cancel = CancelToken::new();
        let discovered = service.discover(&inputs, &cancel)?;

        let mut plan = PlanOptions {
            prefix: opt_str(args, "prefix")?,
            collection_override: opt_str(args, "collection")?,
            material: opt_str(args, "material")?,
            cull: material::parse_cull(value_text(args.get("cull")).as_deref())?,
            renames: PerPack::new(),
            materials: PerPack::new(),
            culls: PerPack::new(),
        };
        for (key, value) in string_map(args, "renames")? {
            plan.renames.insert(key, value);
        }
        for (key, value) in string_map(args, "materials")? {
            plan.materials.insert(key, value);
        }
        if let Some(Value::Object(culls)) = args.get("culls") {
            for (key, value) in culls {
                if let Some(text) = value_text(Some(value)) {
                    plan.culls.insert(key.clone(), material::parse_cull(Some(&text))?);
                }
            }
        }
        let packs = ConvertPlan::build_packs(&discovered, &plan);
        let mut collections = Vec::new();
        if let Some(dir) = opt_str(args, "collection")? {
            let spec = CollectionSpec {
                dir,
                name: opt_str(args, "collectionName")?,
                description: None,
                cover_image: opt_str(args, "collectionCover")?,
            };
            if !spec.is_empty() {
                collections.push(spec);
            }
        }
        let pack_names: Vec<String> = packs.iter().map(|p| p.name.clone()).collect();
        let request = ConvertRequest {
            layout,
            packs,
            collections,
            options: ConvertOptions {
                with_mods: bool_arg(args, "withMods").unwrap_or(false),
                java_functions: bool_arg(args, "javaFunctions").unwrap_or(false),
                validate: bool_arg(args, "validate").unwrap_or(true),
                compact_json: !bool_arg(args, "pretty").unwrap_or(false),
                max_parallel: int_arg(args, "jobs").unwrap_or(0).max(0) as usize,
                ..ConvertOptions::default()
            },
            ref_rp: opt_str(args, "refRp")?,
        };
        let report = service.convert(&request, None, None, &cancel)?;
        let skipped: Vec<String> = discovered
            .iter()
            .filter_map(|d| d.error.as_ref().map(|e| format!("{}: {e}", d.java_dir)))
            .collect();
        let mut text = String::new();
        if !skipped.is_empty() {
            text.push_str(&format!("跳过的输入: {}\n", skipped.join("; ")));
        }
        text.push_str(&report_text(
            &report,
            bool_arg(args, "details").unwrap_or(false),
            Some(&pack_names),
        ));
        self.last_report = Some(report);
        Ok(text)
    }

    fn resolve_layout(&self, args: &Map<String, Value>, allow_create: bool) -> Result<OutputLayout> {
        let root = match opt_str(args, "out")? {
            Some(root) => root,
            None => self
                .default_out
                .clone()
                .ok_or_else(|| Error::Argument("缺 out(组件根目录), 服务器启动时也没给 --out".into()))?,
        };
        if let Some(component) = opt_str(args, "component")? {
            if !allow_create {
                return Err(Error::Argument("该工具不新建组件, 去掉 component".into()));
            }
            return output::ensure_component(&root, &component);
        }
        if !Path::new(&root).is_dir() {
            return Err(Error::Argument(format!(
                "输出目录不存在: {root}(新建组件请传 component)"
            )));
        }
        output::detect(&root)
    }

    fn docs_tool(&self, args: &Map<String, Value>) -> Result<String> {
        let name = opt_str(args, "name")?.filter(|s| !s.trim().is_empty());
        let section = opt_str(args, "section")?.filter(|s| !s.trim().is_empty());
        let query = opt_str(args, "query")?.filter(|s| !s.trim().is_empty());
        if let Some(query) = query {
            let hits = self.docs.search(&query, 60);
            return Ok(if hits.is_empty() {
                format!("文档里没有 \"{query}\"")
            } else {
                hits.iter()
                    .map(|h| format!("{}:{}: {}", h.name, h.line, h.text))
                    .collect::<Vec<_>>()
                    .join("\n")
            });
        }
        if let Some(name) = name {
            if let Some(section) = section {
                return Ok(self
                    .docs
                    .read_section(&name, &section)
                    .unwrap_or_else(|| format!("{name} 里没有包含 \"{section}\" 的标题")));
            }
            return Ok(self
                .docs
                .read(&name)
                .unwrap_or_else(|| format!("没有这份文档: {name}(用不带参数的 ysm_docs 看列表)")));
        }
        let list = self.docs.list();
        Ok(if list.is_empty() {
            format!("文档目录为空: {}", self.docs.dir().display())
        } else {
            list.iter()
                .map(|d| format!("{}  —  {} ({} KB)", d.name, d.title, d.size / 1024))
                .collect::<Vec<_>>()
                .join("\n")
        })
    }
}

fn result_response(id: &Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn error_response(id: &Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

fn report_text(report: &ConversionReport, details: bool, pack_names: Option<&[String]>) -> String {
    let mut json = report.to_json(details);
    if let Some(layout) = &report.layout {
        let names: Vec<String> = match pack_names {
            Some(names) => names.to_vec(),
            None => report.packs.iter().map(|p| p.name.clone()).collect(),
        };
        let mut outputs = Map::new();
        for name in names {
            let collection = report.pack_by_name(Some(&name)).and_then(|p| p.collection.clone());
            outputs.insert(name.clone(), pack_files(layout, &name, collection.as_deref()));
        }
        if let Value::Object(obj) = &mut json {
            obj.insert("outputs".into(), Value::Object(outputs));
        }
    }
    format!(
        "{}\n---- JSON ----\n{}",
        report.to_text(details),
        serde_json::to_string_pretty(&json).unwrap_or_default()
    )
}

fn pack_files(layout: &OutputLayout, pack: &str, collection: Option<&str>) -> Value {
    let models = PathBuf::from(layout.bp_models());
    let direct = models.join(pack).join("ysm.json");
    let manifest = if direct.is_file() {
        Some(direct)
    } else if let Some(c) = collection
        .map(|c| models.join(c).join(pack).join("ysm.json"))
        .filter(|p| p.is_file())
    {
        Some(c)
    } else {
        let mut dirs: Vec<PathBuf> = std::fs::read_dir(&models)
            .map(|it| {
                it.filter_map(|e| e.ok())
                    .map(|e| e.path())
                    .filter(|p| p.is_dir())
                    .collect()
            })
            .unwrap_or_default();
        dirs.sort();
        dirs.into_iter()
            .map(|d| d.join(pack).join("ysm.json"))
            .find(|p| p.is_file())
    };
    let rp = PathBuf::from(&layout.rp_dir);
    let entity = rp.join("entity").join(format!("{pack}.entity.json"));
    let mut obj = Map::new();
    obj.insert("pack".into(), json!(pack));
    if let Some(manifest) = manifest {
        obj.insert("manifest".into(), json!(manifest.display().to_string()));
    }
    obj.insert(
        "geometry".into(),
        json!(files_under(&rp.join("models").join("entity").join(pack))),
    );
    obj.insert(
        "animations".into(),
        json!(files_under(&rp.join("animations").join(pack))),
    );
    obj.insert(
        "animationControllers".into(),
        json!(files_under(&rp.join("animation_controllers").join(pack))),
    );
    obj.insert(
        "textures".into(),
        json!(files_under(&rp.join("textures").join("entity").join(pack))),
    );
    obj.insert(
        "sounds".into(),
        json!(files_under(&rp.join("sounds").join("ysm").join(pack))),
    );
    if entity.is_file() {
        obj.insert("previewEntity".into(), json!(entity.display().to_string()));
    }
    Value::Object(obj)
}

fn files_under(dir: &Path) -> Vec<String> {
    fn walk(dir: &Path, out: &mut Vec<String>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for entry in entries.filter_map(|e| e.ok()) {
            let path = entry.path();
            if path.is_dir() {
                walk(&path, out);
            } else {
                out.push(path.display().to_string());
            }
        }
    }
    let mut files = Vec::new();
    walk(dir, &mut files);
    files.sort();
    files
}

pub fn explain_any(text: &str, kind: Option<&str>) -> Explanation {
    if let Some(kind) = kind.filter(|k| catalog::is_molang_kind(Some(k))) {
        return catalog::explain_molang(kind, strip_molang_prefix(text));
    }
    if kind == Some("validation") {
        return catalog::explain_validation("error", text);
    }
    if kind.is_none()
        && let Some((prefix, rest)) = molang_prefix(text)
    {
        return catalog::explain_molang(prefix, rest);
    }
    // 日志/报告里的展示形态: "[!] molang/zero: 标签 xN" / "[i] molang/const: ..."
    if let Some((shown_kind, label)) = shown_molang(text)
        && catalog::is_molang_kind(Some(shown_kind))
    {
        return catalog::explain_molang(shown_kind, &label);
    }
    let trimmed = text.trim_start();
    let level = if trimmed.starts_with("[ERROR]") {
        "error"
    } else if trimmed.starts_with("[WARN]") {
        "warn"
    } else {
        "notice"
    };
    catalog::explain_log(level, text)
}

/// "zero:ctrl.xxx(...)" 这类带类别前缀的原始标签 → (类别, 正文)。
fn molang_prefix(text: &str) -> Option<(&str, &str)> {
    let colon = text.find(':')?;
    if colon > 0 && colon < 6 && catalog::is_molang_kind(Some(&text[..colon])) {
        Some((&text[..colon], &text[colon + 1..]))
    } else {
        None
    }
}

/// 带类别前缀的原始标签 → 正文(不带前缀原样返回)。
fn strip_molang_prefix(text: &str) -> &str {
    molang_prefix(text).map_or(text, |(_, rest)| rest)
}

/// 正则 `molang/([a-z]+):\s*(.*?)(?:\s+x\d+)?\s*$` 的手写版: 找第一处能整段匹配到行尾的 "molang/<类别>:"。
fn shown_molang(text: &str) -> Option<(&str, String)> {
    let mut search_from = 0;
    while let Some(found) = text[search_from..].find("molang/") {
        let start = search_from + found + "molang/".len();
        search_from = start;
        let rest = &text[start..];
        let kind_len = rest.bytes().take_while(u8::is_ascii_lowercase).count();
        if kind_len == 0 || rest.as_bytes().get(kind_len) != Some(&b':') {
            continue;
        }
        let kind = &rest[..kind_len];
        let body = rest[kind_len + 1..].trim_start_matches(char::is_whitespace);
        // `.` 不跨行, `$` 只容许末尾一个换行
        let body = body.strip_suffix('\n').unwrap_or(body);
        if body.contains('\n') {
            continue;
        }
        let body = body.trim_end();
        let label = strip_count_suffix(body);
        return Some((kind, label.to_string()));
    }
    None
}

/// 去掉末尾的 "<空白>x<数字>"(次数标记)。
fn strip_count_suffix(body: &str) -> &str {
    let digits = body.bytes().rev().take_while(u8::is_ascii_digit).count();
    if digits == 0 {
        return body;
    }
    let before_digits = &body[..body.len() - digits];
    let Some(before_x) = before_digits.strip_suffix('x') else {
        return body;
    };
    if before_x.ends_with(char::is_whitespace) {
        before_x.trim_end()
    } else {
        body
    }
}

// ---------------------------------------------------------------- 参数取值(宽松)
fn opt_str(args: &Map<String, Value>, key: &str) -> Result<Option<String>> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s.clone())),
        Some(_) => Err(Error::Argument(format!("{key} 必须是字符串"))),
    }
}

/// 字符串数组(也认单个字符串)。
fn string_list(args: &Map<String, Value>, key: &str) -> Result<Vec<String>> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(Vec::new()),
        Some(Value::String(s)) => Ok(vec![s.clone()]),
        Some(Value::Array(items)) => {
            let mut list = Vec::new();
            for item in items {
                match item {
                    Value::Null => {}
                    Value::String(s) => list.push(s.clone()),
                    _ => return Err(Error::Argument(format!("{key} 里的每一项都要是字符串"))),
                }
            }
            Ok(list)
        }
        Some(_) => Ok(Vec::new()),
    }
}

fn string_map(args: &Map<String, Value>, key: &str) -> Result<Vec<(String, String)>> {
    let Some(Value::Object(map)) = args.get(key) else {
        return Ok(Vec::new());
    };
    let mut pairs = Vec::new();
    for (k, v) in map {
        match v {
            Value::Null => {}
            Value::String(s) => pairs.push((k.clone(), s.clone())),
            _ => return Err(Error::Argument(format!("{key}.{k} 必须是字符串"))),
        }
    }
    Ok(pairs)
}

/// 标量的文本形态(字符串原样, 布尔 / 数字转文本), 缺失或 null 时 None。
fn value_text(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::Null => None,
        Value::String(s) => Some(s.clone()),
        other => Some(other.to_string()),
    }
}

fn bool_arg(args: &Map<String, Value>, key: &str) -> Option<bool> {
    match args.get(key)? {
        Value::Bool(b) => Some(*b),
        Value::String(s) => match s.trim().to_ascii_lowercase().as_str() {
            "true" => Some(true),
            "false" => Some(false),
            _ => None,
        },
        _ => None,
    }
}

fn int_arg(args: &Map<String, Value>, key: &str) -> Option<i64> {
    match args.get(key)? {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

// ---------------------------------------------------------------- 工具清单
fn schema(props: &[(&str, &str, &str, bool)]) -> Value {
    let mut properties = Map::new();
    let mut required = Vec::new();
    for &(name, kind, description, is_required) in props {
        let prop = match kind {
            "string[]" => json!({ "type": "array", "items": { "type": "string" }, "description": description }),
            "object" => {
                json!({ "type": "object", "additionalProperties": { "type": "string" }, "description": description })
            }
            other => json!({ "type": other, "description": description }),
        };
        properties.insert(name.to_string(), prop);
        if is_required {
            required.push(json!(name));
        }
    }
    let mut obj = Map::new();
    obj.insert("type".into(), json!("object"));
    obj.insert("properties".into(), Value::Object(properties));
    if !required.is_empty() {
        obj.insert("required".into(), Value::Array(required));
    }
    Value::Object(obj)
}

fn tool(name: &str, description: &str, input_schema: Value) -> Value {
    json!({ "name": name, "description": description, "inputSchema": input_schema })
}

fn tool_definitions() -> Vec<Value> {
    vec![
        tool(
            "ysm_info",
            "转换器与内核信息: core 目录、Python、内核版本与来源提交、随附文档列表、缺省输出目录。",
            schema(&[]),
        ),
        tool(
            "ysm_discover",
            "在目录里发现 Java 版 YSM 模型包(单包目录 / 带 ysm-pack.json 的合集目录 / 装着若干包的上级目录, 深 2 层)。返回每个包的目录、建议包名、所属合集、metadata。",
            schema(&[("dirs", "string[]", "要扫描的目录列表", true)]),
        ),
        tool(
            "ysm_convert",
            "一键把 Java 模型包转换成网易基岩版组件并跑体检。inputs 可以是包目录或合集目录; out 是组件根目录: 已有工程(含行为包+资源包)直接用, 传 component 则新建 <out>/<component>_bp 与 _rp。返回文本报告 + JSON(attention = 需要开发者过目的项, outputs = 每个包的产物路径)。",
            schema(&[
                ("inputs", "string[]", "Java 包目录或合集目录", true),
                ("out", "string", "组件根目录(缺省用服务器启动时的 --out)", false),
                ("component", "string", "新建/复用独立组件名(英文数字下划线)", false),
                ("collection", "string", "把全部包归入这个合集文件夹(目录名)", false),
                (
                    "collectionName",
                    "string",
                    "文件夹在游戏里显示的名字(缺省沿用 Java 合集自带的名字)",
                    false,
                ),
                (
                    "collectionCover",
                    "string",
                    "文件夹封面图 PNG 的路径(不超过 1MB, 最好是 Java 卡片 52x90 的比例; 缺省用合集自带的 ysm-pack.png)",
                    false,
                ),
                (
                    "prefix",
                    "string",
                    "包名统一前缀(缺省用内核建议名: 拼音化 + 合集前缀)",
                    false,
                ),
                (
                    "renames",
                    "object",
                    "逐包指定包名: {\"<Java 文件夹名>\": \"<包名>\"}",
                    false,
                ),
                ("withMods", "boolean", "携带第三方模组联动动画, 缺省 false", false),
                (
                    "javaFunctions",
                    "boolean",
                    "转换 Java 自定义函数(实验性, 缺省 false: fn.* 与音效调用按 0 处理)",
                    false,
                ),
                (
                    "material",
                    "string",
                    "全部包的模型材质, 写进产物 ysm.json 顶级 material(缺省自动: YSM 主组件按 Java 包判定 —— properties.all_cutout 打开则剔除背面, 皮肤带 PBR 贴图用 pbr, 其余 cutout)。预设名 cutout / translucent / emissive / pbr / bloom 及剔除背面版 *_cull、自定义材质名(要在资源包 materials 里有定义, 不参与描边泛光), 或 \"default=<材质>,ysm_glow=<材质>\" 逐个材质键指定",
                    false,
                ),
                (
                    "materials",
                    "object",
                    "逐包材质: {\"<Java 文件夹名>\": \"<材质>\"}, 覆盖 material",
                    false,
                ),
                (
                    "cull",
                    "string",
                    "背面剔除: auto(缺省, 照 Java properties.all_cutout) / on(剔除, 负尺寸描边要它) / off",
                    false,
                ),
                (
                    "culls",
                    "object",
                    "逐包背面剔除: {\"<Java 文件夹名>\": \"auto|on|off\"}, 覆盖 cull",
                    false,
                ),
                ("validate", "boolean", "转换后跑资源包体检, 缺省 true", false),
                (
                    "pretty",
                    "boolean",
                    "产物 JSON 按 2 空格缩进(缺省 false = 压成一行, 体积约省 70%; 要逐行读产物、手工改 bug 时设 true)",
                    false,
                ),
                (
                    "jobs",
                    "integer",
                    "同时转换的包数(缺省自动; 多个包总是每包一个内核进程)",
                    false,
                ),
                ("refRp", "string", "java_default 基线所在资源包(缺省自动)", false),
                ("details", "boolean", "报告里带全部内核日志行, 缺省 false", false),
            ]),
        ),
        tool(
            "ysm_validate",
            "对已落盘的产物跑引擎红线体检(molang 语法/资源 ID/空节点/音频登记...)。错误项意味着整份文件会被引擎拒载, 必须修到 0。",
            schema(&[
                ("out", "string", "组件根目录", false),
                ("packs", "string[]", "只体检这些包名(缺省全部)", false),
            ]),
        ),
        tool(
            "ysm_fix",
            "对已落盘的产物就地补跑修复规则(幂等): 控制器死引用剪枝、ysm.json 精简、通道表达式规范化、预览实体变量回填等。Java 源不在手边时用它; 有源的话重新 ysm_convert 更完整。",
            schema(&[
                ("out", "string", "组件根目录", false),
                ("packs", "string[]", "只修这些包名(缺省全部)", false),
                ("validate", "boolean", "修完体检, 缺省 true", false),
                (
                    "pretty",
                    "boolean",
                    "写回的 JSON 按 2 空格缩进(缺省 false = 压成一行)",
                    false,
                ),
            ]),
        ),
        tool(
            "ysm_baseline",
            "把 Java 版内置 default 模型移植成 java_default 基线动画(只产出资源包动画, 不出现在模型列表)。Java 包缺的拉弓/举盾/游泳等动画靠它回落; 只有 YSM 主组件工程需要跑一次。",
            schema(&[
                (
                    "javaDefaultDir",
                    "string",
                    "Java 版 assets/ysm/builtin/default 目录",
                    true,
                ),
                ("out", "string", "YSM 主组件工程根目录", false),
                ("withMods", "boolean", "携带模组联动动画", false),
            ]),
        ),
        tool(
            "ysm_explain",
            "解释一条内核告警/体检文本或 molang 留痕标签: 它意味着什么、要不要处理、该查哪份文档。",
            schema(&[
                ("text", "string", "告警文本或 molang 标签", true),
                (
                    "kind",
                    "string",
                    "molang 类别(map/const/zero/func/warn/lower/norm/skip/tick)或 log/validation, 缺省自动",
                    false,
                ),
            ]),
        ),
        tool(
            "ysm_docs",
            "随附的 YSM 移植文档(格式速查/移植教程/molang 映射清单/动画机制对照/自定义函数/枪械联动协议)。不带参数列出文档; name 读整篇; name+section 读某个标题下的章节; query 全文检索。",
            schema(&[
                ("name", "string", "文档文件名(如 ysm-java-molang-mapping.md)", false),
                ("section", "string", "标题包含的文字", false),
                ("query", "string", "关键字检索", false),
            ]),
        ),
        tool(
            "ysm_last_report",
            "上一次 convert/validate/fix 的完整报告(文本 + JSON)。",
            schema(&[("details", "boolean", "带全部日志行", false)]),
        ),
        tool(
            "ysm_pack_files",
            "列出某个包在组件里的全部产物文件(声明 ysm.json、几何、动画、控制器、预览实体、贴图), 供逐个打开修改。",
            schema(&[("out", "string", "组件根目录", false), ("pack", "string", "包名", true)]),
        ),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    fn server() -> McpServer {
        McpServer {
            service: None,
            docs: DocsLibrary::new(std::env::temp_dir().join("ysmconv-no-docs")),
            default_out: None,
            kernel_error: Some("测试: 没有内核".into()),
            last_report: None,
        }
    }

    #[test]
    fn protocol_basics() {
        let mut s = server();
        let init = s
            .handle_line(r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}"#)
            .unwrap();
        assert_eq!(init["id"], 1);
        assert_eq!(init["result"]["protocolVersion"], "2025-06-18");
        assert_eq!(init["result"]["serverInfo"]["name"], "ysm-convert");
        assert!(
            init["result"]["instructions"]
                .as_str()
                .unwrap()
                .contains("内核当前不可用")
        );
        let unknown_version = s
            .handle_line(r#"{"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"1999-01-01"}}"#)
            .unwrap();
        assert_eq!(unknown_version["result"]["protocolVersion"], KNOWN_PROTOCOL_VERSIONS[0]);
        assert!(
            s.handle_line(r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#)
                .is_none()
        );
        assert_eq!(
            s.handle_line(r#"{"jsonrpc":"2.0","id":"p","method":"ping"}"#).unwrap()["result"],
            json!({})
        );
        let tools = s
            .handle_line(r#"{"jsonrpc":"2.0","id":3,"method":"tools/list"}"#)
            .unwrap();
        let names: Vec<&str> = tools["result"]["tools"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["name"].as_str().unwrap())
            .collect();
        assert_eq!(names, TOOL_NAMES);
        assert_eq!(s.handle_line("{bad").unwrap()["error"]["code"], -32700);
        assert_eq!(s.handle_line("[1]").unwrap()["error"]["code"], -32600);
        assert_eq!(
            s.handle_line(r#"{"jsonrpc":"2.0","id":4,"method":"nope"}"#).unwrap()["error"]["code"],
            -32601
        );
        assert!(s.handle_line(r#"{"jsonrpc":"2.0","method":"nope"}"#).is_none());
        let bad_tool = s
            .handle_line(r#"{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"x"}}"#)
            .unwrap();
        assert_eq!(bad_tool["error"]["code"], -32602);
    }

    #[test]
    fn tools_report_errors_as_content() {
        let mut s = server();
        let r = s
            .handle_line(r#"{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ysm_discover","arguments":{"dirs":["C:\\x"]}}}"#)
            .unwrap();
        assert_eq!(r["result"]["isError"], true);
        assert!(
            r["result"]["content"][0]["text"]
                .as_str()
                .unwrap()
                .contains("内核不可用")
        );
        let empty = s
            .handle_line(
                r#"{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ysm_discover","arguments":{}}}"#,
            )
            .unwrap();
        assert!(
            empty["result"]["content"][0]["text"]
                .as_str()
                .unwrap()
                .contains("dirs 不能为空")
        );
        let info = s
            .handle_line(r#"{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ysm_info"}}"#)
            .unwrap();
        assert_eq!(info["result"]["isError"], false);
        let body: Value = serde_json::from_str(info["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(body["kernelAvailable"], false);
        assert_eq!(body["kernelError"], "测试: 没有内核");
        let last = s
            .handle_line(r#"{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"ysm_last_report"}}"#)
            .unwrap();
        assert_eq!(
            last["result"]["content"][0]["text"],
            "还没有跑过 convert / validate / fix"
        );
    }

    #[test]
    fn explain_any_understands_all_shapes() {
        assert_eq!(explain_any("zero:ctrl.ride(x)", None).category, "molang 置零");
        assert_eq!(explain_any("ctrl.ride(x)", Some("zero")).category, "molang 置零");
        assert_eq!(explain_any("zero:ctrl.ride(x)", Some("zero")).category, "molang 置零");
        assert_eq!(
            explain_any("   [!] molang/zero: fn.move(自定义函数脚本, 基岩无对应) x4", None).category,
            "自定义函数置零"
        );
        assert_eq!(
            explain_any("[i] molang/const: ctrl.tac_gun_id -> '' x6", None).category,
            "TACZ 枪 ID 未接入"
        );
        assert_eq!(
            explain_any("[WARN] 动画缺失: animations/x.json", None).category,
            "Java 包声明的文件不存在"
        );
        assert_eq!(
            explain_any("x: 缺 entities 文件夹", Some("validation")).category,
            "行为包不会被挂载"
        );
        assert_eq!(shown_molang("molang/zero: abc x12  ").unwrap().1, "abc");
        assert_eq!(shown_molang("molang/zero: x3").unwrap().1, "x3");
        assert_eq!(shown_molang("molang/zero: a x3 x4").unwrap().1, "a x3");
        assert_eq!(shown_molang("molang/Zero: a"), None);
        assert_eq!(shown_molang("molang/zero: a\nb"), None);
        assert_eq!(shown_molang("molang/zero: a\n").unwrap().1, "a");
    }

    #[test]
    fn argument_helpers_are_lenient_but_typed() {
        let args = json!({"a": "x", "b": ["y", null, "z"], "c": 3, "d": "true", "e": 2.9, "f": " 7 "})
            .as_object()
            .unwrap()
            .clone();
        assert_eq!(opt_str(&args, "a").unwrap().as_deref(), Some("x"));
        assert!(opt_str(&args, "c").is_err());
        assert_eq!(string_list(&args, "b").unwrap(), vec!["y", "z"]);
        assert_eq!(string_list(&args, "a").unwrap(), vec!["x"]);
        assert_eq!(bool_arg(&args, "d"), Some(true));
        assert_eq!(int_arg(&args, "e"), Some(2));
        assert_eq!(int_arg(&args, "f"), Some(7));
        assert_eq!(value_text(args.get("c")).as_deref(), Some("3"));
    }

    #[test]
    fn pack_files_lists_outputs() {
        let root = std::env::temp_dir().join(format!("ysmconv-mcp-{}", ysmconv_core::win::random_hex()));
        let layout = output::ensure_component(&root.to_string_lossy(), "c").unwrap();
        let rp = PathBuf::from(&layout.rp_dir);
        std::fs::create_dir_all(rp.join("animations/p/sub")).unwrap();
        std::fs::write(rp.join("animations/p/sub/b.json"), "{}").unwrap();
        std::fs::write(rp.join("animations/p/a.json"), "{}").unwrap();
        std::fs::create_dir_all(PathBuf::from(layout.bp_models()).join("coll/p")).unwrap();
        std::fs::write(PathBuf::from(layout.bp_models()).join("coll/p/ysm.json"), "{}").unwrap();
        let files = pack_files(&layout, "p", None);
        assert!(files["manifest"].as_str().unwrap().ends_with("ysm.json"));
        assert_eq!(files["animations"].as_array().unwrap().len(), 2);
        assert!(files.get("previewEntity").is_none());
        let _ = std::fs::remove_dir_all(&root);
    }
}
