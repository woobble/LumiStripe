use std::io::{ErrorKind, Write};
use std::process::{Command, Stdio};
use tempfile::TempDir;

const PATH_WITHOUT_PYTHON: &str = "/lumistripe-test-path-without-python";

#[test]
fn update_state_helper_runs_without_python_on_path() {
    let directory = TempDir::new().expect("temporary helper state directory");
    let project_dir = directory.path().join("project");
    std::fs::create_dir(&project_dir).expect("create project directory");
    let state_file = directory.path().join("state/status.json");

    for interpreter in ["python", "python3"] {
        let error = Command::new(interpreter)
            .env("PATH", PATH_WITHOUT_PYTHON)
            .output()
            .expect_err("Python must not resolve on the helper process PATH");
        assert_eq!(error.kind(), ErrorKind::NotFound);
    }

    let output = Command::new(env!("CARGO_BIN_EXE_lumistripe-deploy-helper"))
        .env_clear()
        .env("PATH", PATH_WITHOUT_PYTHON)
        .args([
            "update-state",
            "initialize",
            "--project-dir",
            project_dir.to_str().expect("UTF-8 project path"),
            "--state-file",
            state_file.to_str().expect("UTF-8 status path"),
        ])
        .output()
        .expect("run native deployment helper without Python");
    assert!(
        output.status.success(),
        "deployment helper failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    let status: serde_json::Value = serde_json::from_slice(
        &std::fs::read(&state_file).expect("helper should initialize update state"),
    )
    .expect("helper should write valid JSON state");
    assert_eq!(status["supported"], true);
    assert_eq!(status["install_state"], "idle");
}

#[test]
fn network_helper_runs_without_python_on_path() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_lumistripe-deploy-helper"))
        .env_clear()
        .env("PATH", PATH_WITHOUT_PYTHON)
        .arg("network")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("start native NetworkManager helper without Python");
    child
        .stdin
        .take()
        .expect("NetworkManager helper stdin")
        .write_all(b"{\"operation\":\"status\"}\n")
        .expect("send read-only status request");

    let output = child
        .wait_with_output()
        .expect("wait for native NetworkManager helper");
    assert!(
        output.status.success(),
        "NetworkManager helper failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let response: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("helper should return JSON");
    assert!(response["ok"].is_boolean());
    assert!(
        response["manager_available"].is_boolean() || response["error_code"].is_string(),
        "helper should return a status snapshot or a structured error: {response}"
    );
}

#[test]
fn spotify_device_name_helper_runs_without_python_on_path() {
    let directory = TempDir::new().expect("temporary service home");
    let output = Command::new(env!("CARGO_BIN_EXE_lumistripe-deploy-helper"))
        .env_clear()
        .env("PATH", PATH_WITHOUT_PYTHON)
        .env("HOME", directory.path())
        .args(["spotify-device-name", "Office speakers"])
        .output()
        .expect("run native Spotify name helper without Python");
    assert!(
        output.status.success(),
        "Spotify device-name helper failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    let name_file = directory
        .path()
        .join(".config/lumistripe/spotify-device-name.env");
    assert_eq!(
        std::fs::read_to_string(name_file).expect("helper should write the device name"),
        "LUMI_SPOTIFY_DEVICE_NAME=\"Office speakers\"\n"
    );
}

#[cfg(unix)]
#[test]
fn soloist_preflight_runs_without_python_on_path() {
    use std::os::unix::fs::PermissionsExt;

    let directory = TempDir::new().expect("temporary Soloist fixture directory");
    let soloist = directory.path().join("soloist");
    std::fs::write(
        &soloist,
        "#!/bin/sh\nprintf '%s\\n' 'soloist 1.3.9.4 build 4000000000'\n",
    )
    .expect("write Soloist version fixture");
    std::fs::set_permissions(&soloist, std::fs::Permissions::from_mode(0o755))
        .expect("make Soloist fixture executable");

    let output = Command::new(env!("CARGO_BIN_EXE_lumistripe-deploy-helper"))
        .env_clear()
        .env("PATH", PATH_WITHOUT_PYTHON)
        .args([
            "soloist-check",
            "--binary",
            soloist.to_str().expect("UTF-8 Soloist path"),
        ])
        .output()
        .expect("run native Soloist preflight without Python");
    assert!(
        output.status.success(),
        "Soloist preflight failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("is valid for"));
}

#[test]
fn release_packaging_and_validation_run_without_python_on_path() {
    use std::os::unix::fs::PermissionsExt;

    let directory = TempDir::new().expect("temporary release fixture directory");
    let project = directory.path().join("project");
    let release = project.join("release");
    std::fs::create_dir_all(release.join("static/assets"))
        .expect("create release fixture directories");
    std::fs::write(project.join(".bun-version"), "1.4.4\n")
        .expect("write frontend toolchain version");
    for name in [
        "lumistripe-web",
        "lumistripe-engine",
        "lumistripe-deploy-helper",
        "lumistripe",
    ] {
        let binary = release.join(name);
        std::fs::write(&binary, "#!/bin/sh\nexit 0\n").expect("write release executable");
        std::fs::set_permissions(&binary, std::fs::Permissions::from_mode(0o755))
            .expect("make release fixture executable");
    }
    std::fs::write(
        release.join("static/index.html"),
        "<!doctype html>fixture\n",
    )
    .expect("write release index");
    std::fs::write(
        release.join("static/assets/app.js"),
        "window.fixture = true;\n",
    )
    .expect("write release asset");
    let build_info = directory.path().join("runtime-build-info.json");
    std::fs::write(
        &build_info,
        r#"{"architecture":"aarch64","target":"aarch64-unknown-linux-gnu","glibc_minimum":"2.36"}"#,
    )
    .expect("write validated runtime metadata");
    let commit = "c3c6f7b54f21da1063a2ef4081b9cde7f6501234";
    let archive = directory
        .path()
        .join(format!("lumistripe-linux-aarch64-{commit}.tar.gz"));

    let packaged = Command::new(env!("CARGO_BIN_EXE_lumistripe-deploy-helper"))
        .env_clear()
        .env("PATH", PATH_WITHOUT_PYTHON)
        .args([
            "release-artifact",
            "package",
            "--release-dir",
            release.to_str().expect("UTF-8 release path"),
            "--output",
            archive.to_str().expect("UTF-8 archive path"),
            "--commit",
            commit,
            "--project-root",
            project.to_str().expect("UTF-8 project path"),
            "--build-info",
            build_info.to_str().expect("UTF-8 build info path"),
        ])
        .output()
        .expect("run Rust release packager without Python");
    assert!(
        packaged.status.success(),
        "Rust release packaging failed without Python: {}",
        String::from_utf8_lossy(&packaged.stderr)
    );
    assert!(archive.is_file());

    let validated = Command::new(env!("CARGO_BIN_EXE_lumistripe-deploy-helper"))
        .env_clear()
        .env("PATH", PATH_WITHOUT_PYTHON)
        .args([
            "release-artifact",
            "validate",
            "--release-dir",
            release.to_str().expect("UTF-8 release path"),
            "--commit",
            commit,
            "--origin",
            "github",
        ])
        .output()
        .expect("run Rust release validator without Python");
    assert!(
        validated.status.success(),
        "Rust release validation failed without Python: {}",
        String::from_utf8_lossy(&validated.stderr)
    );
}
