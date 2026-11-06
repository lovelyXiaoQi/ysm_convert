//! 门面: 发现 Java 包、移植、体检、修复、移植基线。所有入口都返回同一种报告。

use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;
use std::sync::atomic::{AtomicI64, Ordering};

use crate::cancel::CancelToken;
use crate::error::{Error, Result};
use crate::event::{self, KernelEvent};
use crate::locator::KernelPaths;
use crate::models::{
    CollectionSpec, ConvertOptions, DiscoveredPack, JobSpec, KernelInfo, OutputLayout, PackSpec, full_path,
};
use crate::report::ConversionReport;
use crate::runner::KernelRunner;
use crate::{material, names, output, win};

/// 自动并发时每个内核进程预留的内存(GB)。实测大包峰值: 凋灵娘约 0.7GB, 普通包几百 MB。
pub const MEMORY_BUDGET_PER_PROCESS_GB: f64 = 1.5;

/// 事件回调: 多个内核进程并行时由读取线程调用, 服务层已串行化(同一时刻只有一个回调在跑)。
pub type EventSink<'a> = &'a (dyn Fn(&KernelEvent) + Sync);
pub type StderrSink<'a> = &'a (dyn Fn(&str) + Sync);

fn no_event(_: &KernelEvent) {}
fn no_stderr(_: &str) {}

/// 一次转换请求(GUI / CLI / MCP 共用的输入形态)。
#[derive(Debug, Clone)]
pub struct ConvertRequest {
    pub layout: OutputLayout,
    pub packs: Vec<PackSpec>,
    pub collections: Vec<CollectionSpec>,
    pub options: ConvertOptions,
    /// java_default 基线所在资源包; None = 产物资源包自带基线时用它, 否则用内核自带快照。
    pub ref_rp: Option<String>,
}

impl ConvertRequest {
    pub fn new(layout: OutputLayout, packs: Vec<PackSpec>) -> Self {
        ConvertRequest {
            layout,
            packs,
            collections: Vec::new(),
            options: ConvertOptions::default(),
            ref_rp: None,
        }
    }
}

/// 自动并发数: 逻辑核数的一半(最多 8), 再按可用内存 / 每进程预算封顶, 至少 1。
pub fn auto_parallelism() -> usize {
    let cpus = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(2);
    let by_cpu = (cpus / 2).clamp(1, 8);
    let available_gb = win::available_memory_gb();
    let by_memory = if available_gb > 0.0 {
        ((available_gb / MEMORY_BUDGET_PER_PROCESS_GB) as usize).max(1)
    } else {
        by_cpu
    };
    by_cpu.min(by_memory)
}

pub fn effective_parallelism(requested: usize, pack_count: usize) -> usize {
    let wanted = if requested > 0 { requested } else { auto_parallelism() };
    wanted.clamp(1, pack_count.max(1))
}

pub struct ConversionService {
    runner: KernelRunner,
}

impl ConversionService {
    pub fn new(paths: KernelPaths) -> Self {
        ConversionService {
            runner: KernelRunner::new(paths),
        }
    }

    pub fn paths(&self) -> &KernelPaths {
        self.runner.paths()
    }

    pub fn runner(&self) -> &KernelRunner {
        &self.runner
    }

    pub fn get_info(&self, cancel: &CancelToken) -> Result<KernelInfo> {
        self.runner.get_info(cancel)
    }

    pub fn discover(&self, dirs: &[String], cancel: &CancelToken) -> Result<Vec<DiscoveredPack>> {
        self.runner.discover(dirs, cancel)
    }

