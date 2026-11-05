//! Narrow client for the installed, root-owned NetworkManager helper.

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use thiserror::Error;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWriteExt};
use tokio::process::Command;
use tokio::sync::Semaphore;
use tokio::time::timeout;

const MAX_REQUEST_BYTES: usize = 8 * 1024;
const MAX_RESPONSE_BYTES: usize = 1024 * 1024;

#[cfg(test)]
static TEST_HELPER_PROCESS_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());
const HELPER_TIMEOUT: Duration = Duration::from_secs(55);

#[derive(Clone, Debug, Deserialize, Serialize, utoipa::ToSchema)]
pub(crate) struct NetworkStatusResponse {
    pub manager_available: bool,
    pub error: Option<String>,
    #[serde(default)]
    #[schema(default = json!([]))]
    pub adapters: Vec<NetworkAdapterInfo>,
}

#[derive(Clone, Debug, Deserialize, Serialize, utoipa::ToSchema)]
pub(crate) struct NetworkAdapterInfo {
    pub interface: String,
    pub state: String,
    pub connection: Option<String>,
    pub current_ssid: Option<String>,
    #[schema(required = true)]
    pub hotspot: Option<bool>,
    pub connectable: bool,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, utoipa::ToSchema)]
pub(crate) struct WifiNetworkInfo {
    pub ssid: String,
    pub security: String,
    #[schema(minimum = 0, maximum = 100)]
    pub signal: u8,
    pub secured: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize, utoipa::ToSchema)]
pub(crate) struct WifiScanResponse {
    pub interface: String,
    #[serde(default)]
    #[schema(default = json!([]))]
    pub networks: Vec<WifiNetworkInfo>,
}

#[derive(Clone, Debug, Deserialize, Serialize, utoipa::ToSchema)]
pub(crate) struct WifiConnectResponse {
    pub interface: String,
    pub ssid: String,
    pub connected: bool,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
#[serde(deny_unknown_fields)]
pub(crate) struct WifiScanRequest {
    #[schema(min_length = 1, max_length = 15)]
    pub interface: String,
}

#[derive(Debug, Deserialize, utoipa::ToSchema)]
#[schema(as = WifiConnectRequest)]
#[serde(deny_unknown_fields)]
pub(crate) struct WifiConnectRequest {
    #[schema(min_length = 1, max_length = 15)]
    pub interface: String,
    #[schema(min_length = 1, max_length = 32)]
    pub ssid: String,
    #[serde(default)]
    #[schema(default = "", max_length = 63, format = Password, write_only)]
    pub password: String,
}

impl WifiScanRequest {
    pub(crate) fn validate_contract(&self) -> Result<(), NetworkError> {
        validate_contract_string(&self.interface, 1, 15, "Choose a valid Wi-Fi adapter.")
    }

    pub(crate) fn validate_helper_input(&self) -> Result<(), NetworkError> {
        validate_interface(&self.interface)
    }
}

impl WifiConnectRequest {
    pub(crate) fn validate_contract(&self) -> Result<(), NetworkError> {
        validate_contract_string(&self.interface, 1, 15, "Choose a valid Wi-Fi adapter.")?;
        validate_contract_string(&self.ssid, 1, 32, "Choose a valid Wi-Fi network.")?;
        if self.password.chars().count() > 63 {
            return Err(NetworkError::new(
                "invalid_contract",
                "The Wi-Fi password is invalid.",
            ));
        }
        Ok(())
    }

