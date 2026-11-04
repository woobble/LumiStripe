//! Bluetooth discovery, operation coordination, and PipeWire routing.

use crate::bluetooth_models::{
    AudioOutputDevice, BluetoothCapabilities, BluetoothDevice, BluetoothOperationState,
    BluetoothStatus,
};
use crate::bluetooth_process::{BluetoothError, ProcessRunner, SystemProcessRunner};
use crate::bluetooth_providers::{
    BlueZCommand, BlueZProvider, PipeWireCommand, PipeWireProvider, system_bluez_provider,
    system_pipewire_provider,
};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tokio::sync::{Semaphore, watch};
use tokio::task::JoinHandle;
use tokio::time::{MissedTickBehavior, interval};
use tokio_util::sync::CancellationToken;

const SPOTIFY_SINK: &str = "lumistripe_spotify";
pub(crate) const SPOTIFY_MONITOR_SOURCE: &str = "lumistripe_spotify.monitor";
const SPOTIFY_MONITOR: &str = SPOTIFY_MONITOR_SOURCE;
const MAX_DEVICE_COUNT: usize = 256;
const TERMINAL_OPERATION_TTL: Duration = Duration::from_secs(1);

#[derive(Clone)]
pub(crate) struct BluetoothService {
    enabled: bool,
    spotify_audio_routing_enabled: bool,
    max_inputs: usize,
    max_outputs: usize,
    bluez: Arc<dyn BlueZProvider>,
    pipewire: Arc<dyn PipeWireProvider>,
    status: watch::Sender<BluetoothStatus>,
    operation_gate: Arc<Semaphore>,
    route_gate: Arc<Semaphore>,
    operation_counter: Arc<AtomicU64>,
    operation_terminal_until: Arc<std::sync::Mutex<Option<tokio::time::Instant>>>,
    operation_error: Arc<std::sync::Mutex<Option<String>>>,
    cancellation: CancellationToken,
}

#[derive(Clone, Debug)]
enum BluetoothOperation {
    Scan,
    Power(bool),
    Alias(String),
    Pair(String),
    Connect(String, String),
    Forget(String),
    Disconnect(String),
}

impl BluetoothService {
    pub(crate) fn new(
        enabled: bool,
        spotify_audio_routing_enabled: bool,
        max_inputs: usize,
        max_outputs: usize,
        cancellation: CancellationToken,
    ) -> (Self, JoinHandle<()>) {
        let runner: Arc<dyn ProcessRunner> = Arc::new(SystemProcessRunner);
        let bluez = system_bluez_provider();
        let pipewire = system_pipewire_provider(runner);
        Self::with_limits_and_providers(
            enabled,
            spotify_audio_routing_enabled,
            bluez,
            pipewire,
            max_inputs,
            max_outputs,
            cancellation,
        )
    }

    #[cfg(test)]
    pub(crate) fn with_providers(
        enabled: bool,
        spotify_audio_routing_enabled: bool,
        bluez: Arc<dyn BlueZProvider>,
        pipewire: Arc<dyn PipeWireProvider>,
        cancellation: CancellationToken,
    ) -> (Self, JoinHandle<()>) {
        Self::with_limits_and_providers(
            enabled,
            spotify_audio_routing_enabled,
            bluez,
            pipewire,
            1,
            1,
            cancellation,
        )
    }

    pub(crate) fn with_limits_and_providers(
        enabled: bool,
        spotify_audio_routing_enabled: bool,
        bluez: Arc<dyn BlueZProvider>,
        pipewire: Arc<dyn PipeWireProvider>,
        max_inputs: usize,
        max_outputs: usize,
        cancellation: CancellationToken,
    ) -> (Self, JoinHandle<()>) {
        assert!(max_inputs > 0 && max_outputs > 0);
        let initial = BluetoothStatus {
            capabilities: capabilities(max_inputs, max_outputs),
            ..BluetoothStatus::default()
        };
        let (status, _) = watch::channel(initial);
        let service = Self {
            enabled,
            spotify_audio_routing_enabled,
            max_inputs,
            max_outputs,
            bluez,
            pipewire,
            status,
            operation_gate: Arc::new(Semaphore::new(1)),
            route_gate: Arc::new(Semaphore::new(1)),
            operation_counter: Arc::new(AtomicU64::new(1)),
            operation_terminal_until: Arc::new(std::sync::Mutex::new(None)),
            operation_error: Arc::new(std::sync::Mutex::new(None)),
            cancellation: cancellation.clone(),
        };
        let poller = service.clone();
        let task = tokio::spawn(async move {
            if !enabled {
                return;
            }
            let mut cadence = interval(Duration::from_secs(1));
            cadence.set_missed_tick_behavior(MissedTickBehavior::Skip);
            loop {
                tokio::select! {
                    _ = cancellation.cancelled() => break,
                    _ = cadence.tick() => {
                        tokio::select! {
                            _ = cancellation.cancelled() => break,
                            _ = poller.refresh() => {}
                        }
                    }
                }
            }
        });
        (service, task)
    }

    pub(crate) fn status(&self) -> BluetoothStatus {
        self.status.borrow().clone()
    }

    pub(crate) fn enabled(&self) -> bool {
        self.enabled
    }

    pub(crate) async fn refresh(&self) -> BluetoothStatus {
        if !self.enabled {
            return self.status();
        }
        self.clear_expired_terminal_operation();
        let mut next = self.collect_status().await;
        if self.spotify_audio_routing_enabled {
            if next.default_sink.is_some() {
                if let Err(error) = self.ensure_spotify_route_serialized(false).await {
                    next.error = Some(error.message);
                }
            }
        }
        self.status.send_replace(next.clone());
        next
    }

    pub(crate) fn start_scan(&self) -> Result<BluetoothStatus, BluetoothError> {
        self.start_operation("scanning".to_owned(), BluetoothOperation::Scan)
    }

    pub(crate) fn set_power(&self, powered: bool) -> Result<BluetoothStatus, BluetoothError> {
        self.start_operation(
            format!("power:{}", if powered { "on" } else { "off" }),
            BluetoothOperation::Power(powered),
        )
    }

    pub(crate) fn set_alias(&self, alias: String) -> Result<BluetoothStatus, BluetoothError> {
        let alias = validate_alias(&alias)?;
        self.start_operation("renaming".to_owned(), BluetoothOperation::Alias(alias))
    }

    pub(crate) fn pair(&self, address: String) -> Result<BluetoothStatus, BluetoothError> {
        let address = validate_address(&address)?;
        self.start_operation(
            format!("pairing:{address}"),
            BluetoothOperation::Pair(address),
        )
    }

    pub(crate) fn connect(
        &self,
        address: String,
        role: Option<String>,
    ) -> Result<BluetoothStatus, BluetoothError> {
        let address = validate_address(&address)?;
        let role = self.resolve_connection_role(&address, role.as_deref())?;
        self.ensure_connection_slot(&address, &role)?;
        self.start_operation(
            format!("connecting:{address}"),
            BluetoothOperation::Connect(address, role),
        )
    }

    pub(crate) fn forget(&self, address: String) -> Result<BluetoothStatus, BluetoothError> {
        let address = validate_address(&address)?;
        self.start_operation(
            format!("forgetting:{address}"),
            BluetoothOperation::Forget(address),
        )
    }

    pub(crate) fn disconnect(&self, address: String) -> Result<BluetoothStatus, BluetoothError> {
        let address = validate_address(&address)?;
        self.start_operation(
            format!("disconnecting:{address}"),
            BluetoothOperation::Disconnect(address),
        )
    }

