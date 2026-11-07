//! Trusted release packaging, verification, extraction, and staging.

use clap::{Args, Subcommand};
use flate2::Compression;
use flate2::GzBuilder;
use flate2::read::GzDecoder;
use regex::Regex;
use reqwest::header::{ACCEPT, ACCEPT_ENCODING, CONTENT_ENCODING};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufReader, Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};
use tokio::process::Command as TokioCommand;
use tokio::time::timeout;
use url::Url;

const REPOSITORY: &str = "woobble/LumiStripe";
const API_ROOT: &str = "https://api.github.com/repos/woobble/LumiStripe";
const SIGNER_WORKFLOW: &str = "woobble/LumiStripe/.github/workflows/ci.yml";
const RELEASE_PREFIX: &str = "build-";
const RELEASE_SUFFIX: &str = ".tar.gz";
const ATTESTATION_SUFFIX: &str = ".sigstore.json";
const MANIFEST_SUFFIX: &str = ".manifest.json";
const CHECKSUM_SUFFIX: &str = ".sha256";
const FORMAT_VERSION: u64 = 1;
const ARCHIVE_LIMIT: u64 = 512 * 1024 * 1024;
const SIDECAR_LIMIT: u64 = 2 * 1024 * 1024;
const EXPANDED_LIMIT: u64 = 1024 * 1024 * 1024;
const ENTRY_LIMIT: usize = 20_000;
const FILE_LIMIT: u64 = 128 * 1024 * 1024;
const RELEASE_BINARIES: [&str; 4] = [
    "lumistripe-web",
    "lumistripe-engine",
    "lumistripe",
    "lumistripe-deploy-helper",
];
const RELEASE_FEATURES: [&str; 2] = ["bluez", "pipewire"];
const LIBRARIES: [&str; 6] = [
    "libasound.so.2",
    "libatomic.so.1",
    "libdbus-1.so.3",
    "libpipewire-0.3.so.0",
    "libpulse.so.0",
    "libspa-0.2.so.0",
];
const DOWNLOAD_HOSTS: [&str; 3] = [
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
];
const MAX_REDIRECTS: usize = 5;
const TAR_BLOCK: usize = 512;
const TAR_RECORD: usize = 10 * 1024;

#[derive(Debug, Subcommand)]
pub(crate) enum Action {
    /// Create a reproducible CI archive and manifest.
    Package(PackageArgs),
    /// Write and validate a manifest for a local source build.
    SealLocal(SealArgs),
    /// Preserve a legacy frontend while migrating a local release layout.
    MigrateLegacyStatic(MigrationArgs),
    /// Validate a staged release directory.
    Validate(ValidateArgs),
    /// Download, verify, cache, and stage an immutable release.
    Install(InstallArgs),
    /// Extract one executable from an already verified cache entry.
    ExtractBinary(ExtractBinaryArgs),
}

#[derive(Args, Debug)]
pub(crate) struct PackageArgs {
    #[arg(long)]
    release_dir: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long)]
    commit: String,
    #[arg(long)]
    project_root: Option<PathBuf>,
    #[arg(long)]
    glibc_minimum: Option<String>,
    #[arg(long)]
    build_info: Option<PathBuf>,
}

#[derive(Args, Debug)]
pub(crate) struct SealArgs {
    #[arg(long)]
    release_dir: PathBuf,
    #[arg(long)]
    commit: String,
    #[arg(long)]
    project_root: Option<PathBuf>,
}

#[derive(Args, Debug)]
pub(crate) struct MigrationArgs {
    #[arg(long)]
    release_dir: PathBuf,
    #[arg(long)]
    static_dir: PathBuf,
    #[arg(long)]
    commit: String,
    #[arg(long)]
    project_root: Option<PathBuf>,
}

#[derive(Args, Debug)]
pub(crate) struct ValidateArgs {
    #[arg(long)]
    release_dir: PathBuf,
    #[arg(long)]
    commit: String,
    #[arg(long, value_parser = ["github", "source"])]
    origin: Option<String>,
    #[arg(long)]
    check_runtime: bool,
}

#[derive(Args, Debug)]
pub(crate) struct InstallArgs {
    #[arg(long)]
    commit: String,
    #[arg(long)]
    release_root: PathBuf,
    #[arg(long)]
    cache_root: PathBuf,
    #[arg(long)]
    uid: u32,
    #[arg(long)]
    gid: u32,
    #[arg(long, default_value = "/usr/bin/gh")]
    gh: PathBuf,
    #[arg(long)]
    helper_destination: Option<PathBuf>,
    #[arg(long)]
    cached_only: bool,
    #[arg(long, hide = true)]
    skip_target_check: bool,
}

#[derive(Args, Debug)]
pub(crate) struct ExtractBinaryArgs {
    #[arg(long)]
    cache_dir: PathBuf,
    #[arg(long)]
    commit: String,
    #[arg(long)]
    binary: String,
    #[arg(long)]
    destination: PathBuf,
}

#[derive(Debug)]
pub(crate) enum ArtifactError {
    Missing(String),
    Unsupported(String),
    Verification(String),
    General(String),
}

impl ArtifactError {
    pub(crate) fn exit_code(&self) -> u8 {
        match self {
            Self::Missing(_) => 10,
            Self::Unsupported(_) => 11,
            Self::Verification(_) => 12,
            Self::General(_) => 1,
        }
    }
}

impl std::fmt::Display for ArtifactError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Missing(message) => write!(formatter, "release artifact missing: {message}"),
            Self::Unsupported(message) => write!(formatter, "unsupported binary target: {message}"),
            Self::Verification(message) => {
                write!(formatter, "release verification failed: {message}")
            }
            Self::General(message) => write!(formatter, "release retrieval failed: {message}"),
        }
    }
}

type Result<T> = std::result::Result<T, ArtifactError>;

fn verification(message: impl Into<String>) -> ArtifactError {
    ArtifactError::Verification(message.into())
}

fn general(message: impl Into<String>) -> ArtifactError {
    ArtifactError::General(message.into())
}

fn io_verification(context: &str, error: impl std::fmt::Display) -> ArtifactError {
    verification(format!("{context}: {error}"))
}

pub(crate) async fn run(action: Action) -> Result<()> {
    match action {
        Action::Package(arguments) => {
            let mut glibc_minimum = arguments.glibc_minimum;
            if let Some(build_info_path) = arguments.build_info {
                let build_info =
                    read_json_bounded(&build_info_path, SIDECAR_LIMIT).map_err(|_| {
                        verification("ARM64 runtime build information is missing or invalid")
                    })?;
                if build_info.get("architecture").and_then(Value::as_str) != Some("aarch64") {
                    return Err(verification(
                        "ARM64 runtime validation did not report aarch64",
                    ));
                }
                if build_info.get("target").and_then(Value::as_str)
                    != Some("aarch64-unknown-linux-gnu")
                {
                    return Err(verification(
                        "ARM64 runtime validation did not report the GNU target",
                    ));
                }
                let measured = build_info
                    .get("glibc_minimum")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        verification(
                            "ARM64 runtime validation did not report a valid glibc version",
                        )
                    })?;
                if !valid_version(measured) {
                    return Err(verification(
                        "ARM64 runtime validation did not report a valid glibc version",
                    ));
                }
                if glibc_minimum
                    .as_deref()
                    .is_some_and(|provided| provided != measured)
                {
                    return Err(verification(
                        "provided glibc version does not match the validated runtime",
                    ));
                }
                glibc_minimum = Some(measured.to_owned());
            }
            let manifest = make_manifest(
                &arguments.release_dir,
                &arguments.commit,
                "github",
                arguments.project_root.as_deref(),
                glibc_minimum.as_deref(),
            )?;
            let _ = manifest;
            let manifest =
                validate_release_dir(&arguments.release_dir, &arguments.commit, Some("github"))?;
            if manifest["target"]["triple"] != "aarch64-unknown-linux-gnu" {
                return Err(verification(
                    "CI releases must target aarch64-unknown-linux-gnu",
                ));
            }
            package_release(&arguments.release_dir, &arguments.output, &arguments.commit)?;
            println!("{}", arguments.output.display());
            Ok(())
        }
        Action::SealLocal(arguments) => {
            make_manifest(
                &arguments.release_dir,
                &arguments.commit,
                "source",
                arguments.project_root.as_deref(),
                None,
            )?;
            validate_release_dir(&arguments.release_dir, &arguments.commit, Some("source"))?;
            Ok(())
        }
        Action::MigrateLegacyStatic(arguments) => migrate_legacy_static(
            &arguments.release_dir,
            &arguments.static_dir,
            &arguments.commit,
            arguments.project_root.as_deref(),
        ),
        Action::Validate(arguments) => {
            let manifest = validate_release_dir(
                &arguments.release_dir,
                &arguments.commit,
                arguments.origin.as_deref(),
            )?;
            if arguments.check_runtime {
                check_runtime_compatibility(&manifest)?;
            }
            Ok(())
        }
        Action::Install(arguments) => {
            let started = Instant::now();
            let target = install_release(&arguments).await?;
            println!("{}", target.display());
            eprintln!(
                "Artifact download and verification: {:.2} s",
                started.elapsed().as_secs_f64()
            );
            Ok(())
        }
        Action::ExtractBinary(arguments) => {
            extract_cached_binary(
                &arguments.cache_dir,
                &arguments.commit,
                &arguments.binary,
                &arguments.destination,
            )?;
            Ok(())
        }
    }
}

#[derive(Debug, Clone)]
struct InventoryEntry {
    relative: String,
    path: PathBuf,
    directory: bool,
    size: u64,
    mode: u32,
}

fn valid_commit(commit: &str) -> bool {
    commit.len() == 40
        && commit
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn release_name(commit: &str) -> String {
    format!("lumistripe-linux-aarch64-{commit}{RELEASE_SUFFIX}")
}

fn sha256_reader(mut reader: impl Read) -> io::Result<String> {
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(hex_lower(&digest.finalize()))
}

fn sha256_file(path: &Path) -> Result<String> {
    let file = open_read_nofollow(path)
        .map_err(|error| io_verification("could not read release file", error))?;
    sha256_reader(file).map_err(|error| io_verification("could not hash release file", error))
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        result.push(HEX[(byte >> 4) as usize] as char);
        result.push(HEX[(byte & 0x0f) as usize] as char);
    }
    result
}

fn open_read_nofollow(path: &Path) -> io::Result<File> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
}

fn read_bounded(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    let file = open_read_nofollow(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "file is not regular or exceeds size limit",
        ));
    }
    let mut value = Vec::with_capacity(metadata.len() as usize);
    file.take(limit + 1).read_to_end(&mut value)?;
    if value.len() as u64 > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "file exceeds size limit",
        ));
    }
    Ok(value)
}