    pub(crate) fn validate_helper_input(&self) -> Result<(), NetworkError> {
        validate_interface(&self.interface)?;
        if self.ssid.as_bytes().is_empty() || self.ssid.len() > 32 {
            return Err(NetworkError::new(
                "invalid",
                "Choose a valid Wi-Fi network.",
            ));
        }
        if self
            .ssid
            .chars()
            .any(|character| character.is_control() || character == '\u{7f}')
        {
            return Err(NetworkError::new(
                "invalid",
                "The Wi-Fi network name is invalid.",
            ));
        }
        if self
            .password
            .chars()
            .any(|character| matches!(character, '\0' | '\r' | '\n'))
        {
            return Err(NetworkError::new(
                "invalid",
                "The Wi-Fi password is invalid.",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub(crate) struct NetworkManagerService {
    program: PathBuf,
    arguments: Vec<String>,
    timeout: Duration,
    operation: Arc<Semaphore>,
}

impl Default for NetworkManagerService {
    fn default() -> Self {
        Self::new(
            "/usr/bin/sudo",
            ["-n", "/usr/local/sbin/lumistripe-network"],
            HELPER_TIMEOUT,
        )
    }
}

impl NetworkManagerService {
    pub(crate) fn new(
        program: impl Into<PathBuf>,
        arguments: impl IntoIterator<Item = impl Into<String>>,
        timeout: Duration,
    ) -> Self {
        Self {
            program: program.into(),
            arguments: arguments.into_iter().map(Into::into).collect(),
            timeout,
            operation: Arc::new(Semaphore::new(1)),
        }
    }

    pub(crate) async fn status(&self) -> Result<NetworkStatusResponse, NetworkError> {
        let response = self.request(json!({"operation": "status"})).await?;
        decode_response(response)
    }

    pub(crate) async fn scan(
        &self,
        request: WifiScanRequest,
    ) -> Result<WifiScanResponse, NetworkError> {
        request.validate_contract()?;
        request.validate_helper_input()?;
        let response = self
            .request(json!({"operation": "scan", "interface": request.interface}))
            .await?;
        decode_response(response)
    }

    pub(crate) async fn connect(
        &self,
        request: WifiConnectRequest,
    ) -> Result<WifiConnectResponse, NetworkError> {
        request.validate_contract()?;
        request.validate_helper_input()?;
        let response = self
            .request(json!({
                "operation": "connect",
                "interface": request.interface,
                "ssid": request.ssid,
                "password": request.password,
            }))
            .await?;
        decode_response(response)
    }

    async fn request(&self, payload: Value) -> Result<Value, NetworkError> {
        let _permit =
            self.operation.clone().try_acquire_owned().map_err(|_| {
                NetworkError::new("busy", "Network management is busy. Retry shortly.")
            })?;
        let payload = serde_json::to_vec(&payload).map_err(|_| {
            NetworkError::new("invalid", "The network request could not be encoded.")
        })?;
        if payload.len() > MAX_REQUEST_BYTES {
            return Err(NetworkError::new(
                "invalid",
                "The network request is too large.",
            ));
        }

        #[cfg(test)]
        let _test_helper_process_guard = TEST_HELPER_PROCESS_LOCK.lock().await;

        let mut child = Command::new(&self.program)
            .args(&self.arguments)
            .env_clear()
            .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
            .env("LC_ALL", "C")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|_| NetworkError::unavailable())?;
        let mut stdin = child.stdin.take().ok_or_else(NetworkError::unavailable)?;
        let stdout = child.stdout.take().ok_or_else(NetworkError::unavailable)?;
        let mut stdout_task = tokio::spawn(read_limited(stdout, MAX_RESPONSE_BYTES));

        let execute = async {
            stdin
                .write_all(&payload)
                .await
                .map_err(|_| NetworkError::unavailable())?;
            drop(stdin);
            let status = child
                .wait()
                .await
                .map_err(|_| NetworkError::unavailable())?;
            let (output, exceeded_limit) = (&mut stdout_task)
                .await
                .map_err(|_| NetworkError::unavailable())?
                .map_err(|_| NetworkError::unavailable())?;
            if exceeded_limit {
                return Err(NetworkError::new(
                    "unavailable",
                    "Network management returned an oversized response.",
                ));
            }
            if !status.success() {
                return Err(NetworkError::helper_unavailable());
            }
            serde_json::from_slice(&output).map_err(|_| {
                NetworkError::new(
                    "unavailable",
                    "Network management returned an invalid response.",
                )
            })
        };

        match timeout(self.timeout, execute).await {
            Ok(result) => result,
            Err(_) => {
                let _ = child.kill().await;
                let _ = child.wait().await;
                let _ = timeout(Duration::from_secs(1), &mut stdout_task).await;
                Err(NetworkError::unavailable())
            }
        }
    }
}

async fn read_limited<R>(reader: R, max_bytes: usize) -> std::io::Result<(Vec<u8>, bool)>
where
    R: AsyncRead + Unpin,
{
    let mut reader = reader;
    let mut output = Vec::with_capacity(max_bytes.min(16 * 1024));
    let mut exceeded_limit = false;
    let mut buffer = [0; 8 * 1024];
    loop {
        let read = reader.read(&mut buffer).await?;
        if read == 0 {
            break;
        }
        let stored = read.min(max_bytes.saturating_sub(output.len()));
        output.extend_from_slice(&buffer[..stored]);
        exceeded_limit |= stored < read;
    }
    Ok((output, exceeded_limit))
}

#[derive(Debug, Error)]
#[error("{message}")]
pub(crate) struct NetworkError {
    pub code: &'static str,
    pub message: String,
}

impl NetworkError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    fn unavailable() -> Self {
        Self::new(
            "unavailable",
            "Network management is unavailable. Install or update the Pi appliance. Otherwise, retry shortly.",
        )
    }

    fn helper_unavailable() -> Self {
        Self::new(
            "unavailable",
            "Network management is unavailable. Install or update the Pi appliance.",
        )
    }
}

fn validate_contract_string(
    value: &str,
    minimum: usize,
    maximum: usize,
    message: &'static str,
) -> Result<(), NetworkError> {
    let length = value.chars().count();
    if !(minimum..=maximum).contains(&length) {
        return Err(NetworkError::new("invalid_contract", message));
    }
    Ok(())
}

fn validate_interface(interface: &str) -> Result<(), NetworkError> {
    let mut characters = interface.chars();
    let valid_first = characters
        .next()
        .is_some_and(|character| character.is_ascii_alphanumeric());
    if !valid_first
        || interface.len() > 15
        || !characters.all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '_' | '.' | ':' | '-')
        })
    {
        return Err(NetworkError::new(
            "invalid",
            "Choose a valid Wi-Fi adapter.",
        ));
    }
    Ok(())
}

fn decode_response<T>(mut response: Value) -> Result<T, NetworkError>
where
    T: for<'de> Deserialize<'de>,
{
    let Some(object) = response.as_object_mut() else {
        return Err(NetworkError::new(
            "unavailable",
            "Network management returned an invalid response.",
        ));
    };
    if object.remove("ok").and_then(|value| value.as_bool()) != Some(true) {
        let code = object
            .remove("error_code")
            .and_then(|value| value.as_str().map(str::to_owned))
            .unwrap_or_else(|| "operation_failed".to_owned());
        let message = object
            .remove("error")
            .and_then(|value| value.as_str().map(str::to_owned))
            .unwrap_or_else(|| "NetworkManager could not complete the request.".to_owned());
        return Err(NetworkError {
            code: match code.as_str() {
                "invalid" => "invalid",
                "unsupported" => "unsupported",
                "unavailable" => "unavailable",
                "connection_failed" => "connection_failed",
                _ => "operation_failed",
            },
            message,
        });
    }
    object.remove("ok");
    serde_json::from_value(response).map_err(|_| {
        NetworkError::new(
            "unavailable",
            "Network management returned an invalid response.",
        )
    })
}

pub(crate) fn remove_secret_from_message(message: String, secret: &str) -> String {
    if !secret.is_empty() && message.contains(secret) {
        "NetworkManager could not complete the request.".to_owned()
    } else {
        message
    }
}

#[cfg(test)]
mod tests {
    use super::{
        NetworkError, NetworkManagerService, WifiConnectRequest, WifiScanRequest, decode_response,
        remove_secret_from_message,
    };
    use serde_json::json;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::Path;
    use std::time::Duration;
    use tempfile::TempDir;
    use tokio::time::{sleep, timeout};