    pub(crate) async fn set_default_sink(
        &self,
        selector: String,
    ) -> Result<BluetoothStatus, BluetoothError> {
        self.ensure_audio_outputs_enabled()?;
        let sink = self.resolve_output_selector(&selector).await?;
        let _permit = self.acquire_route_permit().await?;
        let mut previous_sink = self.status().default_sink;
        if previous_sink.is_none() {
            previous_sink = self
                .pipewire
                .execute(PipeWireCommand::Info)
                .await
                .ok()
                .and_then(|info| parse_info_value(&info, "Default Sink"));
        }
        self.pipewire
            .execute(PipeWireCommand::SetDefaultSink(sink.clone()))
            .await?;
        self.move_sink_inputs(&sink).await;
        if self.spotify_audio_routing_enabled {
            if let Err(error) = self
                .ensure_spotify_route_for_sink(Some(&sink), true, false)
                .await
            {
                if let Some(previous_sink) = previous_sink {
                    if self
                        .pipewire
                        .execute(PipeWireCommand::SetDefaultSink(previous_sink.clone()))
                        .await
                        .is_ok()
                    {
                        self.move_sink_inputs(&previous_sink).await;
                        let _ = self
                            .ensure_spotify_route_for_sink(Some(&previous_sink), true, false)
                            .await;
                    }
                }
                return Err(error);
            }
        }
        drop(_permit);
        Ok(self.refresh().await)
    }

    pub(crate) async fn set_output_volume(
        &self,
        selector: String,
        volume: f64,
    ) -> Result<BluetoothStatus, BluetoothError> {
        self.ensure_audio_outputs_enabled()?;
        if !volume.is_finite() || !(0.0..=1.0).contains(&volume) {
            return Err(BluetoothError::new(
                "invalid",
                "Output volume must be between 0 and 1",
            ));
        }
        let sink = self.resolve_output_selector(&selector).await?;
        self.pipewire
            .execute(PipeWireCommand::SetVolume { sink, volume })
            .await?;
        Ok(self.refresh().await)
    }

    pub(crate) async fn set_output_mute(
        &self,
        selector: String,
        muted: bool,
    ) -> Result<BluetoothStatus, BluetoothError> {
        self.ensure_audio_outputs_enabled()?;
        let sink = self.resolve_output_selector(&selector).await?;
        self.pipewire
            .execute(PipeWireCommand::SetMute { sink, muted })
            .await?;
        Ok(self.refresh().await)
    }

    pub(crate) async fn ensure_spotify_route(&self) -> Result<(), BluetoothError> {
        self.ensure_spotify_route_serialized(false).await
    }

    pub(crate) async fn refresh_spotify_route(&self) -> Result<(), BluetoothError> {
        self.ensure_spotify_route_serialized(true).await
    }

    pub(crate) async fn default_source(&self) -> Option<String> {
        self.pipewire
            .execute(PipeWireCommand::Info)
            .await
            .ok()
            .and_then(|info| parse_info_value(&info, "Default Source"))
    }

    pub(crate) async fn set_default_source(&self, source: String) -> Result<(), BluetoothError> {
        let source = validate_selector(&source)?;
        let _permit = self.acquire_route_permit().await?;
        self.pipewire
            .execute(PipeWireCommand::SetDefaultSource(source))
            .await
            .map(|_| ())
    }

    pub(crate) async fn restore_default_source(&self, source: Option<String>) {
        if let Some(source) = source {
            if let Err(error) = self.set_default_source(source).await {
                tracing::warn!(%error, "could not restore the default PipeWire source");
            }
        }
    }

    fn start_operation(
        &self,
        name: String,
        operation: BluetoothOperation,
    ) -> Result<BluetoothStatus, BluetoothError> {
        if !self.enabled {
            return Err(BluetoothError::new(
                "unavailable",
                "Bluetooth is only available in hardware mode.",
            ));
        }
        let permit = self
            .operation_gate
            .clone()
            .try_acquire_owned()
            .map_err(|_| {
                BluetoothError::new("busy", "another Bluetooth operation is already running")
            })?;
        let operation_id = format!(
            "{:x}-{:x}",
            std::process::id(),
            self.operation_counter.fetch_add(1, Ordering::Relaxed)
        );
        if let Ok(mut terminal_until) = self.operation_terminal_until.lock() {
            *terminal_until = None;
        }
        if let Ok(mut operation_error) = self.operation_error.lock() {
            *operation_error = None;
        }
        self.status.send_modify(|status| {
            status.operation = Some(name.clone());
            status.operation_id = Some(operation_id.clone());
            status.operation_state = BluetoothOperationState::Running;
            status.error = None;
            status.scanning = name == "scanning";
        });
        let accepted_status = self.status();
        let manager = self.clone();
        let cancellation = self.cancellation.clone();
        tokio::spawn(async move {
            let result = tokio::select! {
                _ = cancellation.cancelled() => Err(BluetoothError::new(
                    "cancelled",
                    "Bluetooth operation was cancelled during shutdown.",
                )),
                result = manager.execute_operation(operation) => result,
            };
            let cancelled = result
                .as_ref()
                .is_err_and(|error| error.code == "cancelled");
            let failure = result.err().map(|error| error.message);
            if let Ok(mut operation_error) = manager.operation_error.lock() {
                *operation_error = failure.clone();
            }
            manager.status.send_modify(|status| {
                status.operation = None;
                status.operation_state = if failure.is_some() {
                    BluetoothOperationState::Failed
                } else {
                    BluetoothOperationState::Complete
                };
                status.scanning = false;
                status.error = failure.clone();
            });
            if let Ok(mut terminal_until) = manager.operation_terminal_until.lock() {
                *terminal_until = Some(tokio::time::Instant::now() + TERMINAL_OPERATION_TTL);
            }
            if !cancelled {
                let _ = manager.refresh().await;
            }
            drop(permit);
        });
        Ok(accepted_status)
    }

    fn clear_expired_terminal_operation(&self) {
        let expired = self
            .operation_terminal_until
            .lock()
            .ok()
            .and_then(|mut terminal_until| {
                if terminal_until.is_some_and(|deadline| tokio::time::Instant::now() >= deadline) {
                    *terminal_until = None;
                    Some(true)
                } else {
                    Some(false)
                }
            })
            .unwrap_or(false);
        if expired {
            if let Ok(mut operation_error) = self.operation_error.lock() {
                *operation_error = None;
            }
            self.status.send_modify(|status| {
                if status.operation.is_none()
                    && matches!(
                        status.operation_state,
                        BluetoothOperationState::Complete | BluetoothOperationState::Failed
                    )
                {
                    status.operation_state = BluetoothOperationState::Idle;
                    status.error = None;
                }
            });
        }
    }

    async fn execute_operation(&self, operation: BluetoothOperation) -> Result<(), BluetoothError> {
        let command = match operation {
            BluetoothOperation::Scan => BlueZCommand::Scan,
            BluetoothOperation::Power(powered) => BlueZCommand::SetPower(powered),
            BluetoothOperation::Alias(alias) => BlueZCommand::SetAlias(alias),
            BluetoothOperation::Pair(address) => BlueZCommand::Pair(address),
            BluetoothOperation::Connect(address, role) => BlueZCommand::Connect { address, role },
            BluetoothOperation::Forget(address) => BlueZCommand::Forget(address),
            BluetoothOperation::Disconnect(address) => BlueZCommand::Disconnect(address),
        };
        self.bluez.execute(command).await
    }