fn read_json_bounded(path: &Path, limit: u64) -> io::Result<Value> {
    let bytes = read_bounded(path, limit)?;
    serde_json::from_slice(&bytes)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

fn allowed_release_path(relative: &str, directory: bool) -> bool {
    if directory {
        relative == "static" || relative.starts_with("static/")
    } else {
        RELEASE_BINARIES.contains(&relative)
            || relative == "source-commit"
            || relative == "manifest.json"
            || (relative.starts_with("static/") && relative != "static/")
    }
}

fn collect_inventory(root: &Path) -> Result<Vec<InventoryEntry>> {
    check_path_ancestors(root, true)?;
    let root_metadata = fs::symlink_metadata(root)
        .map_err(|error| io_verification("release directory is missing", error))?;
    if !root_metadata.is_dir() || root_metadata.file_type().is_symlink() {
        return Err(verification(format!(
            "release directory is not a real directory: {}",
            root.display()
        )));
    }
    let mut entries = Vec::new();
    collect_directory(root, root, &mut entries)?;
    entries.sort_by(|left, right| left.relative.cmp(&right.relative));
    if entries.len() + 1 > ENTRY_LIMIT
        || entries
            .iter()
            .filter(|entry| !entry.directory)
            .map(|entry| entry.size)
            .sum::<u64>()
            > EXPANDED_LIMIT
    {
        return Err(verification(
            "release directory exceeds the supported size or entry limit",
        ));
    }
    Ok(entries)
}

fn collect_directory(root: &Path, current: &Path, entries: &mut Vec<InventoryEntry>) -> Result<()> {
    let mut children = fs::read_dir(current)
        .map_err(|error| io_verification("could not inspect release directory", error))?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<io::Result<Vec<_>>>()
        .map_err(|error| io_verification("could not inspect release directory", error))?;
    children.sort();
    for path in children {
        let metadata = fs::symlink_metadata(&path)
            .map_err(|error| io_verification("could not inspect release entry", error))?;
        if metadata.file_type().is_symlink() {
            return Err(verification(format!(
                "release contains a symlink: {}",
                path.display()
            )));
        }
        let relative_path = path
            .strip_prefix(root)
            .map_err(|error| io_verification("release path escaped its root", error))?;
        if relative_path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
        {
            return Err(verification("release contains an unsafe relative path"));
        }
        let relative = relative_path
            .to_str()
            .ok_or_else(|| verification("release contains a non-UTF-8 path"))?
            .replace(std::path::MAIN_SEPARATOR, "/");
        let directory = metadata.is_dir();
        if !directory && !metadata.is_file() {
            return Err(verification(format!(
                "release contains a non-regular file: {relative}"
            )));
        }
        if !allowed_release_path(&relative, directory) {
            return Err(verification(if directory {
                format!("unexpected release directory: {relative}")
            } else {
                format!("unexpected release file: {relative}")
            }));
        }
        if metadata.len() > FILE_LIMIT {
            return Err(verification(format!(
                "release file is too large: {relative}"
            )));
        }
        entries.push(InventoryEntry {
            relative,
            path: path.clone(),
            directory,
            size: metadata.len(),
            mode: metadata.mode() & 0o7777,
        });
        if directory {
            collect_directory(root, &path, entries)?;
        }
    }
    Ok(())
}

fn valid_version(value: &str) -> bool {
    Regex::new(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
        .expect("static version expression")
        .is_match(value)
}

fn version_tuple(value: &str) -> Option<(u64, u64, u64)> {
    if !valid_version(value) {
        return None;
    }
    let mut parts = value.split('.').map(str::parse::<u64>);
    Some((
        parts.next()?.ok()?,
        parts.next()?.ok()?,
        parts.next().map_or(Some(0), |part| part.ok())?,
    ))
}

fn current_glibc_version() -> Option<String> {
    let output = std::process::Command::new("getconf")
        .arg("GNU_LIBC_VERSION")
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let value = String::from_utf8(output.stdout).ok()?;
    let version = value.split_whitespace().nth(1)?.trim();
    valid_version(version).then(|| version.to_owned())
}

fn bun_version(project_root: Option<&Path>) -> String {
    project_root
        .and_then(|root| read_bounded(&root.join(".bun-version"), 256).ok())
        .and_then(|bytes| String::from_utf8(bytes).ok())
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".to_owned())
}

fn make_manifest(
    release_dir: &Path,
    commit: &str,
    origin: &str,
    project_root: Option<&Path>,
    glibc_minimum: Option<&str>,
) -> Result<Value> {
    if !valid_commit(commit) {
        return Err(verification(
            "source commit must be a full lowercase Git SHA",
        ));
    }
    if !matches!(origin, "github" | "source") {
        return Err(verification("release origin is invalid"));
    }
    match fs::symlink_metadata(release_dir) {
        Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
        Ok(_) => return Err(verification("release directory is not a real directory")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            check_path_ancestors(release_dir, false)?;
            fs::create_dir_all(release_dir)
                .map_err(|error| io_verification("could not create release directory", error))?;
        }
        Err(error) => {
            return Err(io_verification(
                "could not inspect release directory",
                error,
            ));
        }
    }
    let marker = release_dir.join("source-commit");
    atomic_write(&marker, format!("{commit}\n").as_bytes(), 0o644)?;
    let entries = collect_inventory(release_dir)?;
    let files: BTreeMap<String, &InventoryEntry> = entries
        .iter()
        .filter(|entry| !entry.directory)
        .map(|entry| (entry.relative.clone(), entry))
        .collect();
    for binary in RELEASE_BINARIES {
        let entry = files
            .get(binary)
            .ok_or_else(|| verification(format!("release is missing binaries: {binary}")))?;
        if entry.mode & 0o111 == 0 {
            return Err(verification(format!(
                "release binary is not executable: {binary}"
            )));
        }
    }
    let index = files
        .get("static/index.html")
        .ok_or_else(|| verification("release is missing a non-empty static/index.html"))?;
    if index.size == 0 || !files.keys().any(|name| name.starts_with("static/assets/")) {
        return Err(verification("release frontend is missing static assets"));
    }
    let mut checksums = Map::new();
    for (name, entry) in &files {
        if name == "manifest.json" {
            continue;
        }
        checksums.insert(
            name.clone(),
            json!({ "sha256": sha256_file(&entry.path)?, "size": entry.size }),
        );
    }
    let architecture = if origin == "github" {
        "aarch64".to_owned()
    } else {
        std::env::consts::ARCH.to_owned()
    };
    let abi_minimum = glibc_minimum
        .map(str::to_owned)
        .or_else(current_glibc_version)
        .unwrap_or_else(|| "unknown".to_owned());
    let manifest = json!({
        "release_format": FORMAT_VERSION,
        "source_commit": commit,
        "origin": origin,
        "target": {
            "triple": if origin == "github" { "aarch64-unknown-linux-gnu" } else { "host" },
            "architecture": architecture,
            "abi": "GNU/Linux"
        },
        "runtime_abi": {
            "distribution": if origin == "github" { "debian-trixie" } else { "local-host" },
            "glibc_minimum": abi_minimum,
            "libraries": LIBRARIES
        },
        "toolchain": {
            "rust": "1.99.1",
            "bun": bun_version(project_root),
            "builder": if origin == "github" { "debian:trixie-slim" } else { "local" }
        },
        "binaries": RELEASE_BINARIES,
        "required_runtime_features": RELEASE_FEATURES,
        "checksums": Value::Object(checksums)
    });
    atomic_write(
        &release_dir.join("manifest.json"),
        &json_compact(&manifest),
        0o644,
    )?;
    Ok(manifest)
}

fn validate_release_dir(
    release_dir: &Path,
    commit: &str,
    expected_origin: Option<&str>,
) -> Result<Value> {
    if !valid_commit(commit) {
        return Err(verification(
            "source commit must be a full lowercase Git SHA",
        ));
    }
    let entries = collect_inventory(release_dir)?;
    let files: BTreeMap<String, &InventoryEntry> = entries
        .iter()
        .filter(|entry| !entry.directory)
        .map(|entry| (entry.relative.clone(), entry))
        .collect();
    for required in [
        RELEASE_BINARIES[0],
        RELEASE_BINARIES[1],
        RELEASE_BINARIES[2],
        RELEASE_BINARIES[3],
        "source-commit",
        "manifest.json",
        "static/index.html",
    ] {
        if !files.contains_key(required) {
            return Err(verification(format!(
                "release is incomplete; missing: {required}"
            )));
        }
    }
    let marker = read_bounded(&files["source-commit"].path, 128)
        .map_err(|error| io_verification("source-commit marker is missing or invalid", error))?;
    let marker = std::str::from_utf8(&marker)
        .map_err(|error| io_verification("source-commit marker is invalid", error))?
        .trim();
    if marker != commit {
        return Err(verification(
            "source-commit does not match the requested commit",
        ));
    }
    let manifest = read_json_bounded(&files["manifest.json"].path, FILE_LIMIT)
        .map_err(|_| verification("release manifest is missing or invalid JSON"))?;
    if !manifest.is_object() {
        return Err(verification("release manifest must be a JSON object"));
    }
    if manifest.get("release_format").and_then(Value::as_u64) != Some(FORMAT_VERSION) {
        return Err(verification("unsupported release manifest format"));
    }
    if manifest.get("source_commit").and_then(Value::as_str) != Some(commit) {
        return Err(verification(
            "manifest source commit does not match the requested commit",
        ));
    }
    let origin = manifest
        .get("origin")
        .and_then(Value::as_str)
        .ok_or_else(|| verification("release manifest origin is invalid"))?;
    if !matches!(origin, "github" | "source") {
        return Err(verification("release manifest origin is invalid"));
    }
    if expected_origin.is_some_and(|expected| expected != origin) {
        return Err(verification(format!(
            "release origin is not {}",
            expected_origin.unwrap_or_default()
        )));
    }
    if manifest.get("binaries") != Some(&json!(RELEASE_BINARIES)) {
        return Err(verification(
            "manifest binary list is incomplete or unexpected",
        ));
    }
    if manifest.get("required_runtime_features") != Some(&json!(RELEASE_FEATURES)) {
        return Err(verification(
            "manifest runtime features are incomplete or unexpected",
        ));
    }
    let target = manifest
        .get("target")
        .and_then(Value::as_object)
        .ok_or_else(|| verification("manifest target is invalid"))?;
    let triple = target
        .get("triple")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let architecture = target
        .get("architecture")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !matches!(triple, "aarch64-unknown-linux-gnu" | "host") || architecture.is_empty() {
        return Err(verification("manifest target is invalid"));
    }
    if origin == "github" && (triple != "aarch64-unknown-linux-gnu" || architecture != "aarch64") {
        return Err(verification(
            "GitHub release target must be aarch64-unknown-linux-gnu",
        ));
    }
    if origin == "source" && triple != "host" {
        return Err(verification(
            "source release target must describe the local host",
        ));
    }
    if target.get("abi").and_then(Value::as_str) != Some("GNU/Linux") {
        return Err(verification("manifest target ABI is invalid"));
    }
    let runtime = manifest
        .get("runtime_abi")
        .and_then(Value::as_object)
        .ok_or_else(|| verification("manifest runtime ABI is invalid"))?;
    let minimum = runtime
        .get("glibc_minimum")
        .and_then(Value::as_str)
        .ok_or_else(|| verification("manifest runtime ABI is invalid"))?;
    let expected_distribution = if origin == "github" {
        "debian-trixie"
    } else {
        "local-host"
    };
    if runtime.get("distribution").and_then(Value::as_str) != Some(expected_distribution) {
        return Err(verification(
            "manifest runtime distribution does not match its origin",
        ));
    }
    if origin == "github" && !valid_version(minimum) {
        return Err(verification("manifest glibc minimum version is invalid"));
    }
    let libraries = runtime
        .get("libraries")
        .and_then(Value::as_array)
        .ok_or_else(|| verification("manifest runtime library list is invalid"))?;
    if libraries.is_empty()
        || libraries
            .iter()
            .any(|value| value.as_str().is_none_or(str::is_empty))
    {
        return Err(verification("manifest runtime library list is invalid"));
    }
    let toolchain = manifest
        .get("toolchain")
        .and_then(Value::as_object)
        .ok_or_else(|| verification("manifest build toolchain is invalid"))?;
    if toolchain.get("rust").and_then(Value::as_str) != Some("1.99.1")
        || toolchain
            .get("bun")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
        || toolchain.get("builder").and_then(Value::as_str).is_none()
    {
        return Err(verification("manifest build toolchain is invalid"));
    }
    if origin == "github"
        && (!Regex::new(r"^[0-9]+\.[0-9]+\.[0-9]+$")
            .expect("static Bun version expression")
            .is_match(toolchain["bun"].as_str().unwrap_or_default())
            || toolchain.get("builder").and_then(Value::as_str) != Some("debian:trixie-slim"))
    {
        return Err(verification(
            "GitHub release toolchain does not match the CI build",
        ));
    }
    let checksums = manifest
        .get("checksums")
        .and_then(Value::as_object)
        .ok_or_else(|| verification("manifest checksums are missing"))?;
    let expected_names: BTreeSet<&str> = files
        .keys()
        .map(String::as_str)
        .filter(|name| *name != "manifest.json")
        .collect();
    let actual_names: BTreeSet<&str> = checksums.keys().map(String::as_str).collect();
    if expected_names != actual_names {
        return Err(verification(
            "manifest checksums do not cover exactly the release files",
        ));
    }
    for (name, entry) in &files {
        if name == "manifest.json" {
            continue;
        }
        if RELEASE_BINARIES.contains(&name.as_str()) && entry.mode & 0o111 == 0 {
            return Err(verification(format!(
                "release binary is not executable: {name}"
            )));
        }
        if !RELEASE_BINARIES.contains(&name.as_str()) && entry.mode & 0o111 != 0 {
            return Err(verification(format!(
                "non-executable release file has executable permissions: {name}"
            )));
        }
        let checksum = checksums
            .get(name)
            .and_then(Value::as_object)
            .ok_or_else(|| verification(format!("manifest checksum entry is invalid: {name}")))?;
        if checksum.get("sha256").and_then(Value::as_str)
            != Some(sha256_file(&entry.path)?.as_str())
            || checksum.get("size").and_then(Value::as_u64) != Some(entry.size)
        {
            return Err(verification(format!(
                "release checksum does not match: {name}"
            )));
        }
    }
    if !files.keys().any(|name| name.starts_with("static/assets/")) {
        return Err(verification(
            "release frontend is missing static/assets files",
        ));
    }
    Ok(manifest)
}

fn json_compact(value: &Value) -> Vec<u8> {
    let mut bytes = serde_json::to_vec(value).expect("JSON value serialization is infallible");
    bytes.push(b'\n');
    bytes
}

fn atomic_write(destination: &Path, contents: &[u8], mode: u32) -> Result<()> {
    let parent = destination
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    check_path_ancestors(parent, true)?;
    fs::create_dir_all(parent)
        .map_err(|error| io_verification("could not create output directory", error))?;
    let file_name = destination
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| verification("atomic output has an invalid filename"))?;
    let mut temporary = None;
    for attempt in 0..16 {
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
                temporary = Some((candidate, file));
                break;
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(io_verification("could not create atomic output", error)),
        }
    }
    let (temporary_path, mut file) =
        temporary.ok_or_else(|| verification("could not allocate atomic output"))?;
    let result = (|| -> io::Result<()> {
        file.write_all(contents)?;
        file.sync_all()?;
        file.set_permissions(fs::Permissions::from_mode(mode))?;
        fs::rename(&temporary_path, destination)?;
        File::open(parent)?.sync_all()?;
        Ok(())
    })();
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary_path);
        return Err(io_verification("could not atomically write file", error));
    }
    Ok(())
}

