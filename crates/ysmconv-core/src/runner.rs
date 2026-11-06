//! 起 Python 子进程跑 port_cli.py, 把 stdout 的 JSON 行解析成事件; stderr 原样转给调用方。

use std::fs;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;

use serde_json::{Value, json};

use crate::cancel::CancelToken;
use crate::error::{Error, Result};
use crate::event::{self, KernelEvent};
use crate::locator::KernelPaths;
use crate::models::{DiscoveredPack, JobSpec, KernelInfo, full_path};
use crate::win;

/// ERROR_SXS_CANT_GEN_ACTCTX: 解析 exe 的并行程序集清单失败。python.exe 与 python27.dll 的嵌入清单依赖
/// Microsoft.VC90.CRT, 目标机器既没装 VC++ 2008 x64、随包的私有程序集又缺失时, CreateProcess 就以这个错误失败。
const ERROR_SXS_CANT_GEN_ACTCTX: i32 = 14001;
const ERROR_FILE_NOT_FOUND: i32 = 2;
/// CREATE_NO_WINDOW: 内核子进程不弹控制台窗口(也就收不到壳所在控制台的 Ctrl+C, 生命周期由作业对象管)。
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// 缺 VC++ 2008 运行库时给用户看的说明。
pub const MISSING_VC_RUNTIME_MESSAGE: &str = "转换内核用的 Python 2.7 需要 Visual C++ 2008 运行库(x64), 这台机器上没有。\n\n\
请安装 vcredist_x64.exe(Microsoft Visual C++ 2008 SP1 可再发行组件包)后重试:\n\
https://www.microsoft.com/download/details.aspx?id=26368\n\n\
若发布包里带了 core\\python\\Microsoft.VC90.CRT\\, 请确认该目录连同 msvcr90.dll 与清单一起解压完整。";

/// 退出码是不是 NTSTATUS 0xC015xxxx(STATUS_SXS_*, 并行程序集装载失败)。
/// 内核自己的业务退出码只有 0/1/2, 与这一段不重叠。
pub fn is_side_by_side_failure(exit_code: i32) -> bool {
    ((exit_code as u32) >> 16) == 0xC015
}

pub struct KernelRunner {
    paths: KernelPaths,
}

impl KernelRunner {
    pub fn new(paths: KernelPaths) -> Self {
        KernelRunner { paths }
    }

    pub fn paths(&self) -> &KernelPaths {
        &self.paths
    }

    pub fn get_info(&self, cancel: &CancelToken) -> Result<KernelInfo> {
        let mut info: Option<KernelInfo> = None;
        let stderr = std::sync::Mutex::new(String::new());
        let code = self.run(
            &["--version".to_string()],
            &mut |line| {
                if line.starts_with('{')
                    && let Ok(value) = serde_json::from_str::<Value>(line)
                {
                    info = KernelInfo::from_value(&value);
                }
            },
            &|line| {
                let mut s = stderr.lock().unwrap_or_else(|p| p.into_inner());
                s.push_str(line);
                s.push('\n');
            },
            cancel,
        )?;
        info.ok_or_else(|| {
            let stderr = stderr.into_inner().unwrap_or_else(|p| p.into_inner());
            Error::kernel(
                format!("转换内核没有响应(退出码 {code})"),
                Some(if stderr.is_empty() {
                    self.paths.port_cli().display().to_string()
                } else {
                    stderr
                }),
            )
        })
    }

