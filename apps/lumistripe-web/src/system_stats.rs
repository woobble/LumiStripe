//! Cached Linux host statistics for the dashboard.

use serde::Serialize;
use std::fs;
use std::path::PathBuf;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

const CPU_COUNTER_COUNT: usize = 8;
const KIBIBYTE: u64 = 1024;
const SAMPLE_INTERVAL: Duration = Duration::from_secs(5);

#[derive(Clone, Debug, Serialize, utoipa::ToSchema)]
pub(crate) struct SystemStats {
    #[schema(required = true)]
    pub cpu_temperature_c: Option<f64>,
    #[schema(required = true, minimum = 0.0, maximum = 100.0)]
    pub cpu_usage_percent: Option<f64>,
    #[schema(required = true, minimum = 0)]
    pub memory_used_bytes: Option<u64>,
    #[schema(required = true, minimum = 0)]
    pub memory_total_bytes: Option<u64>,
    #[schema(required = true, minimum = 0.0, maximum = 100.0)]
    pub memory_usage_percent: Option<f64>,
    #[schema(required = true, minimum = 0)]
    pub disk_used_bytes: Option<u64>,
    #[schema(required = true, minimum = 0)]
    pub disk_total_bytes: Option<u64>,
    #[schema(required = true, minimum = 0.0, maximum = 100.0)]
    pub disk_usage_percent: Option<f64>,
    #[schema(required = true, minimum = 0.0)]
    pub uptime_seconds: Option<f64>,
    #[schema(required = true, minimum = 0)]
    pub process_memory_used_bytes: Option<u64>,
    #[schema(required = true, minimum = 0)]
    pub process_thread_count: Option<u64>,
    #[schema(format = DateTime)]
    pub sampled_at: String,
}