fn check_path_ancestors(path: &Path, include_leaf: bool) -> Result<()> {
    let mut current = if path.is_absolute() {
        PathBuf::from("/")
    } else {
        std::env::current_dir()
            .map_err(|error| io_verification("could not resolve current directory", error))?
    };
    let components: Vec<_> = path.components().collect();
    for (index, component) in components.iter().enumerate() {
        match component {
            Component::RootDir => continue,
            Component::CurDir => continue,
            Component::ParentDir => {
                return Err(verification("filesystem path contains a parent traversal"));
            }
            Component::Normal(name) => current.push(name),
            Component::Prefix(_) => {
                return Err(verification(
                    "filesystem path contains an unsupported prefix",
                ));
            }
        }
        if !include_leaf && index + 1 == components.len() {
            continue;
        }
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(verification(format!(
                    "filesystem path contains a symlink: {}",
                    current.display()
                )));
            }
            Ok(metadata) if !metadata.is_dir() => {
                return Err(verification(format!(
                    "filesystem path parent is not a directory: {}",
                    current.display()
                )));
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(io_verification("could not inspect filesystem path", error)),
        }
    }
    Ok(())
}

fn check_runtime_compatibility(manifest: &Value) -> Result<()> {
    if manifest["target"]["triple"].as_str() != Some("aarch64-unknown-linux-gnu") {
        return Err(verification(
            "release manifest target does not match aarch64 Linux",
        ));
    }
    if manifest["runtime_abi"]["distribution"].as_str() != Some("debian-trixie") {
        return Err(verification(
            "release manifest is not built for the Debian Trixie runtime ABI",
        ));
    }
    let minimum = manifest["runtime_abi"]["glibc_minimum"]
        .as_str()
        .ok_or_else(|| verification("release manifest has an invalid glibc minimum version"))?;
    let minimum_version = version_tuple(minimum)
        .ok_or_else(|| verification("release manifest has an invalid glibc minimum version"))?;
    let current = current_glibc_version().ok_or_else(|| {
        ArtifactError::Unsupported("could not determine the installed glibc version".to_owned())
    })?;
    let current_version = version_tuple(&current).ok_or_else(|| {
        ArtifactError::Unsupported("could not determine the installed glibc version".to_owned())
    })?;
    if minimum_version > current_version {
        return Err(ArtifactError::Unsupported(format!(
            "release requires glibc {minimum}; this system has glibc {current}"
        )));
    }
    Ok(())
}

fn parse_os_release(path: &Path) -> BTreeMap<String, String> {
    let Ok(bytes) = read_bounded(path, 1024 * 1024) else {
        return BTreeMap::new();
    };
    let Ok(text) = String::from_utf8(bytes) else {
        return BTreeMap::new();
    };
    let mut values = BTreeMap::new();
    for line in text.lines() {
        let Some((key, raw)) = line.split_once('=') else {
            continue;
        };
        if key.is_empty()
            || !key
                .bytes()
                .all(|byte| byte.is_ascii_uppercase() || byte == b'_' || byte.is_ascii_digit())
        {
            continue;
        }
        let value = raw.trim();
        let value = if value.len() >= 2
            && ((value.starts_with('"') && value.ends_with('"'))
                || (value.starts_with('\'') && value.ends_with('\'')))
        {
            &value[1..value.len() - 1]
        } else {
            value
        };
        values.insert(key.to_owned(), value.to_owned());
    }
    values
}

fn target_supported() -> bool {
    if std::env::consts::ARCH != "aarch64" {
        return false;
    }
    let release = parse_os_release(Path::new("/etc/os-release"));
    let id = release.get("ID").map(String::as_str).unwrap_or_default();
    let codename = release
        .get("VERSION_CODENAME")
        .map(String::as_str)
        .unwrap_or_default();
    (matches!(id, "debian" | "raspbian") && codename == "trixie")
        || (id == "ubuntu" && codename == "resolute")
}

