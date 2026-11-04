//! System updater status and bounded helper orchestration.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
#[cfg(unix)]
use std::os::fd::AsRawFd;
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, SystemTime};
use thiserror::Error;
use tokio::io::AsyncReadExt;
use tokio::process::Command;
use tokio::sync::Semaphore;
use tokio::time::timeout;
use tokio_util::sync::CancellationToken;

const MAX_STATUS_BYTES: u64 = 64 * 1024;
const MAX_GIT_OUTPUT_BYTES: usize = 1024 * 1024;
const UPDATE_TRIGGER: &str = "/usr/local/sbin/lumistripe-update-trigger";
const GIT: &str = "/usr/bin/git";
const UPDATE_REMOTE_URL: &str = "https://github.com/woobble/LumiStripe.git";
const UPDATE_BRANCH_REF: &str = "refs/heads/main";
const UPDATE_LOCK_FILE: &str = "/run/lumistripe-update/update.lock";
const UPDATE_CHECK_TIMEOUT: Duration = Duration::from_secs(25);
#[cfg(unix)]
const LOCK_EXCLUSIVE_NONBLOCKING: libc::c_int = libc::LOCK_EX | libc::LOCK_NB;
#[cfg(not(unix))]
const LOCK_EXCLUSIVE_NONBLOCKING: libc::c_int = 0;
#[cfg(unix)]
const LOCK_SHARED_NONBLOCKING: libc::c_int = libc::LOCK_SH | libc::LOCK_NB;
#[cfg(not(unix))]
const LOCK_SHARED_NONBLOCKING: libc::c_int = 0;

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum InstallState {
    #[default]
    Idle,
    Running,
    Success,
    Failed,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
pub(crate) struct UpdateStatus {
    #[schema(required = true)]
    pub supported: bool,
    #[schema(required = true)]
    pub installed_commit: Option<String>,
    #[schema(required = true)]
    pub available_commit: Option<String>,
    #[schema(required = true)]
    pub update_available: bool,
    #[schema(required = true)]
    pub working_tree_dirty: bool,
    #[schema(required = true, format = DateTime)]
    pub checked_at: Option<String>,
    #[schema(required = true)]
    pub check_error: Option<String>,
    #[schema(required = true)]
    pub install_state: InstallState,
    #[schema(required = true, format = DateTime)]
    pub install_started_at: Option<String>,
    #[schema(required = true, format = DateTime)]
    pub install_finished_at: Option<String>,
    #[schema(required = true)]
    pub install_error: Option<String>,
}

fn parse_status(data: &[u8]) -> Option<UpdateStatus> {
    const REQUIRED_FIELDS: [&str; 11] = [
        "supported",
        "installed_commit",
        "available_commit",
        "update_available",
        "working_tree_dirty",
        "checked_at",
        "check_error",
        "install_state",
        "install_started_at",
        "install_finished_at",
        "install_error",
    ];
    let value: Value = serde_json::from_slice(data).ok()?;
    let object = value.as_object()?;
    if !REQUIRED_FIELDS
        .iter()
        .all(|field| object.contains_key(*field))
    {
        return None;
    }
    serde_json::from_value(value).ok()
}

impl UpdateStatus {
    fn unavailable() -> Self {
        Self::default()
    }
}

#[derive(Clone, Debug)]
pub(crate) struct SystemUpdateService {
    project_dir: PathBuf,
    state_file: PathBuf,
    git_path: PathBuf,
    update_lock_file: PathBuf,
    sudo_path: PathBuf,
    update_trigger_path: PathBuf,
    install_timeout: Duration,
    check_timeout: Duration,
    cancellation: CancellationToken,
    check_gate: Arc<Semaphore>,
}

#[derive(Clone, Debug, Error)]
#[error("{message}")]
pub(crate) struct UpdateError {
    pub code: &'static str,
    pub message: &'static str,
}

impl UpdateError {
    fn new(code: &'static str, message: &'static str) -> Self {
        Self { code, message }
    }
}

impl Default for SystemUpdateService {
    fn default() -> Self {
        let project_dir = std::env::var_os("LUMI_PROJECT_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.."));
        let state_file = std::env::var_os("LUMI_UPDATE_STATE_FILE")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/var/lib/lumistripe/update/status.json"));
        Self {
            project_dir,
            state_file,
            git_path: PathBuf::from(GIT),
            update_lock_file: PathBuf::from(UPDATE_LOCK_FILE),
            sudo_path: PathBuf::from("/usr/bin/sudo"),
            update_trigger_path: PathBuf::from(UPDATE_TRIGGER),
            install_timeout: Duration::from_secs(5),
            check_timeout: UPDATE_CHECK_TIMEOUT,
            cancellation: CancellationToken::new(),
            check_gate: Arc::new(Semaphore::new(1)),
        }
    }
}