impl SystemStats {
    fn unavailable() -> Self {
        Self {
            cpu_temperature_c: None,
            cpu_usage_percent: None,
            memory_used_bytes: None,
            memory_total_bytes: None,
            memory_usage_percent: None,
            disk_used_bytes: None,
            disk_total_bytes: None,
            disk_usage_percent: None,
            uptime_seconds: None,
            process_memory_used_bytes: None,
            process_thread_count: None,
            sampled_at: utc_timestamp(SystemTime::now()),
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct SystemStatsCollector {
    proc_root: PathBuf,
    thermal_root: PathBuf,
    disk_path: PathBuf,
    previous_cpu: Option<(u64, u64)>,
}

impl Default for SystemStatsCollector {
    fn default() -> Self {
        Self::new("/proc", "/sys/class/thermal", "/")
    }
}

impl SystemStatsCollector {
    pub(crate) fn new(
        proc_root: impl Into<PathBuf>,
        thermal_root: impl Into<PathBuf>,
        disk_path: impl Into<PathBuf>,
    ) -> Self {
        Self {
            proc_root: proc_root.into(),
            thermal_root: thermal_root.into(),
            disk_path: disk_path.into(),
            previous_cpu: None,
        }
    }

    fn collect(&mut self) -> SystemStats {
        let current_cpu = self.read_cpu_counters();
        let cpu_usage_percent = self.cpu_usage(current_cpu);
        if current_cpu.is_some() {
            self.previous_cpu = current_cpu;
        }
        let (memory_used_bytes, memory_total_bytes, memory_usage_percent) = self.read_memory();
        let (disk_used_bytes, disk_total_bytes, disk_usage_percent) = self.read_disk();
        let (process_memory_used_bytes, process_thread_count) = self.read_process();
        SystemStats {
            cpu_temperature_c: self.read_temperature(),
            cpu_usage_percent,
            memory_used_bytes,
            memory_total_bytes,
            memory_usage_percent,
            disk_used_bytes,
            disk_total_bytes,
            disk_usage_percent,
            uptime_seconds: self.read_uptime(),
            process_memory_used_bytes,
            process_thread_count,
            sampled_at: utc_timestamp(SystemTime::now()),
        }
    }

    fn read_cpu_counters(&self) -> Option<(u64, u64)> {
        let stat = fs::read_to_string(self.proc_root.join("stat")).ok()?;
        let cpu_line = stat.lines().find(|line| line.starts_with("cpu "))?;
        let counters = cpu_line
            .split_ascii_whitespace()
            .skip(1)
            .take(CPU_COUNTER_COUNT)
            .map(str::parse::<u64>)
            .collect::<Result<Vec<_>, _>>()
            .ok()?;
        if counters.len() < 4 {
            return None;
        }
        let total = counters.iter().sum();
        let idle = counters[3] + counters.get(4).copied().unwrap_or_default();
        Some((total, idle))
    }

    fn cpu_usage(&self, current: Option<(u64, u64)>) -> Option<f64> {
        let current = current?;
        let previous = self.previous_cpu?;
        let total_delta = current.0.checked_sub(previous.0)?;
        let idle_delta = current.1.checked_sub(previous.1)?;
        if total_delta == 0 {
            return None;
        }
        Some(round_one_decimal(
            (100.0 * (1.0 - idle_delta as f64 / total_delta as f64)).clamp(0.0, 100.0),
        ))
    }

    fn read_memory(&self) -> (Option<u64>, Option<u64>, Option<f64>) {
        let Ok(contents) = fs::read_to_string(self.proc_root.join("meminfo")) else {
            return (None, None, None);
        };
        let mut total: Option<u64> = None;
        let mut available: Option<u64> = None;
        for line in contents.lines() {
            let Some((key, value)) = line.split_once(':') else {
                continue;
            };
            let destination = match key {
                "MemTotal" => &mut total,
                "MemAvailable" => &mut available,
                _ => continue,
            };
            let Some(kibibytes) = value
                .split_ascii_whitespace()
                .next()
                .and_then(|v| v.parse::<u64>().ok())
            else {
                return (None, None, None);
            };
            *destination = kibibytes.checked_mul(KIBIBYTE);
        }
        let (Some(total), Some(available)) = (total, available) else {
            return (None, None, None);
        };
        if total == 0 {
            return (None, None, None);
        }
        let used = total.saturating_sub(available.min(total));
        (
            Some(used),
            Some(total),
            Some(round_one_decimal(100.0 * used as f64 / total as f64)),
        )
    }

    fn read_disk(&self) -> (Option<u64>, Option<u64>, Option<f64>) {
        #[cfg(target_os = "linux")]
        {
            let path = std::ffi::CString::new(self.disk_path.as_os_str().as_encoded_bytes()).ok();
            let Some(path) = path else {
                return (None, None, None);
            };
            // SAFETY: `path` is NUL terminated and `stats` is writable storage.
            let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
            let result = unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) };
            if result != 0 {
                return (None, None, None);
            }
            // SAFETY: statvfs initialized the structure on success.
            let stats = unsafe { stats.assume_init() };
            return disk_usage_from_blocks(
                stats.f_blocks as u64,
                stats.f_bfree as u64,
                stats.f_frsize as u64,
            );
        }
        #[cfg(not(target_os = "linux"))]
        {
            (None, None, None)
        }
    }

    fn read_uptime(&self) -> Option<f64> {
        let contents = fs::read_to_string(self.proc_root.join("uptime")).ok()?;
        let uptime = contents
            .split_ascii_whitespace()
            .next()?
            .parse::<f64>()
            .ok()?;
        (uptime.is_finite() && uptime >= 0.0).then_some(uptime)
    }

    fn read_process(&self) -> (Option<u64>, Option<u64>) {
        let Ok(contents) = fs::read_to_string(self.proc_root.join("self/status")) else {
            return (None, None);
        };
        let mut resident_bytes = None;
        let mut thread_count = None;
        for line in contents.lines() {
            let Some((key, value)) = line.split_once(':') else {
                continue;
            };
            let Some(number) = value
                .split_ascii_whitespace()
                .next()
                .and_then(|v| v.parse::<u64>().ok())
            else {
                continue;
            };
            match key {
                "VmRSS" => resident_bytes = number.checked_mul(KIBIBYTE),
                "Threads" => thread_count = Some(number),
                _ => {}
            }
        }
        (resident_bytes, thread_count)
    }

    fn read_temperature(&self) -> Option<f64> {
        let entries = fs::read_dir(&self.thermal_root).ok()?;
        let mut candidates = Vec::new();
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if !name.starts_with("thermal_zone") {
                continue;
            }
            let zone = entry.path();
            let Ok(sensor_type) = fs::read_to_string(zone.join("type")) else {
                continue;
            };
            let Ok(raw) = fs::read_to_string(zone.join("temp")) else {
                continue;
            };
            let Ok(raw) = raw.trim().parse::<f64>() else {
                continue;
            };
            let temperature = if raw.abs() > 200.0 { raw / 1000.0 } else { raw };
            if !(-50.0..=150.0).contains(&temperature) {
                continue;
            }
            let sensor_type = sensor_type.trim().to_ascii_lowercase();
            let priority = if sensor_type.contains("cpu") {
                0
            } else if sensor_type.contains("soc") || sensor_type.contains("package") {
                1
            } else {
                2
            };
            candidates.push((priority, name.into_owned(), temperature));
        }
        candidates.sort_by(|left, right| left.0.cmp(&right.0).then(left.1.cmp(&right.1)));
        candidates
            .first()
            .map(|candidate| round_one_decimal(candidate.2))
    }
}

