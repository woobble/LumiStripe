use crate::UpdateStateAction;
use serde_json::{Value, json};
use std::error::Error;
use std::fs::{self, File, OpenOptions, Permissions};
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio::process::Command;
use tokio::time::timeout;

const UPDATE_REMOTE_URL: &str = "https://github.com/woobble/LumiStripe.git";
const BRANCH_REF: &str = "refs/heads/main";
const UPDATE_LOCK_FILE: &str = "/run/lumistripe-update/update.lock";
const GIT_TIMEOUT: Duration = Duration::from_secs(25);
const MAX_GIT_OUTPUT_BYTES: usize = 256 * 1024;
const MAX_STATUS_BYTES: u64 = 1024 * 1024;

type Result<T> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Clone)]
struct StateConfig {
    git_binary: PathBuf,
    remote_url: String,
    branch_ref: String,
    update_lock_file: PathBuf,
    command_timeout: Duration,
}

impl Default for StateConfig {
    fn default() -> Self {
        Self {
            git_binary: PathBuf::from("/usr/bin/git"),
            remote_url: UPDATE_REMOTE_URL.into(),
            branch_ref: BRANCH_REF.into(),
            update_lock_file: UPDATE_LOCK_FILE.into(),
            command_timeout: GIT_TIMEOUT,
        }
    }
}

pub async fn run(action: UpdateStateAction, now: SystemTime) -> Result<()> {
    let config = StateConfig::default();
    match action {
        UpdateStateAction::Initialize(arguments) => {
            write_status(&arguments.state_file, empty_status())
        }
        UpdateStateAction::Check(arguments) => {
            check(&arguments.project_dir, &arguments.state_file, now, &config).await
        }
        UpdateStateAction::InstallStart(arguments) => {
            record_install_state(
                "install-start",
                &arguments.project_dir,
                &arguments.state_file,
                now,
                &config,
            )
            .await
        }
        UpdateStateAction::InstallSucceeded(arguments) => {
            record_install_state(
                "install-succeeded",
                &arguments.project_dir,
                &arguments.state_file,
                now,
                &config,
            )
            .await
        }
        UpdateStateAction::InstallFailed(arguments) => {
            record_install_state(
                "install-failed",
                &arguments.project_dir,
                &arguments.state_file,
                now,
                &config,
            )
            .await
        }
    }
}

fn empty_status() -> Value {
    json!({
        "supported": true,
        "installed_commit": null,
        "available_commit": null,
        "update_available": false,
        "working_tree_dirty": false,
        "checked_at": null,
        "check_error": null,
        "install_state": "idle",
        "install_started_at": null,
        "install_finished_at": null,
        "install_error": null,
    })
}

fn load_status(path: &Path) -> Value {
    let opened = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path);
    let Ok(mut file) = opened else {
        return empty_status();
    };
    if !file.metadata().is_ok_and(|metadata| metadata.is_file()) {
        return empty_status();
    }

    let mut bytes = Vec::new();
    if Read::by_ref(&mut file)
        .take(MAX_STATUS_BYTES + 1)
        .read_to_end(&mut bytes)
        .is_err()
        || bytes.len() as u64 > MAX_STATUS_BYTES
    {
        return empty_status();
    }
    let Ok(value) = serde_json::from_slice::<Value>(&bytes) else {
        return empty_status();
    };
    let Value::Object(existing) = value else {
        return empty_status();
    };
    let mut status = empty_status();
    if let Value::Object(defaults) = &mut status {
        for (key, value) in existing {
            defaults.insert(key, value);
        }
    }
    status
}

fn path_owner(path: &Path) -> io::Result<(u32, u32)> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() => Ok((metadata.uid(), metadata.gid())),
        Ok(_) => Err(io::Error::other(
            "update status path must be a regular file",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let parent = parent_directory(path);
            let metadata = fs::metadata(parent)?;
            if !metadata.is_dir() {
                return Err(io::Error::other("update status parent must be a directory"));
            }
            Ok((metadata.uid(), metadata.gid()))
        }
        Err(error) => Err(error),
    }
}