    pub fn convert(
        &self,
        request: &ConvertRequest,
        on_event: Option<EventSink<'_>>,
        on_stderr: Option<StderrSink<'_>>,
        cancel: &CancelToken,
    ) -> Result<ConversionReport> {
        let mut problems = names::validate(&request.packs);
        problems.extend(material::validate(&request.packs));
        if request.packs.is_empty() {
            problems.insert(0, "没有要转换的 Java 包".into());
        }
        problems.extend(request.collections.iter().filter_map(CollectionSpec::cover_problem));
        if !problems.is_empty() {
            return Err(Error::Argument(problems.join("\n")));
        }
        // 工程形态的已有行为包同样要能被网易挂载(见 ensure_behavior_pack_marker 注)
        output::ensure_behavior_pack_marker(&request.layout.bp_dir)?;
        let ref_rp = self.resolve_ref_rp(&request.layout, request.ref_rp.as_deref());
        let on_event = on_event.unwrap_or(&no_event);
        let on_stderr = on_stderr.unwrap_or(&no_stderr);
        if request.packs.len() == 1 {
            let mut job = JobSpec::new("convert", request.layout.clone());
            job.ref_rp = ref_rp;
            job.packs = request.packs.clone();
            job.collections = request.collections.clone();
            job.options = request.options.clone();
            return self.run(&job, on_event, on_stderr, cancel);
        }
        self.convert_per_pack(request, ref_rp, on_event, on_stderr, cancel)
    }