impl SystemUpdateService {
    pub(crate) fn with_cancellation(cancellation: CancellationToken) -> Self {
        Self {
            cancellation,
            ..Self::default()
        }
    }

    #[cfg(test)]
    fn from_paths(project_dir: PathBuf, state_file: PathBuf) -> Self {
        Self {
            project_dir,
            state_file,
            ..Self::default()
        }
    }

    #[cfg(test)]
    fn with_commands(
        project_dir: PathBuf,
        state_file: PathBuf,
        git_path: PathBuf,
        sudo_path: PathBuf,
        update_trigger_path: PathBuf,
        install_timeout: Duration,
        check_timeout: Duration,
    ) -> Self {
        Self {
            project_dir,
            state_file,
            git_path,
            update_lock_file: PathBuf::from(UPDATE_LOCK_FILE),
            sudo_path,
            update_trigger_path,
            install_timeout,
            check_timeout,
            cancellation: CancellationToken::new(),
            check_gate: Arc::new(Semaphore::new(1)),
        }
    }

    pub(crate) fn status(&self) -> UpdateStatus {
        let Ok(metadata) = std::fs::metadata(&self.state_file) else {
            return UpdateStatus::unavailable();
        };
        if metadata.len() > MAX_STATUS_BYTES {
            return UpdateStatus::unavailable();
        }
        let Ok(data) = std::fs::read(&self.state_file) else {
            return UpdateStatus::unavailable();
        };
        parse_status(&data).unwrap_or_else(UpdateStatus::unavailable)
    }

    pub(crate) async fn check(&self) -> Result<UpdateStatus, UpdateError> {
        let current = self.status();
        if !current.supported {
            return Err(UpdateError::new(
                "unsupported",
                "The system updater is not installed.",
            ));
        }
        let Ok(check_permit) = self.check_gate.clone().try_acquire_owned() else {
            return Ok(current);
        };
        let service = self.clone();
        let cancellation = self.cancellation.clone();
        tokio::spawn(async move {
            let _check_permit = check_permit;
            tokio::select! {
                _ = cancellation.cancelled() => {}
                result = service.refresh_update_status() => {
                    if let Err(error) = result {
                        tracing::warn!(%error, "Rust update check could not refresh status");
                    }
                }
            }
        });
        Ok(current)
    }

    async fn refresh_update_status(&self) -> io::Result<()> {
        let Some(_check_lock) = self.acquire_check_lock()? else {
            return Ok(());
        };
        let mut status = self.status();
        if !status.supported {
            return Ok(());
        }
        if status.install_state == InstallState::Running {
            if self.update_is_running() {
                return Ok(());
            }
            status.install_state = InstallState::Failed;
            status.install_finished_at =
                Some(crate::system_stats::utc_timestamp(SystemTime::now()));
            status.install_error = Some(
                "Update stopped before completion. Check the Pi logs before retrying.".to_owned(),
            );
            self.write_status(&status)?;
        }

        let checked_at = crate::system_stats::utc_timestamp(SystemTime::now());
        let result = self.read_remote_status().await;
        match result {
            Ok((installed_commit, available_commit, working_tree_dirty)) => {
                status.supported = true;
                status.installed_commit = Some(installed_commit.clone());
                status.available_commit =
                    (available_commit != installed_commit).then_some(available_commit);
                status.update_available = status.available_commit.is_some();
                status.working_tree_dirty = working_tree_dirty;
                status.checked_at = Some(checked_at);
                status.check_error = None;
            }
            Err(()) => {
                status.supported = true;
                status.checked_at = Some(checked_at);
                status.check_error =
                    Some("Could not reach GitHub. The next check will retry.".to_owned());
            }
        }
        self.write_status(&status)
    }