    pub fn discover(&self, dirs: &[String], cancel: &CancelToken) -> Result<Vec<DiscoveredPack>> {
        let request = temp_dir()?.join(format!("discover-{}.json", win::random_hex()));
        let dirs: Vec<String> = dirs.iter().map(|d| full_path(d)).collect();
        fs::write(
            &request,
            serde_json::to_string(&json!({ "dirs": dirs })).unwrap_or_default(),
        )?;
        let _cleanup = DeleteOnDrop(request.clone());
        let mut packs = Vec::new();
        let stderr = std::sync::Mutex::new(String::new());
        let code = self.run(
            &["--discover".to_string(), request.display().to_string()],
            &mut |line| {
                if !line.starts_with('{') {
                    return;
                }
                let Ok(value) = serde_json::from_str::<Value>(line) else {
                    return;
                };
                if value.get("event").and_then(Value::as_str) == Some(event::PACK)
                    && let Some(pack) = DiscoveredPack::from_value(&value)
                {
                    packs.push(pack);
                }
            },
            &|line| {
                let mut s = stderr.lock().unwrap_or_else(|p| p.into_inner());
                s.push_str(line);
                s.push('\n');
            },
            cancel,
        )?;
        if code != 0 {
            return Err(Error::kernel(
                format!("内核发现 Java 包失败(退出码 {code})"),
                Some(stderr.into_inner().unwrap_or_else(|p| p.into_inner())),
            ));
        }
        Ok(packs)
    }

    /// 跑一份任务单; 每个事件回调一次, 解析不了的 stdout 行当 stderr 转出。返回内核退出码(0 = 全部成功)。
    pub fn run_job(
        &self,
        job: &JobSpec,
        on_event: &mut dyn FnMut(KernelEvent),
        on_stderr: &(dyn Fn(&str) + Sync),
        cancel: &CancelToken,
    ) -> Result<i32> {
        let job_file = temp_dir()?.join(format!("job-{}.json", win::random_hex()));
        fs::write(&job_file, job.to_json())?;
        let _cleanup = DeleteOnDrop(job_file.clone());
        self.run(
            &["--job".to_string(), job_file.display().to_string()],
            &mut |line| match KernelEvent::try_parse(line) {
                Some(e) => on_event(e),
                None => on_stderr(line),
            },
            on_stderr,
            cancel,
        )
    }

    fn run(
        &self,
        args: &[String],
        on_stdout: &mut dyn FnMut(&str),
        on_stderr: &(dyn Fn(&str) + Sync),
        cancel: &CancelToken,
    ) -> Result<i32> {
        let port_cli = self.paths.port_cli();
        if !port_cli.is_file() {
            return Err(Error::kernel(format!("内核入口不存在: {}", port_cli.display()), None));
        }
        if cancel.is_cancelled() {
            return Err(Error::Cancelled);
        }
        let python = &self.paths.python_exe;
        let mut command = Command::new(python);
        command
            .args(&self.paths.python_args_prefix)
            .arg("-E") // 忽略 PYTHON* 环境变量(宿主机可能装着别的 Python)
            .arg("-s") // 不加载用户 site-packages
            .arg("-B") // 不写 .pyc(内核目录可能只读)
            .arg(&port_cli)
            .args(args)
            .current_dir(&self.paths.kernel_root)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = command.spawn().map_err(|error| launch_error(python, &error))?;
        // 进程树放进作业对象: 取消时整树终止, 壳退出(含崩溃)时系统同样整树终止
        let job = win::JobObject::new().map(Arc::new);
        if let Some(job) = &job {
            job.assign(&child);
        }
        let pid = child.id();
        let killer = job.clone();
        let _registration = cancel.register(move || match &killer {
            Some(job) => job.terminate(),
            None => kill_pid(pid),
        });

        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        let stderr_tail = std::thread::scope(|scope| {
            let tail = scope.spawn(move || {
                let mut tail = String::new();
                if let Some(stderr) = stderr {
                    for_each_line(stderr, |line| {
                        if tail.chars().count() < 4000 {
                            tail.push_str(line);
                            tail.push('\n');
                        }
                        on_stderr(line);
                    });
                }
                tail
            });
            if let Some(stdout) = stdout {
                for_each_line(stdout, |line| on_stdout(line));
            }
            tail.join().unwrap_or_default()
        });

        let status = child.wait()?;
        if cancel.is_cancelled() {
            return Err(Error::Cancelled);
        }
        let exit_code = status.code().unwrap_or(-1);
        // 进程起来了但立刻挂掉: 0xC015xxxx 一族是并行程序集(SxS)错误, 最常见的就是缺 VC++ 2008 运行库,
        // 此时 python27.dll 加载失败, stdout 一个字节都没有。业务退出码(0/1/2)不在这个区间。
        if is_side_by_side_failure(exit_code) {
            return Err(Error::kernel(
                MISSING_VC_RUNTIME_MESSAGE,
                Some(format!(
                    "{} 以 0x{:08X} 退出\n{stderr_tail}",
                    python.display(),
                    exit_code as u32
                )),
            ));
        }
        Ok(exit_code)
    }
}