fn migrate_legacy_static(
    release_dir: &Path,
    static_source: &Path,
    commit: &str,
    project_root: Option<&Path>,
) -> Result<()> {
    check_path_ancestors(release_dir, true)?;
    check_path_ancestors(static_source, true)?;
    if !valid_commit(commit) {
        return Err(verification(
            "source commit must be a full lowercase Git SHA",
        ));
    }
    let release_metadata = fs::symlink_metadata(release_dir)
        .map_err(|error| io_verification("legacy release directory is missing", error))?;
    if !release_metadata.is_dir() || release_metadata.file_type().is_symlink() {
        return Err(verification(
            "legacy release directory is not a real directory",
        ));
    }
    let marker_path = release_dir.join("source-commit");
    let marker = read_bounded(&marker_path, 128)
        .map_err(|error| io_verification("legacy release marker is missing or invalid", error))?;
    if std::str::from_utf8(&marker).map(str::trim).ok() != Some(commit) {
        return Err(verification(
            "legacy release marker does not match the requested commit",
        ));
    }
    let destination = release_dir.join("static");
    if destination.exists() || fs::symlink_metadata(&destination).is_ok() {
        return Err(verification(
            "legacy release already contains an incomplete static directory",
        ));
    }
    let source_metadata = fs::symlink_metadata(static_source)
        .map_err(|_| verification("legacy frontend static directory is missing"))?;
    if !source_metadata.is_dir() || source_metadata.file_type().is_symlink() {
        return Err(verification("legacy frontend static directory is missing"));
    }
    let entries = collect_static_inventory(static_source)?;
    let files: Vec<_> = entries.iter().filter(|entry| !entry.directory).collect();
    if !files.iter().any(|entry| entry.relative == "index.html") {
        return Err(verification("legacy frontend index.html is missing"));
    }
    if !files
        .iter()
        .any(|entry| entry.relative.starts_with("assets/"))
    {
        return Err(verification("legacy frontend assets are missing"));
    }
    let temporary = release_dir.join(format!(".static-migration.{}", std::process::id()));
    if temporary.exists() || fs::symlink_metadata(&temporary).is_ok() {
        return Err(verification(
            "legacy release has a leftover static migration directory",
        ));
    }
    fs::create_dir(&temporary)
        .map_err(|error| io_verification("could not create frontend migration stage", error))?;
    let old_manifest = release_dir.join("manifest.json");
    let old_manifest_bytes = if old_manifest.exists() || fs::symlink_metadata(&old_manifest).is_ok()
    {
        Some(
            read_bounded(&old_manifest, FILE_LIMIT)
                .map_err(|error| io_verification("legacy release manifest is invalid", error))?,
        )
    } else {
        None
    };
    let result = (|| {
        for entry in &entries {
            let target = temporary.join(&entry.relative);
            if entry.directory {
                fs::create_dir_all(&target).map_err(|error| {
                    io_verification("could not copy legacy frontend directory", error)
                })?;
                fs::set_permissions(&target, fs::Permissions::from_mode(0o755)).map_err(
                    |error| io_verification("could not set migrated frontend permissions", error),
                )?;
            } else {
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent).map_err(|error| {
                        io_verification("could not copy legacy frontend directory", error)
                    })?;
                }
                copy_file_nofollow(&entry.path, &target, 0o644, entry.size)?;
            }
        }
        fs::rename(&temporary, &destination)
            .map_err(|error| io_verification("could not install migrated frontend", error))?;
        if old_manifest.exists() || fs::symlink_metadata(&old_manifest).is_ok() {
            fs::remove_file(&old_manifest).map_err(|error| {
                io_verification("could not replace legacy release manifest", error)
            })?;
        }
        make_manifest(release_dir, commit, "source", project_root, None)?;
        validate_release_dir(release_dir, commit, Some("source"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(&temporary);
        let _ = fs::remove_dir_all(&destination);
        if let Some(previous) = old_manifest_bytes {
            let _ = atomic_write(&old_manifest, &previous, 0o644);
        }
    }
    result
}

fn collect_static_inventory(root: &Path) -> Result<Vec<InventoryEntry>> {
    let mut entries = Vec::new();
    fn walk(
        root: &Path,
        current: &Path,
        entries: &mut Vec<InventoryEntry>,
        total: &mut u64,
    ) -> Result<()> {
        let mut children = fs::read_dir(current)
            .map_err(|error| io_verification("could not inspect legacy frontend", error))?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<io::Result<Vec<_>>>()
            .map_err(|error| io_verification("could not inspect legacy frontend", error))?;
        children.sort();
        for path in children {
            let metadata = fs::symlink_metadata(&path).map_err(|error| {
                io_verification("could not inspect legacy frontend entry", error)
            })?;
            if metadata.file_type().is_symlink() || (!metadata.is_dir() && !metadata.is_file()) {
                return Err(verification(
                    "legacy frontend contains a symlink or non-regular file",
                ));
            }
            let relative = path
                .strip_prefix(root)
                .map_err(|error| io_verification("legacy frontend path escaped its root", error))?
                .to_str()
                .ok_or_else(|| verification("legacy frontend contains a non-UTF-8 path"))?
                .replace(std::path::MAIN_SEPARATOR, "/");
            if metadata.len() > FILE_LIMIT {
                return Err(verification(
                    "legacy frontend exceeds the migration size limit",
                ));
            }
            if metadata.is_file() {
                *total = total.saturating_add(metadata.len());
                if *total > EXPANDED_LIMIT {
                    return Err(verification(
                        "legacy frontend exceeds the migration size limit",
                    ));
                }
            }
            entries.push(InventoryEntry {
                relative,
                path: path.clone(),
                directory: metadata.is_dir(),
                size: metadata.len(),
                mode: metadata.mode() & 0o7777,
            });
            if metadata.is_dir() {
                walk(root, &path, entries, total)?;
            }
            if entries.len() > ENTRY_LIMIT {
                return Err(verification("legacy frontend contains too many entries"));
            }
        }
        Ok(())
    }
    walk(root, root, &mut entries, &mut 0)?;
    entries.sort_by(|left, right| left.relative.cmp(&right.relative));
    Ok(entries)
}

fn copy_file_nofollow(
    source: &Path,
    destination: &Path,
    mode: u32,
    expected_size: u64,
) -> Result<()> {
    check_path_ancestors(destination, false)?;
    let mut input = open_read_nofollow(source)
        .map_err(|error| io_verification("could not open release file", error))?;
    let metadata = input
        .metadata()
        .map_err(|error| io_verification("could not inspect release file", error))?;
    if !metadata.is_file() || metadata.len() != expected_size {
        return Err(verification(
            "release file changed while it was being copied",
        ));
    }
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(destination)
        .map_err(|error| io_verification("could not create staged release file", error))?;
    io::copy(&mut input, &mut output)
        .map_err(|error| io_verification("could not copy staged release file", error))?;
    output
        .sync_all()
        .map_err(|error| io_verification("could not sync staged release file", error))?;
    output
        .set_permissions(fs::Permissions::from_mode(mode))
        .map_err(|error| io_verification("could not set staged release permissions", error))?;
    Ok(())
}

fn package_release(release_dir: &Path, output_path: &Path, commit: &str) -> Result<()> {
    if !valid_commit(commit) {
        return Err(verification(
            "source commit must be a full lowercase Git SHA",
        ));
    }
    let entries = collect_inventory(release_dir)?;
    let output_parent = output_path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    check_path_ancestors(output_path, false)?;
    check_path_ancestors(output_parent, true)?;
    fs::create_dir_all(output_parent)
        .map_err(|error| io_verification("could not create release archive directory", error))?;
    let mut temporary_path = None;
    let mut raw_output = None;
    for attempt in 0..16 {
        let candidate = output_parent.join(format!(
            ".{}.{}.{}.tmp",
            output_path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("release"),
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
                raw_output = Some(file);
                break;
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(io_verification(
                    "could not create temporary release archive",
                    error,
                ));
            }
        }
    }
    let temporary_path = temporary_path
        .ok_or_else(|| verification("could not allocate temporary release archive"))?;
    let mut raw_output =
        raw_output.ok_or_else(|| verification("could not allocate temporary release archive"))?;
    let result = (|| -> Result<()> {
        let mut gzip = GzBuilder::new()
            .mtime(0)
            .write(&mut raw_output, Compression::best());
        let mut tar_size = 0_u64;
        let root_header = make_tar_header("release/", true, 0, 0o755)?;
        gzip.write_all(&root_header)
            .map_err(|error| io_verification("could not write release archive", error))?;
        tar_size += TAR_BLOCK as u64;
        for entry in &entries {
            let archive_name = if entry.directory {
                format!("release/{}/", entry.relative)
            } else {
                format!("release/{}", entry.relative)
            };
            let mode = if !entry.directory && RELEASE_BINARIES.contains(&entry.relative.as_str()) {
                0o755
            } else if entry.directory {
                0o755
            } else {
                0o644
            };
            let header = make_tar_header(&archive_name, entry.directory, entry.size, mode)?;
            gzip.write_all(&header)
                .map_err(|error| io_verification("could not write release archive", error))?;
            tar_size += TAR_BLOCK as u64;
            if !entry.directory {
                let input = open_read_nofollow(&entry.path).map_err(|error| {
                    io_verification("could not read release file for packaging", error)
                })?;
                let metadata = input.metadata().map_err(|error| {
                    io_verification("could not inspect release file for packaging", error)
                })?;
                if !metadata.is_file() || metadata.len() != entry.size {
                    return Err(verification(
                        "release file changed while the archive was being packaged",
                    ));
                }
                let copied = io::copy(&mut input.take(entry.size), &mut gzip)
                    .map_err(|error| io_verification("could not package release file", error))?;
                if copied != entry.size {
                    return Err(verification(
                        "release file changed while the archive was being packaged",
                    ));
                }
                tar_size = tar_size.saturating_add(entry.size);
                let padding = ((TAR_BLOCK as u64 - entry.size % TAR_BLOCK as u64)
                    % TAR_BLOCK as u64) as usize;
                if padding > 0 {
                    gzip.write_all(&[0; TAR_BLOCK][..padding])
                        .map_err(|error| {
                            io_verification("could not pad release archive entry", error)
                        })?;
                    tar_size += padding as u64;
                }
            }
            if tar_size > EXPANDED_LIMIT {
                return Err(verification(
                    "release archive exceeds the extraction size limit",
                ));
            }
        }
        gzip.write_all(&[0; TAR_BLOCK * 2])
            .map_err(|error| io_verification("could not finish release archive", error))?;
        tar_size += (TAR_BLOCK * 2) as u64;
        let record_padding =
            ((TAR_RECORD as u64 - tar_size % TAR_RECORD as u64) % TAR_RECORD as u64) as usize;
        if record_padding > 0 {
            let zeros = vec![0; record_padding];
            gzip.write_all(&zeros)
                .map_err(|error| io_verification("could not finish release archive", error))?;
        }
        gzip.finish()
            .map_err(|error| io_verification("could not finish release archive", error))?;
        raw_output
            .sync_all()
            .map_err(|error| io_verification("could not sync release archive", error))?;
        if raw_output
            .metadata()
            .map_err(|error| io_verification("could not inspect release archive", error))?
            .len()
            > ARCHIVE_LIMIT
        {
            return Err(verification(
                "release archive exceeds the 512 MiB package limit",
            ));
        }
        raw_output
            .set_permissions(fs::Permissions::from_mode(0o644))
            .map_err(|error| io_verification("could not set release archive permissions", error))?;
        fs::rename(&temporary_path, output_path).map_err(|error| {
            io_verification("could not atomically install release archive", error)
        })?;
        File::open(output_parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| io_verification("could not sync release archive directory", error))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary_path);
    }
    result
}

fn make_tar_header(name: &str, directory: bool, size: u64, mode: u32) -> Result<[u8; TAR_BLOCK]> {
    let name = name.as_bytes();
    let mut header = [0_u8; TAR_BLOCK];
    let (name_part, prefix_part) = if name.len() <= 100 {
        (name, &[][..])
    } else {
        let mut split = None;
        for (index, _) in name
            .iter()
            .enumerate()
            .filter(|(_, byte)| **byte == b'/')
            .rev()
        {
            if index <= 155 && name.len() - index - 1 <= 100 {
                split = Some(index);
                break;
            }
        }
        let index = split
            .ok_or_else(|| verification("release archive path exceeds the USTAR path limit"))?;
        (&name[index + 1..], &name[..index])
    };
    header[..name_part.len()].copy_from_slice(name_part);
    put_octal(&mut header[100..108], mode as u64)?;
    put_octal(&mut header[108..116], 0)?;
    put_octal(&mut header[116..124], 0)?;
    put_octal(&mut header[124..136], if directory { 0 } else { size })?;
    put_octal(&mut header[136..148], 0)?;
    header[148..156].fill(b' ');
    header[156] = if directory { b'5' } else { b'0' };
    header[257..263].copy_from_slice(b"ustar\0");
    header[263..265].copy_from_slice(b"00");
    put_octal(&mut header[329..337], 0)?;
    put_octal(&mut header[337..345], 0)?;
    header[345..345 + prefix_part.len()].copy_from_slice(prefix_part);
    let checksum: u64 = header.iter().map(|byte| u64::from(*byte)).sum();
    let octal = format!("{checksum:06o}");
    if octal.len() != 6 {
        return Err(verification("release archive header checksum overflowed"));
    }
    header[148..154].copy_from_slice(octal.as_bytes());
    header[154] = 0;
    header[155] = b' ';
    Ok(header)
}

fn put_octal(field: &mut [u8], value: u64) -> Result<()> {
    if field.is_empty() {
        return Err(verification("release archive header is invalid"));
    }
    let digits = format!("{value:o}");
    if digits.len() + 1 > field.len() {
        return Err(verification(
            "release archive field exceeds the USTAR limit",
        ));
    }
    field.fill(b'0');
    let start = field.len() - digits.len() - 1;
    field[start..start + digits.len()].copy_from_slice(digits.as_bytes());
    field[field.len() - 1] = 0;
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct TarEntry {
    name: String,
    directory: bool,
    size: u64,
    mode: u32,
}

fn parse_octal(field: &[u8]) -> Result<u64> {
    let trimmed = field
        .iter()
        .copied()
        .take_while(|byte| *byte != 0 && *byte != b' ')
        .collect::<Vec<_>>();
    if trimmed.is_empty() || trimmed.iter().any(|byte| !(b'0'..=b'7').contains(byte)) {
        return Err(verification(
            "release archive contains an invalid numeric header field",
        ));
    }
    let text = std::str::from_utf8(&trimmed)
        .map_err(|_| verification("release archive contains an invalid numeric header field"))?;
    u64::from_str_radix(text, 8)
        .map_err(|_| verification("release archive contains an overflowing numeric header field"))
}

fn field_text(field: &[u8]) -> Result<String> {
    let end = field
        .iter()
        .position(|byte| *byte == 0)
        .unwrap_or(field.len());
    if field[end..].iter().any(|byte| *byte != 0) {
        return Err(verification(
            "release archive header has data after a string terminator",
        ));
    }
    let value = std::str::from_utf8(&field[..end])
        .map_err(|_| verification("release archive contains a non-UTF-8 path"))?;
    Ok(value.to_owned())
}

fn archive_member_name(header: &[u8; TAR_BLOCK], directory: bool) -> Result<String> {
    let name = field_text(&header[..100])?;
    let prefix = field_text(&header[345..500])?;
    let mut value = if prefix.is_empty() {
        name
    } else if name.is_empty() {
        return Err(verification(
            "release archive contains an invalid USTAR path",
        ));
    } else {
        format!("{prefix}/{name}")
    };
    if directory && !value.ends_with('/') {
        value.push('/');
    }
    safe_archive_name(&value)
}

fn safe_archive_name(name: &str) -> Result<String> {
    if name.is_empty()
        || name.contains('\\')
        || name.contains('\0')
        || name.chars().any(char::is_control)
        || name.len() > 1024
    {
        return Err(verification(
            "release archive contains an invalid or overlong path",
        ));
    }
    let normalized = name.strip_suffix('/').unwrap_or(name);
    let components: Vec<_> = normalized.split('/').collect();
    if normalized.starts_with('/')
        || normalized.is_empty()
        || components
            .iter()
            .any(|part| part.is_empty() || *part == "." || *part == "..")
        || components[0] != "release"
    {
        return Err(verification(format!(
            "release archive contains an unsafe path: {name:?}"
        )));
    }
    if components.join("/") != normalized {
        return Err(verification(format!(
            "release archive path is outside release/: {name:?}"
        )));
    }
    Ok(normalized.to_owned())
}

fn check_tar_header(
    header: &[u8; TAR_BLOCK],
    seen: &mut BTreeSet<String>,
    types: &mut BTreeMap<String, bool>,
) -> Result<TarEntry> {
    let stored_checksum = parse_octal(&header[148..156])?;
    let checksum: u64 = header
        .iter()
        .enumerate()
        .map(|(index, byte)| {
            if (148..156).contains(&index) {
                u64::from(b' ')
            } else {
                u64::from(*byte)
            }
        })
        .sum();
    if stored_checksum != checksum || &header[257..263] != b"ustar\0" || &header[263..265] != b"00"
    {
        return Err(verification(
            "release archive contains an invalid USTAR header",
        ));
    }
    let file_type = header[156];
    let directory = file_type == b'5';
    if !directory && file_type != 0 && file_type != b'0' {
        return Err(verification(
            "release archive contains an unsupported entry type",
        ));
    }
    let name = archive_member_name(header, directory)?;
    if !seen.insert(name.clone()) {
        return Err(verification(format!(
            "release archive contains a duplicate path: {name}"
        )));
    }
    if directory {
        let relative = name.strip_prefix("release/").unwrap_or_default();
        if name != "release" && !(relative == "static" || relative.starts_with("static/")) {
            return Err(verification(format!(
                "release archive contains an unexpected directory: {name}"
            )));
        }
    } else if name != "release"
        && !RELEASE_BINARIES.contains(&name.strip_prefix("release/").unwrap_or(&name))
        && name != "release/source-commit"
        && name != "release/manifest.json"
        && !(name.starts_with("release/static/") && name != "release/static/")
    {
        return Err(verification(format!(
            "release archive contains an unexpected path: {name}"
        )));
    }
    let mode = parse_octal(&header[100..108])? as u32;
    if mode & 0o7000 != 0 {
        return Err(verification(format!(
            "release archive entry has unsafe mode bits: {name}"
        )));
    }
    let uid = parse_octal(&header[108..116])?;
    let gid = parse_octal(&header[116..124])?;
    if uid != 0 || gid != 0 {
        return Err(verification(format!(
            "release archive entry has unexpected ownership: {name}"
        )));
    }
    if header[157..257].iter().any(|byte| *byte != 0)
        || header[265..329].iter().any(|byte| *byte != 0)
        || parse_octal(&header[329..337])? != 0
        || parse_octal(&header[337..345])? != 0
    {
        return Err(verification(format!(
            "release archive contains unsupported metadata: {name}"
        )));
    }
    let size = parse_octal(&header[124..136])?;
    if size > FILE_LIMIT || (directory && size != 0) {
        return Err(verification(format!(
            "release archive entry exceeds the file limit: {name}"
        )));
    }
    if !directory
        && RELEASE_BINARIES.contains(&name.strip_prefix("release/").unwrap_or(&name))
        && mode & 0o111 == 0
    {
        return Err(verification(format!(
            "release binary is not executable in archive: {name}"
        )));
    }
    if !directory
        && !RELEASE_BINARIES.contains(&name.strip_prefix("release/").unwrap_or(&name))
        && mode & 0o111 != 0
    {
        return Err(verification(format!(
            "non-executable release file is executable in archive: {name}"
        )));
    }
    if name == "release" && !directory {
        return Err(verification("release archive root must be a directory"));
    }
    if let Some((parent, _)) = name.rsplit_once('/') {
        if types.get(parent).is_some_and(|is_directory| !is_directory) {
            return Err(verification(
                "release archive contains a file as a parent directory",
            ));
        }
    }
    if !directory
        && types
            .keys()
            .any(|previous| previous.starts_with(&format!("{name}/")))
    {
        return Err(verification(
            "release archive file conflicts with a directory entry",
        ));
    }
    types.insert(name.clone(), directory);
    Ok(TarEntry {
        name,
        directory,
        size,
        mode,
    })
}

fn read_exact_tar_block(reader: &mut impl Read) -> Result<Option<[u8; TAR_BLOCK]>> {
    let mut block = [0_u8; TAR_BLOCK];
    let mut filled = 0;
    while filled < TAR_BLOCK {
        match reader.read(&mut block[filled..]) {
            Ok(0) if filled == 0 => return Ok(None),
            Ok(0) => return Err(verification("release archive ends inside a USTAR header")),
            Ok(read) => filled += read,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(io_verification("could not read release archive", error)),
        }
    }
    Ok(Some(block))
}

fn skip_exact(reader: &mut impl Read, mut remaining: u64) -> Result<()> {
    let mut buffer = [0_u8; 64 * 1024];
    while remaining > 0 {
        let amount = remaining.min(buffer.len() as u64) as usize;
        let read = reader
            .read(&mut buffer[..amount])
            .map_err(|error| io_verification("could not read release archive entry", error))?;
        if read == 0 {
            return Err(verification("release archive ends inside a file entry"));
        }
        remaining -= read as u64;
    }
    Ok(())
}

fn first_pass_archive(archive_path: &Path) -> Result<Vec<TarEntry>> {
    let file = open_read_nofollow(archive_path)
        .map_err(|error| io_verification("release archive is not a regular file", error))?;
    let metadata = file
        .metadata()
        .map_err(|error| io_verification("could not inspect release archive", error))?;
    if !metadata.is_file() {
        return Err(verification("release archive is not a regular file"));
    }
    if metadata.len() > ARCHIVE_LIMIT {
        return Err(verification(
            "release archive exceeds the 512 MiB download limit",
        ));
    }
    let mut reader = GzDecoder::new(BufReader::new(file));
    let mut entries = Vec::new();
    let mut seen = BTreeSet::new();
    let mut types = BTreeMap::new();
    let mut expanded = 0_u64;
    let mut ended = false;
    loop {
        let Some(header) = read_exact_tar_block(&mut reader)? else {
            break;
        };
        expanded = expanded.saturating_add(TAR_BLOCK as u64);
        if expanded > EXPANDED_LIMIT {
            return Err(verification(
                "release archive exceeds the extraction size limit",
            ));
        }
        if header.iter().all(|byte| *byte == 0) {
            let second = read_exact_tar_block(&mut reader)?
                .ok_or_else(|| verification("release archive has an incomplete end marker"))?;
            expanded = expanded.saturating_add(TAR_BLOCK as u64);
            if second.iter().any(|byte| *byte != 0) {
                return Err(verification("release archive has an invalid end marker"));
            }
            ended = true;
            break;
        }
        if entries.len() >= ENTRY_LIMIT {
            return Err(verification("release archive contains too many entries"));
        }
        let entry = check_tar_header(&header, &mut seen, &mut types)?;
        if !entry.directory {
            expanded = expanded.saturating_add(entry.size);
            if expanded > EXPANDED_LIMIT {
                return Err(verification(
                    "release archive exceeds the extraction size limit",
                ));
            }
        }
        skip_exact(&mut reader, entry.size)?;
        let padding = (TAR_BLOCK as u64 - entry.size % TAR_BLOCK as u64) % TAR_BLOCK as u64;
        skip_exact(&mut reader, padding)?;
        expanded = expanded.saturating_add(padding);
        if expanded > EXPANDED_LIMIT {
            return Err(verification(
                "release archive exceeds the extraction size limit",
            ));
        }
        entries.push(entry);
    }
    if !ended || !types.get("release").copied().unwrap_or(false) {
        return Err(verification(
            "release archive is missing its root directory or end marker",
        ));
    }
    let required = [
        "release/lumistripe-web",
        "release/lumistripe-engine",
        "release/lumistripe",
        "release/lumistripe-deploy-helper",
        "release/source-commit",
        "release/manifest.json",
        "release/static/index.html",
    ];
    if required
        .iter()
        .any(|path| !types.get(*path).is_some_and(|directory| !directory))
    {
        return Err(verification(
            "release archive is missing required binaries or frontend files",
        ));
    }
    if !types
        .keys()
        .any(|path| path.starts_with("release/static/assets/"))
    {
        return Err(verification("release archive is missing frontend assets"));
    }
    let mut trailing = [0_u8; 4096];
    loop {
        let read = reader
            .read(&mut trailing)
            .map_err(|error| io_verification("could not finish reading release archive", error))?;
        if read == 0 {
            break;
        }
        if trailing[..read].iter().any(|byte| *byte != 0) {
            return Err(verification(
                "release archive has unexpected data after its end marker",
            ));
        }
        expanded = expanded.saturating_add(read as u64);
        if expanded > EXPANDED_LIMIT {
            return Err(verification(
                "release archive exceeds the extraction size limit",
            ));
        }
    }
    Ok(entries)
}

fn consume_archive_end(reader: &mut impl Read) -> Result<()> {
    for _ in 0..2 {
        let block = read_exact_tar_block(reader)?
            .ok_or_else(|| verification("release archive has an incomplete end marker"))?;
        if block.iter().any(|byte| *byte != 0) {
            return Err(verification("release archive has an invalid end marker"));
        }
    }
    let mut trailing = [0_u8; 4096];
    let mut total = (TAR_BLOCK * 2) as u64;
    loop {
        let read = reader.read(&mut trailing).map_err(|error| {
            io_verification("could not finish extracting release archive", error)
        })?;
        if read == 0 {
            break;
        }
        total = total.saturating_add(read as u64);
        if total > EXPANDED_LIMIT || trailing[..read].iter().any(|byte| *byte != 0) {
            return Err(verification("release archive has unexpected trailing data"));
        }
    }
    Ok(())
}

fn extract_archive(archive_path: &Path, destination: &Path, commit: &str) -> Result<PathBuf> {
    if !valid_commit(commit) {
        return Err(verification(
            "release commit must be a full lowercase Git SHA",
        ));
    }
    if fs::symlink_metadata(destination).is_ok() {
        return Err(verification(format!(
            "release extraction destination already exists: {}",
            destination.display()
        )));
    }
    check_path_ancestors(destination, false)?;
    let entries = first_pass_archive(archive_path)?;
    fs::create_dir_all(destination.parent().unwrap_or_else(|| Path::new(".")))
        .map_err(|error| io_verification("could not create release extraction parent", error))?;
    fs::create_dir(destination)
        .map_err(|error| io_verification("could not create release extraction directory", error))?;
    let release_root = destination.join("release");
    let result = (|| {
        fs::create_dir(&release_root)
            .map_err(|error| io_verification("could not create extracted release root", error))?;
        let file = open_read_nofollow(archive_path)
            .map_err(|error| io_verification("could not reopen release archive", error))?;
        let mut reader = GzDecoder::new(BufReader::new(file));
        let mut seen = BTreeSet::new();
        let mut types = BTreeMap::new();
        for entry in &entries {
            let header = read_exact_tar_block(&mut reader)?.ok_or_else(|| {
                verification("release archive changed between validation and extraction")
            })?;
            let check_entry = check_tar_header(&header, &mut seen, &mut types)?;
            if &check_entry != entry {
                return Err(verification(
                    "release archive changed between validation and extraction",
                ));
            }
            let name = entry.name.strip_prefix("release/").unwrap_or("");
            if entry.name == "release" {
                skip_exact(&mut reader, entry.size)?;
                continue;
            }
            let target = release_root.join(name);
            if entry.directory {
                fs::create_dir_all(&target).map_err(|error| {
                    io_verification("could not create extracted release directory", error)
                })?;
                fs::set_permissions(&target, fs::Permissions::from_mode(0o755)).map_err(
                    |error| io_verification("could not set extracted directory permissions", error),
                )?;
            } else {
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent).map_err(|error| {
                        io_verification("could not create extracted release directory", error)
                    })?;
                }
                let mode = if RELEASE_BINARIES.contains(&name) {
                    0o755
                } else {
                    0o644
                };
                let mut output = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
                    .open(&target)
                    .map_err(|error| {
                        io_verification("could not safely create extracted release file", error)
                    })?;
                let copied = io::copy(&mut (&mut reader).take(entry.size), &mut output)
                    .map_err(|error| io_verification("could not extract release file", error))?;
                if copied != entry.size {
                    return Err(verification(
                        "release archive changed between validation and extraction",
                    ));
                }
                output.sync_all().map_err(|error| {
                    io_verification("could not sync extracted release file", error)
                })?;
                output
                    .set_permissions(fs::Permissions::from_mode(mode))
                    .map_err(|error| {
                        io_verification("could not set extracted file permissions", error)
                    })?;
                let padding = (TAR_BLOCK as u64 - entry.size % TAR_BLOCK as u64) % TAR_BLOCK as u64;
                skip_exact(&mut reader, padding)?;
            }
        }
        consume_archive_end(&mut reader)?;
        let manifest = validate_release_dir(&release_root, commit, Some("github"))?;
        let _ = manifest;
        Ok(release_root.clone())
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(destination);
    }
    result
}

fn github_client(allowed_hosts: &'static [&'static str]) -> Result<reqwest::Client> {
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(45))
        .redirect(reqwest::redirect::Policy::custom(move |attempt| {
            let url = attempt.url();
            let host_ok = url.host_str().is_some_and(|host| {
                allowed_hosts
                    .iter()
                    .any(|allowed| host.eq_ignore_ascii_case(allowed))
            });
            if attempt.previous().len() < MAX_REDIRECTS
                && url.scheme() == "https"
                && url.port_or_known_default() == Some(443)
                && url.username().is_empty()
                && url.password().is_none()
                && host_ok
            {
                attempt.follow()
            } else {
                attempt.stop()
            }
        }))
        .build()
        .map_err(|_| general("could not initialize the GitHub download client"))?;
    Ok(client)
}