    fn helper(temp: &TempDir, script: &str, timeout: Duration) -> NetworkManagerService {
        let path = temp.path().join("helper.sh");
        fs::write(&path, format!("#!/bin/sh\n{script}\n")).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        NetworkManagerService::new(&path, std::iter::empty::<String>(), timeout)
    }

    #[cfg(target_os = "linux")]
    #[tokio::test]
    #[ignore = "requires active NetworkManager and the installed Rust helper"]
    async fn live_networkmanager_status_decodes_read_only_helper_snapshot() {
        let helper = Path::new("/usr/local/libexec/lumistripe/network/lumistripe-network-helper");
        let service = NetworkManagerService::new(helper, ["network"], Duration::from_secs(10));

        let status = service
            .status()
            .await
            .expect("Rust should decode the live helper response");
        assert!(
            status.manager_available,
            "NetworkManager should report itself as available"
        );
        assert!(
            status.error.is_none(),
            "successful status should have no error"
        );
    }

    #[cfg(target_os = "linux")]
    #[tokio::test]
    #[ignore = "actively asks NetworkManager to rescan the configured Wi-Fi adapter"]
    async fn live_networkmanager_scan_uses_the_restricted_wrapper() {
        let service = NetworkManagerService::default();
        let status = service
            .status()
            .await
            .expect("the restricted wrapper should return live adapter status");
        assert!(status.manager_available);
        let interface = status
            .adapters
            .iter()
            .find(|adapter| adapter.interface == "wlan0")
            .expect("the live adapter should expose wlan0")
            .interface
            .clone();

        let scan = service
            .scan(WifiScanRequest {
                interface: interface.clone(),
            })
            .await
            .expect("the restricted wrapper should complete a Wi-Fi scan");
        assert_eq!(scan.interface, interface);
        assert!(scan.networks.iter().all(|network| network.signal <= 100));
    }