    async fn read_remote_status(&self) -> Result<(String, String, bool), ()> {
        let installed_commit = self.git_output(&["rev-parse", "HEAD"]).await?;
        let remote = self
            .git_output(&[
                "ls-remote",
                "--exit-code",
                UPDATE_REMOTE_URL,
                UPDATE_BRANCH_REF,
            ])
            .await?;
        let available_commit = remote.split_whitespace().next().ok_or(())?;
        if available_commit.len() != 40
            || !available_commit
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(());
        }
        let dirty = !self
            .git_output(&["status", "--porcelain"])
            .await?
            .is_empty();
        Ok((installed_commit, available_commit.to_owned(), dirty))
    }

    async fn git_output(&self, arguments: &[&str]) -> Result<String, ()> {
        let home = std::env::var_os("HOME").unwrap_or_else(|| "/".into());
        let mut command = Command::new(&self.git_path);
        command
            .arg("-C")
            .arg(&self.project_dir)
            .args(arguments)
            .env_clear()
            .env("HOME", home)
            .env("PATH", "/usr/bin:/bin")
            .env("GIT_TERMINAL_PROMPT", "0")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        let mut child = command.spawn().map_err(|_| ())?;
        let mut stdout = child.stdout.take().ok_or(())?;
        timeout(self.check_timeout, async move {
            let mut output = Vec::with_capacity(4096);
            let mut buffer = [0_u8; 8192];
            loop {
                let count = stdout.read(&mut buffer).await.map_err(|_| ())?;
                if count == 0 {
                    break;
                }
                if output.len().saturating_add(count) > MAX_GIT_OUTPUT_BYTES {
                    let _ = child.kill().await;
                    let _ = child.wait().await;
                    return Err(());
                }
                output.extend_from_slice(&buffer[..count]);
            }
            let status = child.wait().await.map_err(|_| ())?;
            if !status.success() {
                return Err(());
            }
            Ok(String::from_utf8_lossy(&output).trim().to_owned())
        })
        .await
        .map_err(|_| ())?
    }

    fn acquire_check_lock(&self) -> io::Result<Option<File>> {
        let lock_path = self.state_file.with_file_name("update-check.lock");
        if let Some(parent) = lock_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        let file = options.open(lock_path)?;
        if try_file_lock(&file, LOCK_EXCLUSIVE_NONBLOCKING)? {
            Ok(Some(file))
        } else {
            Ok(None)
        }
    }

    fn update_is_running(&self) -> bool {
        let Ok(file) = OpenOptions::new().read(true).open(&self.update_lock_file) else {
            return false;
        };
        !try_file_lock(&file, LOCK_SHARED_NONBLOCKING).unwrap_or(true)
    }

    fn write_status(&self, status: &UpdateStatus) -> io::Result<()> {
        let Some(parent) = self.state_file.parent() else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "update status path has no parent directory",
            ));
        };
        fs::create_dir_all(parent)?;
        let stamp = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let temporary_path =
            parent.join(format!(".update-status-{}-{stamp}.tmp", std::process::id()));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        options.mode(0o644);
        let mut file = options.open(&temporary_path)?;
        #[cfg(unix)]
        file.set_permissions(fs::Permissions::from_mode(0o644))?;
        let result = (|| {
            serde_json::to_writer(&mut file, status).map_err(io::Error::other)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            fs::rename(&temporary_path, &self.state_file)
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary_path);
        }
        result
    }

    pub(crate) async fn install(&self) -> Result<UpdateStatus, UpdateError> {
        let mut current = self.status();
        if !current.supported {
            return Err(UpdateError::new(
                "unsupported",
                "The system updater is not installed.",
            ));
        }
        if current.install_state == InstallState::Running {
            return Err(UpdateError::new(
                "already_running",
                "An update is already being installed.",
            ));
        }
        if current.working_tree_dirty {
            return Err(UpdateError::new(
                "dirty_checkout",
                "The project checkout has local changes and cannot be updated.",
            ));
        }
        if !current.update_available {
            return Err(UpdateError::new(
                "no_update",
                "There is no update ready to install.",
            ));
        }

        let mut command = Command::new(&self.sudo_path);
        command
            .arg("-n")
            .arg(&self.update_trigger_path)
            .env_clear()
            .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        let status = timeout(self.install_timeout, command.status())
            .await
            .map_err(|_| {
                UpdateError::new(
                    "start_install_failed",
                    "Could not start the system updater. Check the Pi installation.",
                )
            })?
            .map_err(|_| {
                UpdateError::new(
                    "start_install_failed",
                    "Could not start the system updater. Check the Pi installation.",
                )
            })?;
        if !status.success() {
            return Err(UpdateError::new(
                "start_install_failed",
                "Could not start the system updater. Check the Pi installation.",
            ));
        }
        current.install_state = InstallState::Running;
        current.install_started_at = Some(crate::system_stats::utc_timestamp(SystemTime::now()));
        current.install_finished_at = None;
        current.install_error = None;
        Ok(current)
    }
}