fn safe_github_url(raw: &str, allowed_hosts: &[&str]) -> Result<Url> {
    let url =
        Url::parse(raw).map_err(|_| verification("GitHub returned an invalid download URL"))?;
    let host_ok = url.host_str().is_some_and(|host| {
        allowed_hosts
            .iter()
            .any(|allowed| host.eq_ignore_ascii_case(allowed))
    });
    if url.scheme() != "https"
        || url.port_or_known_default() != Some(443)
        || !url.username().is_empty()
        || url.password().is_some()
        || !host_ok
    {
        return Err(verification(
            "GitHub returned a non-HTTPS or unexpected download URL",
        ));
    }
    Ok(url)
}

async fn read_response_bounded(response: reqwest::Response, limit: u64) -> Result<Vec<u8>> {
    if !response
        .headers()
        .get(CONTENT_ENCODING)
        .is_none_or(|value| value.as_bytes().eq_ignore_ascii_case(b"identity"))
    {
        return Err(verification(
            "GitHub response used unexpected HTTP compression",
        ));
    }
    if response
        .content_length()
        .is_some_and(|length| length > limit)
    {
        return Err(verification(
            "GitHub response exceeds the configured size limit",
        ));
    }
    let mut body = Vec::new();
    let mut stream = response.bytes_stream();
    use futures_util::StreamExt;
    loop {
        let next = timeout(Duration::from_secs(30), stream.next())
            .await
            .map_err(|_| general("GitHub response timed out"))?;
        let Some(next) = next else { break };
        let chunk = next.map_err(|_| general("GitHub response could not be read"))?;
        if (body.len() as u64).saturating_add(chunk.len() as u64) > limit {
            return Err(verification(
                "GitHub response exceeds the configured size limit",
            ));
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body)
}

async fn request_json(url: &str) -> Result<(u16, Option<Value>)> {
    let url = safe_github_url(url, &["api.github.com"])?;
    let client = github_client(&["api.github.com"])?;
    let response = client
        .get(url)
        .header(ACCEPT, "application/vnd.github+json")
        .header("X-GitHub-Api-Version", "2022-11-28")
        .header(ACCEPT_ENCODING, "identity")
        .header(
            reqwest::header::USER_AGENT,
            "LumiStripe-release-installer/1",
        )
        .send()
        .await
        .map_err(|_| general("GitHub request failed"))?;
    let status = response.status().as_u16();
    if matches!(status, 404 | 410) {
        return Ok((status, None));
    }
    if status == 403
        && response
            .headers()
            .get("X-RateLimit-Remaining")
            .is_some_and(|value| value == "0")
    {
        return Err(general(
            "GitHub API rate limit reached; retry the update check later",
        ));
    }
    if !(200..300).contains(&status) {
        return Err(general(format!("GitHub request failed with HTTP {status}")));
    }
    if !safe_github_url(response.url().as_str(), &["api.github.com"]).is_ok() {
        return Err(verification(
            "GitHub API request left the expected HTTPS host",
        ));
    }
    let body = read_response_bounded(response, SIDECAR_LIMIT).await?;
    let value: Value = serde_json::from_slice(&body)
        .map_err(|_| verification("GitHub returned invalid JSON metadata"))?;
    if !value.is_object() {
        return Err(verification(
            "GitHub returned an unexpected metadata document",
        ));
    }
    Ok((status, Some(value)))
}

#[derive(Debug)]
struct ReleaseAsset {
    size: u64,
    digest: String,
    url: String,
}

#[derive(Debug)]
struct GitHubRelease {
    id: u64,
    assets: BTreeMap<String, ReleaseAsset>,
}

async fn release_assets(commit: &str) -> Result<GitHubRelease> {
    let tag = format!("{RELEASE_PREFIX}{commit}");
    let metadata_url = format!("{API_ROOT}/releases/tags/{tag}");
    let (status, release) = request_json(&metadata_url).await?;
    if status == 404 || release.is_none() {
        return Err(ArtifactError::Missing(format!(
            "no prebuilt release is published for commit {commit}"
        )));
    }
    let release =
        release.ok_or_else(|| verification("GitHub returned an empty release document"))?;
    if release.get("tag_name").and_then(Value::as_str) != Some(tag.as_str())
        || release.get("draft").and_then(Value::as_bool) != Some(false)
        || release.get("prerelease").and_then(Value::as_bool) != Some(false)
        || release.get("immutable").and_then(Value::as_bool) != Some(true)
    {
        return Err(verification(
            "GitHub release metadata does not identify a published immutable release for the requested commit",
        ));
    }
    let asset_values = release
        .get("assets")
        .and_then(Value::as_array)
        .ok_or_else(|| verification("GitHub release metadata has no asset list"))?;
    let mut assets = BTreeMap::new();
    for asset in asset_values {
        let name = asset
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| verification("GitHub release contains malformed asset metadata"))?;
        let size = asset
            .get("size")
            .and_then(Value::as_u64)
            .ok_or_else(|| verification("GitHub release asset has an invalid size"))?;
        let digest = asset
            .get("digest")
            .and_then(Value::as_str)
            .ok_or_else(|| verification("GitHub release asset has no SHA-256 digest"))?;
        let url = asset
            .get("browser_download_url")
            .and_then(Value::as_str)
            .ok_or_else(|| verification("GitHub release asset has no download URL"))?;
        if assets.contains_key(name) {
            return Err(verification(
                "GitHub release contains duplicate asset names",
            ));
        }
        assets.insert(
            name.to_owned(),
            ReleaseAsset {
                size,
                digest: digest.to_owned(),
                url: url.to_owned(),
            },
        );
    }
    Ok(GitHubRelease {
        id: release.get("id").and_then(Value::as_u64).unwrap_or(0),
        assets,
    })
}

