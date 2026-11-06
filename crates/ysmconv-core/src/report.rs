//! 一次内核运行(convert / validate / fix / baseline)的全部结果, 由事件流逐条喂出来。

use serde_json::{Map, Value, json};

use crate::catalog;
use crate::event::{self, KernelEvent};
use crate::models::OutputLayout;
use crate::molang;
use crate::time::LocalTime;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LogLine {
    pub pack: String,
    pub level: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MolangItem {
    pub pack: String,
    pub kind: String,
    pub label: String,
    pub count: i64,
    pub attention: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidationItem {
    pub level: String,
    pub text: String,
}

/// 一个合集清单的落盘结果; `cover` = 这次装上的自定义封面纹理路径, `warning` = 封面没装上的原因。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CollectionWritten {
    pub dir: String,
    pub path: String,
    pub written: bool,
    pub cover: Option<String>,
    pub warning: Option<String>,
}

/// 需要开发者过目的一条(告警/置零/体检错误), 带解释。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AttentionItem {
    pub pack: String,
    /// error / warn / notice(要看) / info(知道一下: 中性常量、动画名转小写的按包汇总)。
    pub severity: String,
    pub category: String,
    pub text: String,
    pub count: i64,
    pub advice: String,
    pub doc: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct PackResult {
    pub name: String,
    pub java_dir: Option<String>,
    pub collection: Option<String>,
    pub index: i64,
    pub total: i64,
    pub ok: Option<bool>,
    pub seconds: f64,
    pub error: Option<String>,
    pub errors: i64,
    pub warnings: i64,
    pub notices: i64,
    pub lines: Vec<LogLine>,
    pub molang: Vec<MolangItem>,
}

impl PackResult {
    fn new(name: String) -> Self {
        PackResult {
            name,
            ..Default::default()
        }
    }

    pub fn finished(&self) -> bool {
        self.ok.is_some()
    }

    pub fn attention_count(&self) -> i64 {
        self.errors
            + self.warnings
            + self.notices
            + molang::rows(&self.molang).len() as i64
            + if self.ok == Some(false) { 1 } else { 0 }
    }
}

#[derive(Debug, Clone)]
pub struct ConversionReport {
    pub action: String,
    pub started_at: LocalTime,
    pub layout: Option<OutputLayout>,
    pub ref_rp: Option<String>,
    pub baseline_present: Option<bool>,
    pub kernel_version: Option<String>,
    pub python_version: Option<String>,
    pub packs: Vec<PackResult>,
    pub collections: Vec<CollectionWritten>,
    pub validation: Vec<ValidationItem>,
    pub validation_errors: Option<i64>,
    pub validation_warnings: Option<i64>,
    pub animation_files: Option<i64>,
    pub controller_files: Option<i64>,
    pub done: bool,
    pub ok: bool,
    pub packs_ok: i64,
    pub packs_failed: i64,
    pub exit_code: i32,
    pub fatal_error: Option<String>,
    pub stderr: Vec<String>,
}

impl Default for ConversionReport {
    fn default() -> Self {
        Self::new("convert", None)
    }
}

impl ConversionReport {
    pub fn new(action: &str, layout: Option<OutputLayout>) -> Self {
        ConversionReport {
            action: action.to_string(),
            started_at: LocalTime::now(),
            layout,
            ref_rp: None,
            baseline_present: None,
            kernel_version: None,
            python_version: None,
            packs: Vec::new(),
            collections: Vec::new(),
            validation: Vec::new(),
            validation_errors: None,
            validation_warnings: None,
            animation_files: None,
            controller_files: None,
            done: false,
            ok: false,
            packs_ok: 0,
            packs_failed: 0,
            exit_code: 0,
            fatal_error: None,
            stderr: Vec::new(),
        }
    }

    pub fn pack_by_name(&self, name: Option<&str>) -> Option<&PackResult> {
        let name = name?;
        self.packs.iter().find(|p| p.name == name)
    }

    fn ensure_pack(&mut self, name: Option<&str>) -> &mut PackResult {
        let name = name.unwrap_or("?");
        if let Some(index) = self.packs.iter().position(|p| p.name == name) {
            return &mut self.packs[index];
        }
        self.packs.push(PackResult::new(name.to_string()));
        self.packs.last_mut().expect("刚放进去")
    }

    /// 喂一个事件。
    pub fn apply(&mut self, e: &KernelEvent) {
        match e.event.as_str() {
            event::START => {
                if let Some(action) = &e.action {
                    self.action = action.clone();
                }
                if let Some(layout) = e.extra.get("layout").and_then(Value::as_object) {
                    if let Some(ref_rp) = layout.get("refRp") {
                        self.ref_rp = ref_rp.as_str().map(str::to_string);
                    }
                    if let Some(present) = layout.get("baselinePresent").and_then(Value::as_bool) {
                        self.baseline_present = Some(present);
                    }
                }
                if let Some(kernel) = e.extra.get("kernel").and_then(Value::as_object) {
                    if let Some(v) = kernel.get("version") {
                        self.kernel_version = Some(value_text(v));
                    }
                    if let Some(py) = kernel.get("python") {
                        self.python_version = Some(value_text(py));
                    }
                }
            }
            event::PACK_START => {
                let pack = self.ensure_pack(e.pack.as_deref());
                pack.java_dir = e.java_dir.clone();
                pack.collection = e.collection.clone();
                pack.index = e.index.unwrap_or(0);
                pack.total = e.total.unwrap_or(0);
            }
            event::LOG => {
                let pack = self.ensure_pack(e.pack.as_deref());
                let line = LogLine {
                    pack: pack.name.clone(),
                    level: e.level.clone().unwrap_or_else(|| "info".into()),
                    text: e.text.clone().unwrap_or_default(),
                };
                pack.lines.push(line);
            }
            event::MOLANG => {
                let pack = self.ensure_pack(e.pack.as_deref());
                let item = MolangItem {
                    pack: pack.name.clone(),
                    kind: e.kind.clone().unwrap_or_else(|| "other".into()),
                    label: e.label.clone().unwrap_or_default(),
                    count: e.count.unwrap_or(0),
                    attention: e.attention.unwrap_or(false),
                };
                pack.molang.push(item);
            }
            event::PACK_DONE => {
                let pack = self.ensure_pack(e.pack.as_deref());
                pack.ok = Some(e.ok.unwrap_or(false));
                pack.seconds = e.seconds.unwrap_or(0.0);
                pack.error = e.error.clone();
                pack.errors = e.errors.unwrap_or(0);
                pack.warnings = e.warnings.unwrap_or(0);
                pack.notices = e.notices.unwrap_or(0);
            }
            event::COLLECTION => self.collections.push(CollectionWritten {
                dir: e.dir.clone().unwrap_or_default(),
                path: e.path.clone().unwrap_or_default(),
                written: e.written.unwrap_or(false),
                cover: e.extra_string("cover").filter(|s| !s.is_empty()),
                warning: e.extra_string("warning").filter(|s| !s.is_empty()),
            }),
            event::VALIDATE_ITEM => self.validation.push(ValidationItem {
                level: e.level.clone().unwrap_or_else(|| "warn".into()),
                text: e.text.clone().unwrap_or_default(),
            }),
            event::VALIDATE_DONE => {
                self.validation_errors = e.errors;
                self.validation_warnings = e.warnings;
                self.animation_files = e.animations;
                self.controller_files = e.controllers;
            }
            event::DONE => {
                self.done = true;
                self.ok = e.ok.unwrap_or(false);
                self.packs_ok = e.packs_ok.unwrap_or(0);
                self.packs_failed = e.packs_failed.unwrap_or(0);
                if self.validation_errors.is_none() {
                    self.validation_errors = e.validation_errors;
                }
            }
            _ => {}
        }
    }

    /// 需要人工过目的全部项目(失败/错误/警告/提醒/置零与中性常量 molang/体检错误与警告), 带解释。
    pub fn build_attention(&self) -> Vec<AttentionItem> {
        let mut items = Vec::new();
        let item = |pack: &str, severity: &str, ex: catalog::Explanation, text: String, count: i64| AttentionItem {
            pack: pack.to_string(),
            severity: severity.to_string(),
            category: ex.category.to_string(),
            text,
            count,
            advice: ex.advice.to_string(),
            doc: ex.doc.map(str::to_string),
        };
        for pack in &self.packs {
            if pack.ok == Some(false) {
                items.push(AttentionItem {
                    pack: pack.name.clone(),
                    severity: "error".into(),
                    category: "移植失败".into(),
                    text: last_line(pack.error.as_deref()).unwrap_or_else(|| "内核异常".into()),
                    count: 1,
                    advice: "该包没有完整落盘; 展开日志看异常堆栈, 通常是 Java 包文件损坏或声明指向不存在的文件。"
                        .into(),
                    doc: Some(catalog::PORT_TUTORIAL_DOC.into()),
                });
            }
            for line in &pack.lines {
                if !matches!(line.level.as_str(), "error" | "warn" | "notice") {
                    continue;
                }
                let ex = catalog::explain_log(&line.level, &line.text);
                items.push(item(&pack.name, &line.level, ex, line.text.trim().to_string(), 1));
            }
            for row in molang::rows(&pack.molang) {
                let ex = catalog::explain_molang(&row.kind, &row.text);
                items.push(item(&pack.name, row.severity, ex, row.text.clone(), row.count));
            }
        }
        for v in &self.validation {
            let ex = catalog::explain_validation(&v.level, &v.text);
            items.push(item(&self.pack_of_validation(&v.text), &v.level, ex, v.text.clone(), 1));
        }
        for c in self.collections.iter().filter(|c| c.warning.is_some()) {
            items.push(AttentionItem {
                pack: format!("合集 {}", c.dir),
                severity: "warn".into(),
                category: "文件夹封面未装上".into(),
                text: c.warning.clone().unwrap_or_default(),
                count: 1,
                advice: "封面要求 PNG、不超过 1MB; 文件夹卡片按 Java 52x90 的比例等比铺满。没装上时游戏里用默认封面, 模型本身不受影响。".into(),
                doc: Some(catalog::PACK_GUIDE_DOC.into()),
            });
        }
        if let Some(fatal) = &self.fatal_error {
            items.push(AttentionItem {
                pack: String::new(),
                severity: "error".into(),
                category: "内核未能运行".into(),
                text: fatal.clone(),
                count: 1,
                advice: "检查 core/ 目录与 Python 运行时是否完整。".into(),
                doc: None,
            });
        }
        items
    }

    fn pack_of_validation(&self, text: &str) -> String {
        if let Some(pack) = self.packs.iter().find(|p| text.contains(&p.name)) {
            return pack.name.clone();
        }
        match text.find(':') {
            Some(colon) if colon > 0 && text[..colon].chars().count() < 80 => text[..colon].trim().to_string(),
            _ => String::new(),
        }
    }

    pub fn to_text(&self, details: bool) -> String {
        let mut sb = String::new();
        let mut line = |s: &str| {
            sb.push_str(s);
            sb.push('\n');
        };
        line(&format!(
            "== YSM 转换报告 ({}) {}",
            self.action,
            self.started_at.format_report()
        ));
        if let Some(layout) = &self.layout {
            line(&format!("   行为包: {}", layout.bp_dir));
            line(&format!("   资源包: {}", layout.rp_dir));
        }
        if let Some(ref_rp) = &self.ref_rp {
            line(&format!(
                "   基线资源包: {ref_rp}{}",
                if self.baseline_present == Some(false) {
                    " (缺 java_default 基线! 拉弓/举盾等回落动画将缺失)"
                } else {
                    ""
                }
            ));
        }
        if let Some(version) = &self.kernel_version {
            line(&format!(
                "   内核 {version} / Python {}",
                self.python_version.as_deref().unwrap_or("")
            ));
        }
        for pack in &self.packs {
            let status = match pack.ok {
                Some(true) => "OK",
                Some(false) => "FAILED",
                None => "未完成",
            };
            line("");
            line(&format!(
                "== {} [{status}] {:.1}s  错误 {} / 警告 {} / 提醒 {}{}",
                pack.name,
                pack.seconds,
                pack.errors,
                pack.warnings,
                pack.notices,
                pack.collection
                    .as_ref()
                    .map(|c| format!("  合集 {c}"))
                    .unwrap_or_default()
            ));
            if let Some(java_dir) = &pack.java_dir {
                line(&format!("   来源: {java_dir}"));
            }
            for l in &pack.lines {
                if details {
                    line(&format!("   {}", l.text));
                } else if matches!(l.level.as_str(), "error" | "warn" | "notice") {
                    line(&format!("   {}", l.text.trim()));
                }
            }
            for row in molang::rows(&pack.molang) {
                line(&format!(
                    "   {} molang/{}: {}{}",
                    molang::marker(Some(&row.kind)),
                    row.kind,
                    row.text,
                    if row.count > 1 {
                        format!(" x{}", row.count)
                    } else {
                        String::new()
                    }
                ));
            }
            if let Some(error) = &pack.error {
                line("   ---- 异常 ----");
                line(error.trim_end());
            }
        }
        for c in &self.collections {
            line(&format!(
                "合集清单 {}: {} {}",
                c.dir,
                if c.written { "已写入" } else { "沿用 Java 源" },
                c.path
            ));
            if let Some(cover) = &c.cover {
                line(&format!("   文件夹封面 → {cover}.png"));
            }
            if let Some(warning) = &c.warning {
                line(&format!("   [WARN] {warning}"));
            }
        }
        if self.validation_errors.is_some() || !self.validation.is_empty() {
            line("");
            let warnings = self
                .validation_warnings
                .unwrap_or_else(|| self.validation.iter().filter(|v| v.level == "warn").count() as i64);
            line(&format!(
                "== 体检: 动画文件 {} / 控制器文件 {}; 错误 {} / 警告 {warnings}",
                self.animation_files.unwrap_or(0),
                self.controller_files.unwrap_or(0),
                self.validation_errors.unwrap_or(0)
            ));
            for v in &self.validation {
                line(&format!("   [{}] {}", v.level.to_uppercase(), v.text));
            }
        }
        if let Some(fatal) = &self.fatal_error {
            line(&format!("[FATAL] {fatal}"));
        }
        line("");
        line(&if self.done {
            format!(
                "== 结果: {} 成功 {} / 失败 {} / 体检错误 {}",
                if self.ok {
                    "全部成功"
                } else {
                    "有失败或体检错误"
                },
                self.packs_ok,
                self.packs_failed,
                self.validation_errors.unwrap_or(0)
            )
        } else {
            format!("== 结果: 未正常结束(退出码 {})", self.exit_code)
        });
        sb
    }

    pub fn to_json(&self, include_lines: bool) -> Value {
        let packs: Vec<Value> = self
            .packs
            .iter()
            .map(|p| {
                let mut obj = Map::new();
                obj.insert("name".into(), json!(p.name));
                obj.insert("javaDir".into(), json!(p.java_dir));
                obj.insert("collection".into(), json!(p.collection));
                obj.insert("ok".into(), json!(p.ok));
                obj.insert("seconds".into(), json!(p.seconds));
                obj.insert("errors".into(), json!(p.errors));
                obj.insert("warnings".into(), json!(p.warnings));
                obj.insert("notices".into(), json!(p.notices));
                obj.insert("error".into(), json!(p.error));
                let molang: Vec<Value> = p
                    .molang
                    .iter()
                    .filter(|m| m.attention)
                    .map(|m| {
                        json!({
                            "kind": m.kind,
                            "label": m.label,
                            "count": m.count,
                            "severity": molang::severity(Some(&m.kind)),
                        })
                    })
                    .collect();
                obj.insert("molangAttention".into(), Value::Array(molang));
                if include_lines {
                    let lines: Vec<Value> = p
                        .lines
                        .iter()
                        .map(|l| json!({ "level": l.level, "text": l.text }))
                        .collect();
                    obj.insert("lines".into(), Value::Array(lines));
                }
                Value::Object(obj)
            })
            .collect();
        let attention: Vec<Value> = self
            .build_attention()
            .into_iter()
            .map(|a| {
                json!({
                    "pack": a.pack,
                    "severity": a.severity,
                    "category": a.category,
                    "text": a.text,
                    "count": a.count,
                    "advice": a.advice,
                    "doc": a.doc,
                })
            })
            .collect();
        json!({
            "action": self.action,
            "startedAt": self.started_at.format_iso(),
            "layout": self.layout.as_ref().map(|l| json!({ "root": l.root, "bp": l.bp_dir, "rp": l.rp_dir })),
            "refRp": self.ref_rp,
            "baselinePresent": self.baseline_present,
            "done": self.done,
            "ok": self.ok,
            "packsOk": self.packs_ok,
            "packsFailed": self.packs_failed,
            "exitCode": self.exit_code,
            "fatalError": self.fatal_error,
            "validation": {
                "errors": self.validation_errors,
                "warnings": self.validation_warnings,
                "animationFiles": self.animation_files,
                "controllerFiles": self.controller_files,
                "items": self.validation.iter().map(|v| json!({ "level": v.level, "text": v.text })).collect::<Vec<_>>(),
            },
            "collections": self.collections.iter().map(|c| json!({
                "dir": c.dir, "path": c.path, "written": c.written, "cover": c.cover, "warning": c.warning,
            })).collect::<Vec<_>>(),
            "packs": packs,
            "attention": attention,
            "stderr": self.stderr,
        })
    }

    pub fn to_json_string(&self, include_lines: bool) -> String {
        serde_json::to_string_pretty(&self.to_json(include_lines)).unwrap_or_default()
    }
}

fn value_text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// 异常文本的最后一个非空行(Python traceback 的结论行)。
fn last_line(text: Option<&str>) -> Option<String> {
    let text = text?;
    if text.trim().is_empty() {
        return None;
    }
    text.replace('\r', "")
        .split('\n')
        .rfind(|l| !l.trim().is_empty())
        .map(|l| l.trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn replay(lines: &[&str]) -> ConversionReport {
        let mut report = ConversionReport::default();
        for line in lines {
            report.apply(&KernelEvent::try_parse(line).expect(line));
        }
        report
    }

    #[test]
    fn aggregates_pack_lifecycle() {
        let report = replay(&[
            r#"{"event": "start", "action": "convert", "packs": 1, "layout": {"refRp": "R", "baselinePresent": true}, "kernel": {"version": "1.0", "python": "2.7.18"}}"#,
            r#"{"event": "pack_start", "pack": "a", "javaDir": "J", "collection": "c", "index": 0, "total": 1}"#,
            r#"{"event": "log", "pack": "a", "level": "info", "text": "geometry ok"}"#,
            r#"{"event": "log", "pack": "a", "level": "notice", "text": "[!] script skipped"}"#,
            r#"{"event": "molang", "pack": "a", "kind": "zero", "label": "ctrl.ride(x)", "count": 3, "attention": true}"#,
            r#"{"event": "molang", "pack": "a", "kind": "map", "label": "math.e", "count": 1, "attention": false}"#,
            r#"{"event": "pack_done", "pack": "a", "ok": true, "seconds": 1.5, "errors": 0, "warnings": 0, "notices": 1}"#,
            r#"{"event": "collection", "dir": "c", "path": "P", "written": true}"#,
            r#"{"event": "validate_item", "level": "error", "text": "bad molang"}"#,
            r#"{"event": "validate_done", "errors": 1, "warnings": 0, "animations": 3, "controllers": 2}"#,
            r#"{"event": "done", "ok": false, "packsOk": 1, "packsFailed": 0, "validationErrors": 1}"#,
        ]);
        assert!(report.done);
        assert!(!report.ok);
        assert_eq!(report.ref_rp.as_deref(), Some("R"));
        assert_eq!(report.baseline_present, Some(true));
        assert_eq!(report.kernel_version.as_deref(), Some("1.0"));
        assert_eq!(report.packs.len(), 1);
        let pack = &report.packs[0];
        assert_eq!(pack.name, "a");
        assert_eq!(pack.collection.as_deref(), Some("c"));
        assert_eq!(pack.ok, Some(true));
        assert_eq!(pack.lines.len(), 2);
        assert_eq!(pack.molang.len(), 2);
        assert_eq!(report.validation_errors, Some(1));
        assert_eq!(report.collections.len(), 1);

        let attention = report.build_attention();
        // notice 行 + 置零 molang + 体检错误 = 3 项; map 类不算
        assert_eq!(attention.len(), 3);
        assert!(attention.iter().any(|a| a.category == "molang 置零" && a.count == 3));
        assert!(
            attention
                .iter()
                .any(|a| a.severity == "error" && a.text == "bad molang")
        );

        let json = report.to_json(true);
        assert_eq!(json["attention"].as_array().unwrap().len(), 3);
        let text = report.to_text(false);
        assert!(text.contains("== a [OK]"));
        assert!(text.contains("体检"));
    }

    #[test]
    fn failed_pack_becomes_attention() {
        let report = replay(&[
            r#"{"event": "pack_start", "pack": "b", "javaDir": "J", "collection": null, "index": 0, "total": 1}"#,
            r#"{"event": "pack_done", "pack": "b", "ok": false, "seconds": 0.1, "error": "Traceback...\nKeyError: 'x'", "errors": 1, "warnings": 0, "notices": 0}"#,
            r#"{"event": "done", "ok": false, "packsOk": 0, "packsFailed": 1, "validationErrors": 0}"#,
        ]);
        let attention = report.build_attention();
        assert_eq!(attention.len(), 1);
        assert_eq!(attention[0].category, "移植失败");
        assert_eq!(attention[0].text, "KeyError: 'x'");
    }

    #[test]
    fn constants_are_info_and_lowercase_renames_merge_per_pack() {
        let report = replay(&[
            r#"{"event": "pack_start", "pack": "a", "javaDir": "J", "index": 0, "total": 1}"#,
            r#"{"event": "molang", "pack": "a", "kind": "lower", "label": "Mouth_1 -> mouth_1", "count": 1, "attention": true}"#,
            r#"{"event": "molang", "pack": "a", "kind": "const", "label": "ctrl.tac_gun_id -> ''", "count": 6, "attention": true}"#,
            r#"{"event": "molang", "pack": "a", "kind": "lower", "label": "Mouth_2 -> mouth_2", "count": 1, "attention": true}"#,
            r#"{"event": "molang", "pack": "a", "kind": "zero", "label": "fn.move(自定义函数脚本, 基岩无对应)", "count": 4, "attention": true}"#,
            r#"{"event": "molang", "pack": "a", "kind": "lower", "label": "Sit_1 -> sit_1", "count": 1, "attention": true}"#,
            r#"{"event": "molang", "pack": "a", "kind": "map", "label": "weather -> query.mod.ysm_weather", "count": 2, "attention": false}"#,
            r#"{"event": "pack_done", "pack": "a", "ok": true, "seconds": 1, "errors": 0, "warnings": 0, "notices": 0}"#,
        ]);
        let attention = report.build_attention();
        assert_eq!(attention.len(), 3); // 置零 + 中性常量 + 小写汇总一行
        assert_eq!(attention[0].severity, "notice"); // 要看的排在前面
        assert_eq!(attention[0].category, "自定义函数置零");
        assert!(
            attention
                .iter()
                .any(|a| a.severity == "info" && a.category == "TACZ 枪 ID 未接入" && a.count == 6)
        );
        let lower: Vec<_> = attention.iter().filter(|a| a.category == "动画名转小写").collect();
        assert_eq!(lower.len(), 1);
        assert_eq!(lower[0].severity, "info");
        assert!(lower[0].text.starts_with("3 个动画名改成小写: Mouth_1→mouth_1"));
        assert_eq!(report.packs[0].attention_count(), 3);

        let text = report.to_text(false);
        assert!(text.contains("[i] molang/lower: 3 个动画名改成小写"));
        assert!(text.contains("[i] molang/const: ctrl.tac_gun_id -> '' x6"));
        assert!(text.contains("[!] molang/zero: fn.move"));
        let raw = report.to_json(true)["packs"][0]["molangAttention"]
            .as_array()
            .unwrap()
            .clone();
        assert_eq!(raw.len(), 5); // 机读形态保留逐条
        assert_eq!(raw[1]["severity"], "info");
    }

    #[test]
    fn validation_items_find_their_pack() {
        let mut report = replay(&[r#"{"event": "pack_start", "pack": "fox_01", "index": 0, "total": 1}"#]);
        report.validation.push(ValidationItem {
            level: "error".into(),
            text: "x/fox_01/a.json: bad".into(),
        });
        report.validation.push(ValidationItem {
            level: "warn".into(),
            text: "other_pack: bad".into(),
        });
        report.validation.push(ValidationItem {
            level: "warn".into(),
            text: "no colon here".into(),
        });
        report.collections.push(CollectionWritten {
            dir: "fox".into(),
            path: "P".into(),
            written: true,
            cover: None,
            warning: Some("封面图超过 1MB".into()),
        });
        report.fatal_error = Some("boom".into());
        let attention = report.build_attention();
        let packs: Vec<&str> = attention.iter().map(|a| a.pack.as_str()).collect();
        assert_eq!(packs, vec!["fox_01", "other_pack", "", "合集 fox", ""]);
        assert_eq!(attention.last().unwrap().category, "内核未能运行");
    }
}
