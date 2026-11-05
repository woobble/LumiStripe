use serde_json::{Value, json};
use std::fs::{self, File, OpenOptions, Permissions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio::process::Command;
use tokio::time::timeout;
use uuid::Uuid;

const MAX_REQUEST_BYTES: usize = 8 * 1024;
const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
const MAX_COMMAND_OUTPUT_BYTES: usize = 512 * 1024;
const NETWORK_CONNECTION_DIR: &str = "/etc/NetworkManager/system-connections";
const WIFI_CONNECTION_PREFIX: &str = "lumistripe-wifi-";
const MAX_PROFILE_BYTES: u64 = 64 * 1024;

type Result<T> = std::result::Result<T, NetworkOperationError>;

#[derive(Clone)]
struct NetworkConfig {
    nmcli_binary: PathBuf,
    iw_binary: PathBuf,
    connection_dir: PathBuf,
}

impl Default for NetworkConfig {
    fn default() -> Self {
        Self {
            nmcli_binary: PathBuf::from("/usr/bin/nmcli"),
            iw_binary: PathBuf::from("/usr/sbin/iw"),
            connection_dir: PathBuf::from(NETWORK_CONNECTION_DIR),
        }
    }
}

#[derive(Debug)]
struct NetworkOperationError {
    code: &'static str,
    message: &'static str,
}

impl NetworkOperationError {
    fn invalid(message: &'static str) -> Self {
        Self {
            code: "invalid",
            message,
        }
    }

    fn unavailable() -> Self {
        Self {
            code: "unavailable",
            message: "NetworkManager is unavailable.",
        }
    }

    fn operation_failed() -> Self {
        Self {
            code: "operation_failed",
            message: "NetworkManager could not complete the request.",
        }
    }

    fn unsupported() -> Self {
        Self {
            code: "unsupported",
            message: "This network security type is not supported.",
        }
    }

    fn connection_failed() -> Self {
        Self {
            code: "connection_failed",
            message: "Could not connect to that Wi-Fi network. Check the password, router security mode, and adapter compatibility.",
        }
    }
}

struct CommandOutput {
    success: bool,
    stdout: Vec<u8>,
}

struct BoundedOutput {
    bytes: Vec<u8>,
    truncated: bool,
}

pub async fn run_stdin() {
    let response = match read_request() {
        Ok(request) => match parse_request(&request) {
            Ok(payload) => match dispatch(payload, &NetworkConfig::default()).await {
                Ok(response) => response,
                Err(error) => error_response(error),
            },
            Err(error) => error_response(error),
        },
        Err(error) => error_response(error),
    };
    let mut encoded = serde_json::to_vec(&response).unwrap_or_else(|_| {
        br#"{"ok":false,"error_code":"invalid","error":"The network request is invalid."}"#.to_vec()
    });
    if encoded.len() > MAX_RESPONSE_BYTES {
        encoded = serde_json::to_vec(&error_response(NetworkOperationError::operation_failed()))
            .expect("fixed network error response serializes");
    }
    let stdout = io::stdout();
    let mut stdout = stdout.lock();
    let _ = stdout.write_all(&encoded);
    let _ = stdout.write_all(b"\n");
}

fn read_request() -> Result<Vec<u8>> {
    let mut request = Vec::new();
    io::stdin()
        .take((MAX_REQUEST_BYTES + 1) as u64)
        .read_to_end(&mut request)
        .map_err(|_| NetworkOperationError::invalid("The network request is invalid."))?;
    if request.len() > MAX_REQUEST_BYTES {
        return Err(NetworkOperationError::invalid(
            "The network request is too large.",
        ));
    }
    Ok(request)
}

fn parse_request(request: &[u8]) -> Result<Value> {
    if request.len() > MAX_REQUEST_BYTES {
        return Err(NetworkOperationError::invalid(
            "The network request is too large.",
        ));
    }
    serde_json::from_slice(request)
        .map_err(|_| NetworkOperationError::invalid("The network request is invalid."))
}

fn error_response(error: NetworkOperationError) -> Value {
    json!({"ok": false, "error_code": error.code, "error": error.message})
}

async fn dispatch(payload: Value, config: &NetworkConfig) -> Result<Value> {
    let Some(payload) = payload.as_object() else {
        return Err(NetworkOperationError::invalid(
            "The network request is invalid.",
        ));
    };
    let operation = payload
        .get("operation")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if operation == "status" {
        if !network_manager_running(config).await {
            return Ok(json!({
                "ok": true,
                "manager_available": false,
                "error": "NetworkManager is unavailable.",
                "adapters": [],
            }));
        }
        return Ok(json!({
            "ok": true,
            "manager_available": true,
            "error": Value::Null,
            "adapters": get_adapters(config).await?,
        }));
    }
    if !network_manager_running(config).await {
        return Err(NetworkOperationError::unavailable());
    }
    match operation {
        "scan" => {
            let interface = validate_interface(payload.get("interface"))?;
            adapter_for_connection(config, &interface).await?;
            Ok(json!({
                "ok": true,
                "interface": interface,
                "networks": get_networks(config, &interface).await?,
            }))
        }
        "connect" => connect(config, payload).await,
        _ => Err(NetworkOperationError::invalid(
            "The network request is invalid.",
        )),
    }
}

async fn run_process(
    binary: &Path,
    arguments: &[String],
    timeout_duration: Duration,
) -> Result<CommandOutput> {
    #[cfg(test)]
    let _test_subprocess_guard = crate::TEST_SUBPROCESS_LOCK.lock().await;
    let mut command = Command::new(binary);
    command
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let mut child = command
        .spawn()
        .map_err(|_| NetworkOperationError::unavailable())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(NetworkOperationError::unavailable)?;
    let mut stdout_task = tokio::spawn(read_bounded(stdout, MAX_COMMAND_OUTPUT_BYTES));
    let status = match timeout(timeout_duration, child.wait()).await {
        Ok(Ok(status)) => status,
        Ok(Err(_)) => return Err(NetworkOperationError::unavailable()),
        Err(_) => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            stdout_task.abort();
            return Err(NetworkOperationError::unavailable());
        }
    };
    let output = (&mut stdout_task)
        .await
        .map_err(|_| NetworkOperationError::operation_failed())?
        .map_err(|_| NetworkOperationError::operation_failed())?;
    if output.truncated {
        return Err(NetworkOperationError::operation_failed());
    }
    Ok(CommandOutput {
        success: status.success(),
        stdout: output.bytes,
    })
}

