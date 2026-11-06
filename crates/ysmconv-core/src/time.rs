//! 本地时间戳(报告抬头、日志时间、导出文件名)。

/// 某一刻的本地时间与时区偏移。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LocalTime {
    pub year: i32,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
    /// 秒以下的 100 纳秒刻度(0..10_000_000)。
    pub ticks: u32,
    /// 本地时间相对 UTC 的偏移(分钟)。
    pub offset_minutes: i32,
}

impl LocalTime {
    pub fn now() -> Self {
        let (local_ticks, offset_minutes) = crate::win::local_now_ticks();
        Self::from_unix_ticks(local_ticks, offset_minutes)
    }

    /// 自 1970-01-01 起的 100 纳秒刻度(已是本地时间) → 年月日时分秒。
    fn from_unix_ticks(ticks: i64, offset_minutes: i32) -> Self {
        let secs = ticks.div_euclid(10_000_000);
        let sub = ticks.rem_euclid(10_000_000) as u32;
        let days = secs.div_euclid(86_400);
        let rem = secs.rem_euclid(86_400);
        let (year, month, day) = civil_from_days(days);
        LocalTime {
            year,
            month,
            day,
            hour: (rem / 3600) as u32,
            minute: (rem % 3600 / 60) as u32,
            second: (rem % 60) as u32,
            ticks: sub,
            offset_minutes,
        }
    }

    /// `yyyy-MM-dd HH:mm:ss`
    pub fn format_report(&self) -> String {
        format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )
    }

    /// ISO 8601 往返格式 `yyyy-MM-ddTHH:mm:ss.fffffff+08:00`。
    pub fn format_iso(&self) -> String {
        let sign = if self.offset_minutes < 0 { '-' } else { '+' };
        let offset = self.offset_minutes.unsigned_abs();
        format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:07}{sign}{:02}:{:02}",
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
            self.ticks,
            offset / 60,
            offset % 60
        )
    }

    /// `HH:mm:ss`
    pub fn format_clock(&self) -> String {
        format!("{:02}:{:02}:{:02}", self.hour, self.minute, self.second)
    }

    /// `yyyyMMdd-HHmmss`
    pub fn format_compact(&self) -> String {
        format!(
            "{:04}{:02}{:02}-{:02}{:02}{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )
    }
}

/// 1970-01-01 起的天数 → (年, 月, 日)(Howard Hinnant 的 civil_from_days)。
fn civil_from_days(days: i64) -> (i32, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    ((if m <= 2 { y + 1 } else { y }) as i32, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_unix_ticks() {
        // 2026-11-06 10:20:30.1234567 +08:00
        let secs: i64 = 1_793_960_430;
        let t = LocalTime::from_unix_ticks(secs * 10_000_000 + 1_234_567, 480);
        assert_eq!(t.format_report(), "2026-11-06 10:20:30");
        assert_eq!(t.format_iso(), "2026-11-06T10:20:30.1234567+08:00");
        assert_eq!(t.format_clock(), "10:20:30");
        assert_eq!(t.format_compact(), "20261106-102030");
        assert_eq!(
            LocalTime::from_unix_ticks(0, -150).format_iso(),
            "1970-01-01T00:00:00.0000000-02:30"
        );
        assert_eq!(civil_from_days(-1), (1969, 12, 31));
        assert_eq!(civil_from_days(19_782), (2024, 2, 29));
    }

    #[test]
    fn now_is_sane() {
        let t = LocalTime::now();
        assert!(t.year >= 2024 && (1..=12).contains(&t.month) && (1..=31).contains(&t.day));
        assert!(t.offset_minutes.abs() <= 14 * 60);
    }
}
