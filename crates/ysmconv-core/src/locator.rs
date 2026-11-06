//! 定内核根: 环境变量 YSMCONV_CORE > 程序目录旁的 core(发布包形态) > 向上找到的 YSM 工程根(开发形态, 直接用
//! 工程的 devtools/ 与 ysm_rp/) > 向上找到的 core/kernel(旧开发形态)。
//!
//! 转换器就住在 YSM 工程里(<工程>/ysm_convert), 开发时直接跑工程的 devtools —— 改了规则不用先同步快照,
//! 也就不存在"两份内核各自演化"。发布包里没有工程, 用 build/sync-core.ps1 拷进去的 core/kernel 快照;
//! 程序目录旁有 core 时优先用它, 免得把解压到工程里的发布包"测成"实时内核。
//!
//! Python 解释器只在 core/python 整个不存在时(= 开发机还没跑 make-python.ps1)才回落到系统安装;
//! core/python 在、python.exe 不在, 说明发布包被削过, 直接报错 —— 悄悄改用系统 Python 会让产物口径失控。

use std::path::{Path, PathBuf};

use crate::error::{Error, Result};

pub const CORE_DIR_ENV: &str = "YSMCONV_CORE";
pub const BUNDLED_PYTHON_SOURCE: &str = "bundled";
const MAX_WALK_UP: usize = 8;

/// 转换内核的落点: 便携 Python 解释器 + devtools(项目源码或随包快照)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KernelPaths {
    pub core_dir: PathBuf,
    pub python_exe: PathBuf,
    pub python_args_prefix: Vec<String>,
    pub python_source: String,
    pub kernel_root: PathBuf,
    pub docs_dir: PathBuf,
    pub from_project: bool,
}

impl KernelPaths {
    pub fn port_cli(&self) -> PathBuf {
        self.kernel_root.join("devtools").join("port_cli.py")
    }

    /// 内核自带的 java_default 基线所在资源包(独立组件模式下的参考资源包)。
    pub fn bundled_ref_rp(&self) -> PathBuf {
        self.kernel_root.join("ysm_rp")
    }

    pub fn source_info_file(&self) -> PathBuf {
        self.kernel_root.join("KERNEL_SOURCE.json")
    }

    /// 用的是随包的便携解释器(发布形态), 而不是开发机上装的 Python。
    pub fn uses_bundled_python(&self) -> bool {
        self.python_source == BUNDLED_PYTHON_SOURCE
    }

    /// 内核来源的人话说明(info / MCP 里显示)。
    pub fn kernel_source_text(&self) -> &'static str {
        if self.from_project {
            "项目 devtools(实时)"
        } else {
            "随包快照 core/kernel"
        }
    }
}

pub struct KernelLocator;

impl KernelLocator {
    pub fn locate() -> Result<KernelPaths> {
        Self::try_locate().map_err(Error::Invalid)
    }

    /// 从程序所在目录起解析。
    pub fn try_locate() -> std::result::Result<KernelPaths, String> {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(Path::to_path_buf))
            .unwrap_or_else(|| PathBuf::from("."));
        Self::try_locate_from(&exe_dir)
    }

    /// 从指定目录起解析(单元测试用; 正式入口是 [`KernelLocator::try_locate`])。
    pub fn try_locate_from(start_dir: &Path) -> std::result::Result<KernelPaths, String> {
        if let Some(from_env) = std::env::var_os(CORE_DIR_ENV).filter(|v| !v.to_string_lossy().trim().is_empty()) {
            let dir = PathBuf::from(from_env);
            if is_core_dir(&dir) {
                return build(absolute(&dir), None);
            }
        }

        // 发布包: 程序目录旁就是 core/(解压到工程里也按发布包算, 不去用工程的实时内核)
        let beside_exe = start_dir.join("core");
        if is_core_dir(&beside_exe) {
            return build(absolute(&beside_exe), None);
        }

        let mut core: Option<PathBuf> = None;
        let mut project: Option<PathBuf> = None;
        let start = absolute(start_dir);
        let mut dir = Some(start.as_path());
        let mut depth = 0;
        while let Some(current) = dir {
            if depth >= MAX_WALK_UP {
                break;
            }
            let candidate = current.join("core");
            if core.is_none() && is_core_dir(&candidate) {
                core = Some(candidate);
            }
            if project.is_none() && is_project_root(current) {
                project = Some(current.to_path_buf());
            }
            // core/python 与工程各在一层: 两个都要接着往上找, 不能找到一个就停
            dir = current.parent();
            depth += 1;
        }
        if let Some(project) = project {
            let core = core.unwrap_or_else(|| project.parent().unwrap_or(&project).join("core"));
            return build(core, Some(project));
        }
        if let Some(core) = core {
            return build(core, None);
        }
        Err(format!(
            "找不到转换内核: 程序目录旁没有 core/(kernel/devtools/port_cli.py), 也没找到 YSM 工程根\
             (含 devtools/port_cli.py 与 ysm_rp/animations/java_default)。把程序放在带 core/ 的目录运行, \
             或设置环境变量 {CORE_DIR_ENV} 指向 core 目录。"
        ))
    }
}