async fn read_bounded<R: AsyncRead + Unpin>(
    mut reader: R,
    limit: usize,
) -> io::Result<BoundedOutput> {
    let mut output = Vec::with_capacity(limit.min(16 * 1024));
    let mut buffer = [0_u8; 4096];
    let mut truncated = false;
    loop {
        let count = reader.read(&mut buffer).await?;
        if count == 0 {
            break;
        }
        let remaining = limit.saturating_sub(output.len());
        let accepted = count.min(remaining);
        output.extend_from_slice(&buffer[..accepted]);
        truncated |= accepted < count;
    }
    Ok(BoundedOutput {
        bytes: output,
        truncated,
    })
}

async fn run_nmcli(
    config: &NetworkConfig,
    arguments: &[&str],
    wait_seconds: u64,
) -> Result<String> {
    let mut command_arguments = vec!["--wait".to_owned(), wait_seconds.to_string()];
    command_arguments.extend(arguments.iter().map(|argument| (*argument).to_owned()));
    let output = run_process(
        &config.nmcli_binary,
        &command_arguments,
        Duration::from_secs(wait_seconds.saturating_add(5)),
    )
    .await
    .map_err(|error| {
        if error.code == "unavailable" {
            NetworkOperationError::unavailable()
        } else {
            error
        }
    })?;
    if !output.success {
        return Err(NetworkOperationError::operation_failed());
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

async fn network_manager_running(config: &NetworkConfig) -> bool {
    let arguments = [
        "--terse".to_owned(),
        "--fields".to_owned(),
        "RUNNING".to_owned(),
        "general".to_owned(),
    ];
    run_process(&config.nmcli_binary, &arguments, Duration::from_secs(5))
        .await
        .is_ok_and(|output| {
            output.success
                && String::from_utf8_lossy(&output.stdout)
                    .trim()
                    .eq_ignore_ascii_case("running")
        })
}

fn split_nmcli_row(line: &str) -> Vec<String> {
    let mut fields = Vec::new();
    let mut current = String::new();
    let mut escaped = false;
    for character in line.chars() {
        if escaped {
            current.push(match character {
                'n' => '\n',
                'r' => '\r',
                't' => '\t',
                other => other,
            });
            escaped = false;
        } else if character == '\\' {
            escaped = true;
        } else if character == ':' {
            fields.push(std::mem::take(&mut current));
        } else {
            current.push(character);
        }
    }
    if escaped {
        current.push('\\');
    }
    fields.push(current);
    fields
}

async fn interface_mode(config: &NetworkConfig, interface: &str) -> Option<String> {
    let arguments = vec!["dev".to_owned(), interface.to_owned(), "info".to_owned()];
    let output = run_process(&config.iw_binary, &arguments, Duration::from_secs(5))
        .await
        .ok()?;
    if !output.success {
        return None;
    }
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .find_map(|line| {
            let mut parts = line.split_whitespace();
            (parts.next()?.eq_ignore_ascii_case("type"))
                .then(|| parts.next().map(str::to_lowercase))
                .flatten()
        })
}

async fn connected_ssid(config: &NetworkConfig, connection: &str) -> Option<String> {
    if connection.is_empty() || connection == "--" {
        return None;
    }
    let output = run_nmcli(
        config,
        &[
            "--get-values",
            "802-11-wireless.ssid",
            "connection",
            "show",
            connection,
        ],
        5,
    )
    .await
    .ok()?;
    let value = output.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

async fn get_adapters(config: &NetworkConfig) -> Result<Vec<Value>> {
    let output = run_nmcli(
        config,
        &[
            "--terse",
            "--escape",
            "yes",
            "--fields",
            "DEVICE,TYPE,STATE,CONNECTION",
            "device",
            "status",
        ],
        15,
    )
    .await?;
    let mut adapters = Vec::new();
    for line in output.lines() {
        let fields = split_nmcli_row(line);
        if fields.len() != 4 || !fields[1].eq_ignore_ascii_case("wifi") {
            continue;
        }
        let interface = fields[0].as_str();
        let connection = fields[3].as_str();
        let mode = interface_mode(config, interface).await;
        let hotspot = mode.as_deref().map(|mode| matches!(mode, "ap" | "p2p-go"));
        let connectable = hotspot == Some(false);
        let reason = match hotspot {
            Some(true) => Some("This adapter is providing a hotspot."),
            None => Some("Could not verify this adapter's Wi-Fi mode."),
            Some(false) => None,
        };
        adapters.push(json!({
            "interface": interface,
            "state": fields[2],
            "connection": if connection.is_empty() || connection == "--" { Value::Null } else { json!(connection) },
            "current_ssid": connected_ssid(config, connection).await,
            "hotspot": hotspot,
            "connectable": connectable,
            "reason": reason,
        }));
    }
    Ok(adapters)
}

fn validate_interface(value: Option<&Value>) -> Result<String> {
    let Some(interface) = value.and_then(Value::as_str) else {
        return Err(NetworkOperationError::invalid(
            "Choose a valid Wi-Fi adapter.",
        ));
    };
    let mut characters = interface.chars();
    let Some(first) = characters.next() else {
        return Err(NetworkOperationError::invalid(
            "Choose a valid Wi-Fi adapter.",
        ));
    };
    if interface.len() > 15
        || !first.is_ascii_alphanumeric()
        || !characters.all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '_' | '.' | ':' | '-')
        })
    {
        return Err(NetworkOperationError::invalid(
            "Choose a valid Wi-Fi adapter.",
        ));
    }
    Ok(interface.to_owned())
}