async fn download_asset(
    asset: &ReleaseAsset,
    expected_name: &str,
    destination: &Path,
    limit: u64,
) -> Result<String> {
    if !Regex::new(r"^sha256:[0-9a-f]{64}$")
        .expect("static SHA-256 expression")
        .is_match(&asset.digest)
    {
        return Err(verification(format!(
            "GitHub release does not publish a SHA-256 digest for {expected_name}"
        )));
    }
    if asset.size == 0 || asset.size > limit {
        return Err(verification(format!(
            "GitHub release asset has an invalid size: {expected_name}"
        )));
    }
    let url = safe_github_url(&asset.url, &DOWNLOAD_HOSTS)?;
    let client = github_client(&DOWNLOAD_HOSTS)?;
    let response = client
        .get(url)
        .header(ACCEPT, "application/octet-stream")
        .header(ACCEPT_ENCODING, "identity")
        .header(
            reqwest::header::USER_AGENT,
            "LumiStripe-release-installer/1",
        )
        .send()
        .await
        .map_err(|_| {
            general(format!(
                "could not download GitHub release asset {expected_name}"
            ))
        })?;
    if !response.status().is_success() {
        return Err(general(format!(
            "GitHub release asset {expected_name} returned HTTP {}",
            response.status().as_u16()
        )));
    }
    safe_github_url(response.url().as_str(), &DOWNLOAD_HOSTS)?;
    if response
        .headers()
        .get(CONTENT_ENCODING)
        .is_some_and(|value| !value.as_bytes().eq_ignore_ascii_case(b"identity"))
    {
        return Err(verification(
            "GitHub asset used unexpected HTTP compression",
        ));
    }
    if response
        .content_length()
        .is_some_and(|length| length != asset.size)
    {
        return Err(verification(format!(
            "GitHub asset size does not match its publication metadata: {expected_name}"
        )));
    }
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(destination)
        .map_err(|error| io_verification("could not create private release download", error))?;
    let mut digest = Sha256::new();
    let mut total = 0_u64;
    use futures_util::StreamExt;
    let mut stream = response.bytes_stream();
    let result = async {
        loop {
            let chunk = timeout(Duration::from_secs(45), stream.next())
                .await
                .map_err(|_| {
                    general(format!(
                        "download timed out for GitHub release asset {expected_name}"
                    ))
                })?;
            let Some(chunk) = chunk else { break };
            let chunk = chunk.map_err(|_| {
                general(format!(
                    "could not read GitHub release asset {expected_name}"
                ))
            })?;
            total = total.saturating_add(chunk.len() as u64);
            if total > limit || total > asset.size {
                return Err(verification(format!(
                    "GitHub release asset exceeds the download limit: {expected_name}"
                )));
            }
            digest.update(&chunk);
            output.write_all(&chunk).map_err(|error| {
                io_verification("could not write downloaded release asset", error)
            })?;
        }
        if total != asset.size {
            return Err(verification(format!(
                "downloaded asset size does not match GitHub metadata: {expected_name}"
            )));
        }
        output
            .sync_all()
            .map_err(|error| io_verification("could not sync downloaded release asset", error))?;
        let actual = hex_lower(&digest.finalize());
        if actual != asset.digest.strip_prefix("sha256:").unwrap_or_default() {
            return Err(verification(format!(
                "downloaded asset digest does not match GitHub metadata: {expected_name}"
            )));
        }
        Ok(actual)
    }
    .await;
    if result.is_err() {
        drop(output);
        let _ = fs::remove_file(destination);
    }
    result
}

fn parse_checksum(bytes: &[u8], archive_name: &str) -> Result<String> {
    let text = std::str::from_utf8(bytes)
        .map_err(|_| verification("release checksum asset is missing or invalid"))?;
    let line = text.strip_suffix('\n').unwrap_or(text);
    if line.contains(['\n', '\r']) {
        return Err(verification("release checksum asset is invalid"));
    }
    let (digest, name) = line
        .split_once(' ')
        .ok_or_else(|| verification("release checksum asset is invalid"))?;
    let name = name
        .strip_prefix('*')
        .or_else(|| name.strip_prefix(' '))
        .unwrap_or(name);
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || name != archive_name
    {
        return Err(verification(
            "release checksum asset does not name the requested archive",
        ));
    }
    Ok(digest.to_owned())
}

async fn verify_attestation(
    archive: &Path,
    bundle: &Path,
    commit: &str,
    gh_path: &Path,
) -> Result<()> {
    let mut version_command = TokioCommand::new(gh_path);
    version_command
        .arg("--version")
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", "/root")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let version = timeout(Duration::from_secs(10), version_command.output())
        .await
        .map_err(|_| general("GitHub CLI version check timed out"))?
        .map_err(|_| general("GitHub CLI is required to verify release provenance"))?;
    if !version.status.success() {
        return Err(general(
            "GitHub CLI is required to verify release provenance",
        ));
    }
    let version_text = String::from_utf8_lossy(&version.stdout);
    let re =
        Regex::new(r"gh version ([0-9]+)\.([0-9]+)").expect("static GitHub CLI version expression");
    let captures = re.captures(&version_text).ok_or_else(|| {
        general("GitHub CLI 2.67 or newer is required for release attestation verification")
    })?;
    let major = captures
        .get(1)
        .and_then(|value| value.as_str().parse::<u64>().ok())
        .unwrap_or(0);
    let minor = captures
        .get(2)
        .and_then(|value| value.as_str().parse::<u64>().ok())
        .unwrap_or(0);
    if (major, minor) < (2, 67) {
        return Err(general(
            "GitHub CLI 2.67 or newer is required for release attestation verification",
        ));
    }
    let mut command = TokioCommand::new(gh_path);
    command
        .args(["attestation", "verify"])
        .arg(archive)
        .args([
            "--repo",
            REPOSITORY,
            "--signer-workflow",
            SIGNER_WORKFLOW,
            "--source-digest",
            commit,
            "--deny-self-hosted-runners",
            "--bundle",
        ])
        .arg(bundle)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", "/root")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let output = timeout(Duration::from_secs(120), command.output())
        .await
        .map_err(|_| general("release attestation verification timed out"))?
        .map_err(|_| general("release attestation verification could not complete"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail = if detail.trim().is_empty() {
            String::from_utf8_lossy(&output.stdout).to_string()
        } else {
            detail.to_string()
        };
        return Err(verification(format!(
            "GitHub provenance verification failed: {}",
            detail.trim().chars().take(1000).collect::<String>()
        )));
    }
    Ok(())
}

fn ensure_directory(path: &Path, mode: u32) -> Result<()> {
    check_path_ancestors(path, true)?;
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if !metadata.is_dir() || metadata.file_type().is_symlink() {
                return Err(verification(format!(
                    "directory is not a real directory: {}",
                    path.display()
                )));
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            fs::create_dir_all(path)
                .map_err(|error| io_verification("could not create directory", error))?;
        }
        Err(error) => return Err(io_verification("could not inspect directory", error)),
    }
    if unsafe { libc::geteuid() } == 0 {
        set_owner(path, 0, 0)?;
        fs::set_permissions(path, fs::Permissions::from_mode(mode)).map_err(|error| {
            io_verification("could not set protected directory permissions", error)
        })?;
    }
    Ok(())
}