    async fn collect_status(&self) -> BluetoothStatus {
        let previous = self.status();
        let bluez = match self.bluez.snapshot().await {
            Ok(value) => value,
            Err(error) => return preserve_status(&previous, error.message),
        };
        if !self.pipewire.available() {
            let mut status = self.empty_status();
            status.available = true;
            status.powered = bluez.powered;
            status.adapter_alias = bluez.alias;
            status.scanning = bluez.scanning || previous.operation.as_deref() == Some("scanning");
            status.devices = bluez.devices;
            status.operation = previous.operation;
            status.operation_id = previous.operation_id;
            status.operation_state = previous.operation_state;
            status.error = Some("pactl is not installed.".to_owned());
            return status;
        }
        let sources = match self.pipewire.execute(PipeWireCommand::Sources).await {
            Ok(value) => value,
            Err(error) => return preserve_status(&previous, error.message),
        };
        let sinks = match self.pipewire.execute(PipeWireCommand::Sinks).await {
            Ok(value) => value,
            Err(error) => return preserve_status(&previous, error.message),
        };
        let info = match self.pipewire.execute(PipeWireCommand::Info).await {
            Ok(value) => value,
            Err(error) => return preserve_status(&previous, error.message),
        };
        let sink_details = self
            .pipewire
            .execute(PipeWireCommand::SinkDetails)
            .await
            .unwrap_or_default();
        let stream_status = if self.pipewire.has_wpctl() {
            self.pipewire
                .execute(PipeWireCommand::StreamStatus)
                .await
                .unwrap_or_default()
        } else {
            String::new()
        };

        let previous_roles = previous
            .devices
            .iter()
            .map(|device| (device.address.to_ascii_lowercase(), device.roles.clone()))
            .collect::<std::collections::HashMap<_, _>>();
        let mut devices = Vec::new();
        for mut device in bluez.devices.into_iter().take(MAX_DEVICE_COUNT) {
            let key = device.address.to_ascii_lowercase();
            if device.roles.is_empty() {
                device.roles = previous_roles.get(&key).cloned().unwrap_or_default();
            }
            if contains_bluetooth_token(&sinks, &device.address) {
                merge_role(&mut device.roles, "output");
            }
            if has_bluetooth_input_source(&sources, &device.address)
                || has_active_bluetooth_stream(&stream_status, &device.address)
            {
                merge_role(&mut device.roles, "input");
            }
            devices.push(device);
        }
        let connected_inputs = devices
            .iter()
            .filter(|device| device.connected && device.roles.iter().any(|role| role == "input"))
            .cloned()
            .collect::<Vec<_>>();
        let connected_outputs = devices
            .iter()
            .filter(|device| device.connected && device.roles.iter().any(|role| role == "output"))
            .cloned()
            .collect::<Vec<_>>();
        let connected_device = connected_inputs
            .first()
            .or_else(|| devices.iter().find(|device| device.connected))
            .cloned();
        let default_sink = parse_info_value(&info, "Default Sink");
        let mut input_source = None;
        for device in &connected_inputs {
            input_source = find_bluetooth_source(&sources, &device.address);
            if input_source.is_none()
                && has_active_bluetooth_stream(&stream_status, &device.address)
            {
                input_source = find_sink_monitor(&sources, default_sink.as_deref());
            }
            if input_source.is_some() {
                break;
            }
        }
        let output_devices = parse_output_devices(&sinks, &sink_details, &devices);
        let default_output = output_devices
            .iter()
            .find(|device| Some(device.selector.as_str()) == default_sink.as_deref());
        let output_volume = default_output.and_then(|device| device.volume);
        let output_muted = default_output.is_some_and(|device| device.muted);
        let operation_error = if previous.operation_state == BluetoothOperationState::Failed {
            self.operation_error
                .lock()
                .ok()
                .and_then(|operation_error| operation_error.clone())
        } else {
            None
        };
        BluetoothStatus {
            available: true,
            powered: bluez.powered,
            adapter_alias: bluez.alias,
            scanning: bluez.scanning || previous.operation.as_deref() == Some("scanning"),
            streaming: input_source.is_some()
                && (!connected_inputs.is_empty() || connected_device.is_some()),
            devices,
            connected_inputs,
            connected_outputs,
            connected_device,
            input_source,
            output_devices,
            default_sink: default_sink.clone(),
            output_volume,
            output_muted,
            output_ready: default_sink.is_some() && !sinks.trim().is_empty(),
            capabilities: capabilities(self.max_inputs, self.max_outputs),
            operation: previous.operation,
            operation_id: previous.operation_id,
            operation_state: previous.operation_state,
            error: operation_error,
        }
    }

    async fn resolve_output_selector(&self, selector: &str) -> Result<String, BluetoothError> {
        let selector = validate_selector(selector)?;
        let mut status = self.status();
        if status.output_devices.is_empty() {
            status = self.refresh().await;
        }
        status
            .output_devices
            .iter()
            .find(|device| device.selector == selector)
            .map(|device| device.selector.clone())
            .ok_or_else(|| {
                BluetoothError::new("unavailable", "Selected PipeWire output is unavailable")
            })
    }

    async fn move_sink_inputs(&self, sink: &str) {
        let Ok(inputs) = self.pipewire.execute(PipeWireCommand::SinkInputs).await else {
            return;
        };
        for line in inputs.lines() {
            let Some(input_id) = line
                .split_whitespace()
                .next()
                .filter(|id| id.chars().all(|character| character.is_ascii_digit()))
            else {
                continue;
            };
            let _ = self
                .pipewire
                .execute(PipeWireCommand::MoveSinkInput {
                    input_id: input_id.to_owned(),
                    sink: sink.to_owned(),
                })
                .await;
        }
    }

    async fn ensure_spotify_route_for_sink(
        &self,
        requested_sink: Option<&str>,
        create: bool,
        force: bool,
    ) -> Result<(), BluetoothError> {
        if !self.spotify_audio_routing_enabled {
            return Err(BluetoothError::new(
                "unavailable",
                "Spotify audio routing is disabled",
            ));
        }
        let default_sink = match requested_sink
            .map(str::to_owned)
            .or(self.status().default_sink)
        {
            Some(sink) => sink,
            None => {
                let info = self.pipewire.execute(PipeWireCommand::Info).await?;
                parse_info_value(&info, "Default Sink").ok_or_else(|| {
                    BluetoothError::new(
                        "unavailable",
                        "no default PipeWire output is available for Spotify",
                    )
                })?
            }
        };
        let modules = self.pipewire.execute(PipeWireCommand::Modules).await?;
        let source_argument = format!("source={SPOTIFY_MONITOR}");
        let existing = modules.lines().find_map(|line| {
            let fields = line.split_whitespace().collect::<Vec<_>>();
            if fields.len() < 2
                || fields[0].parse::<u64>().is_err()
                || fields[1] != "module-loopback"
                || !fields.contains(&source_argument.as_str())
            {
                return None;
            }
            let sink = fields
                .iter()
                .find_map(|field| field.strip_prefix("sink="))
                .map(str::to_owned);
            Some((fields[0].to_owned(), sink))
        });
        match existing {
            None if !create => return Ok(()),
            Some((_, Some(sink))) if sink == default_sink && !force => return Ok(()),
            Some((module_id, _)) => {
                self.pipewire
                    .execute(PipeWireCommand::UnloadModule(module_id))
                    .await?;
            }
            None => {}
        }
        let response = self
            .pipewire
            .execute(PipeWireCommand::LoadModule {
                name: "module-loopback".to_owned(),
                arguments: format!(
                    "source={SPOTIFY_MONITOR} sink={default_sink} latency_msec=20 sink_input_properties=application.name=LumiStripe-Spotify"
                ),
            })
            .await?;
        if response.trim().parse::<u64>().is_err() {
            return Err(BluetoothError::new(
                "routing_failed",
                "PipeWire could not create the Spotify output route",
            ));
        }
        Ok(())
    }

