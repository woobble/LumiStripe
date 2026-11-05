use clap::{Parser, Subcommand};
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::SystemTime;

#[cfg(test)]
pub(crate) static TEST_SUBPROCESS_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

#[derive(Debug, Parser)]
#[command(
    name = "lumistripe-deploy-helper",
    version,
    about = "Restricted LumiStripe deployment utilities"
)]
struct Arguments {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Check that the installed Spotify Soloist build is current enough to run.
    SoloistCheck {
        /// Path to the installed Soloist executable.
        #[arg(long)]
        binary: PathBuf,
    },
    /// Read and atomically update the system updater status document.
    UpdateState {
        #[command(subcommand)]
        action: UpdateStateAction,
    },
    /// Atomically update the non-secret Spotify player name override.
    SpotifyDeviceName {
        #[arg(allow_hyphen_values = true)]
        name: String,
    },
    /// Serve one bounded NetworkManager JSON request from standard input.
    Network,
    /// Download and safely extract the official Spotify Soloist archive.
    SoloistDownload {
        #[arg(long)]
        url: String,
        #[arg(long)]
        archive: PathBuf,
        #[arg(long)]
        destination: PathBuf,
    },
    /// Safely extract a Soloist executable from a local archive.
    SoloistExtract {
        archive: PathBuf,
        destination: PathBuf,
    },
}

#[derive(Debug, Subcommand)]
enum UpdateStateAction {
    Initialize(UpdateStateArguments),
    Check(UpdateStateArguments),
    InstallStart(UpdateStateArguments),
    InstallSucceeded(UpdateStateArguments),
    InstallFailed(UpdateStateArguments),
}

#[derive(clap::Args, Debug)]
struct UpdateStateArguments {
    #[arg(long)]
    project_dir: PathBuf,
    #[arg(long)]
    state_file: PathBuf,
}

#[tokio::main]
async fn main() -> ExitCode {
    let arguments = Arguments::parse();
    match arguments.command {
        Command::SoloistCheck { binary } => {
            match soloist::validate_soloist_binary(
                &binary,
                SystemTime::now(),
                soloist::VERSION_TIMEOUT,
            )
            .await
            {
                Ok(build) => {
                    let now = SystemTime::now();
                    println!("{}", build.status_message(now));
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("Spotify Soloist check failed: {error}");
                    ExitCode::FAILURE
                }
            }
        }
        Command::UpdateState { action } => {
            match update_state::run(action, SystemTime::now()).await {
                Ok(()) => ExitCode::SUCCESS,
                Err(_) => {
                    eprintln!("Could not write LumiStripe update status.");
                    ExitCode::FAILURE
                }
            }
        }
        Command::SpotifyDeviceName { name } => match spotify_device_name::run(&name) {
            Ok(()) => ExitCode::SUCCESS,
            Err(_) => {
                eprintln!("Could not update the Spotify player name.");
                ExitCode::FAILURE
            }
        },
        Command::Network => {
            network_helper::run_stdin().await;
            ExitCode::SUCCESS
        }
        Command::SoloistDownload {
            url,
            archive,
            destination,
        } => match soloist_archive::download_and_extract(&url, &archive, &destination).await {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("Could not safely download or extract Soloist: {error}");
                ExitCode::FAILURE
            }
        },
        Command::SoloistExtract {
            archive,
            destination,
        } => match soloist_archive::extract(&archive, &destination) {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("Could not safely extract Soloist: {error}");
                ExitCode::FAILURE
            }
        },
    }
}

