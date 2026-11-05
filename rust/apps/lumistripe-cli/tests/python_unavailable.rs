use std::process::Command;

#[test]
fn mock_playback_works_with_no_python_command_on_path() {
    let output = Command::new(env!("CARGO_BIN_EXE_lumistripe"))
        .env("PATH", "/lumistripe-test-path-without-python")
        .args([
            "--engine-binary",
            env!("CARGO_BIN_EXE_lumistripe-cli-test-engine"),
            "--output-backend",
            "mock",
            "--pixels",
            "8",
            "--pixels-2",
            "4",
            "--mode",
            "cycling",
            "--stats",
            "--stats-interval-ms",
            "100",
            "--duration-seconds",
            "1",
        ])
        .output()
        .expect("run the native CLI without Python in PATH");

    assert!(
        output.status.success(),
        "CLI failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("Playback started"));
    assert!(stdout.contains("PERF fps="));
}

#[test]
fn dynamic_demo_audio_uses_the_engine_without_python() {
    let output = Command::new(env!("CARGO_BIN_EXE_lumistripe"))
        .env("PATH", "/lumistripe-test-path-without-python")
        .args([
            "--engine-binary",
            env!("CARGO_BIN_EXE_lumistripe-cli-test-engine"),
            "run",
            "--backend",
            "mock",
            "--pixels",
            "8",
            "--mode",
            "dynamic",
            "--audio-source",
            "demo",
            "--debug-selector",
            "--stats-interval-ms",
            "100",
            "--audio-config",
            r#"{"target_level":0.48,"noise_floor":0.021}"#,
            "--duration-seconds",
            "1",
        ])
        .output()
        .expect("run demo audio through the native engine without Python in PATH");

    assert!(
        output.status.success(),
        "CLI failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("Playback started"));
    assert!(stdout.contains("selector=[RenderSelectorDiagnostics"));
    assert!(stdout.contains("scores: [RenderSelectorScore"));
    assert!(stdout.contains("reasons: [\"weight="));
}

#[test]
fn legacy_mic_profile_write_works_without_opening_a_microphone() {
    let directory = tempfile::tempdir().expect("create temporary profile directory");
    let profile_path = directory.path().join("mic-profile.json");
    let output = Command::new(env!("CARGO_BIN_EXE_lumistripe"))
        .env("PATH", "/lumistripe-test-path-without-python")
        .args([
            "--engine-binary",
            env!("CARGO_BIN_EXE_lumistripe-cli-test-engine"),
            "--mic-profile",
            "pcm2902",
            "--write-mic-profile",
        ])
        .arg(&profile_path)
        .output()
        .expect("write a mic profile through the native CLI without Python");

    assert!(
        output.status.success(),
        "CLI failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("Wrote mic profile"));
    let profile: serde_json::Value =
        serde_json::from_slice(&std::fs::read(profile_path).expect("read the written profile"))
            .expect("written profile should be valid JSON");
    assert_eq!(profile["mic_target_level"], 0.403);
    assert_eq!(profile["mic_noise_floor"], 0.0137);
}