fn set_owner(path: &Path, uid: u32, gid: u32) -> Result<()> {
    use std::os::unix::ffi::OsStrExt;
    let path = std::ffi::CString::new(path.as_os_str().as_bytes())
        .map_err(|_| verification("filesystem path contains a NUL byte"))?;
    let status = unsafe { libc::lchown(path.as_ptr(), uid, gid) };
    if status != 0 {
        return Err(io_verification(
            "could not set release ownership",
            io::Error::last_os_error(),
        ));
    }
    Ok(())
}

fn check_trusted_file(path: &Path) -> Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| io_verification("verified release cache is incomplete", error))?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(verification(format!(
            "verified release cache contains an unsafe file: {}",
            path.display()
        )));
    }
    if unsafe { libc::geteuid() } == 0 && metadata.uid() != 0 {
        return Err(verification(
            "previously verified release cache is not root-owned",
        ));
    }
    if metadata.mode() & 0o022 != 0 {
        return Err(verification(
            "previously verified release cache is writable by another user",
        ));
    }
    Ok(metadata)
}

fn verify_cache_dir(cache_dir: &Path, commit: &str) -> Result<Value> {
    let metadata = fs::symlink_metadata(cache_dir)
        .map_err(|error| io_verification("previously verified release cache is missing", error))?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(verification(
            "previously verified release cache is not a real directory",
        ));
    }
    if unsafe { libc::geteuid() } == 0 && metadata.uid() != 0 {
        return Err(verification(
            "previously verified release cache directory is not root-owned",
        ));
    }
    if unsafe { libc::geteuid() } == 0 && metadata.mode() & 0o077 != 0 {
        return Err(verification(
            "previously verified release cache directory is accessible to other users",
        ));
    }
    let marker_path = cache_dir.join("verified.json");
    let archive_path = cache_dir.join("bundle.tar.gz");
    let bundle_path = cache_dir.join("bundle.sigstore.json");
    let checksum_path = cache_dir.join("bundle.sha256");
    let manifest_path = cache_dir.join("manifest.json");
    let extracted_release = cache_dir.join("release");
    check_trusted_file(&marker_path)?;
    let marker = read_json_bounded(&marker_path, SIDECAR_LIMIT)
        .map_err(|_| verification("previously verified release cache marker is invalid"))?;
    for path in [&archive_path, &bundle_path, &checksum_path, &manifest_path] {
        check_trusted_file(path)?;
    }
    let extracted_metadata = fs::symlink_metadata(&extracted_release).map_err(|error| {
        io_verification("previously verified release cache is incomplete", error)
    })?;
    if !extracted_metadata.is_dir()
        || extracted_metadata.file_type().is_symlink()
        || (unsafe { libc::geteuid() } == 0 && extracted_metadata.uid() != 0)
        || extracted_metadata.mode() & 0o022 != 0
    {
        return Err(verification(
            "previously verified cached release directory is unsafe",
        ));
    }
    if marker.get("source_commit").and_then(Value::as_str) != Some(commit)
        || marker.get("verified").and_then(Value::as_bool) != Some(true)
    {
        return Err(verification(
            "previously verified release cache does not match the requested commit",
        ));
    }
    let archive_digest = sha256_file(&archive_path)?;
    if marker.get("archive_sha256").and_then(Value::as_str) != Some(archive_digest.as_str()) {
        return Err(verification(
            "previously verified release cache archive was modified",
        ));
    }
    let checksum = read_bounded(&checksum_path, SIDECAR_LIMIT).map_err(|error| {
        io_verification("previously verified release checksum is invalid", error)
    })?;
    if parse_checksum(&checksum, &release_name(commit))? != archive_digest {
        return Err(verification(
            "previously verified release cache checksum was modified",
        ));
    }
    let cached_manifest = read_json_bounded(&manifest_path, SIDECAR_LIMIT)
        .map_err(|_| verification("previously verified release manifest is invalid"))?;
    let extracted_manifest = validate_release_dir(&extracted_release, commit, Some("github"))?;
    if cached_manifest != extracted_manifest {
        return Err(verification(
            "previously verified release cache manifest does not match its staged files",
        ));
    }
    Ok(marker)
}

async fn install_release(arguments: &InstallArgs) -> Result<PathBuf> {
    if !valid_commit(&arguments.commit) {
        return Err(verification(
            "release commit must be a full lowercase Git SHA",
        ));
    }
    if !arguments.skip_target_check && !target_supported() {
        return Err(ArtifactError::Unsupported(
            "prebuilt releases require aarch64 Debian or Raspberry Pi OS Trixie, or aarch64 Ubuntu 26.04 LTS".to_owned(),
        ));
    }
    ensure_directory(&arguments.release_root, 0o755)?;
    ensure_directory(&arguments.cache_root, 0o755)?;
    let cache_dir = arguments.cache_root.join(&arguments.commit);
    let cache_exists = fs::symlink_metadata(&cache_dir).is_ok();
    if !cache_exists {
        if arguments.cached_only {
            return Err(ArtifactError::Missing(format!(
                "no previously verified cached release exists for commit {}",
                arguments.commit
            )));
        }
        download_verified_cache(arguments, &cache_dir).await?;
    }
    let _marker = verify_cache_dir(&cache_dir, &arguments.commit)?;
    let target = arguments.release_root.join(&arguments.commit);
    if fs::symlink_metadata(&target).is_ok() {
        let existing = validate_release_dir(&target, &arguments.commit, Some("github"))
            .map_err(|error| verification(format!("refusing to overwrite an existing incomplete release directory: {target:?}: {error}")))?;
        let expected = read_json_bounded(&cache_dir.join("manifest.json"), SIDECAR_LIMIT)
            .map_err(|_| verification("verified release cache manifest is invalid"))?;
        if existing != expected {
            return Err(verification(
                "existing release manifest differs from its verified bundle",
            ));
        }
        return Ok(target);
    }
    let stage = tempfile::Builder::new()
        .prefix(&format!(".stage.{}.", arguments.commit))
        .tempdir_in(&arguments.release_root)
        .map_err(|error| io_verification("could not create release staging directory", error))?;
    let extracted = extract_archive(
        &cache_dir.join("bundle.tar.gz"),
        &stage.path().join("extract"),
        &arguments.commit,
    )?;
    let staged_release = stage.path().join("release");
    fs::rename(&extracted, &staged_release)
        .map_err(|error| io_verification("could not prepare release staging directory", error))?;
    if let Some(parent) = extracted.parent() {
        fs::remove_dir(parent).map_err(|error| {
            io_verification("could not finalize release staging directory", error)
        })?;
    }
    let manifest = validate_release_dir(&staged_release, &arguments.commit, Some("github"))?;
    let expected = read_json_bounded(&cache_dir.join("manifest.json"), SIDECAR_LIMIT)
        .map_err(|_| verification("verified release cache manifest is invalid"))?;
    if manifest != expected {
        return Err(verification(
            "verified archive manifest changed after cache validation",
        ));
    }
    if let Some(helper_destination) = arguments.helper_destination.as_deref() {
        install_verified_helper(
            &staged_release.join("lumistripe-deploy-helper"),
            helper_destination,
        )?;
    }
    set_tree_owner_mode(&staged_release, arguments.uid, arguments.gid, false)?;
    set_owner(stage.path(), arguments.uid, arguments.gid)?;
    fs::rename(&staged_release, &target)
        .map_err(|error| io_verification("could not atomically activate staged release", error))?;
    Ok(target)
}

async fn download_verified_cache(arguments: &InstallArgs, cache_dir: &Path) -> Result<()> {
    let release = release_assets(&arguments.commit).await?;
    let archive_name = release_name(&arguments.commit);
    let expected_names = [
        archive_name.clone(),
        format!("{archive_name}{CHECKSUM_SUFFIX}"),
        format!("{archive_name}{ATTESTATION_SUFFIX}"),
        format!("{archive_name}{MANIFEST_SUFFIX}"),
    ];
    let missing: Vec<_> = expected_names
        .iter()
        .filter(|name| !release.assets.contains_key(*name))
        .cloned()
        .collect();
    if !missing.is_empty() {
        return Err(verification(format!(
            "published release is missing required assets: {}",
            missing.join(", ")
        )));
    }
    let temporary = tempfile::Builder::new()
        .prefix(&format!(".release-download.{}.", arguments.commit))
        .tempdir_in(&arguments.cache_root)
        .map_err(|error| {
            io_verification("could not create private release download directory", error)
        })?;
    let archive_path = temporary.path().join("bundle.tar.gz.download");
    let checksum_path = temporary.path().join("bundle.sha256.download");
    let bundle_path = temporary.path().join("bundle.sigstore.json.download");
    let sidecar_manifest_path = temporary.path().join("sidecar-manifest.json");
    let archive_asset = &release.assets[&expected_names[0]];
    let checksum_asset = &release.assets[&expected_names[1]];
    let bundle_asset = &release.assets[&expected_names[2]];
    let manifest_asset = &release.assets[&expected_names[3]];
    let archive_digest = download_asset(
        archive_asset,
        &expected_names[0],
        &archive_path,
        ARCHIVE_LIMIT,
    )
    .await?;
    download_asset(
        checksum_asset,
        &expected_names[1],
        &checksum_path,
        SIDECAR_LIMIT,
    )
    .await?;
    download_asset(
        bundle_asset,
        &expected_names[2],
        &bundle_path,
        SIDECAR_LIMIT,
    )
    .await?;
    download_asset(
        manifest_asset,
        &expected_names[3],
        &sidecar_manifest_path,
        SIDECAR_LIMIT,
    )
    .await?;
    let checksum = read_bounded(&checksum_path, SIDECAR_LIMIT)
        .map_err(|error| io_verification("release checksum asset is missing or invalid", error))?;
    if parse_checksum(&checksum, &archive_name)? != archive_digest {
        return Err(verification(
            "release SHA-256 sidecar does not match GitHub's asset digest",
        ));
    }
    verify_attestation(
        &archive_path,
        &bundle_path,
        &arguments.commit,
        &arguments.gh,
    )
    .await?;
    let extracted_root = temporary.path().join("extracted");
    let extracted_release = extract_archive(&archive_path, &extracted_root, &arguments.commit)?;
    let manifest = validate_release_dir(&extracted_release, &arguments.commit, Some("github"))?;
    let sidecar_manifest = read_json_bounded(&sidecar_manifest_path, SIDECAR_LIMIT)
        .map_err(|_| verification("release manifest asset is invalid JSON"))?;
    if sidecar_manifest != manifest {
        return Err(verification(
            "release manifest asset does not match the manifest in the archive",
        ));
    }
    check_runtime_compatibility(&manifest)?;

    fs::rename(&archive_path, temporary.path().join("bundle.tar.gz"))
        .map_err(|error| io_verification("could not prepare verified release cache", error))?;
    fs::rename(&checksum_path, temporary.path().join("bundle.sha256"))
        .map_err(|error| io_verification("could not prepare verified release cache", error))?;
    fs::rename(&bundle_path, temporary.path().join("bundle.sigstore.json"))
        .map_err(|error| io_verification("could not prepare verified release cache", error))?;
    fs::rename(
        &sidecar_manifest_path,
        temporary.path().join("manifest.json"),
    )
    .map_err(|error| io_verification("could not prepare verified release cache", error))?;
    let cached_release = temporary.path().join("release");
    fs::rename(&extracted_release, &cached_release)
        .map_err(|error| io_verification("could not prepare verified release cache", error))?;
    fs::remove_dir(&extracted_root)
        .map_err(|error| io_verification("could not prepare verified release cache", error))?;
    let marker = json!({
        "source_commit": arguments.commit,
        "release_id": release.id,
        "archive_sha256": archive_digest,
        "verified": true,
        "verification": "GitHub Actions SLSA provenance and GitHub release asset digest"
    });
    atomic_write(
        &temporary.path().join("verified.json"),
        &json_compact(&marker),
        0o444,
    )?;
    set_tree_owner_mode(&cached_release, 0, 0, true)?;
    for filename in [
        "bundle.tar.gz",
        "bundle.sha256",
        "bundle.sigstore.json",
        "manifest.json",
        "verified.json",
    ] {
        let path = temporary.path().join(filename);
        if unsafe { libc::geteuid() } == 0 {
            set_owner(&path, 0, 0)?;
        }
        fs::set_permissions(&path, fs::Permissions::from_mode(0o444))
            .map_err(|error| io_verification("could not protect verified cache file", error))?;
    }
    fs::set_permissions(&cached_release, fs::Permissions::from_mode(0o555))
        .map_err(|error| io_verification("could not protect cached release files", error))?;
    if unsafe { libc::geteuid() } == 0 {
        set_owner(temporary.path(), 0, 0)?;
    }
    fs::set_permissions(temporary.path(), fs::Permissions::from_mode(0o700))
        .map_err(|error| io_verification("could not protect verified cache directory", error))?;
    let temporary_path = temporary.keep();
    if fs::symlink_metadata(cache_dir).is_ok() {
        let _ = fs::remove_dir_all(&temporary_path);
        return Err(verification(
            "a cache entry already exists for this commit and cannot be replaced",
        ));
    }
    if let Err(error) = fs::rename(&temporary_path, cache_dir) {
        let _ = fs::remove_dir_all(&temporary_path);
        return Err(io_verification(
            "could not atomically install verified release cache",
            error,
        ));
    }
    File::open(&arguments.cache_root)
        .and_then(|directory| directory.sync_all())
        .map_err(|error| io_verification("could not sync verified release cache root", error))?;
    Ok(())
}