fn build(core_dir: PathBuf, project_root: Option<PathBuf>) -> std::result::Result<KernelPaths, String> {
    let (python_exe, python_args_prefix, python_source) = find_python(&core_dir)?;
    let (kernel_root, docs_dir) = match &project_root {
        Some(project) => (project.clone(), project.join("docs")),
        None => (core_dir.join("kernel"), core_dir.join("docs")),
    };
    Ok(KernelPaths {
        core_dir,
        python_exe,
        python_args_prefix,
        python_source,
        kernel_root,
        docs_dir,
        from_project: project_root.is_some(),
    })
}

fn is_core_dir(dir: &Path) -> bool {
    dir.join("kernel").join("devtools").join("port_cli.py").is_file()
}

/// YSM 工程根: devtools 的内核入口 + 带 java_default 基线的资源包(两样都在才是工程, 不会误认 core/kernel)。
fn is_project_root(dir: &Path) -> bool {
    dir.join("devtools").join("port_cli.py").is_file()
        && dir.join("ysm_rp").join("animations").join("java_default").is_dir()
}

fn find_python(core_dir: &Path) -> std::result::Result<(PathBuf, Vec<String>, String), String> {
    let python_dir = core_dir.join("python");
    let bundled = python_dir.join("python.exe");
    if bundled.is_file() {
        return Ok((bundled, Vec::new(), BUNDLED_PYTHON_SOURCE.to_string()));
    }
    if python_dir.is_dir() {
        // 发布包里 core/python 应当是完整的一份解释器。它在、python.exe 却不在, 多半是解压不全或被杀毒删了。
        // 这时绝不能回落到机器上装的 Python: 那份的版本与 site-packages 都不受控, 转换产物要和游戏内
        // 同一个 2.7 解释器对齐才作数, 而且悄悄换掉用户根本看不出来。
        return Err(format!(
            "随包的 Python 运行时不完整, 缺 {}。\n请重新完整解压发布包; 若是杀毒软件删的, 把整个目录加进白名单后重新解压。",
            bundled.display()
        ));
    }

    // core/python 整个不在 = 开发机上还没跑过 build/make-python.ps1, 用本机的 Python 2.7 顶上
    let system = PathBuf::from(r"C:\Python27\python.exe");
    if system.is_file() {
        return Ok((system, Vec::new(), r"C:\Python27".to_string()));
    }
    let windows = std::env::var_os("SystemRoot")
        .or_else(|| std::env::var_os("windir"))
        .map(PathBuf::from);
    if let Some(launcher) = windows.map(|w| w.join("py.exe")).filter(|p| p.is_file()) {
        return Ok((launcher, vec!["-2.7".to_string()], "py -2.7".to_string()));
    }
    Err(format!(
        "找不到 Python 2.7 解释器: 发布包应带 {}(由 build/make-python.ps1 生成); \
         开发环境下也可以装一份 C:\\Python27, 或让 py 启动器带 2.7。",
        bundled.display()
    ))
}