fn launch_error(python: &Path, error: &std::io::Error) -> Error {
    match error.raw_os_error() {
        Some(ERROR_SXS_CANT_GEN_ACTCTX) => Error::kernel(
            MISSING_VC_RUNTIME_MESSAGE,
            Some(format!("{}\n{error}", python.display())),
        ),
        Some(ERROR_FILE_NOT_FOUND) => Error::kernel(format!("找不到 Python 解释器: {}", python.display()), None),
        Some(code) => Error::kernel(
            format!("无法启动 Python 子进程(Win32 错误 {code}): {}", python.display()),
            None,
        ),
        None => Error::kernel(
            format!("无法启动 Python 子进程: {}", python.display()),
            Some(error.to_string()),
        ),
    }
}

/// 作业对象建不出来时的退路: 只终止内核进程本身。
fn kill_pid(pid: u32) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .status();
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("kill").args(["-9", &pid.to_string()]).status();
    }
}

/// 按行读(\n 或 \r\n), 每行按 UTF-8 解码(坏字节换成替换符)。
fn for_each_line(reader: impl Read, mut f: impl FnMut(&str)) {
    let mut reader = BufReader::new(reader);
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match reader.read_until(b'\n', &mut buf) {
            Ok(0) | Err(_) => break,
            Ok(_) => {
                while matches!(buf.last(), Some(b'\n') | Some(b'\r')) {
                    buf.pop();
                }
                f(&String::from_utf8_lossy(&buf));
            }
        }
    }
}

fn temp_dir() -> Result<PathBuf> {
    let dir = std::env::temp_dir().join("YsmConvert");
    fs::create_dir_all(&dir)?;
    Ok(dir)
}

struct DeleteOnDrop(PathBuf);

impl Drop for DeleteOnDrop {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0); // 留着也无妨
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_side_by_side_exit_codes() {
        let cases: [(u32, bool); 8] = [
            (0xC015_0002, true), // STATUS_SXS_CANT_GEN_ACTCTX: 缺 VC++ 2008 运行库
            (0xC015_0004, true), // STATUS_SXS_ASSEMBLY_NOT_FOUND
            (0xC015_FFFF, true),
            (0, false), // 内核业务退出码
            (1, false),
            (2, false),
            (u32::MAX, false),    // -1
            (0xC000_0005, false), // 访问冲突, 不是 SxS
        ];
        for (code, expected) in cases {
            assert_eq!(is_side_by_side_failure(code as i32), expected, "{code:#x}");
        }
    }

    #[test]
    fn missing_runtime_message_names_the_fix() {
        assert!(MISSING_VC_RUNTIME_MESSAGE.contains("Visual C++ 2008"));
        assert!(MISSING_VC_RUNTIME_MESSAGE.contains("vcredist_x64"));
        assert!(MISSING_VC_RUNTIME_MESSAGE.contains("Microsoft.VC90.CRT"));
    }

    #[test]
    fn splits_lines_and_tolerates_bad_bytes() {
        let mut lines = Vec::new();
        for_each_line(&b"a\r\nb\n\xffc\nlast"[..], |l| lines.push(l.to_string()));
        assert_eq!(lines, vec!["a", "b", "\u{fffd}c", "last"]);
    }
}