fn parent_directory(path: &Path) -> &Path {
    path.parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."))
}

fn set_file_owner_and_mode(file: &File, owner: (u32, u32), mode: u32) -> io::Result<()> {
    let current = file.metadata()?;
    if (current.uid(), current.gid()) != owner {
        // The caller runs as the status owner or as the root updater.
        if unsafe { libc::fchown(file.as_raw_fd(), owner.0, owner.1) } != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    file.set_permissions(Permissions::from_mode(mode))
}

fn write_status(path: &Path, status: Value) -> Result<()> {
    let parent = parent_directory(path);
    fs::create_dir_all(parent)?;
    let owner = path_owner(path)?;
    let encoded = serde_json::to_vec(&status)?;
    if encoded.len() as u64 > MAX_STATUS_BYTES {
        return Err(io::Error::other("update status exceeds the size limit").into());
    }

    let file_name = path
        .file_name()
        .ok_or_else(|| io::Error::other("update status path has no file name"))?
        .to_string_lossy();
    let mut temporary_path = None;
    let mut temporary_file = None;
    for attempt in 0..32_u32 {
        let candidate = parent.join(format!(
            ".{file_name}.{}.{}.tmp",
            std::process::id(),
            attempt
        ));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&candidate)
        {
            Ok(file) => {
                temporary_path = Some(candidate);
                temporary_file = Some(file);
                break;
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        }
    }
    let temporary_path = temporary_path
        .ok_or_else(|| io::Error::other("could not create an update status temporary file"))?;
    let mut temporary_file = temporary_file.expect("path and file are set together");
    let result = (|| -> Result<()> {
        set_file_owner_and_mode(&temporary_file, owner, 0o644)?;
        temporary_file.write_all(&encoded)?;
        temporary_file.write_all(b"\n")?;
        temporary_file.sync_all()?;
        drop(temporary_file);
        fs::rename(&temporary_path, path)?;
        File::open(parent)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary_path);
    }
    result
}

fn open_update_lock(status_path: &Path) -> Result<File> {
    let lock_path = status_path.with_file_name("update-check.lock");
    let parent = parent_directory(status_path);
    fs::create_dir_all(parent)?;
    let owner = path_owner(status_path)?;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o644)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(lock_path)?;
    if !file.metadata()?.is_file() {
        return Err(io::Error::other("update lock path must be a regular file").into());
    }
    set_file_owner_and_mode(&file, owner, 0o644)?;
    Ok(file)
}

fn try_flock(file: &File, operation: libc::c_int) -> io::Result<bool> {
    if unsafe { libc::flock(file.as_raw_fd(), operation | libc::LOCK_NB) } == 0 {
        return Ok(true);
    }
    let error = io::Error::last_os_error();
    if error
        .raw_os_error()
        .is_some_and(|code| code == libc::EWOULDBLOCK || code == libc::EAGAIN)
    {
        Ok(false)
    } else {
        Err(error)
    }
}

fn lock_exclusive(file: &File) -> io::Result<()> {
    loop {
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) } == 0 {
            return Ok(());
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

fn update_is_running(path: &Path) -> bool {
    let opened = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path);
    let Ok(file) = opened else {
        return false;
    };
    if !file.metadata().is_ok_and(|metadata| metadata.is_file()) {
        return false;
    }
    !matches!(try_flock(&file, libc::LOCK_SH), Ok(true) | Err(_))
}