pub(crate) fn spawn_system_stats_publisher(
    cancellation: CancellationToken,
) -> (watch::Receiver<SystemStats>, JoinHandle<()>) {
    let (publisher, receiver) = watch::channel(SystemStats::unavailable());
    let task = tokio::spawn(async move {
        let mut collector = SystemStatsCollector::default();
        let mut interval = tokio::time::interval(SAMPLE_INTERVAL);
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        interval.tick().await;
        loop {
            let (next_collector, sample) = match tokio::task::spawn_blocking(move || {
                let sample = collector.collect();
                (collector, sample)
            })
            .await
            {
                Ok(result) => result,
                Err(error) => {
                    tracing::warn!(error = %error, "system statistics sampling task failed");
                    break;
                }
            };
            collector = next_collector;
            publisher.send_replace(sample);
            tokio::select! {
                _ = cancellation.cancelled() => break,
                _ = interval.tick() => {}
            }
        }
    });
    (receiver, task)
}

fn disk_usage_from_blocks(
    total_blocks: u64,
    free_blocks: u64,
    block_size: u64,
) -> (Option<u64>, Option<u64>, Option<f64>) {
    let Some(total) = total_blocks.checked_mul(block_size) else {
        return (None, None, None);
    };
    let Some(free) = free_blocks.checked_mul(block_size) else {
        return (None, None, None);
    };
    if total == 0 {
        return (None, None, None);
    }
    let used = total.saturating_sub(free.min(total));
    (
        Some(used),
        Some(total),
        Some(round_one_decimal(100.0 * used as f64 / total as f64)),
    )
}