    async fn ensure_spotify_route_serialized(&self, force: bool) -> Result<(), BluetoothError> {
        let _permit = self.acquire_route_permit().await?;
        let info = self.pipewire.execute(PipeWireCommand::Info).await?;
        let sink = parse_info_value(&info, "Default Sink")
            .or_else(|| self.status().default_sink)
            .ok_or_else(|| {
                BluetoothError::new(
                    "unavailable",
                    "no default PipeWire output is available for Spotify",
                )
            })?;
        self.ensure_spotify_route_for_sink(Some(&sink), true, force)
            .await
    }

    async fn acquire_route_permit(
        &self,
    ) -> Result<tokio::sync::OwnedSemaphorePermit, BluetoothError> {
        self.route_gate
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| BluetoothError::new("cancelled", "PipeWire routing is shutting down"))
    }

    fn ensure_audio_outputs_enabled(&self) -> Result<(), BluetoothError> {
        if !self.enabled {
            return Err(BluetoothError::new(
                "unavailable",
                "Audio outputs are only available in hardware mode.",
            ));
        }
        Ok(())
    }

    fn resolve_connection_role(
        &self,
        address: &str,
        role: Option<&str>,
    ) -> Result<String, BluetoothError> {
        if let Some(role) = role {
            if role != "input" && role != "output" {
                return Err(BluetoothError::new(
                    "invalid",
                    "Bluetooth role must be input or output",
                ));
            }
            return Ok(role.to_owned());
        }
        let role = self
            .status()
            .devices
            .into_iter()
            .find(|device| device.address.eq_ignore_ascii_case(address))
            .filter(|device| device.roles.iter().any(|role| role == "output"))
            .map(|_| "output")
            .unwrap_or("input");
        Ok(role.to_owned())
    }

    fn ensure_connection_slot(&self, address: &str, role: &str) -> Result<(), BluetoothError> {
        let status = self.status();
        let (connected, limit) = if role == "input" {
            (status.connected_inputs, self.max_inputs)
        } else {
            (status.connected_outputs, self.max_outputs)
        };
        let mut active = connected
            .into_iter()
            .filter(|device| !device.address.eq_ignore_ascii_case(address));
        if let Some(conflict) = active.nth(limit.saturating_sub(1)) {
            return Err(BluetoothError::new(
                "busy",
                format!(
                    "Bluetooth {role} is already connected to {}. Disconnect it before connecting another {role}.",
                    conflict.name
                ),
            ));
        }
        Ok(())
    }

    fn empty_status(&self) -> BluetoothStatus {
        BluetoothStatus {
            capabilities: capabilities(self.max_inputs, self.max_outputs),
            ..BluetoothStatus::default()
        }
    }
}

