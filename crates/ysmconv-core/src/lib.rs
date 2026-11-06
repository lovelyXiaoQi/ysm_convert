//! YSM 转换器的壳层核心, GUI(`YsmConvert.exe`)与 CLI / MCP(`ysmconv.exe`)共用。
//!
//! 转换规则本身在 Python 2.7 内核里(YSM 工程的 `devtools/port_cli.py` 一族, 直接 import 游戏运行时的
//! `packParser`, 产物与游戏解析口径一致), 这里只做编排: 定位内核、起子进程、写任务单、解析 JSON Lines 事件流、
//! 汇总报告、解释告警、新建组件目录。

pub mod cancel;
pub mod catalog;
pub mod docs;
pub mod error;
pub mod event;
pub mod jsonc;
pub mod links;
pub mod locator;
pub mod material;
pub mod models;
pub mod molang;
pub mod names;
pub mod output;
pub mod report;
pub mod runner;
pub mod service;
pub mod time;
pub mod win;

pub use cancel::{CancelToken, Registration};
pub use error::{Error, Result};
pub use event::KernelEvent;
pub use locator::{KernelLocator, KernelPaths};
pub use models::{
    CollectionSpec, ConvertOptions, DiscoveredPack, JobSpec, KernelInfo, MaterialProfile, OutputLayout, PackSpec,
};
pub use report::ConversionReport;
pub use runner::KernelRunner;
pub use service::{ConversionService, ConvertPlan, ConvertRequest};

/// 程序版本: 发版流水线经环境变量 `YSMCONV_VERSION` 注入(按 tag 递增), 本地构建取工作区 Cargo.toml 的版本。
pub const VERSION: &str = match option_env!("YSMCONV_VERSION") {
    Some(v) => v,
    None => env!("CARGO_PKG_VERSION"),
};