    /// 多个包: 每包一个内核进程, 最多同时跑 [`effective_parallelism`] 个, 全部结束后跑一次收尾任务(finalize)
    /// 统一写合集清单与体检。并行之外还有一个理由必须拆进程: 同一个 Python 进程连续转多个大包时内存只涨不回,
    /// 2026-09-18 实测 30 个包串行跑到第 28 个时 MemoryError, 而那个包单独跑峰值只有 0.7GB。
    /// 共享文件(sound_definitions.json、合集清单与封面)由内核的跨进程锁保护(port_java_pack.SharedFileLock)。
    fn convert_per_pack(
        &self,
        request: &ConvertRequest,
        ref_rp: Option<String>,
        on_event: EventSink<'_>,
        on_stderr: StderrSink<'_>,
        cancel: &CancelToken,
    ) -> Result<ConversionReport> {
        struct Shared {
            report: ConversionReport,
            start_sent: bool,
            outcome: HashMap<String, bool>,
            fatal: Option<String>,
            io_error: Option<Error>,
        }
        let total = request.packs.len() as i64;
        let shared = Mutex::new(Shared {
            report: ConversionReport::new("convert", Some(request.layout.clone())),
            start_sent: false,
            outcome: HashMap::new(),
            fatal: None,
            io_error: None,
        });
        let started = AtomicI64::new(0);
        let finished = AtomicI64::new(0);
        let (linked, _link) = cancel.child();
        let queue = Mutex::new(request.packs.iter().cloned().collect::<VecDeque<PackSpec>>());
        let parallel = effective_parallelism(request.options.max_parallel, request.packs.len());

        // 子进程的事件来自多个读取线程: 串行化后再交给报告与调用方(GUI 投递到界面线程, CLI 直接打印)
        let deliver = |e: KernelEvent| {
            let mut s = lock(&shared);
            s.report.apply(&e);
            on_event(&e);
        };
        let stderr = |line: &str| {
            let mut s = lock(&shared);
            s.report.stderr.push(line.to_string());
            on_stderr(line);
        };

        let run_one = |pack: PackSpec| {
            let mut job = JobSpec::new("convert", request.layout.clone());
            job.ref_rp = ref_rp.clone();
            job.packs = vec![pack.clone()];
            job.options = ConvertOptions {
                with_mods: request.options.with_mods,
                java_functions: request.options.java_functions,
                validate: false,
                compact_json: request.options.compact_json,
                write_collections: false,
                max_parallel: 0,
            };
            let mut saw_done = false;
            let result = self.runner.run_job(
                &job,
                &mut |mut e: KernelEvent| match e.event.clone().as_str() {
                    event::START => {
                        {
                            let mut s = lock(&shared);
                            if s.start_sent {
                                return;
                            }
                            s.start_sent = true;
                        }
                        e.packs = Some(total);
                        deliver(e);
                    }
                    event::PACK_START => {
                        e.index = Some(started.fetch_add(1, Ordering::SeqCst));
                        e.total = Some(total);
                        deliver(e);
                    }
                    event::PACK_DONE => {
                        saw_done = true;
                        lock(&shared).outcome.insert(pack.name.clone(), e.ok == Some(true));
                        e.index = Some(finished.fetch_add(1, Ordering::SeqCst));
                        e.total = Some(total);
                        deliver(e);
                    }
                    event::DONE => {} // 子任务各自的 done 不转发, 最后合成一个
                    _ => deliver(e),
                },
                &stderr,
                &linked,
            );
            match result {
                Ok(_) => {}
                Err(Error::Cancelled) => return,
                Err(error @ Error::Kernel { .. }) => {
                    // 解释器起不来(缺 VC++ 2008 运行库、内核文件缺失)对每个包都一样: 记一次, 其余子任务不再启动
                    let mut s = lock(&shared);
                    if s.fatal.is_none() {
                        s.fatal = Some(error.full_text("\n"));
                    }
                    drop(s);
                    linked.cancel();
                }
                Err(other) => {
                    let mut s = lock(&shared);
                    if s.io_error.is_none() {
                        s.io_error = Some(other);
                    }
                    drop(s);
                    linked.cancel();
                }
            }
            if !saw_done && !linked.is_cancelled() {
                // 子进程没发 pack_done 就退出(被杀 / 崩溃): 补一条失败, 报告与进度都要算上它
                lock(&shared).outcome.insert(pack.name.clone(), false);
                let mut e = KernelEvent::new(event::PACK_DONE);
                e.pack = Some(pack.name.clone());
                e.ok = Some(false);
                e.error = Some("内核进程意外退出, 没有报告结果(完整输出见报告的 stderr 段)".into());
                e.errors = Some(1);
                e.warnings = Some(0);
                e.notices = Some(0);
                e.seconds = Some(0.0);
                e.index = Some(finished.fetch_add(1, Ordering::SeqCst));
                e.total = Some(total);
                deliver(e);
            }
        };

        std::thread::scope(|scope| {
            for _ in 0..parallel {
                scope.spawn(|| {
                    loop {
                        if linked.is_cancelled() {
                            break;
                        }
                        let Some(pack) = lock(&queue).pop_front() else { break };
                        run_one(pack);
                    }
                });
            }
        });

        if cancel.is_cancelled() {
            return Err(Error::Cancelled);
        }
        let mut s = shared.into_inner().unwrap_or_else(|p| p.into_inner());
        if let Some(error) = s.io_error.take() {
            return Err(error);
        }
        if let Some(fatal) = s.fatal.take() {
            s.report.fatal_error = Some(fatal);
            s.report.exit_code = -1;
            return Ok(s.report);
        }

        let succeeded: Vec<PackSpec> = request
            .packs
            .iter()
            .filter(|p| s.outcome.get(&p.name).copied() == Some(true))
            .cloned()
            .collect();
        let failed_count = request.packs.len() - succeeded.len();
        let mut validation_errors = 0i64;
        let need_finalize =
            !succeeded.is_empty() && (request.options.validate || succeeded.iter().any(|p| p.collection.is_some()));
        let mut report = s.report;
        if need_finalize {
            let mut finalize = JobSpec::new("finalize", request.layout.clone());
            finalize.ref_rp = ref_rp.clone();
            finalize.packs = succeeded.clone();
            finalize.collections = request.collections.clone();
            finalize.options = ConvertOptions {
                validate: request.options.validate,
                compact_json: request.options.compact_json,
                ..ConvertOptions::default()
            };
            let result = {
                let report_cell = Mutex::new(&mut report);
                self.runner.run_job(
                    &finalize,
                    &mut |e: KernelEvent| {
                        if e.is(event::DONE) {
                            validation_errors = e.validation_errors.unwrap_or(0);
                            return;
                        }
                        if !e.is(event::START) {
                            lock(&report_cell).apply(&e);
                            on_event(&e);
                        }
                    },
                    &|line: &str| {
                        lock(&report_cell).stderr.push(line.to_string());
                        on_stderr(line);
                    },
                    cancel,
                )
            };
            match result {
                Ok(_) => {}
                Err(Error::Kernel { message, details }) => {
                    report.fatal_error = Some(Error::Kernel { message, details }.full_text("\n"));
                }
                Err(other) => return Err(other),
            }
        }
        let mut done = KernelEvent::new(event::DONE);
        done.ok = Some(failed_count == 0 && validation_errors == 0 && report.fatal_error.is_none());
        done.packs_ok = Some(succeeded.len() as i64);
        done.packs_failed = Some(failed_count as i64);
        done.validation_errors = Some(validation_errors);
        report.apply(&done);
        on_event(&done);
        report.exit_code = if failed_count == 0 { 0 } else { 1 };
        Ok(report)
    }