mod network_helper;
mod soloist_archive;
mod spotify_device_name;
mod update_state;
mod soloist {
    use regex::Regex;
    use std::fs;
    use std::io;
    use std::path::Path;
    use std::process::Stdio;
    use std::sync::OnceLock;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};
    use tokio::io::{AsyncRead, AsyncReadExt};
    use tokio::process::Command;
    use tokio::time::timeout;

    pub const VERSION_TIMEOUT: Duration = Duration::from_secs(5);
    const BUILD_LIFETIME: Duration = Duration::from_secs(90 * 24 * 60 * 60);
    const WARNING_WINDOW: Duration = Duration::from_secs(7 * 24 * 60 * 60);
    const MAX_VERSION_OUTPUT_BYTES: usize = 16 * 1024;

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct SoloistBuild {
        pub built_at: SystemTime,
        pub expires_at: SystemTime,
        built_at_date: String,
    }

    impl SoloistBuild {
        pub fn status_message(&self, now: SystemTime) -> String {
            let remaining = self.expires_at.duration_since(now).unwrap_or_default();
            let remaining_days = remaining
                .as_secs()
                .saturating_add(86_399)
                .checked_div(86_400)
                .unwrap_or(0)
                .max(1);
            let mut message = format!(
                "Spotify Soloist build from {} is valid for {remaining_days} more day(s).",
                self.built_at_date
            );
            if remaining <= WARNING_WINDOW {
                message.push_str(" Update it soon with deploy/update.sh.");
            }
            message
        }
    }

    #[derive(Debug)]
    pub struct SoloistDiagnosticError(String);

    impl std::fmt::Display for SoloistDiagnosticError {
        fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str(&self.0)
        }
    }

    impl std::error::Error for SoloistDiagnosticError {}

    impl SoloistDiagnosticError {
        fn new(message: impl Into<String>) -> Self {
            Self(message.into())
        }
    }

    pub async fn validate_soloist_binary(
        executable: &Path,
        now: SystemTime,
        timeout_duration: Duration,
    ) -> Result<SoloistBuild, SoloistDiagnosticError> {
        validate_executable(executable)?;
        let output = run_version(executable, timeout_duration).await?;
        let (epoch, printed_date) = parse_build(&output).ok_or_else(|| {
            SoloistDiagnosticError::new(
                "Spotify Soloist --version did not include a recognized build timestamp; run deploy/update.sh.",
            )
        })?;

        let built_at = UNIX_EPOCH
            .checked_add(Duration::from_secs(epoch))
            .ok_or_else(|| {
                SoloistDiagnosticError::new(
                    "Spotify Soloist reported an invalid build timestamp; run deploy/update.sh.",
                )
            })?;
        let built_at_date = utc_date(epoch).ok_or_else(|| {
            SoloistDiagnosticError::new(
                "Spotify Soloist reported an invalid build timestamp; run deploy/update.sh.",
            )
        })?;
        if printed_date
            .as_deref()
            .is_some_and(|date| date != built_at_date.replace('-', ""))
        {
            return Err(SoloistDiagnosticError::new(
                "Spotify Soloist reported inconsistent build timestamp fields; run deploy/update.sh.",
            ));
        }

        let expires_at = built_at.checked_add(BUILD_LIFETIME).ok_or_else(|| {
            SoloistDiagnosticError::new(
                "Spotify Soloist reported an invalid build timestamp; run deploy/update.sh.",
            )
        })?;
        if now >= expires_at {
            let expiry_epoch = epoch.checked_add(BUILD_LIFETIME.as_secs()).ok_or_else(|| {
                SoloistDiagnosticError::new(
                    "Spotify Soloist reported an invalid build timestamp; run deploy/update.sh.",
                )
            })?;
            return Err(SoloistDiagnosticError::new(format!(
                "Spotify Soloist build expired on {} UTC; run deploy/update.sh to download the current official build.",
                utc_date(expiry_epoch).unwrap_or_default()
            )));
        }

        Ok(SoloistBuild {
            built_at,
            expires_at,
            built_at_date,
        })
    }

    fn validate_executable(executable: &Path) -> Result<(), SoloistDiagnosticError> {
        let metadata = fs::metadata(executable).map_err(|_| {
            SoloistDiagnosticError::new(format!(
                "Spotify Soloist is missing or unreadable at {}; run deploy/update.sh to install the official build.",
                executable.display()
            ))
        })?;
        if !metadata.is_file() || !is_executable(&metadata) {
            return Err(SoloistDiagnosticError::new(format!(
                "Spotify Soloist is missing or not executable at {}; run deploy/update.sh to install the official build.",
                executable.display()
            )));
        }
        Ok(())
    }

    #[cfg(unix)]
    fn is_executable(metadata: &fs::Metadata) -> bool {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }

    #[cfg(not(unix))]
    fn is_executable(_metadata: &fs::Metadata) -> bool {
        false
    }

    async fn run_version(
        executable: &Path,
        timeout_duration: Duration,
    ) -> Result<String, SoloistDiagnosticError> {
        #[cfg(test)]
        let _test_subprocess_guard = crate::TEST_SUBPROCESS_LOCK.lock().await;
        let mut command = Command::new(executable);
        command
            .arg("--version")
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("LC_ALL", "C")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let mut child = command.spawn().map_err(|_| {
            SoloistDiagnosticError::new(
                "Spotify Soloist could not be run for its version check; run deploy/update.sh.",
            )
        })?;

        let stdout = child.stdout.take().ok_or_else(|| {
            SoloistDiagnosticError::new("Spotify Soloist version output was unavailable.")
        })?;
        let stderr = child.stderr.take().ok_or_else(|| {
            SoloistDiagnosticError::new("Spotify Soloist version output was unavailable.")
        })?;
        let mut stdout_task = tokio::spawn(read_bounded(stdout));
        let mut stderr_task = tokio::spawn(read_bounded(stderr));

        let result = timeout(timeout_duration, async {
            let status = child.wait().await?;
            let stdout = (&mut stdout_task)
                .await
                .map_err(|_| io::Error::other("stdout reader task failed"))??;
            let stderr = (&mut stderr_task)
                .await
                .map_err(|_| io::Error::other("stderr reader task failed"))??;
            Ok::<_, io::Error>((status, stdout, stderr))
        })
        .await;
        let (status, stdout, stderr) = match result {
            Ok(Ok(result)) => result,
            Ok(Err(error)) => {
                return Err(SoloistDiagnosticError::new(format!(
                    "Spotify Soloist version check failed ({error}); run deploy/update.sh."
                )));
            }
            Err(_) => {
                let _ = child.kill().await;
                let _ = child.wait().await;
                stdout_task.abort();
                stderr_task.abort();
                return Err(SoloistDiagnosticError::new(format!(
                    "Spotify Soloist --version did not finish within {} seconds; run deploy/update.sh.",
                    timeout_duration.as_secs_f64()
                )));
            }
        };
        if stdout.truncated
            || stderr.truncated
            || stdout.bytes.len().saturating_add(stderr.bytes.len()) > MAX_VERSION_OUTPUT_BYTES
        {
            return Err(SoloistDiagnosticError::new(
                "Spotify Soloist --version output exceeded the 16 KiB limit; run deploy/update.sh.",
            ));
        }
        if !status.success() {
            return Err(SoloistDiagnosticError::new(format!(
                "Spotify Soloist --version failed with exit status {}; run deploy/update.sh.",
                status.code().unwrap_or(-1)
            )));
        }

        let mut output = stdout.bytes;
        if !stderr.bytes.is_empty() {
            output.push(b'\n');
            output.extend(stderr.bytes);
        }
        Ok(String::from_utf8_lossy(&output).into_owned())
    }

    struct BoundedOutput {
        bytes: Vec<u8>,
        truncated: bool,
    }

    async fn read_bounded<R: AsyncRead + Unpin>(mut reader: R) -> io::Result<BoundedOutput> {
        let mut output = Vec::with_capacity(MAX_VERSION_OUTPUT_BYTES);
        let mut buffer = [0_u8; 4096];
        let mut truncated = false;
        loop {
            let count = reader.read(&mut buffer).await?;
            if count == 0 {
                break;
            }
            let remaining = MAX_VERSION_OUTPUT_BYTES.saturating_sub(output.len());
            let accepted = count.min(remaining);
            output.extend_from_slice(&buffer[..accepted]);
            truncated |= accepted < count;
        }
        Ok(BoundedOutput {
            bytes: output,
            truncated,
        })
    }

    fn parse_build(output: &str) -> Option<(u64, Option<String>)> {
        static BUILD_PATTERN: OnceLock<Regex> = OnceLock::new();
        let pattern = BUILD_PATTERN.get_or_init(|| {
            Regex::new(r"\bbuild\s+(\d+)(?:\s+\((\d{8})\))?")
                .expect("constant Soloist build pattern is valid")
        });
        let captures = pattern.captures(output)?;
        let epoch = captures.get(1)?.as_str().parse::<u64>().ok()?;
        let printed_date = captures.get(2).map(|value| value.as_str().to_owned());
        Some((epoch, printed_date))
    }

    fn utc_date(epoch: u64) -> Option<String> {
        let days = i64::try_from(epoch / 86_400).ok()?;
        let z = days.checked_add(719_468)?;
        let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
        let day_of_era = z - era * 146_097;
        let year_of_era =
            (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
        let mut year = year_of_era + era * 400;
        let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
        let month_prime = (5 * day_of_year + 2) / 153;
        let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
        let month = month_prime + if month_prime < 10 { 3 } else { -9 };
        if month <= 2 {
            year += 1;
        }
        Some(format!("{year:04}-{month:02}-{day:02}"))
    }

    #[cfg(test)]
    mod tests {
        use super::{VERSION_TIMEOUT, utc_date, validate_soloist_binary};
        use std::fs;
        use std::path::Path;
        use std::time::{Duration, SystemTime, UNIX_EPOCH};
        use tempfile::tempdir;

        fn fake_soloist(path: &Path, script: &str) {
            fs::write(path, format!("#!/bin/sh\n{script}\n")).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
            }
        }

        fn version(epoch: u64, date: &str) -> String {
            format!("printf '%s\\n' 'soloist 1.3.9.4 build {epoch} ({date})'")
        }

        #[test]
        fn formats_utc_dates_across_epoch_and_leap_day_boundaries() {
            assert_eq!(utc_date(0).as_deref(), Some("1970-01-01"));
            assert_eq!(utc_date(951_782_400).as_deref(), Some("2000-02-29"));
            assert_eq!(utc_date(1_793_750_400).as_deref(), Some("2026-11-04"));
        }

        #[tokio::test]
        async fn accepts_recent_builds_and_warns_near_expiry() {
            let directory = tempdir().unwrap();
            let executable = directory.path().join("soloist");
            let now = UNIX_EPOCH + Duration::from_secs(1_793_750_400); // 2026-11-04 UTC
            let built_at = now - Duration::from_secs(83 * 86_400);
            let epoch = built_at.duration_since(UNIX_EPOCH).unwrap().as_secs();
            fake_soloist(
                &executable,
                &version(epoch, &super::utc_date(epoch).unwrap().replace('-', "")),
            );

            let build = validate_soloist_binary(&executable, now, VERSION_TIMEOUT)
                .await
                .unwrap();

            assert_eq!(build.built_at, built_at);
            assert!(build.status_message(now).contains("Update it soon"));
        }

        #[tokio::test]
        async fn accepts_build_without_optional_printed_date() {
            let directory = tempdir().unwrap();
            let executable = directory.path().join("soloist");
            let now = UNIX_EPOCH + Duration::from_secs(1_793_750_400);
            let built_at = now - Duration::from_secs(30 * 86_400);
            let epoch = built_at.duration_since(UNIX_EPOCH).unwrap().as_secs();
            fake_soloist(
                &executable,
                &format!("printf '%s\\n' 'soloist 1.3.9.4 build {epoch}'"),
            );

            assert!(
                validate_soloist_binary(&executable, now, VERSION_TIMEOUT)
                    .await
                    .is_ok()
            );
        }

        #[tokio::test]
        async fn rejects_expired_and_inconsistent_dates() {
            let directory = tempdir().unwrap();
            let executable = directory.path().join("soloist");
            let now = UNIX_EPOCH + Duration::from_secs(1_793_750_400);
            let expired_epoch = (now - Duration::from_secs(90 * 86_400))
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs();
            fake_soloist(
                &executable,
                &version(
                    expired_epoch,
                    &super::utc_date(expired_epoch).unwrap().replace('-', ""),
                ),
            );
            let expired = validate_soloist_binary(&executable, now, VERSION_TIMEOUT)
                .await
                .unwrap_err()
                .to_string();
            assert!(expired.contains("build expired"), "{expired}");

            let recent_epoch = (now - Duration::from_secs(30 * 86_400))
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs();
            fake_soloist(&executable, &version(recent_epoch, "20000101"));
            let inconsistent = validate_soloist_binary(&executable, now, VERSION_TIMEOUT)
                .await
                .unwrap_err()
                .to_string();
            assert!(inconsistent.contains("inconsistent build timestamp"));
        }

        #[tokio::test]
        async fn rejects_failed_timed_out_and_oversized_version_commands() {
            let directory = tempdir().unwrap();
            let executable = directory.path().join("soloist");
            fake_soloist(&executable, "echo failed >&2; exit 7");
            let failed = validate_soloist_binary(&executable, SystemTime::now(), VERSION_TIMEOUT)
                .await
                .unwrap_err()
                .to_string();
            assert!(failed.contains("exit status 7"), "{failed}");

            fake_soloist(&executable, "exec /bin/sleep 10");
            let timed_out =
                validate_soloist_binary(&executable, SystemTime::now(), Duration::from_millis(20))
                    .await
                    .unwrap_err()
                    .to_string();
            assert!(timed_out.contains("did not finish"));

            fake_soloist(&executable, "head -c 20000 /dev/zero");
            let oversized =
                validate_soloist_binary(&executable, SystemTime::now(), VERSION_TIMEOUT)
                    .await
                    .unwrap_err()
                    .to_string();
            assert!(oversized.contains("exceeded the 16 KiB limit"));
        }

        #[tokio::test]
        async fn rejects_missing_and_non_executable_files() {
            let directory = tempdir().unwrap();
            let missing = directory.path().join("missing");
            let error = validate_soloist_binary(&missing, SystemTime::now(), VERSION_TIMEOUT)
                .await
                .unwrap_err()
                .to_string();
            assert!(error.contains("missing or unreadable"));

            let executable = directory.path().join("not-executable");
            fs::write(&executable, "not an executable").unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&executable, fs::Permissions::from_mode(0o644)).unwrap();
            }
            let error = validate_soloist_binary(&executable, SystemTime::now(), VERSION_TIMEOUT)
                .await
                .unwrap_err()
                .to_string();
            assert!(error.contains("not executable"));
        }
    }
}