fn install_verified_helper(source: &Path, destination: &Path) -> Result<()> {
    check_path_ancestors(destination, false)?;
    let parent = destination.parent().unwrap_or_else(|| Path::new("."));
    let parent_metadata = fs::symlink_metadata(parent)
        .map_err(|error| io_verification("trusted helper directory is missing", error))?;
    if !parent_metadata.is_dir()
        || parent_metadata.file_type().is_symlink()
        || (unsafe { libc::geteuid() } == 0 && parent_metadata.uid() != 0)
        || parent_metadata.mode() & 0o022 != 0
    {
        return Err(verification("trusted helper directory is not protected"));
    }
    let source_metadata = fs::symlink_metadata(source)
        .map_err(|error| io_verification("verified release helper is missing", error))?;
    if !source_metadata.is_file()
        || source_metadata.file_type().is_symlink()
        || source_metadata.mode() & 0o111 == 0
        || (unsafe { libc::geteuid() } == 0 && source_metadata.uid() != 0)
    {
        return Err(verification(
            "verified release helper is not a protected executable",
        ));
    }
    let temporary = parent.join(format!(
        ".lumistripe-deploy-helper.{}.tmp",
        std::process::id()
    ));
    if fs::symlink_metadata(&temporary).is_ok() {
        return Err(verification(
            "trusted helper update has a conflicting temporary file",
        ));
    }
    copy_file_nofollow(source, &temporary, 0o700, source_metadata.len())?;
    if unsafe { libc::geteuid() } == 0 {
        set_owner(&temporary, 0, 0)?;
    }
    fs::set_permissions(&temporary, fs::Permissions::from_mode(0o755))
        .map_err(|error| io_verification("could not protect installed deployment helper", error))?;
    if let Err(error) = fs::rename(&temporary, destination) {
        let _ = fs::remove_file(&temporary);
        return Err(io_verification(
            "could not atomically install deployment helper",
            error,
        ));
    }
    File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(|error| io_verification("could not sync deployment helper directory", error))?;
    Ok(())
}

fn set_tree_owner_mode(root: &Path, uid: u32, gid: u32, cache: bool) -> Result<()> {
    let mut paths = Vec::new();
    fn walk(path: &Path, paths: &mut Vec<PathBuf>) -> Result<()> {
        paths.push(path.to_owned());
        let metadata = fs::symlink_metadata(path)
            .map_err(|error| io_verification("could not inspect release staging path", error))?;
        if metadata.file_type().is_symlink() {
            return Err(verification("release staging contains a symlink"));
        }
        if metadata.is_dir() {
            let mut children = fs::read_dir(path)
                .map_err(|error| {
                    io_verification("could not inspect release staging directory", error)
                })?
                .map(|entry| entry.map(|entry| entry.path()))
                .collect::<io::Result<Vec<_>>>()
                .map_err(|error| {
                    io_verification("could not inspect release staging directory", error)
                })?;
            children.sort();
            for child in children {
                walk(&child, paths)?;
            }
        } else if !metadata.is_file() {
            return Err(verification("release staging contains a non-regular file"));
        }
        Ok(())
    }
    walk(root, &mut paths)?;
    for path in paths.iter().rev() {
        let metadata = fs::symlink_metadata(path)
            .map_err(|error| io_verification("could not inspect release staging path", error))?;
        let executable = RELEASE_BINARIES.contains(
            &path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or_default(),
        );
        let mode = if metadata.is_dir() {
            if cache { 0o555 } else { 0o755 }
        } else if cache {
            if executable { 0o555 } else { 0o444 }
        } else if executable {
            0o755
        } else {
            0o644
        };
        if unsafe { libc::geteuid() } == 0 {
            set_owner(path, uid, gid)?;
        }
        fs::set_permissions(path, fs::Permissions::from_mode(mode))
            .map_err(|error| io_verification("could not set release staging permissions", error))?;
    }
    Ok(())
}

fn extract_cached_binary(
    cache_dir: &Path,
    commit: &str,
    binary: &str,
    destination: &Path,
) -> Result<()> {
    if !RELEASE_BINARIES.contains(&binary) {
        return Err(verification(
            "requested executable is not part of a LumiStripe release",
        ));
    }
    verify_cache_dir(cache_dir, commit)?;
    if fs::symlink_metadata(destination).is_ok() {
        return Err(verification(format!(
            "binary extraction destination already exists: {}",
            destination.display()
        )));
    }
    let temporary = tempfile::Builder::new()
        .prefix("lumistripe-verified-extract-")
        .tempdir()
        .map_err(|error| {
            io_verification("could not create verified extraction directory", error)
        })?;
    let extracted = extract_archive(
        &cache_dir.join("bundle.tar.gz"),
        &temporary.path().join("extract"),
        commit,
    )?;
    let binary_path = extracted.join(binary);
    let metadata = fs::symlink_metadata(&binary_path)
        .map_err(|error| io_verification("verified release binary is missing", error))?;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.mode() & 0o111 == 0 {
        return Err(verification(format!(
            "verified release is missing executable {binary}"
        )));
    }
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| io_verification("could not create binary output directory", error))?;
    }
    copy_file_nofollow(&binary_path, destination, 0o700, metadata.len())?;
    fs::set_permissions(destination, fs::Permissions::from_mode(0o755))
        .map_err(|error| io_verification("could not set extracted binary permissions", error))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use tempfile::tempdir;

    const TEST_COMMIT: &str = "a3c6f7b54f21da1063a2ef4081b9cde7f6501234";

    fn fixture(root: &Path) -> PathBuf {
        let release = root.join("release");
        fs::create_dir_all(release.join("static/assets")).unwrap();
        for name in RELEASE_BINARIES {
            let path = release.join(name);
            fs::write(&path, format!("#!/bin/sh\nprintf '%s\\n' '{name}'\n")).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        }
        fs::write(
            release.join("static/index.html"),
            "<!doctype html>release\n",
        )
        .unwrap();
        fs::write(
            release.join("static/assets/app.js"),
            "console.log('release')\n",
        )
        .unwrap();
        fs::write(root.join(".bun-version"), "1.4.4\n").unwrap();
        make_manifest(&release, TEST_COMMIT, "github", Some(root), Some("2.36")).unwrap();
        release
    }

    #[test]
    fn package_archive_is_reproducible_and_extracts_a_valid_commit_matched_release() {
        let root = tempdir().unwrap();
        let release = fixture(root.path());
        let first = root.path().join("first.tar.gz");
        let second = root.path().join("second.tar.gz");
        package_release(&release, &first, TEST_COMMIT).unwrap();
        package_release(&release, &second, TEST_COMMIT).unwrap();

        assert_eq!(sha256_file(&first).unwrap(), sha256_file(&second).unwrap());
        let extracted = extract_archive(&first, &root.path().join("extract"), TEST_COMMIT).unwrap();
        let manifest = validate_release_dir(&extracted, TEST_COMMIT, Some("github")).unwrap();
        assert_eq!(manifest["target"]["triple"], "aarch64-unknown-linux-gnu");
        assert_eq!(
            fs::read_to_string(extracted.join("static/index.html")).unwrap(),
            "<!doctype html>release\n"
        );
    }

    #[test]
    fn manifest_detects_corruption_wrong_commits_and_invalid_target_metadata() {
        let root = tempdir().unwrap();
        let release = fixture(root.path());
        assert!(
            validate_release_dir(&release, "b3c6f7b54f21da1063a2ef4081b9cde7f6501234", None)
                .is_err()
        );
        fs::write(release.join("lumistripe-web"), "modified\n").unwrap();
        assert!(validate_release_dir(&release, TEST_COMMIT, None).is_err());

        let root = tempdir().unwrap();
        let release = fixture(root.path());
        let manifest_path = release.join("manifest.json");
        let mut manifest = read_json_bounded(&manifest_path, FILE_LIMIT).unwrap();
        manifest["target"]["architecture"] = json!("x86_64");
        fs::write(&manifest_path, json_compact(&manifest)).unwrap();
        assert!(validate_release_dir(&release, TEST_COMMIT, None).is_err());
    }

    #[test]
    fn unsafe_archive_members_are_rejected_before_a_destination_is_created() {
        let root = tempdir().unwrap();
        let archive_path = root.path().join("unsafe.tar.gz");
        let mut compressed = GzBuilder::new()
            .mtime(0)
            .write(Vec::new(), Compression::best());
        compressed
            .write_all(&make_tar_header("release/", true, 0, 0o755).unwrap())
            .unwrap();
        compressed
            .write_all(&make_tar_header("release/../outside", false, 3, 0o644).unwrap())
            .unwrap();
        compressed.write_all(b"bad").unwrap();
        compressed.write_all(&[0; TAR_BLOCK - 3]).unwrap();
        compressed.write_all(&[0; TAR_BLOCK * 2]).unwrap();
        let padding = vec![0; TAR_RECORD - TAR_BLOCK * 5];
        compressed.write_all(&padding).unwrap();
        fs::write(&archive_path, compressed.finish().unwrap()).unwrap();
        let destination = root.path().join("extract");

        assert!(extract_archive(&archive_path, &destination, TEST_COMMIT).is_err());
        assert!(!destination.exists());
        assert!(!root.path().join("outside").exists());
    }

    #[test]
    fn source_release_metadata_keeps_the_host_target_and_origin() {
        let root = tempdir().unwrap();
        let release = fixture(root.path());
        let manifest =
            make_manifest(&release, TEST_COMMIT, "source", Some(root.path()), None).unwrap();
        let checked = validate_release_dir(&release, TEST_COMMIT, Some("source")).unwrap();
        assert_eq!(checked, manifest);
        assert_eq!(manifest["origin"], "source");
        assert_eq!(manifest["target"]["triple"], "host");
    }
}