    pub fn validate(
        &self,
        layout: &OutputLayout,
        packs: &[String],
        on_event: Option<EventSink<'_>>,
        on_stderr: Option<StderrSink<'_>>,
        cancel: &CancelToken,
    ) -> Result<ConversionReport> {
        let mut job = JobSpec::new("validate", layout.clone());
        job.ref_rp = self.resolve_ref_rp(layout, None);
        job.packs = name_only_packs(packs);
        self.run(
            &job,
            on_event.unwrap_or(&no_event),
            on_stderr.unwrap_or(&no_stderr),
            cancel,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn fix(
        &self,
        layout: &OutputLayout,
        packs: &[String],
        validate: bool,
        compact_json: bool,
        on_event: Option<EventSink<'_>>,
        on_stderr: Option<StderrSink<'_>>,
        cancel: &CancelToken,
    ) -> Result<ConversionReport> {
        // 旧版转换器产出的组件行为包可能没有 entities, 就地修复一并补上(见 ensure_behavior_pack_marker 注)
        output::ensure_behavior_pack_marker(&layout.bp_dir)?;
        let mut job = JobSpec::new("fix", layout.clone());
        job.ref_rp = self.resolve_ref_rp(layout, None);
        job.packs = name_only_packs(packs);
        job.options = ConvertOptions {
            validate,
            compact_json,
            ..ConvertOptions::default()
        };
        self.run(
            &job,
            on_event.unwrap_or(&no_event),
            on_stderr.unwrap_or(&no_stderr),
            cancel,
        )
    }

    pub fn baseline(
        &self,
        layout: &OutputLayout,
        java_default_dir: &str,
        with_mods: bool,
        on_event: Option<EventSink<'_>>,
        on_stderr: Option<StderrSink<'_>>,
        cancel: &CancelToken,
    ) -> Result<ConversionReport> {
        let mut job = JobSpec::new("baseline", layout.clone());
        job.packs = vec![PackSpec {
            java_dir: full_path(java_default_dir),
            name: "java_default".into(),
            ..Default::default()
        }];
        // 基线在 YSM 主工程里入库, 保持缩进
        job.options = ConvertOptions {
            with_mods,
            validate: false,
            compact_json: false,
            ..ConvertOptions::default()
        };
        self.run(
            &job,
            on_event.unwrap_or(&no_event),
            on_stderr.unwrap_or(&no_stderr),
            cancel,
        )
    }

    pub fn resolve_ref_rp(&self, layout: &OutputLayout, explicit_ref_rp: Option<&str>) -> Option<String> {
        if let Some(explicit) = explicit_ref_rp.filter(|r| !r.is_empty()) {
            return Some(full_path(explicit));
        }
        if layout.has_baseline() {
            return None;
        }
        let bundled = self.paths().bundled_ref_rp();
        bundled
            .join("animations")
            .join("java_default")
            .is_dir()
            .then(|| bundled.display().to_string())
    }

    fn run(
        &self,
        job: &JobSpec,
        on_event: EventSink<'_>,
        on_stderr: StderrSink<'_>,
        cancel: &CancelToken,
    ) -> Result<ConversionReport> {
        let report = Mutex::new(ConversionReport::new(&job.action, Some(job.layout.clone())));
        let result = self.runner.run_job(
            job,
            &mut |e: KernelEvent| {
                lock(&report).apply(&e);
                on_event(&e);
            },
            &|line: &str| {
                lock(&report).stderr.push(line.to_string());
                on_stderr(line);
            },
            cancel,
        );
        let mut report = report.into_inner().unwrap_or_else(|p| p.into_inner());
        match result {
            Ok(code) => {
                report.exit_code = code;
                if !report.done && report.fatal_error.is_none() {
                    let tail: Vec<&str> = report
                        .stderr
                        .iter()
                        .rev()
                        .take(5)
                        .rev()
                        .map(String::as_str)
                        .collect::<Vec<_>>();
                    report.fatal_error = Some(format!(
                        "内核未正常结束(退出码 {code}){}",
                        if tail.is_empty() {
                            String::new()
                        } else {
                            format!(": {}", tail.join(" | "))
                        }
                    ));
                }
            }
            Err(error @ Error::Kernel { .. }) => {
                report.fatal_error = Some(error.full_text(": "));
                report.exit_code = -1;
            }
            Err(other) => return Err(other),
        }
        Ok(report)
    }
}

fn name_only_packs(names: &[String]) -> Vec<PackSpec> {
    names
        .iter()
        .map(|n| PackSpec {
            java_dir: String::new(),
            name: n.clone(),
            ..Default::default()
        })
        .collect()
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// 把发现结果整理成转换任务: 包名(建议名 / 统一前缀 / 逐包改名)、合集归属、材质与背面剔除(整批 / 逐包)。
pub struct ConvertPlan;

/// 逐包覆盖表: 键是 Java 文件夹名或 Java 目录(不分大小写)。
#[derive(Debug, Clone, Default)]
pub struct PerPack<T> {
    entries: Vec<(String, T)>,
}

impl<T: Clone> PerPack<T> {
    pub fn new() -> Self {
        PerPack { entries: Vec::new() }
    }

    /// 同一个键写多次时后写的胜出(与命令行"可多次"的直觉一致)。
    pub fn insert(&mut self, key: impl Into<String>, value: T) {
        let key = key.into();
        let lower = key.to_lowercase();
        match self.entries.iter_mut().find(|(k, _)| k.to_lowercase() == lower) {
            Some(entry) => entry.1 = value,
            None => self.entries.push((key, value)),
        }
    }

    pub fn get(&self, key: &str) -> Option<&T> {
        let lower = key.to_lowercase();
        self.entries
            .iter()
            .find(|(k, _)| k.to_lowercase() == lower)
            .map(|(_, v)| v)
    }

    fn lookup(&self, folder: &str, java_dir: &str) -> Option<&T> {
        self.get(folder).or_else(|| self.get(java_dir))
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

/// [`ConvertPlan::build_packs`] 的选项。
#[derive(Debug, Clone, Default)]
pub struct PlanOptions {
    pub prefix: Option<String>,
    pub collection_override: Option<String>,
    pub renames: PerPack<String>,
    /// 全部包的材质(None = 自动); `materials` 按 Java 文件夹名或目录逐包覆盖。
    pub material: Option<String>,
    /// 全部包的背面剔除(None = 照 Java all_cutout); `culls` 逐包覆盖。
    pub cull: Option<bool>,
    pub materials: PerPack<String>,
    pub culls: PerPack<Option<bool>>,
}

impl ConvertPlan {
    pub fn build_packs(discovered: &[DiscoveredPack], options: &PlanOptions) -> Vec<PackSpec> {
        let mut packs = Vec::new();
        for d in discovered {
            if d.error.is_some() {
                continue;
            }
            let folder = d.folder_name();
            let name = if let Some(renamed) = options.renames.lookup(&folder, &d.java_dir) {
                renamed.clone()
            } else if let Some(prefix) = options.prefix.as_deref().filter(|p| !p.trim().is_empty()) {
                format!(
                    "{}_{}",
                    names::sanitize(prefix),
                    names::sanitize(d.suggested_name.as_deref().unwrap_or(&folder)).trim_start_matches('_')
                )
            } else {
                d.suggested_name.clone().unwrap_or_else(|| names::sanitize(&folder))
            };
            let pack_material = options
                .materials
                .lookup(&folder, &d.java_dir)
                .cloned()
                .or_else(|| options.material.clone());
            let pack_cull = match options.culls.lookup(&folder, &d.java_dir) {
                Some(own) => *own,
                None => options.cull,
            };
            packs.push(PackSpec {
                java_dir: d.java_dir.clone(),
                name,
                collection: options
                    .collection_override
                    .clone()
                    .or_else(|| d.collection_folder.clone()),
                material: material::normalize(pack_material.as_deref()),
                cull: pack_cull,
            });
        }
        packs
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn discovered(java_dir: &str, folder: &str, suggested: Option<&str>, collection: Option<&str>) -> DiscoveredPack {
        DiscoveredPack {
            java_dir: java_dir.into(),
            folder: Some(folder.into()),
            suggested_name: suggested.map(str::to_string),
            collection_folder: collection.map(str::to_string),
            ..Default::default()
        }
    }

    #[test]
    fn parallelism_stays_within_bounds() {
        assert_eq!(effective_parallelism(0, 1), 1);
        assert_eq!(effective_parallelism(3, 10), 3);
        assert_eq!(effective_parallelism(5, 2), 2); // 不超过包数
        assert!((1..=8).contains(&auto_parallelism()));
    }

    #[test]
    fn convert_plan_applies_prefix_collection_and_renames() {
        let mut bad = discovered(r"C:\j\bad", "bad", None, None);
        bad.error = Some("x".into());
        let list = vec![
            discovered(r"C:\j\01_maid", "01_maid", Some("fox_01_maid"), Some("fox")),
            discovered(r"C:\j\02", "02", Some("fox_02"), Some("fox")),
            bad,
        ];
        let mut renames = PerPack::new();
        renames.insert("02", "renamed_02".to_string());
        let packs = ConvertPlan::build_packs(
            &list,
            &PlanOptions {
                collection_override: Some("all".into()),
                renames,
                ..Default::default()
            },
        );
        assert_eq!(packs.len(), 2);
        assert_eq!(packs[0].name, "fox_01_maid");
        assert_eq!(packs[1].name, "renamed_02");
        assert!(packs.iter().all(|p| p.collection.as_deref() == Some("all")));

        let prefixed = ConvertPlan::build_packs(
            &list,
            &PlanOptions {
                prefix: Some("Me".into()),
                ..Default::default()
            },
        );
        assert_eq!(prefixed[0].name, "me_fox_01_maid");
        assert_eq!(prefixed[0].collection.as_deref(), Some("fox"));
    }

    #[test]
    fn convert_plan_applies_material_and_cull() {
        let list = vec![
            discovered(r"C:\j\01", "01", Some("p01"), None),
            discovered(r"C:\j\02", "02", Some("p02"), None),
        ];
        let mut materials = PerPack::new();
        materials.insert("02", "自动".to_string());
        let mut culls = PerPack::new();
        culls.insert("02", None);
        let packs = ConvertPlan::build_packs(
            &list,
            &PlanOptions {
                material: Some("translucent".into()),
                cull: Some(true),
                materials,
                culls,
                ..Default::default()
            },
        );
        assert_eq!(packs[0].material.as_deref(), Some("translucent"));
        assert_eq!(packs[0].cull, Some(true));
        assert_eq!(packs[1].material, None);
        assert_eq!(packs[1].cull, None);
        assert!(
            ConvertPlan::build_packs(&list, &PlanOptions::default())
                .iter()
                .all(|p| p.material.is_none() && p.cull.is_none())
        );
    }

    #[test]
    fn per_pack_keys_ignore_case_and_last_wins() {
        let mut map = PerPack::new();
        map.insert("Fox", 1);
        map.insert("fox", 2);
        assert_eq!(map.get("FOX"), Some(&2));
        assert_eq!(map.get("wolf"), None);
    }
}