    #[test]
    fn validates_wifi_requests_before_invoking_the_helper() {
        let invalid_interface = WifiScanRequest {
            interface: "wlan0;id".to_owned(),
        };
        assert_eq!(
            invalid_interface.validate_helper_input().unwrap_err().code,
            "invalid"
        );
        assert_eq!(
            WifiScanRequest {
                interface: "".to_owned()
            }
            .validate_contract()
            .unwrap_err()
            .code,
            "invalid_contract"
        );
        assert_eq!(
            WifiConnectRequest {
                interface: "wlan0".to_owned(),
                ssid: "x".repeat(33),
                password: String::new(),
            }
            .validate_contract()
            .unwrap_err()
            .code,
            "invalid_contract"
        );
    }

    #[test]
    fn maps_privileged_helper_error_codes_and_does_not_echo_credentials() {
        let error: NetworkError = decode_response::<serde_json::Value>(json!({
            "ok": false,
            "error_code": "connection_failed",
            "error": "Could not connect.",
        }))
        .unwrap_err();
        assert_eq!(error.code, "connection_failed");
        assert_eq!(
            remove_secret_from_message("secret leaked".to_owned(), "secret"),
            "NetworkManager could not complete the request."
        );
    }

    #[tokio::test]
    async fn helper_protocol_is_stdin_only_and_parses_structured_responses() {
        let temp = TempDir::new().unwrap();
        let service = helper(
            &temp,
            "IFS= read -r request || :\ncase \"$request\" in *'correct-horse'*) printf '%s' '{\"ok\":true,\"interface\":\"wlan1\",\"ssid\":\"Party Wi-Fi\",\"connected\":true}' ;; *) printf '%s' '{\"ok\":false,\"error_code\":\"protocol\",\"error\":\"test helper did not receive the expected request\"}' ;; esac",
            Duration::from_secs(2),
        );
        let response = service
            .connect(WifiConnectRequest {
                interface: "wlan1".to_owned(),
                ssid: "Party Wi-Fi".to_owned(),
                password: "correct-horse".to_owned(),
            })
            .await
            .unwrap();
        assert_eq!(response.interface, "wlan1");
        assert_eq!(response.ssid, "Party Wi-Fi");
        assert!(response.connected);
    }

    #[tokio::test]
    async fn helper_timeout_and_output_limit_are_enforced() {
        let slow = NetworkManagerService::new("/usr/bin/sleep", ["2"], Duration::from_millis(20));
        let error = slow.status().await.unwrap_err();
        assert_eq!(error.code, "unavailable");
        assert!(error.message.contains("retry shortly"));

        let oversized = NetworkManagerService::new(
            "/usr/bin/head",
            ["-c", "2000000", "/dev/zero"],
            Duration::from_secs(2),
        );
        let error = oversized.status().await.unwrap_err();
        assert!(error.message.contains("oversized"));
    }

    #[tokio::test]
    async fn cancelling_a_helper_request_terminates_the_child_process() {
        let temp = TempDir::new().unwrap();
        let pid_file = temp.path().join("helper.pid");
        let script = format!(
            "cat >/dev/null; printf '%s' \"$$\" > '{}'; exec /usr/bin/sleep 30",
            pid_file.display()
        );
        let service =
            NetworkManagerService::new("/bin/sh", ["-c", script.as_str()], Duration::from_secs(30));
        let request = tokio::spawn(async move { service.status().await });

        let pid = timeout(Duration::from_secs(2), async {
            loop {
                if let Ok(contents) = fs::read_to_string(&pid_file)
                    && let Ok(pid) = contents.parse::<u32>()
                {
                    break pid;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("helper should write its PID before the request is cancelled");

        request.abort();
        assert!(request.await.unwrap_err().is_cancelled());
        timeout(Duration::from_secs(2), async {
            while Path::new(&format!("/proc/{pid}")).exists() {
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("cancelled helper process should be terminated");
    }
}