fn try_file_lock(file: &File, operation: libc::c_int) -> io::Result<bool> {
    #[cfg(unix)]
    {
        // SAFETY: `file` owns a live descriptor for the duration of this call.
        let result = unsafe { libc::flock(file.as_raw_fd(), operation) };
        if result == 0 {
            return Ok(true);
        }
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::WouldBlock {
            return Ok(false);
        }
        Err(error)
    }
    #[cfg(not(unix))]
    {
        let _ = (file, operation);
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::{InstallState, SystemUpdateService, UpdateStatus};
    use serde_json::json;
    use std::fs;
    use std::path::Path;
    use std::time::Duration;
    use tempfile::TempDir;

    fn supported_status() -> UpdateStatus {
        UpdateStatus {
            supported: true,
            installed_commit: Some("a".repeat(40)),
            available_commit: Some("b".repeat(40)),
            update_available: true,
            working_tree_dirty: false,
            checked_at: Some("2026-01-02T03:04:05Z".to_owned()),
            check_error: None,
            install_state: InstallState::Idle,
            install_started_at: None,
            install_finished_at: None,
            install_error: None,
        }
    }

    fn write_status(path: &Path, status: &UpdateStatus) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, serde_json::to_vec(status).unwrap()).unwrap();
    }

    fn test_service(
        temp: &TempDir,
        state_file: &Path,
        git_path: &Path,
        sudo_path: &Path,
        update_trigger_path: &Path,
        install_timeout: Duration,
    ) -> SystemUpdateService {
        let mut service = SystemUpdateService::with_commands(
            temp.path().join("project"),
            state_file.to_path_buf(),
            git_path.to_path_buf(),
            sudo_path.to_path_buf(),
            update_trigger_path.to_path_buf(),
            install_timeout,
            Duration::from_secs(1),
        );
        service.update_lock_file = temp.path().join("update.lock");
        service
    }

    fn shell_quote(path: &Path) -> String {
        format!("'{}'", path.to_string_lossy().replace('\'', "'\\''"))
    }

    #[cfg(unix)]
    fn write_executable(path: &Path, body: &str) {
        use std::os::unix::fs::PermissionsExt;

        fs::write(path, format!("#!/bin/sh\nset -eu\n{body}\n")).unwrap();
        let mut permissions = fs::metadata(path).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(path, permissions).unwrap();
    }

    #[test]
    fn status_reads_valid_update_state_and_uses_unsupported_fallback_for_bad_files() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        fs::create_dir_all(state_file.parent().unwrap()).unwrap();
        let service =
            SystemUpdateService::from_paths(temp.path().to_path_buf(), state_file.clone());

        assert!(!service.status().supported);
        fs::write(
            &state_file,
            json!({
                "supported": true,
                "installed_commit": "a".repeat(40),
                "available_commit": null,
                "update_available": false,
                "working_tree_dirty": false,
                "checked_at": null,
                "check_error": null,
                "install_state": "idle",
                "install_started_at": null,
                "install_finished_at": null,
                "install_error": null,
                "future_field": "ignored",
            })
            .to_string(),
        )
        .unwrap();
        let status = service.status();
        assert!(status.supported);
        assert_eq!(status.install_state, InstallState::Idle);
        let expected_commit = "a".repeat(40);
        assert_eq!(
            status.installed_commit.as_deref(),
            Some(expected_commit.as_str())
        );

        fs::write(&state_file, b"not-json").unwrap();
        assert!(!service.status().supported);
        fs::write(&state_file, vec![b'x'; 65 * 1024]).unwrap();
        assert!(!service.status().supported);
    }

    #[test]
    fn partial_status_documents_use_the_unsupported_fallback() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        fs::create_dir_all(state_file.parent().unwrap()).unwrap();
        fs::write(&state_file, r#"{"supported":true,"update_available":true}"#).unwrap();
        let service =
            SystemUpdateService::from_paths(temp.path().to_path_buf(), state_file.clone());

        let status = service.status();

        assert!(!status.supported);
        assert!(!status.update_available);
        assert!(!status.working_tree_dirty);
        assert_eq!(status.install_state, InstallState::Idle);
    }

    #[tokio::test]
    async fn check_rejects_unsupported_status() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        let missing_command = temp.path().join("missing-git");
        let service = test_service(
            &temp,
            &state_file,
            &missing_command,
            &missing_command,
            &missing_command,
            Duration::from_secs(1),
        );

        assert_eq!(service.check().await.unwrap_err().code, "unsupported");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn check_refreshes_status_with_bounded_git_commands_without_python() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        let mut current = supported_status();
        current.checked_at = Some("2026-01-02T03:04:05Z".to_owned());
        current.check_error = Some("previous check error".to_owned());
        write_status(&state_file, &current);

        let installed_commit = "a".repeat(40);
        let available_commit = "b".repeat(40);
        let git_path = temp.path().join("git");
        write_executable(
            &git_path,
            &format!(
                "case \"$3\" in\n  rev-parse) printf '%s\\n' '{installed_commit}' ;;\n  ls-remote) printf '{available_commit}\\trefs/heads/main\\n' ;;\n  status) printf ' M README.md\\n' ;;\n  *) exit 9 ;;\nesac"
            ),
        );
        let missing = temp.path().join("unused-command");
        let service = test_service(
            &temp,
            &state_file,
            &git_path,
            &missing,
            &missing,
            Duration::from_secs(1),
        );

        let returned = service.check().await.unwrap();
        assert_eq!(
            returned.check_error.as_deref(),
            Some("previous check error")
        );
        tokio::time::timeout(Duration::from_secs(2), async {
            while service.status().checked_at.as_deref() == Some("2026-01-02T03:04:05Z") {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("the Rust update checker should refresh its status snapshot");
        let refreshed = service.status();
        assert_eq!(
            refreshed.installed_commit.as_deref(),
            Some(installed_commit.as_str())
        );
        assert_eq!(
            refreshed.available_commit.as_deref(),
            Some(available_commit.as_str())
        );
        assert!(refreshed.update_available);
        assert!(refreshed.working_tree_dirty);
        assert!(refreshed.check_error.is_none());
    }

    #[tokio::test]
    async fn check_records_a_safe_error_when_git_cannot_run() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        let mut current = supported_status();
        current.checked_at = Some("2026-01-02T03:04:05Z".to_owned());
        write_status(&state_file, &current);
        let missing = temp.path().join("missing-git");
        let service = test_service(
            &temp,
            &state_file,
            &missing,
            &missing,
            &missing,
            Duration::from_secs(1),
        );

        service.check().await.unwrap();
        tokio::time::timeout(Duration::from_secs(2), async {
            while service.status().checked_at.as_deref() == Some("2026-01-02T03:04:05Z") {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("failed update check should still record its timestamp");
        assert_eq!(
            service.status().check_error.as_deref(),
            Some("Could not reach GitHub. The next check will retry.")
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn check_timeout_records_a_safe_error() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        let mut current = supported_status();
        current.checked_at = Some("2026-01-02T03:04:05Z".to_owned());
        write_status(&state_file, &current);
        let git_path = temp.path().join("git");
        write_executable(&git_path, "exec /bin/sleep 10");
        let missing = temp.path().join("unused-command");
        let mut service = test_service(
            &temp,
            &state_file,
            &git_path,
            &missing,
            &missing,
            Duration::from_secs(1),
        );
        service.check_timeout = Duration::from_millis(25);

        service.check().await.unwrap();
        tokio::time::timeout(Duration::from_secs(2), async {
            while service.status().checked_at.as_deref() == Some("2026-01-02T03:04:05Z") {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("timed-out Git check should still record its timestamp");
        assert_eq!(
            service.status().check_error.as_deref(),
            Some("Could not reach GitHub. The next check will retry.")
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn shutdown_cancels_an_inflight_git_check_and_releases_its_gate() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        let mut current = supported_status();
        current.checked_at = Some("2026-01-02T03:04:05Z".to_owned());
        write_status(&state_file, &current);
        let git_path = temp.path().join("git");
        let started_marker = temp.path().join("git-started");
        write_executable(
            &git_path,
            &format!(
                "printf started > {}\nexec /bin/sleep 10",
                shell_quote(&started_marker)
            ),
        );
        let missing = temp.path().join("unused-command");
        let service = test_service(
            &temp,
            &state_file,
            &git_path,
            &missing,
            &missing,
            Duration::from_secs(1),
        );

        service.check().await.unwrap();
        tokio::time::timeout(Duration::from_secs(1), async {
            while !started_marker.exists() {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("Git check should start before shutdown cancellation");
        service.cancellation.cancel();
        tokio::time::timeout(Duration::from_secs(1), async {
            while service.check_gate.available_permits() != 1 {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("shutdown should cancel the check and release its gate");
        assert_eq!(
            service.status().checked_at.as_deref(),
            Some("2026-01-02T03:04:05Z")
        );
    }

    #[tokio::test]
    async fn install_enforces_support_busy_checkout_and_update_guards() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        let missing = temp.path().join("unused-command");
        let service = test_service(
            &temp,
            &state_file,
            &missing,
            &missing,
            &missing,
            Duration::from_secs(1),
        );

        assert_eq!(service.install().await.unwrap_err().code, "unsupported");

        let mut current = supported_status();
        current.install_state = InstallState::Running;
        write_status(&state_file, &current);
        assert_eq!(service.install().await.unwrap_err().code, "already_running");

        current.install_state = InstallState::Idle;
        current.working_tree_dirty = true;
        write_status(&state_file, &current);
        assert_eq!(service.install().await.unwrap_err().code, "dirty_checkout");

        current.working_tree_dirty = false;
        current.update_available = false;
        write_status(&state_file, &current);
        assert_eq!(service.install().await.unwrap_err().code, "no_update");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn install_starts_sudo_trigger_and_redacts_failure_output() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        write_status(&state_file, &supported_status());

        let argument_log = temp.path().join("sudo-arguments");
        let sudo_path = temp.path().join("sudo");
        write_executable(
            &sudo_path,
            &format!("printf '%s\\n' \"$@\" > {}", shell_quote(&argument_log)),
        );
        let trigger = temp.path().join("update-trigger");
        let missing = temp.path().join("unused-command");
        let service = test_service(
            &temp,
            &state_file,
            &missing,
            &sudo_path,
            &trigger,
            Duration::from_secs(1),
        );

        let started = service.install().await.unwrap();
        assert_eq!(started.install_state, InstallState::Running);
        assert!(started.install_started_at.is_some());
        assert!(started.install_finished_at.is_none());
        assert!(started.install_error.is_none());
        let arguments = fs::read_to_string(argument_log).unwrap();
        assert_eq!(arguments.lines().next(), Some("-n"));
        assert_eq!(
            arguments.lines().nth(1),
            Some(trigger.to_string_lossy().as_ref())
        );

        write_status(&state_file, &supported_status());
        write_executable(
            &sudo_path,
            "printf '%s\\n' 'private pairing code' >&2\nexit 7",
        );
        let error = service.install().await.unwrap_err();
        assert_eq!(error.code, "start_install_failed");
        assert_eq!(
            error.message,
            "Could not start the system updater. Check the Pi installation."
        );
        assert!(!error.message.contains("private pairing code"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn install_timeout_returns_a_bounded_start_error() {
        let temp = TempDir::new().unwrap();
        let state_file = temp.path().join("update/status.json");
        write_status(&state_file, &supported_status());

        let sudo_path = temp.path().join("sudo");
        write_executable(&sudo_path, "exec /bin/sleep 10");
        let missing = temp.path().join("unused-command");
        let service = test_service(
            &temp,
            &state_file,
            &missing,
            &sudo_path,
            &missing,
            Duration::from_millis(20),
        );

        let error = service.install().await.unwrap_err();
        assert_eq!(error.code, "start_install_failed");
    }
}
