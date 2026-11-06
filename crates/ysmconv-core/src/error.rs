use std::fmt;

/// 壳层错误。分类决定调用方怎么呈现: 参数问题给"参数错误"、内核问题带细节、取消不算失败。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// 输入不合法(包名 / 材质写法 / 封面 / 命令行参数), 改了再来。
    Argument(String),
    /// 环境或目录状态不满足(找不到内核、输出目录里没有成对的行为包与资源包)。
    Invalid(String),
    /// 转换内核起不来或异常(缺 VC++ 2008 运行库、解释器缺失、没有响应); `details` 是给人排查的补充信息。
    Kernel { message: String, details: Option<String> },
    /// 用户取消。
    Cancelled,
    /// 读写文件失败。
    Io(String),
}

pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    pub fn kernel(message: impl Into<String>, details: Option<String>) -> Self {
        Error::Kernel {
            message: message.into(),
            details,
        }
    }

    /// 完整说明: 内核错误把细节接在后面(`separator` 决定接法: 报告里用 ": ", 弹框 / 终端里用换行)。
    pub fn full_text(&self, separator: &str) -> String {
        match self {
            Error::Kernel {
                message,
                details: Some(details),
            } => format!("{message}{separator}{details}"),
            other => other.to_string(),
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Argument(m) | Error::Invalid(m) | Error::Io(m) => f.write_str(m),
            Error::Kernel { message, .. } => f.write_str(message),
            Error::Cancelled => f.write_str("已取消"),
        }
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(error: std::io::Error) -> Self {
        Error::Io(error.to_string())
    }
}