async fn check(
    project_dir: &Path,
    status_path: &Path,
    now: SystemTime,
    config: &StateConfig,
) -> Result<()> {
    let lock_file = open_update_lock(status_path)?;
    if !try_flock(&lock_file, libc::LOCK_EX)? {
        return Ok(());
    }

    let mut status = load_status(status_path);
    if status["install_state"] == "running" {
        if update_is_running(&config.update_lock_file) {
            return Ok(());
        }
        status["install_state"] = json!("failed");
        status["install_finished_at"] = json!(timestamp(now));
        status["install_error"] =
            json!("Update stopped before completion. Check the Pi logs before retrying.");
        write_status(status_path, status.clone())?;
    }

    let checked_at = timestamp(now);
    let check_result = async {
        let installed_commit = run_git(config, project_dir, &["rev-parse", "HEAD"]).await?;
        let remote = run_git(
            config,
            project_dir,
            &[
                "ls-remote",
                "--exit-code",
                &config.remote_url,
                &config.branch_ref,
            ],
        )
        .await?;
        let available_commit = remote
            .split_whitespace()
            .next()
            .filter(|commit| {
                commit.len() == 40
                    && commit
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            })
            .ok_or_else(|| io::Error::other("remote returned an invalid commit"))?;
        let dirty_output = run_git(config, project_dir, &["status", "--porcelain"]).await?;
        Ok::<_, Box<dyn Error + Send + Sync>>((
            installed_commit,
            available_commit.to_owned(),
            !dirty_output.trim().is_empty(),
        ))
    }
    .await;

    match check_result {
        Ok((installed_commit, available_commit, working_tree_dirty)) => {
            let update_available = available_commit != installed_commit;
            status["supported"] = json!(true);
            status["installed_commit"] = json!(installed_commit);
            status["available_commit"] = if update_available {
                json!(available_commit)
            } else {
                Value::Null
            };
            status["update_available"] = json!(update_available);
            status["working_tree_dirty"] = json!(working_tree_dirty);
            status["checked_at"] = json!(checked_at);
            status["check_error"] = Value::Null;
        }
        Err(_) => {
            status["supported"] = json!(true);
            status["checked_at"] = json!(checked_at);
            status["check_error"] = json!("Could not reach GitHub. The next check will retry.");
        }
    }
    write_status(status_path, status)
}

async fn record_install_state(
    command: &str,
    project_dir: &Path,
    status_path: &Path,
    now: SystemTime,
    config: &StateConfig,
) -> Result<()> {
    let lock_file = open_update_lock(status_path)?;
    lock_exclusive(&lock_file)?;
    let mut status = load_status(status_path);
    let timestamp = timestamp(now);
    match command {
        "install-start" => {
            status["supported"] = json!(true);
            status["install_state"] = json!("running");
            status["install_started_at"] = json!(timestamp);
            status["install_finished_at"] = Value::Null;
            status["install_error"] = Value::Null;
        }
        "install-succeeded" => {
            let installed_commit = run_git(config, project_dir, &["rev-parse", "HEAD"]).await?;
            let dirty_output = run_git(config, project_dir, &["status", "--porcelain"]).await?;
            status["supported"] = json!(true);
            status["installed_commit"] = json!(installed_commit);
            status["available_commit"] = Value::Null;
            status["update_available"] = json!(false);
            status["working_tree_dirty"] = json!(!dirty_output.trim().is_empty());
            status["checked_at"] = json!(timestamp);
            status["check_error"] = Value::Null;
            status["install_state"] = json!("success");
            status["install_finished_at"] = json!(timestamp);
            status["install_error"] = Value::Null;
        }
        _ => {
            status["supported"] = json!(true);
            status["install_state"] = json!("failed");
            status["install_finished_at"] = json!(timestamp);
            status["install_error"] = json!("Update failed. Check the Pi logs before retrying.");
        }
    }
    write_status(status_path, status)
}