fn absolute(path: &Path) -> PathBuf {
    std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf())
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::fs;
    use std::sync::Mutex;

    /// 两组用例都要摆弄同一个**进程级**环境变量 YSMCONV_CORE, 而 cargo test 默认多线程并行: 串行化, 免得
    /// 一个用例把另一个的前提踩掉(C# 版 2026-09-23 实测默认并行下约三次跑挂一次)。
    pub(crate) static ENV_LOCK: Mutex<()> = Mutex::new(());

    struct Temp(PathBuf);

    impl Temp {
        fn new(tag: &str) -> Self {
            let dir = std::env::temp_dir().join(format!("ysmconv-{tag}-{}", crate::win::random_hex()));
            fs::create_dir_all(&dir).unwrap();
            Temp(dir)
        }
    }

    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn touch(path: &Path, text: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }

    fn with_env<T>(value: Option<&Path>, f: impl FnOnce() -> T) -> T {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let saved = std::env::var_os(CORE_DIR_ENV);
        // SAFETY: 环境变量读写都在 ENV_LOCK 里, 本 crate 的测试不会并发碰它
        unsafe {
            match value {
                Some(v) => std::env::set_var(CORE_DIR_ENV, v),
                None => std::env::remove_var(CORE_DIR_ENV),
            }
        }
        let result = f();
        unsafe {
            match saved {
                Some(v) => std::env::set_var(CORE_DIR_ENV, v),
                None => std::env::remove_var(CORE_DIR_ENV),
            }
        }
        result
    }

    #[test]
    fn prefers_bundled_interpreter() {
        let root = Temp::new("loc");
        touch(&root.0.join("kernel/devtools/port_cli.py"), "# stub");
        touch(&root.0.join("python/python.exe"), "stub");
        let paths = with_env(Some(&root.0), || {
            KernelLocator::try_locate_from(&root.0.join("nowhere"))
        })
        .unwrap();
        assert!(paths.uses_bundled_python());
        assert_eq!(paths.python_exe, absolute(&root.0).join("python").join("python.exe"));
    }

    #[test]
    fn refuses_to_fall_back_when_bundled_runtime_is_gutted() {
        // core/python 在但 python.exe 不在: 解压不全或杀毒删了。绝不能悄悄改用机器上装的 Python 2.7,
        // 那份的版本与 site-packages 不受控, 产物口径会和游戏对不上, 而用户看不出被换了。
        let root = Temp::new("loc");
        touch(&root.0.join("kernel/devtools/port_cli.py"), "# stub");
        fs::create_dir_all(root.0.join("python")).unwrap();
        let error = with_env(Some(&root.0), || {
            KernelLocator::try_locate_from(&root.0.join("nowhere"))
        })
        .unwrap_err();
        assert!(error.contains("不完整"), "{error}");
    }

    #[test]
    fn reports_missing_core_directory() {
        let root = Temp::new("loc");
        let start = root.0.join("bin");
        fs::create_dir_all(&start).unwrap();
        let result = with_env(Some(&root.0.join("nope")), || KernelLocator::try_locate_from(&start));
        assert!(result.unwrap_err().contains("core"));
    }

    /// 工程: devtools 入口 + 带 java_default 基线的资源包; 转换器: 仓库里提交的快照 + 便携解释器。
    fn project_layout(root: &Path) -> (PathBuf, PathBuf) {
        let project = root.join("ysm");
        let converter = project.join("ysm_convert");
        fs::create_dir_all(project.join("ysm_rp/animations/java_default")).unwrap();
        fs::create_dir_all(project.join("docs")).unwrap();
        touch(&project.join("devtools/port_cli.py"), "# live");
        touch(&converter.join("core/kernel/devtools/port_cli.py"), "# snapshot");
        touch(&converter.join("core/python/python.exe"), "stub");
        (project, converter)
    }

    #[test]
    fn dev_build_runs_the_project_devtools() {
        let root = Temp::new("src");
        let (project, converter) = project_layout(&root.0);
        let bin = converter.join("target").join("debug");
        fs::create_dir_all(&bin).unwrap();
        let paths = with_env(None, || KernelLocator::try_locate_from(&bin)).unwrap();
        assert!(paths.from_project);
        let project = absolute(&project);
        assert_eq!(paths.kernel_root, project);
        assert_eq!(paths.port_cli(), project.join("devtools").join("port_cli.py"));
        assert_eq!(paths.bundled_ref_rp(), project.join("ysm_rp")); // 基线读工程的资源包
        assert_eq!(paths.docs_dir, project.join("docs"));
        assert!(paths.uses_bundled_python()); // 解释器仍取转换器的 core/python
    }

    #[test]
    fn released_package_runs_its_own_snapshot_even_inside_the_project() {
        // 发布包解压到工程里(dist/YsmConvert)也按发布包算: 测的是它自己带的快照, 不会被工程的实时内核顶掉
        let root = Temp::new("src");
        let (_project, converter) = project_layout(&root.0);
        let dist = converter.join("dist").join("YsmConvert");
        touch(&dist.join("core/kernel/devtools/port_cli.py"), "# shipped");
        touch(&dist.join("core/python/python.exe"), "stub");
        let paths = with_env(None, || KernelLocator::try_locate_from(&dist)).unwrap();
        assert!(!paths.from_project);
        assert_eq!(paths.kernel_root, absolute(&dist).join("core").join("kernel"));
        assert_eq!(paths.docs_dir, absolute(&dist).join("core").join("docs"));
    }

    #[test]
    fn falls_back_to_snapshot_without_project() {
        // 转换器单独拿出来(没有工程): 用仓库里的快照
        let root = Temp::new("src");
        let lonely = root.0.join("lonely");
        touch(&lonely.join("core/kernel/devtools/port_cli.py"), "# snapshot");
        touch(&lonely.join("core/python/python.exe"), "stub");
        let bin = lonely.join("target").join("release");
        fs::create_dir_all(&bin).unwrap();
        let paths = with_env(None, || KernelLocator::try_locate_from(&bin)).unwrap();
        assert!(!paths.from_project);
        assert_eq!(paths.kernel_root, absolute(&lonely).join("core").join("kernel"));
    }
}