async fn adapter_for_connection(config: &NetworkConfig, interface: &str) -> Result<Value> {
    let adapters = get_adapters(config).await?;
    let adapter = adapters
        .into_iter()
        .find(|adapter| adapter["interface"] == interface)
        .ok_or_else(|| {
            NetworkOperationError::invalid("The selected Wi-Fi adapter is unavailable.")
        })?;
    if adapter["hotspot"] == true {
        return Err(NetworkOperationError::invalid(
            "The hotspot adapter cannot connect to an internet network.",
        ));
    }
    if adapter["connectable"] != true {
        return Err(NetworkOperationError::invalid(
            "Could not verify that this adapter is safe to use.",
        ));
    }
    Ok(adapter)
}

fn parse_networks(output: &str) -> Vec<Value> {
    let mut strongest_by_ssid = std::collections::HashMap::<String, (u8, Value)>::new();
    for line in output.lines() {
        let fields = split_nmcli_row(line);
        if fields.len() != 3 {
            continue;
        }
        let ssid = &fields[0];
        if ssid.is_empty()
            || ssid == "--"
            || ssid
                .chars()
                .any(|character| (character as u32) < 32 || character == '\u{7f}')
        {
            continue;
        }
        let Ok(raw_signal) = fields[2].parse::<i32>() else {
            continue;
        };
        let signal = raw_signal.clamp(0, 100) as u8;
        let security = if fields[1].is_empty() || fields[1] == "--" {
            "Open"
        } else {
            &fields[1]
        };
        let network = json!({
            "ssid": ssid,
            "security": security,
            "signal": signal,
            "secured": security != "Open",
        });
        let current = strongest_by_ssid.get(ssid).map(|(value, _)| *value);
        if current.is_none_or(|current| signal > current) {
            strongest_by_ssid.insert(ssid.clone(), (signal, network));
        }
    }
    let mut networks = strongest_by_ssid
        .into_values()
        .map(|(_, network)| network)
        .collect::<Vec<_>>();
    networks.sort_by(|left, right| {
        let left_signal = left["signal"].as_u64().unwrap_or_default();
        let right_signal = right["signal"].as_u64().unwrap_or_default();
        right_signal.cmp(&left_signal).then_with(|| {
            left["ssid"]
                .as_str()
                .unwrap_or_default()
                .to_lowercase()
                .cmp(&right["ssid"].as_str().unwrap_or_default().to_lowercase())
        })
    });
    networks
}