fn round_one_decimal(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

pub(crate) fn utc_timestamp(time: SystemTime) -> String {
    let seconds = time
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    let days = seconds.div_euclid(86_400);
    let second_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = second_of_day / 3_600;
    let minute = second_of_day % 3_600 / 60;
    let second = second_of_day % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

// Gregorian calendar conversion for days relative to 1970-01-01.
fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let days = days_since_epoch + 719_468;
    let era = if days >= 0 {
        days / 146_097
    } else {
        (days - 146_096) / 146_097
    };
    let day_of_era = days - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    (year, month, day)
}
#[cfg(test)]
mod tests {
    use super::{SystemStatsCollector, civil_from_days, disk_usage_from_blocks, utc_timestamp};
    use std::fs;
    use std::time::{Duration, UNIX_EPOCH};
    use tempfile::TempDir;

    fn write_proc(root: &std::path::Path, cpu: &str) {
        fs::create_dir_all(root).unwrap();
        fs::write(root.join("stat"), format!("{cpu}\ncpu0 0 0 0 0\n")).unwrap();
        fs::write(
            root.join("meminfo"),
            "MemTotal:       1024 kB\nMemAvailable:    256 kB\n",
        )
        .unwrap();
        fs::write(root.join("uptime"), "1234.5 500.0\n").unwrap();
        fs::create_dir_all(root.join("self")).unwrap();
        fs::write(
            root.join("self/status"),
            "Name:\ttest\nVmRSS:\t4096 kB\nThreads:\t5\n",
        )
        .unwrap();
    }

    #[test]
    fn reads_linux_metrics_and_cpu_deltas() {
        let temp = TempDir::new().unwrap();
        let proc_root = temp.path().join("proc");
        let thermal_root = temp.path().join("thermal");
        let cpu_zone = thermal_root.join("thermal_zone0");
        fs::create_dir_all(&cpu_zone).unwrap();
        fs::write(cpu_zone.join("type"), "cpu-thermal\n").unwrap();
        fs::write(cpu_zone.join("temp"), "53500\n").unwrap();
        write_proc(&proc_root, "cpu 100 0 50 800 0 0 0 0");
        let mut collector = SystemStatsCollector::new(&proc_root, &thermal_root, temp.path());

        let first = collector.collect();
        assert_eq!(first.cpu_temperature_c, Some(53.5));
        assert_eq!(first.cpu_usage_percent, None);
        assert_eq!(first.memory_used_bytes, Some(768 * 1024));
        assert_eq!(first.memory_total_bytes, Some(1024 * 1024));
        assert_eq!(first.memory_usage_percent, Some(75.0));
        assert!(first.disk_used_bytes.is_some());
        assert!(first.disk_total_bytes.is_some());
        assert!(first.disk_usage_percent.is_some());
        assert_eq!(first.uptime_seconds, Some(1234.5));
        assert_eq!(first.process_memory_used_bytes, Some(4 * 1024 * 1024));
        assert_eq!(first.process_thread_count, Some(5));
        assert!(first.sampled_at.ends_with('Z'));

        write_proc(&proc_root, "cpu 200 0 100 900 0 0 0 0");
        let second = collector.collect();
        assert_eq!(second.cpu_usage_percent, Some(60.0));
    }

    #[test]
    fn unavailable_sources_are_reported_as_null() {
        let temp = TempDir::new().unwrap();
        let mut collector = SystemStatsCollector::new(
            temp.path().join("missing-proc"),
            temp.path().join("missing-thermal"),
            temp.path().join("missing-disk"),
        );
        let stats = collector.collect();
        assert_eq!(stats.cpu_temperature_c, None);
        assert_eq!(stats.cpu_usage_percent, None);
        assert_eq!(stats.memory_used_bytes, None);
        assert_eq!(stats.memory_total_bytes, None);
        assert_eq!(stats.memory_usage_percent, None);
        assert_eq!(stats.disk_used_bytes, None);
        assert_eq!(stats.disk_total_bytes, None);
        assert_eq!(stats.disk_usage_percent, None);
        assert_eq!(stats.uptime_seconds, None);
        assert_eq!(stats.process_memory_used_bytes, None);
        assert_eq!(stats.process_thread_count, None);
    }

    #[test]
    #[ignore = "reads live Linux system statistics from the Raspberry Pi"]
    fn live_linux_metrics_are_available() {
        let mut collector = SystemStatsCollector::default();
        let _ = collector.collect();
        std::thread::sleep(Duration::from_millis(50));
        let stats = collector.collect();

        assert!(stats.cpu_temperature_c.is_some());
        assert!(stats.cpu_usage_percent.is_some());
        assert!(stats.memory_used_bytes.is_some());
        assert!(stats.memory_total_bytes.is_some());
        assert!(stats.memory_usage_percent.is_some());
        assert!(stats.disk_used_bytes.is_some());
        assert!(stats.disk_total_bytes.is_some());
        assert!(stats.disk_usage_percent.is_some());
        assert!(stats.uptime_seconds.is_some());
        assert!(stats.process_memory_used_bytes.is_some());
        assert!(stats.process_thread_count.is_some());
        assert!(stats.sampled_at.ends_with('Z'));
    }

    #[test]
    fn disk_usage_counts_reserved_blocks_as_used_like_python() {
        // `shutil.disk_usage` bases used space on f_bfree, while f_bavail
        // excludes blocks reserved for privileged processes.
        assert_eq!(
            disk_usage_from_blocks(100, 20, 4096),
            (Some(80 * 4096), Some(100 * 4096), Some(80.0))
        );
    }

    #[test]
    fn utc_timestamp_uses_rfc3339_calendar_dates() {
        assert_eq!(utc_timestamp(UNIX_EPOCH), "1970-01-01T00:00:00Z");
        assert_eq!(
            utc_timestamp(UNIX_EPOCH + Duration::from_secs(1_767_225_600)),
            "2026-01-01T00:00:00Z"
        );
        assert_eq!(civil_from_days(0), (1970, 1, 1));
    }
}