fn capabilities(max_inputs: usize, max_outputs: usize) -> BluetoothCapabilities {
    BluetoothCapabilities {
        operations: [
            "power",
            "rename",
            "scan",
            "pair",
            "connect",
            "disconnect",
            "forget",
            "output_select",
            "output_volume",
            "output_mute",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect(),
        max_inputs,
        max_outputs,
    }
}

fn preserve_status(previous: &BluetoothStatus, error: String) -> BluetoothStatus {
    let mut status = previous.clone();
    status.available = true;
    status.error = Some(error);
    status
}

fn validate_address(address: &str) -> Result<String, BluetoothError> {
    let address = address.trim().to_ascii_uppercase();
    let valid = address.len() == 17
        && address.split(':').count() == 6
        && address
            .split(':')
            .all(|part| part.len() == 2 && part.bytes().all(|byte| byte.is_ascii_hexdigit()));
    if !valid {
        return Err(BluetoothError::new(
            "invalid",
            "Bluetooth address must look like AA:BB:CC:DD:EE:FF",
        ));
    }
    Ok(address)
}

fn validate_alias(alias: &str) -> Result<String, BluetoothError> {
    let alias = alias.trim();
    if alias.is_empty() {
        return Err(BluetoothError::new(
            "invalid",
            "Bluetooth name cannot be empty",
        ));
    }
    if alias.chars().count() > 64 {
        return Err(BluetoothError::new(
            "invalid",
            "Bluetooth name must be 64 characters or fewer",
        ));
    }
    if alias.chars().any(char::is_control) {
        return Err(BluetoothError::new(
            "invalid",
            "Bluetooth name cannot contain control characters",
        ));
    }
    Ok(alias.to_owned())
}

fn validate_selector(selector: &str) -> Result<String, BluetoothError> {
    let selector = selector.trim();
    if selector.is_empty()
        || selector.len() > 256
        || selector.starts_with('-')
        || !selector
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_:.@+-".contains(&byte))
    {
        return Err(BluetoothError::new(
            "invalid",
            "PipeWire selector is invalid",
        ));
    }
    Ok(selector.to_owned())
}

fn merge_role(roles: &mut Vec<String>, role: &str) {
    if !roles.iter().any(|existing| existing == role) {
        if role == "input" {
            roles.insert(0, role.to_owned());
        } else {
            roles.push(role.to_owned());
        }
    }
}

fn contains_bluetooth_token(output: &str, address: &str) -> bool {
    output
        .to_ascii_lowercase()
        .contains(&address.replace(':', "_").to_ascii_lowercase())
}

fn has_bluetooth_input_source(output: &str, address: &str) -> bool {
    let token = address.replace(':', "_").to_ascii_lowercase();
    output.lines().any(|line| {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        fields.get(1).is_some_and(|source| {
            source.to_ascii_lowercase().starts_with("bluez_input.")
                && source.to_ascii_lowercase().contains(&token)
        })
    })
}

fn has_active_bluetooth_stream(output: &str, address: &str) -> bool {
    let token = address.replace(':', "_").to_ascii_lowercase();
    let lines = output.lines().collect::<Vec<_>>();
    lines.iter().enumerate().any(|(index, line)| {
        line.to_ascii_lowercase()
            .contains(&format!("bluez_input.{token}"))
            && lines[index..(index + 5).min(lines.len())]
                .iter()
                .any(|candidate| candidate.to_ascii_lowercase().contains("[active]"))
    })
}

fn parse_info_value(output: &str, property: &str) -> Option<String> {
    let prefix = format!("{}:", property.to_ascii_lowercase());
    output.lines().find_map(|line| {
        let line = line.trim();
        line.to_ascii_lowercase()
            .starts_with(&prefix)
            .then(|| {
                line.split_once(':')
                    .map(|(_, value)| value.trim().to_owned())
            })
            .flatten()
            .filter(|value| !value.is_empty())
    })
}

fn parse_bool_property(output: &str, property: &str) -> bool {
    parse_info_value(output, property).is_some_and(|value| value.eq_ignore_ascii_case("yes"))
}

fn find_bluetooth_source(output: &str, address: &str) -> Option<String> {
    let token = address.replace(':', "_").to_ascii_lowercase();
    let mut candidates = Vec::new();
    for line in output.lines() {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        if fields.len() < 2 || !fields[1].to_ascii_lowercase().contains(&token) {
            continue;
        }
        let source = fields[1];
        let lower = source.to_ascii_lowercase();
        if lower.starts_with("bluez_output.") && lower.ends_with(".monitor") {
            candidates.insert(0, source.to_owned());
        } else {
            candidates.push(source.to_owned());
        }
    }
    candidates.into_iter().next()
}

fn find_sink_monitor(output: &str, default_sink: Option<&str>) -> Option<String> {
    let monitors = output
        .lines()
        .filter_map(|line| {
            let fields = line.split_whitespace().collect::<Vec<_>>();
            (fields.len() >= 2 && fields[1].to_ascii_lowercase().ends_with(".monitor")).then(|| {
                (
                    fields[1].to_owned(),
                    fields.last().unwrap_or(&"").to_ascii_lowercase(),
                )
            })
        })
        .collect::<Vec<_>>();
    if let Some(default_sink) = default_sink {
        let expected = format!("{default_sink}.monitor");
        if let Some((source, _)) = monitors
            .iter()
            .find(|(source, _)| source.eq_ignore_ascii_case(&expected))
        {
            return Some(source.clone());
        }
    }
    monitors
        .iter()
        .find(|(_, state)| state == "running")
        .or_else(|| monitors.first())
        .map(|(source, _)| source.clone())
}

fn parse_output_devices(
    sinks: &str,
    details: &str,
    bluetooth_devices: &[BluetoothDevice],
) -> Vec<AudioOutputDevice> {
    let mut sink_details = std::collections::HashMap::new();
    for block in details.split("Sink #") {
        let Some(selector) = parse_info_value(block, "Name") else {
            continue;
        };
        let description = parse_info_value(block, "Description");
        let muted = parse_bool_property(block, "Mute");
        let volume = block.lines().find_map(|line| {
            let lower = line.trim().to_ascii_lowercase();
            if !lower.starts_with("volume:") {
                return None;
            }
            let percent = lower.split('/').nth(1)?.trim().strip_suffix('%')?.trim();
            percent
                .parse::<f64>()
                .ok()
                .filter(|number| number.is_finite())
                .map(|number| (number / 100.0).clamp(0.0, 1.0))
        });
        sink_details.insert(selector.to_ascii_lowercase(), (description, volume, muted));
    }
    sinks
        .lines()
        .filter_map(|line| {
            let fields = line.split_whitespace().collect::<Vec<_>>();
            let selector = *fields.get(1)?;
            if selector == SPOTIFY_SINK {
                return None;
            }
            let address = bluetooth_address_from_node(selector);
            let details = sink_details.get(&selector.to_ascii_lowercase());
            let bluetooth = address.is_some();
            let name = address
                .as_deref()
                .and_then(|address| {
                    bluetooth_devices
                        .iter()
                        .find(|device| device.address.eq_ignore_ascii_case(address))
                        .map(|device| device.name.clone())
                })
                .or_else(|| details.and_then(|details| details.0.clone()))
                .unwrap_or_else(|| selector.to_owned());
            Some(AudioOutputDevice {
                selector: selector.to_owned(),
                name,
                volume: details.and_then(|details| details.1),
                muted: details.is_some_and(|details| details.2),
                bluetooth,
                connected: address.as_deref().is_none_or(|address| {
                    bluetooth_devices.iter().any(|device| {
                        device.connected && device.address.eq_ignore_ascii_case(address)
                    })
                }),
            })
        })
        .collect()
}

fn bluetooth_address_from_node(node: &str) -> Option<String> {
    let (_, suffix) = node.split_once("bluez_")?;
    let (_, suffix) = suffix.split_once('.')?;
    let address = suffix.split(['.', '_']).take(6).collect::<Vec<_>>();
    if address.len() != 6
        || address
            .iter()
            .any(|part| part.len() != 2 || !part.bytes().all(|byte| byte.is_ascii_hexdigit()))
    {
        return None;
    }
    Some(address.join(":").to_ascii_uppercase())
}

#[cfg(test)]
mod tests {
    use super::{BluetoothService, validate_address, validate_alias};
    use crate::bluetooth_models::BluetoothDevice;
    use crate::bluetooth_process::BluetoothError;
    use crate::bluetooth_providers::{
        BlueZCommand, BlueZProvider, BlueZSnapshot, PipeWireCommand, PipeWireProvider,
    };
    use futures_util::future::BoxFuture;
    use lumistripe_engine_client::EngineClient;
    use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
    use lumistripe_engine_protocol::{
        RenderOutput, RenderPowerConfig, RenderSceneUpdate, RenderSchedulerConfig, ResponseResult,
    };
    use std::sync::Arc;
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::Duration;
    use std::{path::PathBuf, process::Stdio};
    use tokio::process::Command;
    use tokio::sync::{mpsc, oneshot};
    use tokio::time::{sleep, timeout};
    use tokio_util::sync::CancellationToken;

    struct FakeBlueZ;

    impl BlueZProvider for FakeBlueZ {
        fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
            Box::pin(async {
                Ok(BlueZSnapshot {
                    powered: true,
                    alias: Some("LumiStripe".to_owned()),
                    scanning: false,
                    devices: vec![BluetoothDevice {
                        address: "AA:BB:CC:DD:EE:01".to_owned(),
                        name: "Test Headset".to_owned(),
                        paired: true,
                        connected: true,
                        roles: vec!["input".to_owned()],
                    }],
                })
            })
        }

        fn execute(
            &self,
            _command: BlueZCommand,
        ) -> BoxFuture<'static, Result<(), BluetoothError>> {
            Box::pin(async { Ok(()) })
        }
    }

    struct SwitchableBlueZ {
        snapshot: Arc<Mutex<BlueZSnapshot>>,
        unavailable: Arc<AtomicBool>,
        fail_operations: Arc<AtomicBool>,
    }

    impl BlueZProvider for SwitchableBlueZ {
        fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
            let snapshot = self.snapshot.clone();
            let unavailable = self.unavailable.clone();
            Box::pin(async move {
                if unavailable.load(Ordering::Acquire) {
                    return Err(BluetoothError::new(
                        "unavailable",
                        "BlueZ D-Bus service is unavailable.",
                    ));
                }
                Ok(snapshot.lock().unwrap().clone())
            })
        }

        fn execute(
            &self,
            _command: BlueZCommand,
        ) -> BoxFuture<'static, Result<(), BluetoothError>> {
            let fail_operations = self.fail_operations.clone();
            Box::pin(async move {
                if fail_operations.load(Ordering::Acquire) {
                    Err(BluetoothError::new(
                        "rejected",
                        "BlueZ command was rejected.",
                    ))
                } else {
                    Ok(())
                }
            })
        }
    }

    struct FakePipeWire;

    impl PipeWireProvider for FakePipeWire {
        fn available(&self) -> bool {
            true
        }

        fn has_wpctl(&self) -> bool {
            true
        }

        fn execute(
            &self,
            command: PipeWireCommand,
        ) -> BoxFuture<'static, Result<String, BluetoothError>> {
            Box::pin(async move {
                let output = match command {
                    PipeWireCommand::Sources => {
                        "3 bluez_input.AA_BB_CC_DD_EE_01.0 PipeWire s16le 2ch 48000Hz RUNNING"
                    }
                    PipeWireCommand::Sinks => {
                        "4 bluez_output.AA_BB_CC_DD_EE_02.1 PipeWire s16le 2ch 48000Hz RUNNING"
                    }
                    PipeWireCommand::SinkDetails => {
                        "Sink #4\n Name: bluez_output.AA_BB_CC_DD_EE_02.1\n Description: Test Speaker\n Mute: no\n Volume: front-left: 65536 / 100%"
                    }
                    PipeWireCommand::Info => {
                        "Default Sink: bluez_output.AA_BB_CC_DD_EE_02.1\nDefault Source: alsa_input.usb"
                    }
                    PipeWireCommand::StreamStatus => "",
                    PipeWireCommand::SinkInputs
                    | PipeWireCommand::Modules
                    | PipeWireCommand::SetDefaultSink(_)
                    | PipeWireCommand::SetDefaultSource(_)
                    | PipeWireCommand::MoveSinkInput { .. }
                    | PipeWireCommand::LoadModule { .. }
                    | PipeWireCommand::UnloadModule(_)
                    | PipeWireCommand::SetVolume { .. }
                    | PipeWireCommand::SetMute { .. } => "",
                };
                Ok(output.to_owned())
            })
        }
    }

    #[derive(Default)]
    struct RoutingState {
        default_sink: String,
        modules: String,
        load_count: usize,
        unload_count: usize,
        next_module_id: u64,
        failed_load_sink: Option<String>,
        default_sink_changes: Vec<String>,
    }

    struct RecordingPipeWire(Arc<Mutex<RoutingState>>);

    impl PipeWireProvider for RecordingPipeWire {
        fn available(&self) -> bool {
            true
        }

        fn has_wpctl(&self) -> bool {
            false
        }

        fn execute(
            &self,
            command: PipeWireCommand,
        ) -> BoxFuture<'static, Result<String, BluetoothError>> {
            let state = self.0.clone();
            Box::pin(async move {
                let mut state = state.lock().unwrap();
                match command {
                    PipeWireCommand::Info => Ok(format!("Default Sink: {}", state.default_sink)),
                    PipeWireCommand::Sinks => Ok(
                        "1 speaker.one module-test s16le 2ch 48000Hz RUNNING\n2 speaker.two module-test s16le 2ch 48000Hz SUSPENDED".to_owned(),
                    ),
                    PipeWireCommand::SinkDetails => Ok(
                        "Sink #1\n Name: speaker.one\n Description: Speaker One\n Mute: no\n Volume: front-left: 65536 / 70%\n\nSink #2\n Name: speaker.two\n Description: Speaker Two\n Mute: no\n Volume: front-left: 65536 / 70%".to_owned(),
                    ),
                    PipeWireCommand::Modules => Ok(state.modules.clone()),
                    PipeWireCommand::LoadModule { name, arguments } => {
                        let requested_sink = arguments
                            .split_whitespace()
                            .find_map(|argument| argument.strip_prefix("sink="));
                        if state
                            .failed_load_sink
                            .as_deref()
                            .is_some_and(|failed_sink| requested_sink == Some(failed_sink))
                        {
                            return Err(BluetoothError::new(
                                "test_route_failure",
                                "test loopback creation failure",
                            ));
                        }
                        let id = state.next_module_id.max(1);
                        state.next_module_id = id + 1;
                        state.load_count += 1;
                        state.modules = format!("{id} {name} {arguments}");
                        Ok(id.to_string())
                    }
                    PipeWireCommand::UnloadModule(module_id) => {
                        state.unload_count += 1;
                        if state.modules.starts_with(&format!("{module_id} ")) {
                            state.modules.clear();
                        }
                        Ok(String::new())
                    }
                    PipeWireCommand::SetDefaultSink(sink) => {
                        state.default_sink_changes.push(sink.clone());
                        state.default_sink = sink;
                        Ok(String::new())
                    }
                    _ => Ok(String::new()),
                }
            })
        }
    }

    struct ControlledBlueZ {
        commands: mpsc::UnboundedSender<BlueZCommand>,
        release: Mutex<Option<oneshot::Receiver<()>>>,
        failure: Option<&'static str>,
    }

    impl BlueZProvider for ControlledBlueZ {
        fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
            Box::pin(async { Ok(BlueZSnapshot::default()) })
        }

        fn execute(&self, command: BlueZCommand) -> BoxFuture<'static, Result<(), BluetoothError>> {
            let commands = self.commands.clone();
            let release = self.release.lock().unwrap().take();
            let failure = self.failure;
            Box::pin(async move {
                let _ = commands.send(command);
                if let Some(release) = release {
                    let _ = release.await;
                }
                match failure {
                    Some(message) => Err(BluetoothError::new("bluez", message)),
                    None => Ok(()),
                }
            })
        }
    }

    #[tokio::test]
    async fn refresh_builds_a_stable_input_and_output_snapshot_from_bluez_and_pipewire() {
        let cancellation = CancellationToken::new();
        let (service, poller) = BluetoothService::with_providers(
            true,
            false,
            Arc::new(FakeBlueZ),
            Arc::new(FakePipeWire),
            cancellation.clone(),
        );

        let status = service.refresh().await;

        assert!(status.available);
        assert!(status.powered);
        assert!(status.streaming);
        assert_eq!(status.adapter_alias.as_deref(), Some("LumiStripe"));
        assert_eq!(
            status.input_source.as_deref(),
            Some("bluez_input.AA_BB_CC_DD_EE_01.0")
        );
        assert_eq!(status.connected_inputs[0].name, "Test Headset");
        assert_eq!(status.connected_inputs[0].roles, ["input"]);
        assert_eq!(status.output_devices[0].name, "Test Speaker");
        assert!(status.output_devices[0].bluetooth);
        assert_eq!(
            status.default_sink.as_deref(),
            Some("bluez_output.AA_BB_CC_DD_EE_02.1")
        );

        cancellation.cancel();
        tokio::time::timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[tokio::test]
    async fn refresh_reconciles_out_of_band_changes_and_preserves_state_during_bluez_outage() {
        let cancellation = CancellationToken::new();
        let snapshot = Arc::new(Mutex::new(BlueZSnapshot {
            powered: true,
            alias: Some("LumiStripe".to_owned()),
            scanning: false,
            devices: vec![BluetoothDevice {
                address: "AA:BB:CC:DD:EE:01".to_owned(),
                name: "Test Headset".to_owned(),
                paired: true,
                connected: true,
                roles: vec!["input".to_owned()],
            }],
        }));
        let unavailable = Arc::new(AtomicBool::new(false));
        let fail_operations = Arc::new(AtomicBool::new(false));
        let (service, poller) = BluetoothService::with_providers(
            true,
            false,
            Arc::new(SwitchableBlueZ {
                snapshot: snapshot.clone(),
                unavailable: unavailable.clone(),
                fail_operations: fail_operations.clone(),
            }),
            Arc::new(FakePipeWire),
            cancellation.clone(),
        );

        let initial = service.refresh().await;
        assert_eq!(initial.connected_inputs.len(), 1);
        assert_eq!(
            initial.default_sink.as_deref(),
            Some("bluez_output.AA_BB_CC_DD_EE_02.1")
        );

        {
            let mut changed = snapshot.lock().unwrap();
            changed.alias = Some("LumiStripe Stage".to_owned());
            changed.devices[0].name = "Renamed Headset".to_owned();
            changed.devices[0].connected = false;
        }
        let changed = service.refresh().await;
        assert_eq!(changed.adapter_alias.as_deref(), Some("LumiStripe Stage"));
        assert_eq!(changed.devices[0].name, "Renamed Headset");
        assert!(!changed.devices[0].connected);
        assert!(changed.connected_inputs.is_empty());
        assert!(changed.input_source.is_none());
        assert_eq!(changed.output_devices[0].name, "Test Speaker");

        unavailable.store(true, Ordering::Release);
        let degraded = service.refresh().await;
        assert!(degraded.available);
        assert_eq!(
            degraded.error.as_deref(),
            Some("BlueZ D-Bus service is unavailable.")
        );
        assert_eq!(degraded.devices, changed.devices);
        assert_eq!(degraded.output_devices, changed.output_devices);
        assert_eq!(degraded.default_sink, changed.default_sink);

        unavailable.store(false, Ordering::Release);
        let recovered = service.refresh().await;
        assert!(recovered.error.is_none());
        assert_eq!(recovered.devices, changed.devices);
        assert_eq!(recovered.default_sink, changed.default_sink);

        fail_operations.store(true, Ordering::Release);
        service.set_power(false).unwrap();
        timeout(Duration::from_secs(1), async {
            while service.status().operation_state != super::BluetoothOperationState::Failed {
                sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .expect("rejected BlueZ command should publish its terminal failure");
        assert_eq!(
            service.status().error.as_deref(),
            Some("BlueZ command was rejected.")
        );

        unavailable.store(true, Ordering::Release);
        let operation_and_provider_failure = service.refresh().await;
        assert_eq!(
            operation_and_provider_failure.error.as_deref(),
            Some("BlueZ D-Bus service is unavailable.")
        );
        unavailable.store(false, Ordering::Release);
        let operation_failure_recovered = service.refresh().await;
        assert_eq!(
            operation_failure_recovered.error.as_deref(),
            Some("BlueZ command was rejected.")
        );

        cancellation.cancel();
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[tokio::test]
    async fn configured_connection_limits_are_reported_and_enforced_per_role() {
        let cancellation = CancellationToken::new();
        let (service, poller) = BluetoothService::with_limits_and_providers(
            true,
            false,
            Arc::new(FakeBlueZ),
            Arc::new(FakePipeWire),
            2,
            3,
            cancellation.clone(),
        );
        let status = service.refresh().await;
        assert_eq!(status.capabilities.max_inputs, 2);
        assert_eq!(status.capabilities.max_outputs, 3);
        assert!(
            service
                .connect("AA:BB:CC:DD:EE:02".to_owned(), Some("input".to_owned()))
                .is_ok()
        );
        tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                let status = service.status();
                if status.operation.is_none()
                    && status.operation_state == super::BluetoothOperationState::Complete
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("connection operation should publish completion");
        cancellation.cancel();
        tokio::time::timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();

        let cancellation = CancellationToken::new();
        let (service, poller) = BluetoothService::with_limits_and_providers(
            true,
            false,
            Arc::new(FakeBlueZ),
            Arc::new(FakePipeWire),
            1,
            1,
            cancellation.clone(),
        );
        service.refresh().await;
        let error = service
            .connect("AA:BB:CC:DD:EE:02".to_owned(), Some("input".to_owned()))
            .unwrap_err();
        assert_eq!(error.code, "busy");
        cancellation.cancel();
        tokio::time::timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[tokio::test]
    async fn operations_are_serialized_and_terminal_results_remain_visible_until_the_next_poll() {
        let cancellation = CancellationToken::new();
        let (command_sender, mut command_receiver) = mpsc::unbounded_channel();
        let (release_sender, release_receiver) = oneshot::channel();
        let bluez = ControlledBlueZ {
            commands: command_sender,
            release: Mutex::new(Some(release_receiver)),
            failure: None,
        };
        let (service, poller) = BluetoothService::with_limits_and_providers(
            true,
            false,
            Arc::new(bluez),
            Arc::new(FakePipeWire),
            1,
            1,
            cancellation.clone(),
        );

        let started = service.set_power(false).unwrap();
        assert_eq!(
            started.operation_state,
            super::BluetoothOperationState::Running
        );
        assert_eq!(started.operation.as_deref(), Some("power:off"));
        assert_eq!(
            service.set_alias("LumiStripe".to_owned()).unwrap_err().code,
            "busy"
        );
        assert!(matches!(
            command_receiver.recv().await,
            Some(BlueZCommand::SetPower(false))
        ));

        release_sender.send(()).unwrap();
        timeout(Duration::from_secs(1), async {
            loop {
                let status = service.status();
                if status.operation_state == super::BluetoothOperationState::Complete {
                    assert!(status.operation.is_none());
                    assert!(status.operation_id.is_some());
                    assert_eq!(status.error, None);
                    break;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("successful operation should publish its terminal state");

        sleep(super::TERMINAL_OPERATION_TTL + Duration::from_millis(25)).await;
        let refreshed = service.refresh().await;
        assert_eq!(
            refreshed.operation_state,
            super::BluetoothOperationState::Idle
        );
        assert_eq!(refreshed.error, None);

        cancellation.cancel();
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[tokio::test]
    async fn active_operation_is_cancelled_and_released_during_shutdown() {
        let cancellation = CancellationToken::new();
        let (command_sender, mut command_receiver) = mpsc::unbounded_channel();
        let (release_sender, release_receiver) = oneshot::channel();
        let bluez = ControlledBlueZ {
            commands: command_sender,
            release: Mutex::new(Some(release_receiver)),
            failure: None,
        };
        let (service, poller) = BluetoothService::with_limits_and_providers(
            true,
            false,
            Arc::new(bluez),
            Arc::new(FakePipeWire),
            1,
            1,
            cancellation.clone(),
        );

        let started = service.set_power(false).unwrap();
        assert_eq!(
            started.operation_state,
            super::BluetoothOperationState::Running
        );
        assert!(matches!(
            command_receiver.recv().await,
            Some(BlueZCommand::SetPower(false))
        ));

        cancellation.cancel();
        timeout(Duration::from_secs(1), async {
            loop {
                if service.operation_gate.available_permits() == 1 {
                    break;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("shutdown cancellation should release the Bluetooth operation gate");
        let status = service.status();
        assert_eq!(
            status.operation_state,
            super::BluetoothOperationState::Failed
        );
        assert!(status.operation.is_none());
        assert_eq!(
            status.error.as_deref(),
            Some("Bluetooth operation was cancelled during shutdown.")
        );

        drop(release_sender);
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[tokio::test]
    async fn failed_operations_publish_the_provider_error_and_failed_state() {
        let cancellation = CancellationToken::new();
        let (command_sender, _command_receiver) = mpsc::unbounded_channel();
        let bluez = ControlledBlueZ {
            commands: command_sender,
            release: Mutex::new(None),
            failure: Some("pairing was rejected"),
        };
        let (service, poller) = BluetoothService::with_limits_and_providers(
            true,
            false,
            Arc::new(bluez),
            Arc::new(FakePipeWire),
            1,
            1,
            cancellation.clone(),
        );

        service.pair("AA:BB:CC:DD:EE:10".to_owned()).unwrap();
        timeout(Duration::from_secs(1), async {
            loop {
                let status = service.status();
                if status.operation_state == super::BluetoothOperationState::Failed {
                    assert!(status.operation.is_none());
                    assert!(status.operation_id.is_some());
                    assert_eq!(status.error.as_deref(), Some("pairing was rejected"));
                    break;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("failed operation should publish its error and terminal state");

        cancellation.cancel();
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[tokio::test]
    async fn repeated_connect_disconnect_cycles_release_the_operation_gate() {
        let cancellation = CancellationToken::new();
        let (command_sender, mut command_receiver) = mpsc::unbounded_channel();
        let bluez = ControlledBlueZ {
            commands: command_sender,
            release: Mutex::new(None),
            failure: None,
        };
        let (service, poller) = BluetoothService::with_limits_and_providers(
            true,
            false,
            Arc::new(bluez),
            Arc::new(FakePipeWire),
            2,
            1,
            cancellation.clone(),
        );
        let address = "AA:BB:CC:DD:EE:02".to_owned();

        for _ in 0..3 {
            service
                .connect(address.clone(), Some("input".to_owned()))
                .unwrap();
            assert!(matches!(
                timeout(Duration::from_secs(1), command_receiver.recv())
                    .await
                    .unwrap(),
                Some(BlueZCommand::Connect { address: command_address, role })
                    if command_address == address && role == "input"
            ));
            timeout(Duration::from_secs(1), async {
                while service.operation_gate.available_permits() == 0 {
                    sleep(Duration::from_millis(5)).await;
                }
            })
            .await
            .expect("connect operation should release its serialization permit");

            service.disconnect(address.clone()).unwrap();
            assert!(matches!(
                timeout(Duration::from_secs(1), command_receiver.recv())
                    .await
                    .unwrap(),
                Some(BlueZCommand::Disconnect(command_address)) if command_address == address
            ));
            timeout(Duration::from_secs(1), async {
                while service.operation_gate.available_permits() == 0 {
                    sleep(Duration::from_millis(5)).await;
                }
            })
            .await
            .expect("disconnect operation should release its serialization permit");
        }

        cancellation.cancel();
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[cfg(unix)]
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn active_disconnect_does_not_pause_engine_rendering() {
        let directory = tempfile::tempdir().unwrap();
        let socket_path = directory.path().join("engine.sock");
        let engine_binary = std::env::var_os("LUMISTRIPE_ENGINE_TEST_BINARY")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../../target/debug/lumistripe-engine")
            });
        assert!(
            engine_binary.is_file(),
            "build the engine binary before this test: {}",
            engine_binary.display()
        );
        let mut engine = Command::new(engine_binary)
            .arg("--socket")
            .arg(&socket_path)
            .arg("--owner-pid")
            .arg(std::process::id().to_string())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .expect("mock-output engine should start");
        let client = EngineClient::new(&socket_path, Duration::from_secs(2));

        timeout(Duration::from_secs(5), async {
            loop {
                if matches!(client.health().await, Ok(ResponseResult::Health { .. })) {
                    break;
                }
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("engine IPC should become ready");
        assert!(matches!(
            client
                .configure(
                    EngineConfig::new(vec![OutputConfig {
                        id: "mock".to_owned(),
                        pixel_count: 32,
                        reversed: false,
                        backend: OutputBackend::Mock,
                    }])
                    .unwrap(),
                )
                .await
                .unwrap(),
            ResponseResult::Configured { .. }
        ));
        assert!(matches!(
            client
                .update_scene(RenderSceneUpdate {
                    revision: 1,
                    timestamp_ns: 1,
                    brightness: 1.0,
                    power: RenderPowerConfig::default(),
                    outputs: vec![RenderOutput {
                        animation: "rainbow".to_owned(),
                        ..RenderOutput::default()
                    }],
                    scheduler: RenderSchedulerConfig { max_fps: 120 },
                })
                .await
                .unwrap(),
            ResponseResult::SceneUpdated { revision: 1, .. }
        ));

        let rendered_before_disconnect = timeout(Duration::from_secs(3), async {
            loop {
                if let ResponseResult::Health {
                    frames_rendered, ..
                } = client.health().await.unwrap()
                    && frames_rendered >= 2
                {
                    break frames_rendered;
                }
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("engine should render the active scene before disconnect");

        let (command_sender, mut command_receiver) = mpsc::unbounded_channel();
        let (release_sender, release_receiver) = oneshot::channel();
        let bluez = ControlledBlueZ {
            commands: command_sender,
            release: Mutex::new(Some(release_receiver)),
            failure: None,
        };
        let cancellation = CancellationToken::new();
        let (service, poller) = BluetoothService::with_providers(
            true,
            false,
            Arc::new(bluez),
            Arc::new(FakePipeWire),
            cancellation.clone(),
        );
        let address = "AA:BB:CC:DD:EE:02".to_owned();
        service.disconnect(address.clone()).unwrap();
        assert!(matches!(
            timeout(Duration::from_secs(1), command_receiver.recv())
                .await
                .unwrap(),
            Some(BlueZCommand::Disconnect(command_address)) if command_address == address
        ));
        assert_eq!(
            service.status().operation_state,
            super::BluetoothOperationState::Running
        );

        let rendered_while_disconnect_pending = timeout(Duration::from_secs(3), async {
            loop {
                if let ResponseResult::Health {
                    frames_rendered, ..
                } = client.health().await.unwrap()
                    && frames_rendered >= rendered_before_disconnect + 2
                {
                    break frames_rendered;
                }
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("engine should keep rendering while BlueZ disconnect is in flight");
        assert!(rendered_while_disconnect_pending >= rendered_before_disconnect + 2);
        release_sender.send(()).unwrap();
        timeout(Duration::from_secs(1), async {
            while service.status().operation_state != super::BluetoothOperationState::Complete {
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("disconnect should finish after the provider releases it");

        cancellation.cancel();
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
        assert!(matches!(
            client.shutdown().await.unwrap(),
            ResponseResult::Shutdown
        ));
        let exit = timeout(Duration::from_secs(2), engine.wait())
            .await
            .expect("engine should exit after shutdown")
            .unwrap();
        assert!(exit.success(), "engine exited unsuccessfully: {exit}");
    }

    #[tokio::test]
    async fn spotify_loopback_is_not_duplicated_and_moves_after_the_default_sink_changes() {
        let cancellation = CancellationToken::new();
        let state = Arc::new(Mutex::new(RoutingState {
            default_sink: "speaker.one".to_owned(),
            ..RoutingState::default()
        }));
        let (service, poller) = BluetoothService::with_providers(
            false,
            true,
            Arc::new(FakeBlueZ),
            Arc::new(RecordingPipeWire(state.clone())),
            cancellation.clone(),
        );

        service.ensure_spotify_route().await.unwrap();
        service.ensure_spotify_route().await.unwrap();
        {
            let state = state.lock().unwrap();
            assert_eq!(state.load_count, 1);
            assert!(state.modules.contains("source=lumistripe_spotify.monitor"));
            assert!(state.modules.contains("sink=speaker.one"));
            assert!(
                state
                    .modules
                    .contains("application.name=LumiStripe-Spotify")
            );
        }

        state.lock().unwrap().default_sink = "speaker.two".to_owned();
        service.refresh_spotify_route().await.unwrap();
        let state = state.lock().unwrap();
        assert_eq!(state.unload_count, 1);
        assert_eq!(state.load_count, 2);
        assert!(state.modules.contains("sink=speaker.two"));

        cancellation.cancel();
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("disabled Bluetooth poller should already be stopped")
            .unwrap();
    }

    #[tokio::test]
    async fn default_sink_change_rolls_back_when_spotify_loopback_creation_fails() {
        let cancellation = CancellationToken::new();
        let state = Arc::new(Mutex::new(RoutingState {
            default_sink: "speaker.one".to_owned(),
            modules: "1 module-loopback source=lumistripe_spotify.monitor sink=speaker.one"
                .to_owned(),
            next_module_id: 2,
            failed_load_sink: Some("speaker.two".to_owned()),
            ..RoutingState::default()
        }));
        let (service, poller) = BluetoothService::with_providers(
            true,
            true,
            Arc::new(FakeBlueZ),
            Arc::new(RecordingPipeWire(state.clone())),
            cancellation.clone(),
        );

        let initial = service.refresh().await;
        assert_eq!(initial.default_sink.as_deref(), Some("speaker.one"));
        let error = service
            .set_default_sink("speaker.two".to_owned())
            .await
            .expect_err("loopback failure should reject the output switch");
        assert_eq!(error.code, "test_route_failure");

        let state = state.lock().unwrap();
        assert_eq!(state.default_sink_changes, ["speaker.two", "speaker.one"]);
        assert_eq!(state.default_sink, "speaker.one");
        assert_eq!(state.unload_count, 1);
        assert_eq!(state.load_count, 1);
        assert!(state.modules.contains("source=lumistripe_spotify.monitor"));
        assert!(state.modules.contains("sink=speaker.one"));
        drop(state);
        assert_eq!(
            service.status().default_sink.as_deref(),
            Some("speaker.one")
        );

        cancellation.cancel();
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[test]
    fn validates_bluetooth_addresses_and_aliases_before_commands_are_queued() {
        assert_eq!(
            validate_address("aa:bb:cc:dd:ee:ff").unwrap(),
            "AA:BB:CC:DD:EE:FF"
        );
        assert!(validate_address("AA:BB:CC:DD:EE:FG").is_err());
        assert_eq!(validate_alias("  LumiStripe  ").unwrap(), "LumiStripe");
        assert!(validate_alias("\n").is_err());
        assert!(validate_alias(&"x".repeat(65)).is_err());
    }
}