async fn get_networks(config: &NetworkConfig, interface: &str) -> Result<Vec<Value>> {
    let output = run_nmcli(
        config,
        &[
            "--terse",
            "--escape",
            "yes",
            "--fields",
            "SSID,SECURITY,SIGNAL",
            "device",
            "wifi",
            "list",
            "--rescan",
            "yes",
            "ifname",
            interface,
        ],
        25,
    )
    .await?;
    Ok(parse_networks(&output))
}

fn key_management(security: &str, password: &str) -> Result<Option<&'static str>> {
    if security == "Open" {
        if !password.is_empty() {
            return Err(NetworkOperationError::invalid(
                "This network is open; leave the password blank.",
            ));
        }
        return Ok(None);
    }
    let upper = security.to_uppercase();
    if upper.contains("802.1X") || upper.contains("EAP") || upper.contains("WEP") {
        return Err(NetworkOperationError::unsupported());
    }
    let key_management =
        if upper.contains("WPA2") && (upper.contains("WPA3") || upper.contains("SAE")) {
            "wpa-psk"
        } else if upper.contains("WPA3") || upper.contains("SAE") {
            "sae"
        } else if upper.contains("WPA") {
            "wpa-psk"
        } else {
            return Err(NetworkOperationError::unsupported());
        };
    let password_bytes = password.as_bytes();
    if !(8..=63).contains(&password_bytes.len()) {
        return Err(NetworkOperationError::invalid(
            "Wi-Fi passwords must be 8 to 63 UTF-8 bytes.",
        ));
    }
    if password
        .chars()
        .any(|character| matches!(character, '\0' | '\r' | '\n'))
    {
        return Err(NetworkOperationError::invalid(
            "The Wi-Fi password is invalid.",
        ));
    }
    Ok(Some(key_management))
}

fn keyfile_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('\t', "\\t")
        .replace('\r', "\\r")
        .replace('\n', "\\n")
}