async fn run_git(config: &StateConfig, project_dir: &Path, arguments: &[&str]) -> Result<String> {
    #[cfg(test)]
    let _test_subprocess_guard = crate::TEST_SUBPROCESS_LOCK.lock().await;
    let mut command = Command::new(&config.git_binary);
    command
        .arg("-C")
        .arg(project_dir)
        .args(arguments)
        .env("LC_ALL", "C")
        .env("GIT_TERMINAL_PROMPT", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let mut child = command.spawn()?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| io::Error::other("git stdout was unavailable"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| io::Error::other("git stderr was unavailable"))?;
    let mut stdout_task = tokio::spawn(read_bounded(stdout));
    let mut stderr_task = tokio::spawn(read_bounded(stderr));
    let result = timeout(config.command_timeout, async {
        let exit = child.wait().await?;
        let stdout = (&mut stdout_task)
            .await
            .map_err(|_| io::Error::other("git stdout reader failed"))??;
        let stderr = (&mut stderr_task)
            .await
            .map_err(|_| io::Error::other("git stderr reader failed"))??;
        Ok::<_, io::Error>((exit, stdout, stderr))
    })
    .await;
    let (exit, stdout, stderr) = match result {
        Ok(Ok(result)) => result,
        Ok(Err(error)) => return Err(error.into()),
        Err(_) => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            stdout_task.abort();
            stderr_task.abort();
            return Err(io::Error::new(io::ErrorKind::TimedOut, "git command timed out").into());
        }
    };
    if stdout.truncated || stderr.truncated {
        return Err(io::Error::other("git output exceeded the size limit").into());
    }
    if !exit.success() {
        return Err(io::Error::other("git command failed").into());
    }
    Ok(String::from_utf8_lossy(&stdout.bytes).trim().to_owned())
}

struct BoundedOutput {
    bytes: Vec<u8>,
    truncated: bool,
}

async fn read_bounded<R: AsyncRead + Unpin>(mut reader: R) -> io::Result<BoundedOutput> {
    let mut output = Vec::with_capacity(MAX_GIT_OUTPUT_BYTES);
    let mut buffer = [0_u8; 4096];
    let mut truncated = false;
    loop {
        let count = reader.read(&mut buffer).await?;
        if count == 0 {
            break;
        }
        let remaining = MAX_GIT_OUTPUT_BYTES.saturating_sub(output.len());
        let accepted = count.min(remaining);
        output.extend_from_slice(&buffer[..accepted]);
        truncated |= accepted < count;
    }
    Ok(BoundedOutput {
        bytes: output,
        truncated,
    })
}

