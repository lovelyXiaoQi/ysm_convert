//! Windows 接口的薄封装: 可用内存、作业对象(内核进程树的生命周期)、外壳打开、随机数、本地时间。
//! 非 Windows 平台给出无害的退化实现, 只为 `cargo check` / 单元测试能跑。

/// 系统可用内存(GB): 可用物理内存与可用提交额度取小; 取不到时返回 0。
pub fn available_memory_gb() -> f64 {
    imp::available_memory_gb()
}

/// 用系统外壳打开网址 / 目录 / 文件。
pub fn shell_open(target: &str) -> Result<(), String> {
    imp::shell_open(target)
}

/// 随机 UUID v4(小写, 带连字符)。
pub fn uuid_v4() -> String {
    let mut b = [0u8; 16];
    imp::random_bytes(&mut b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let hex: String = b.iter().map(|x| format!("{x:02x}")).collect();
    format!(
        "{}-{}-{}-{}-{}",
        &hex[0..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..32]
    )
}

/// 32 位十六进制随机串(临时文件名用)。
pub fn random_hex() -> String {
    let mut b = [0u8; 16];
    imp::random_bytes(&mut b);
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// (本地时间自 1970-01-01 起的 100 纳秒刻度, 本地相对 UTC 的偏移分钟)。
pub fn local_now_ticks() -> (i64, i32) {
    imp::local_now_ticks()
}

pub use imp::JobObject;

#[cfg(windows)]
mod imp {
    use std::os::windows::io::AsRawHandle;
    use std::ptr::null;

    use windows_sys::Win32::Foundation::{CloseHandle, FILETIME, HANDLE};
    use windows_sys::Win32::Security::Cryptography::{BCRYPT_USE_SYSTEM_PREFERRED_RNG, BCryptGenRandom};
    use windows_sys::Win32::Storage::FileSystem::FileTimeToLocalFileTime;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation, SetInformationJobObject,
        TerminateJobObject,
    };
    use windows_sys::Win32::System::SystemInformation::{
        GetSystemTimePreciseAsFileTime, GlobalMemoryStatusEx, MEMORYSTATUSEX,
    };
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

    /// FILETIME 纪元(1601-01-01)到 Unix 纪元的 100 纳秒刻度数。
    const EPOCH_DIFF_TICKS: i64 = 116_444_736_000_000_000;

    pub fn available_memory_gb() -> f64 {
        let mut status: MEMORYSTATUSEX = unsafe { std::mem::zeroed() };
        status.dwLength = std::mem::size_of::<MEMORYSTATUSEX>() as u32;
        if unsafe { GlobalMemoryStatusEx(&mut status) } == 0 {
            return 0.0;
        }
        status.ullAvailPhys.min(status.ullAvailPageFile) as f64 / (1024.0 * 1024.0 * 1024.0)
    }

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    pub fn shell_open(target: &str) -> Result<(), String> {
        let verb = wide("open");
        let file = wide(target);
        let result = unsafe {
            ShellExecuteW(
                std::ptr::null_mut(),
                verb.as_ptr(),
                file.as_ptr(),
                null(),
                null(),
                SW_SHOWNORMAL,
            )
        };
        // 返回值 > 32 表示成功(HINSTANCE 的历史约定)
        if result as usize > 32 {
            Ok(())
        } else {
            Err(format!("ShellExecute 失败(代码 {})", result as usize))
        }
    }

    pub fn random_bytes(buf: &mut [u8]) {
        let status = unsafe {
            BCryptGenRandom(
                std::ptr::null_mut(),
                buf.as_mut_ptr(),
                buf.len() as u32,
                BCRYPT_USE_SYSTEM_PREFERRED_RNG,
            )
        };
        if status != 0 {
            super::fallback_random(buf);
        }
    }

    fn to_ticks(ft: FILETIME) -> i64 {
        (((ft.dwHighDateTime as u64) << 32) | ft.dwLowDateTime as u64) as i64
    }

    pub fn local_now_ticks() -> (i64, i32) {
        let mut utc = FILETIME {
            dwLowDateTime: 0,
            dwHighDateTime: 0,
        };
        unsafe { GetSystemTimePreciseAsFileTime(&mut utc) };
        let mut local = FILETIME {
            dwLowDateTime: 0,
            dwHighDateTime: 0,
        };
        if unsafe { FileTimeToLocalFileTime(&utc, &mut local) } == 0 {
            local = utc;
        }
        let utc_ticks = to_ticks(utc);
        let local_ticks = to_ticks(local);
        let offset_minutes = ((local_ticks - utc_ticks) as f64 / 600_000_000.0).round() as i32;
        (local_ticks - EPOCH_DIFF_TICKS, offset_minutes)
    }

    /// 作业对象: 内核子进程及其后代放进来, 取消时整树终止; 句柄关闭(含壳进程意外退出)时系统同样整树终止,
    /// 不会留下孤儿 Python 进程。
    pub struct JobObject(HANDLE);

    // 句柄只在拥有者线程创建 / 关闭, 终止操作对句柄是线程安全的
    unsafe impl Send for JobObject {}
    unsafe impl Sync for JobObject {}

    impl JobObject {
        pub fn new() -> Option<JobObject> {
            let handle = unsafe { CreateJobObjectW(null(), null()) };
            if handle.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = unsafe {
                SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const core::ffi::c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            };
            if ok == 0 {
                unsafe { CloseHandle(handle) };
                return None;
            }
            Some(JobObject(handle))
        }

        pub fn assign(&self, child: &std::process::Child) -> bool {
            unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) != 0 }
        }

        pub fn terminate(&self) {
            unsafe { TerminateJobObject(self.0, 1) };
        }
    }

    impl Drop for JobObject {
        fn drop(&mut self) {
            unsafe { CloseHandle(self.0) };
        }
    }
}

#[cfg(not(windows))]
mod imp {
    pub fn available_memory_gb() -> f64 {
        0.0
    }

    pub fn shell_open(target: &str) -> Result<(), String> {
        Err(format!("当前平台不支持打开: {target}"))
    }

    pub fn random_bytes(buf: &mut [u8]) {
        super::fallback_random(buf);
    }

    pub fn local_now_ticks() -> (i64, i32) {
        let ticks = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| (d.as_nanos() / 100) as i64)
            .unwrap_or(0);
        (ticks, 0)
    }

    pub struct JobObject;

    impl JobObject {
        pub fn new() -> Option<JobObject> {
            None
        }
        pub fn assign(&self, _child: &std::process::Child) -> bool {
            false
        }
        pub fn terminate(&self) {}
    }
}

/// 系统随机源不可用时的退路(只用于临时文件名 / uuid 的唯一性, 不涉及安全)。
fn fallback_random(buf: &mut [u8]) {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    let state = RandomState::new();
    for (i, chunk) in buf.chunks_mut(8).enumerate() {
        let mut hasher = state.build_hasher();
        hasher.write_usize(i);
        hasher.write_u128(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0),
        );
        let bytes = hasher.finish().to_le_bytes();
        chunk.copy_from_slice(&bytes[..chunk.len()]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uuid_has_version_and_variant() {
        let id = uuid_v4();
        assert_eq!(id.len(), 36);
        assert_eq!(&id[14..15], "4");
        assert!(matches!(&id[19..20], "8" | "9" | "a" | "b"));
        assert_ne!(uuid_v4(), id);
        assert_eq!(random_hex().len(), 32);
    }

    #[test]
    fn memory_is_reported_on_windows() {
        let gb = available_memory_gb();
        if cfg!(windows) {
            assert!(gb > 0.0);
        }
    }
}