struct ProfileSettings<'a> {
    connection_id: &'a str,
    connection_uuid: &'a str,
    interface: &'a str,
    ssid: &'a str,
    password: &'a str,
    key_management: Option<&'a str>,
    autoconnect: bool,
}

fn write_profile(directory: &Path, path: &Path, settings: ProfileSettings<'_>) -> io::Result<()> {
    let mut contents = format!(
        "[connection]\nid={}\nuuid={}\ntype=wifi\ninterface-name={}\nautoconnect={}\nautoconnect-priority=20\n\n[802-11-wireless]\nmode=infrastructure\nssid={}\nhidden=false\n\n",
        keyfile_escape(settings.connection_id),
        settings.connection_uuid,
        settings.interface,
        if settings.autoconnect {
            "true"
        } else {
            "false"
        },
        keyfile_escape(settings.ssid),
    );
    if let Some(key_management) = settings.key_management {
        contents.push_str(&format!(
            "[802-11-wireless-security]\nkey-mgmt={key_management}\npsk={}\npsk-flags=0\n\n",
            keyfile_escape(settings.password)
        ));
    }
    contents.push_str("[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n\n");
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(directory)?;
    if path.file_name().is_none() {
        return Err(io::Error::other("network profile has no file name"));
    }
    let (temporary_path, mut temporary_file) = (0..32_u32)
        .find_map(|attempt| {
            let candidate = directory.join(format!(
                ".lumistripe-{}.{}.tmp",
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
                Ok(file) => Some(Ok((candidate, file))),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => None,
                Err(error) => Some(Err(error)),
            }
        })
        .transpose()?
        .ok_or_else(|| io::Error::other("could not create a network profile temporary file"))?;
    let result = (|| -> io::Result<()> {
        temporary_file.write_all(contents.as_bytes())?;
        temporary_file.set_permissions(Permissions::from_mode(0o600))?;
        temporary_file.sync_all()?;
        drop(temporary_file);
        fs::rename(&temporary_path, path)?;
        File::open(directory)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary_path);
    }
    result
}

fn decode_keyfile_value(value: &str) -> String {
    let mut decoded = String::new();
    let mut escaped = false;
    for character in value.chars() {
        if escaped {
            decoded.push(match character {
                'n' => '\n',
                'r' => '\r',
                't' => '\t',
                other => other,
            });
            escaped = false;
        } else if character == '\\' {
            escaped = true;
        } else {
            decoded.push(character);
        }
    }
    if escaped {
        decoded.push('\\');
    }
    decoded
}

fn profile_matches_network(path: &Path, interface: &str, ssid: &str) -> bool {
    let Ok(file) = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
    else {
        return false;
    };
    if !file
        .metadata()
        .is_ok_and(|metadata| metadata.is_file() && metadata.len() <= MAX_PROFILE_BYTES)
    {
        return false;
    }
    let mut contents = String::new();
    if file
        .take(MAX_PROFILE_BYTES + 1)
        .read_to_string(&mut contents)
        .is_err()
    {
        return false;
    }
    let mut section = "";
    let mut profile_interface = None;
    let mut profile_ssid = None;
    for line in contents.lines() {
        if line.starts_with('[') && line.ends_with(']') {
            section = &line[1..line.len() - 1];
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        match (section, key) {
            ("connection", "interface-name") => {
                profile_interface = Some(decode_keyfile_value(value));
            }
            ("802-11-wireless", "ssid") => profile_ssid = Some(decode_keyfile_value(value)),
            _ => {}
        }
    }
    profile_interface.as_deref() == Some(interface) && profile_ssid.as_deref() == Some(ssid)
}

fn remove_old_profiles(directory: &Path, active_path: &Path, interface: &str, ssid: &str) {
    let Ok(entries) = fs::read_dir(directory) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path == active_path
            || !path
                .file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| {
                    name.starts_with(WIFI_CONNECTION_PREFIX) && name.ends_with(".nmconnection")
                })
        {
            continue;
        }
        if profile_matches_network(&path, interface, ssid) {
            let _ = fs::remove_file(path);
        }
    }
}