fn timestamp(now: SystemTime) -> String {
    let duration = now.duration_since(UNIX_EPOCH).unwrap_or_default();
    let seconds = duration.as_secs();
    let date = utc_date(seconds).unwrap_or_else(|| "1970-01-01".into());
    let seconds_in_day = seconds % 86_400;
    let hour = seconds_in_day / 3_600;
    let minute = (seconds_in_day % 3_600) / 60;
    let second = seconds_in_day % 60;
    if duration.subsec_nanos() == 0 {
        format!("{date}T{hour:02}:{minute:02}:{second:02}+00:00")
    } else {
        format!(
            "{date}T{hour:02}:{minute:02}:{second:02}.{:09}+00:00",
            duration.subsec_nanos()
        )
    }
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
    use super::{
        StateConfig, check, empty_status, load_status, open_update_lock, record_install_state,
        timestamp, write_status,
    };
    use serde_json::{Value, json};
    use std::fs;
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    use std::path::{Path, PathBuf};
    use std::time::{Duration, UNIX_EPOCH};
    use tempfile::tempdir;

    fn fake_git(path: &Path, script: &str) -> PathBuf {
        fs::write(path, format!("#!/bin/sh\n{script}\n")).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
        path.to_path_buf()
    }

    fn config(git_binary: PathBuf, update_lock_file: PathBuf) -> StateConfig {
        StateConfig {
            git_binary,
            remote_url: "https://example.invalid/LumiStripe.git".into(),
            branch_ref: "refs/heads/main".into(),
            update_lock_file,
            command_timeout: Duration::from_secs(1),
        }
    }

    #[test]
    fn status_document_has_compatible_defaults_and_timestamp() {
        let status = empty_status();
        assert_eq!(status["supported"], true);
        assert_eq!(status["install_state"], "idle");
        assert_eq!(status["available_commit"], Value::Null);
        assert_eq!(
            timestamp(UNIX_EPOCH + Duration::from_secs(1_793_750_400)),
            "2026-11-04T00:00:00+00:00"
        );
    }

    #[test]
    fn status_write_is_atomic_mode_limited_and_preserves_owner() {
        let directory = tempdir().unwrap();
        let state_path = directory.path().join("status.json");
        let directory_owner = fs::metadata(directory.path()).unwrap();
        write_status(&state_path, empty_status()).unwrap();

        let metadata = fs::metadata(&state_path).unwrap();
        assert_eq!(metadata.uid(), directory_owner.uid());
        assert_eq!(metadata.gid(), directory_owner.gid());
        assert_eq!(metadata.permissions().mode() & 0o777, 0o644);
        assert_eq!(load_status(&state_path)["install_state"], "idle");
    }

    #[test]
    fn status_read_does_not_follow_symlinks_and_write_rejects_them() {
        let directory = tempdir().unwrap();
        let target = directory.path().join("target.json");
        fs::write(&target, "{\"secret\":true}").unwrap();
        let state_path = directory.path().join("status.json");
        std::os::unix::fs::symlink(&target, &state_path).unwrap();

        assert_eq!(load_status(&state_path)["install_state"], "idle");
        assert!(write_status(&state_path, empty_status()).is_err());
        assert_eq!(fs::read_to_string(target).unwrap(), "{\"secret\":true}");
    }

    #[test]
    fn status_owner_is_preserved_when_root_rewrites_for_service_user() {
        if unsafe { libc::geteuid() } != 0 {
            return;
        }
        let directory = tempdir().unwrap();
        let state_path = directory.path().join("status.json");
        write_status(&state_path, empty_status()).unwrap();
        let file = fs::File::open(&state_path).unwrap();
        assert_eq!(unsafe { libc::fchown(file.as_raw_fd(), 65_534, 65_534) }, 0);

        write_status(&state_path, empty_status()).unwrap();

        let metadata = fs::metadata(state_path).unwrap();
        assert_eq!((metadata.uid(), metadata.gid()), (65_534, 65_534));
    }

    #[test]
    fn update_lock_inherits_status_owner_and_mode() {
        let directory = tempdir().unwrap();
        let state_path = directory.path().join("status.json");
        write_status(&state_path, empty_status()).unwrap();
        let state_metadata = fs::metadata(&state_path).unwrap();

        let lock = open_update_lock(&state_path).unwrap();

        let metadata = lock.metadata().unwrap();
        assert_eq!(
            (metadata.uid(), metadata.gid()),
            (state_metadata.uid(), state_metadata.gid())
        );
        assert_eq!(metadata.permissions().mode() & 0o777, 0o644);
    }

    #[tokio::test]
    async fn check_recovers_a_stale_install_and_records_the_remote_commit() {
        let directory = tempdir().unwrap();
        let state_path = directory.path().join("update/status.json");
        let installed = "a".repeat(40);
        let available = "b".repeat(40);
        write_status(
            &state_path,
            json!({
                "install_state": "running",
                "install_started_at": "2026-01-01T00:00:00+00:00"
            }),
        )
        .unwrap();
        let git = fake_git(
            &directory.path().join("git"),
            &format!(
                "case \"$*\" in\n  *'rev-parse HEAD'*) printf '%s\\n' '{installed}' ;;\n  *'ls-remote'*) printf '%s\\trefs/heads/main\\n' '{available}' ;;\n  *'status --porcelain'*) printf '' ;;\nesac"
            ),
        );
        let config = config(git, directory.path().join("missing-update.lock"));

        check(
            directory.path(),
            &state_path,
            UNIX_EPOCH + Duration::from_secs(1_793_750_400),
            &config,
        )
        .await
        .unwrap();

        let status = load_status(&state_path);
        assert_eq!(status["install_state"], "failed");
        assert_eq!(status["installed_commit"], installed);
        assert_eq!(status["available_commit"], available);
        assert_eq!(status["update_available"], true);
        assert_eq!(status["working_tree_dirty"], false);
        assert!(
            status["install_error"]
                .as_str()
                .unwrap()
                .contains("stopped")
        );
    }

    #[tokio::test]
    async fn offline_check_preserves_last_known_available_commit() {
        let directory = tempdir().unwrap();
        let state_path = directory.path().join("update/status.json");
        let installed = "a".repeat(40);
        let available = "b".repeat(40);
        write_status(
            &state_path,
            json!({
                "installed_commit": installed,
                "available_commit": available,
                "update_available": true
            }),
        )
        .unwrap();
        let git = fake_git(
            &directory.path().join("git"),
            &format!(
                "case \"$*\" in\n  *'rev-parse HEAD'*) printf '%s\\n' '{installed}' ;;\n  *'ls-remote'*) exit 128 ;;\nesac"
            ),
        );
        let config = config(git, directory.path().join("missing-update.lock"));

        check(
            directory.path(),
            &state_path,
            UNIX_EPOCH + Duration::from_secs(1_793_750_400),
            &config,
        )
        .await
        .unwrap();

        let status = load_status(&state_path);
        assert_eq!(status["available_commit"], available);
        assert_eq!(status["update_available"], true);
        assert!(status["check_error"].as_str().unwrap().contains("GitHub"));
    }

    #[tokio::test]
    async fn check_skips_when_another_check_holds_the_status_lock() {
        let directory = tempdir().unwrap();
        let state_path = directory.path().join("update/status.json");
        write_status(&state_path, empty_status()).unwrap();
        let lock = open_update_lock(&state_path).unwrap();
        assert!(super::try_flock(&lock, libc::LOCK_EX).unwrap());
        let git = fake_git(&directory.path().join("unused-git"), "exit 99");
        let config = config(git, directory.path().join("missing-update.lock"));

        check(
            directory.path(),
            &state_path,
            UNIX_EPOCH + Duration::from_secs(1_793_750_400),
            &config,
        )
        .await
        .unwrap();

        assert_eq!(load_status(&state_path)["checked_at"], Value::Null);
    }

    #[tokio::test]
    async fn check_keeps_install_running_while_the_global_update_lock_is_held() {
        let directory = tempdir().unwrap();
        let state_path = directory.path().join("update/status.json");
        write_status(
            &state_path,
            json!({
                "install_state": "running",
                "install_started_at": "2026-01-01T00:00:00+00:00"
            }),
        )
        .unwrap();
        let update_lock_path = directory.path().join("update.lock");
        let update_lock = fs::File::create(&update_lock_path).unwrap();
        assert_eq!(
            unsafe { libc::flock(update_lock.as_raw_fd(), libc::LOCK_EX) },
            0
        );
        let git = fake_git(&directory.path().join("unused-git"), "exit 99");
        let config = config(git, update_lock_path);

        check(
            directory.path(),
            &state_path,
            UNIX_EPOCH + Duration::from_secs(1_793_750_400),
            &config,
        )
        .await
        .unwrap();

        let status = load_status(&state_path);
        assert_eq!(status["install_state"], "running");
        assert_eq!(status["checked_at"], Value::Null);
    }

    #[tokio::test]
    async fn install_state_transitions_keep_the_existing_json_contract() {
        let directory = tempdir().unwrap();
        let state_path = directory.path().join("update/status.json");
        let installed = "c".repeat(40);
        let git = fake_git(
            &directory.path().join("git"),
            &format!(
                "case \"$*\" in\n  *'rev-parse HEAD'*) printf '%s\\n' '{installed}' ;;\n  *'status --porcelain'*) printf ' M settings.json\\n' ;;\nesac"
            ),
        );
        let config = config(git, directory.path().join("missing-update.lock"));
        let now = UNIX_EPOCH + Duration::from_secs(1_793_750_400);

        record_install_state("install-start", directory.path(), &state_path, now, &config)
            .await
            .unwrap();
        assert_eq!(load_status(&state_path)["install_state"], "running");
        record_install_state(
            "install-succeeded",
            directory.path(),
            &state_path,
            now,
            &config,
        )
        .await
        .unwrap();
        let status = load_status(&state_path);
        assert_eq!(status["install_state"], "success");
        assert_eq!(status["installed_commit"], installed);
        assert_eq!(status["working_tree_dirty"], true);
        assert_eq!(status["install_error"], Value::Null);
    }
}