async fn connect(
    config: &NetworkConfig,
    payload: &serde_json::Map<String, Value>,
) -> Result<Value> {
    let interface = validate_interface(payload.get("interface"))?;
    let adapter = adapter_for_connection(config, &interface).await?;
    let ssid = payload
        .get("ssid")
        .and_then(Value::as_str)
        .ok_or_else(|| NetworkOperationError::invalid("Choose a valid Wi-Fi network."))?;
    if !(1..=32).contains(&ssid.len())
        || ssid
            .chars()
            .any(|character| (character as u32) < 32 || character == '\u{7f}')
    {
        return Err(NetworkOperationError::invalid(
            "Choose a valid Wi-Fi network.",
        ));
    }
    let password = payload
        .get("password")
        .map(|value| {
            value
                .as_str()
                .ok_or_else(|| NetworkOperationError::invalid("The Wi-Fi password is invalid."))
        })
        .transpose()?
        .unwrap_or_default();
    if password.chars().count() > 63 {
        return Err(NetworkOperationError::invalid(
            "The Wi-Fi password is invalid.",
        ));
    }
    let networks = get_networks(config, &interface).await?;
    let selected = networks
        .iter()
        .find(|network| network["ssid"] == ssid)
        .ok_or_else(|| {
            NetworkOperationError::invalid(
                "That network is no longer visible. Scan again and retry.",
            )
        })?;
    let security = selected["security"].as_str().unwrap_or_default();
    let key_management = key_management(security, password)?;
    let profile_uuid = Uuid::new_v4().to_string();
    let profile_path = config.connection_dir.join(format!(
        "{WIFI_CONNECTION_PREFIX}{profile_uuid}.nmconnection"
    ));
    let connection_id = format!("LumiStripe Wi-Fi on {interface}: {ssid}");
    write_profile(
        &config.connection_dir,
        &profile_path,
        ProfileSettings {
            connection_id: &connection_id,
            connection_uuid: &profile_uuid,
            interface: &interface,
            ssid,
            password,
            key_management,
            autoconnect: false,
        },
    )
    .map_err(|_| NetworkOperationError::invalid("The network request is invalid."))?;

    let activation = async {
        run_nmcli(
            config,
            &[
                "connection",
                "load",
                profile_path.to_str().ok_or_else(|| {
                    NetworkOperationError::invalid("The network request is invalid.")
                })?,
            ],
            10,
        )
        .await?;
        run_nmcli(
            config,
            &[
                "connection",
                "up",
                "uuid",
                &profile_uuid,
                "ifname",
                &interface,
            ],
            40,
        )
        .await?;
        write_profile(
            &config.connection_dir,
            &profile_path,
            ProfileSettings {
                connection_id: &connection_id,
                connection_uuid: &profile_uuid,
                interface: &interface,
                ssid,
                password,
                key_management,
                autoconnect: true,
            },
        )
        .map_err(|_| NetworkOperationError::operation_failed())?;
        run_nmcli(config, &["connection", "reload"], 10).await?;
        remove_old_profiles(&config.connection_dir, &profile_path, &interface, ssid);
        run_nmcli(config, &["connection", "reload"], 10).await?;
        Ok::<(), NetworkOperationError>(())
    }
    .await;

    if activation.is_err() {
        let _ = run_nmcli(config, &["connection", "delete", "uuid", &profile_uuid], 10).await;
        let _ = fs::remove_file(&profile_path);
        return Err(NetworkOperationError::connection_failed());
    }

    Ok(json!({
        "ok": true,
        "interface": interface,
        "ssid": ssid,
        "connected": true,
        "state": adapter["state"],
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use tempfile::TempDir;

    fn config_for(temp: &TempDir, nmcli_script: &str) -> NetworkConfig {
        let nmcli = temp.path().join("nmcli");
        let iw = temp.path().join("iw");
        fs::write(&nmcli, nmcli_script).expect("write fake nmcli");
        fs::write(
            &iw,
            "#!/bin/sh\nprintf 'Interface wlan0\\n type managed\\n'\n",
        )
        .expect("write fake iw");
        fs::set_permissions(&nmcli, fs::Permissions::from_mode(0o755))
            .expect("make fake nmcli executable");
        fs::set_permissions(&iw, fs::Permissions::from_mode(0o755))
            .expect("make fake iw executable");
        let connection_dir = temp.path().join("connections");
        fs::create_dir(&connection_dir).expect("create test connection directory");
        NetworkConfig {
            nmcli_binary: nmcli,
            iw_binary: iw,
            connection_dir,
        }
    }

    fn shell_quote(value: &Path) -> String {
        format!("'{}'", value.to_string_lossy().replace('\'', "'\\''"))
    }

    fn fake_nmcli(temp: &TempDir, fail_activation: bool) -> String {
        let args_log = shell_quote(&temp.path().join("nmcli-args.log"));
        let password = "LumiStripeTestPassword";
        let failed_activation = if fail_activation { "exit 1" } else { ":" };
        format!(
            "#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" >> {args_log}\n\
case \" $* \" in\n\
  *\" --fields RUNNING general \"*) printf 'running\\n' ;;\n\
  *\" DEVICE,TYPE,STATE,CONNECTION device status \"*) printf 'wlan0:wifi:connected:Home\\n' ;;\n\
  *\" SSID,SECURITY,SIGNAL device wifi list \"*) printf 'Cafe:WPA2:70\\n' ;;\n\
  *\" --get-values 802-11-wireless.ssid connection show Home \"*) printf 'Cafe\\n' ;;\n\
  *\" connection load \"*)\n\
    profile=\"$5\"\n\
    [ \"$(stat -c '%a' \"$profile\")\" = 600 ]\n\
    grep -F 'psk={password}' \"$profile\" >/dev/null\n\
    ;;\n\
  *\" connection up uuid \"*) {failed_activation} ;;\n\
  *) : ;;\n\
esac\n"
        )
    }

    fn connection_request() -> Value {
        json!({
            "operation": "connect",
            "interface": "wlan0",
            "ssid": "Cafe",
            "password": "LumiStripeTestPassword",
        })
    }

    #[test]
    fn request_parser_rejects_oversize_and_malformed_json() {
        assert_eq!(
            parse_request(&vec![b' '; MAX_REQUEST_BYTES + 1])
                .expect_err("oversize request rejected")
                .code,
            "invalid"
        );
        assert_eq!(
            parse_request(b"{not json")
                .expect_err("malformed request rejected")
                .code,
            "invalid"
        );
    }

    #[test]
    fn nmcli_parser_unescapes_fields_and_networks_are_deduplicated_and_sorted() {
        assert_eq!(
            split_nmcli_row(r"Cafe\:Guest:WPA2\:SAE:80"),
            ["Cafe:Guest", "WPA2:SAE", "80"]
        );
        let networks = parse_networks(concat!(
            "weak:WPA2:20\n",
            "strong:WPA2:90\n",
            "weak:WPA2:65\n",
            ":WPA2:99\n",
            "invalid:WPA2:x\n",
        ));
        assert_eq!(networks.len(), 2);
        assert_eq!(networks[0]["ssid"], "strong");
        assert_eq!(networks[0]["signal"], 90);
        assert_eq!(networks[1]["ssid"], "weak");
        assert_eq!(networks[1]["signal"], 65);
    }

    #[test]
    fn key_management_validates_open_and_personal_security() {
        assert_eq!(key_management("Open", "").expect("open network"), None);
        assert_eq!(
            key_management("WPA2", "password").expect("WPA2"),
            Some("wpa-psk")
        );
        assert_eq!(
            key_management("WPA3-SAE", "password").expect("WPA3"),
            Some("sae")
        );
        assert_eq!(
            key_management("WPA2 WPA3-SAE", "password").expect("transition"),
            Some("wpa-psk")
        );
        assert_eq!(
            key_management("WPA2 802.1X", "password")
                .expect_err("enterprise")
                .code,
            "unsupported"
        );
        assert_eq!(
            key_management("Open", "password")
                .expect_err("open with password")
                .code,
            "invalid"
        );
        assert_eq!(
            key_management("WPA2", "short")
                .expect_err("short password")
                .code,
            "invalid"
        );
    }

    #[test]
    fn profile_writer_creates_private_directory_and_replaces_symlink_safely() {
        let temp = TempDir::new().expect("create temp dir");
        let private_directory = temp.path().join("new-private-connections");
        let private_profile = private_directory.join("profile.nmconnection");
        write_profile(
            &private_directory,
            &private_profile,
            ProfileSettings {
                connection_id: "LumiStripe Wi-Fi",
                connection_uuid: "c999d6f4-3d86-4e28-a847-e610313d2233",
                interface: "wlan0",
                ssid: "Cafe",
                password: "",
                key_management: None,
                autoconnect: false,
            },
        )
        .expect("create private profile directory");
        assert_eq!(
            fs::metadata(&private_directory)
                .expect("private directory metadata")
                .permissions()
                .mode()
                & 0o777,
            0o700
        );

        let directory = temp.path().join("new-connections");
        let profile = directory.join("lumistripe-wifi-profile.nmconnection");
        let outside_target = temp.path().join("outside-target");
        fs::write(&outside_target, "leave this file alone\n").expect("write target");
        fs::create_dir(&directory).expect("create profile directory");
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o755))
            .expect("set initial permissions");
        std::os::unix::fs::symlink(&outside_target, &profile).expect("create profile symlink");

        write_profile(
            &directory,
            &profile,
            ProfileSettings {
                connection_id: "LumiStripe Wi-Fi",
                connection_uuid: "c999d6f4-3d86-4e28-a847-e610313d2233",
                interface: "wlan0",
                ssid: "Cafe",
                password: "",
                key_management: None,
                autoconnect: false,
            },
        )
        .expect("write profile atomically");

        assert!(
            !fs::symlink_metadata(&profile)
                .expect("profile metadata")
                .file_type()
                .is_symlink()
        );
        assert_eq!(
            fs::read_to_string(&outside_target).expect("read outside target"),
            "leave this file alone\n"
        );
        assert_eq!(
            fs::metadata(&profile)
                .expect("profile metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }

    #[tokio::test]
    async fn connect_writes_a_private_profile_without_passing_password_to_nmcli() {
        let temp = TempDir::new().expect("create temp dir");
        let config = config_for(&temp, &fake_nmcli(&temp, false));
        let response = dispatch(connection_request(), &config)
            .await
            .expect("connect fake network");

        assert_eq!(response["ok"], true);
        assert_eq!(response["interface"], "wlan0");
        assert_eq!(response["ssid"], "Cafe");
        assert_eq!(response["connected"], true);
        let profiles = fs::read_dir(&config.connection_dir)
            .expect("read profiles")
            .collect::<std::result::Result<Vec<_>, _>>()
            .expect("collect profiles");
        assert_eq!(profiles.len(), 1);
        let profile = &profiles[0].path();
        assert_eq!(
            fs::metadata(profile)
                .expect("profile metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        let contents = fs::read_to_string(profile).expect("read profile");
        assert!(contents.contains("psk=LumiStripeTestPassword"));
        assert!(contents.contains("autoconnect=true"));
        let args = fs::read_to_string(temp.path().join("nmcli-args.log"))
            .expect("read fake nmcli arguments");
        assert!(!args.contains("LumiStripeTestPassword"));
    }

    #[tokio::test]
    async fn failed_connection_removes_profile_and_returns_redacted_error() {
        let temp = TempDir::new().expect("create temp dir");
        let config = config_for(&temp, &fake_nmcli(&temp, true));
        let error = dispatch(connection_request(), &config)
            .await
            .expect_err("fake activation fails");

        assert_eq!(error.code, "connection_failed");
        assert!(!error.message.contains("LumiStripeTestPassword"));
        assert_eq!(
            fs::read_dir(&config.connection_dir)
                .expect("read profiles")
                .count(),
            0
        );
        let args = fs::read_to_string(temp.path().join("nmcli-args.log"))
            .expect("read fake nmcli arguments");
        assert!(!args.contains("LumiStripeTestPassword"));
    }
}
