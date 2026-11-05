//! BlueZ and PipeWire command boundaries.

use crate::bluetooth_models::BluetoothDevice;
use crate::bluetooth_process::{BluetoothError, ProcessRunner};
use futures_util::future::BoxFuture;
use std::sync::Arc;
use std::time::Duration;

#[derive(Clone, Debug)]
#[allow(dead_code)]
pub(crate) enum BlueZCommand {
    Scan,
    SetPower(bool),
    SetAlias(String),
    Pair(String),
    Connect { address: String, role: String },
    Forget(String),
    Disconnect(String),
}

#[derive(Clone, Debug, Default)]
pub(crate) struct BlueZSnapshot {
    pub powered: bool,
    pub alias: Option<String>,
    pub scanning: bool,
    pub devices: Vec<BluetoothDevice>,
}

pub(crate) trait BlueZProvider: Send + Sync {
    fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>>;
    fn execute(&self, command: BlueZCommand) -> BoxFuture<'static, Result<(), BluetoothError>>;
}

pub(crate) fn system_bluez_provider() -> Arc<dyn BlueZProvider> {
    #[cfg(all(feature = "bluez", target_os = "linux"))]
    {
        Arc::new(crate::bluetooth_bluer::BluerBlueZProvider::default())
    }
    #[cfg(not(all(feature = "bluez", target_os = "linux")))]
    {
        Arc::new(UnavailableBlueZProvider)
    }
}

pub(crate) fn system_pipewire_provider(
    runner: Arc<dyn ProcessRunner>,
) -> Arc<dyn PipeWireProvider> {
    #[cfg(all(feature = "pipewire", target_os = "linux"))]
    {
        Arc::new(crate::pipewire_bindings::PipeWireBindingsProvider::new(
            runner,
        ))
    }
    #[cfg(not(all(feature = "pipewire", target_os = "linux")))]
    {
        Arc::new(CommandPipeWireProvider::new(runner))
    }
}

#[cfg(not(all(feature = "bluez", target_os = "linux")))]
struct UnavailableBlueZProvider;

#[cfg(not(all(feature = "bluez", target_os = "linux")))]
impl BlueZProvider for UnavailableBlueZProvider {
    fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
        Box::pin(async {
            Err(BluetoothError::new(
                "unavailable",
                "BlueZ support was not enabled in this build.",
            ))
        })
    }

    fn execute(&self, _command: BlueZCommand) -> BoxFuture<'static, Result<(), BluetoothError>> {
        Box::pin(async {
            Err(BluetoothError::new(
                "unavailable",
                "BlueZ support was not enabled in this build.",
            ))
        })
    }
}

#[derive(Clone, Debug)]
pub(crate) enum PipeWireCommand {
    Sources,
    Sinks,
    SinkDetails,
    SinkInputs,
    Modules,
    Info,
    StreamStatus,
    SetDefaultSink(String),
    SetDefaultSource(String),
    MoveSinkInput { input_id: String, sink: String },
    LoadModule { name: String, arguments: String },
    UnloadModule(String),
    SetVolume { sink: String, volume: f64 },
    SetMute { sink: String, muted: bool },
}

pub(crate) trait PipeWireProvider: Send + Sync {
    fn available(&self) -> bool;
    fn has_wpctl(&self) -> bool;
    fn execute(
        &self,
        command: PipeWireCommand,
    ) -> BoxFuture<'static, Result<String, BluetoothError>>;
}

#[derive(Clone)]
pub(crate) struct CommandPipeWireProvider {
    runner: Arc<dyn ProcessRunner>,
}

impl CommandPipeWireProvider {
    pub(crate) fn new(runner: Arc<dyn ProcessRunner>) -> Self {
        Self { runner }
    }
}

impl PipeWireProvider for CommandPipeWireProvider {
    fn available(&self) -> bool {
        self.runner.exists("pactl")
    }

    fn has_wpctl(&self) -> bool {
        self.runner.exists("wpctl")
    }

    fn execute(
        &self,
        command: PipeWireCommand,
    ) -> BoxFuture<'static, Result<String, BluetoothError>> {
        let runner = self.runner.clone();
        Box::pin(async move {
            match command {
                PipeWireCommand::Sources => {
                    run_pipewire(
                        &runner,
                        vec!["list", "short", "sources"],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::Sinks => {
                    run_pipewire(
                        &runner,
                        vec!["list", "short", "sinks"],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::SinkDetails => {
                    run_pipewire(&runner, vec!["list", "sinks"], Duration::from_secs(2)).await
                }
                PipeWireCommand::SinkInputs => {
                    run_pipewire(
                        &runner,
                        vec!["list", "short", "sink-inputs"],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::Modules => {
                    run_pipewire(
                        &runner,
                        vec!["list", "short", "modules"],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::Info => {
                    run_pipewire(&runner, vec!["info"], Duration::from_secs(2)).await
                }
                PipeWireCommand::StreamStatus => {
                    runner
                        .run(
                            "wpctl",
                            vec!["status".to_owned()],
                            None,
                            Duration::from_secs(2),
                        )
                        .await
                }
                PipeWireCommand::SetDefaultSink(sink) => {
                    run_pipewire(
                        &runner,
                        vec!["set-default-sink", &sink],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::SetDefaultSource(source) => {
                    run_pipewire(
                        &runner,
                        vec!["set-default-source", &source],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::MoveSinkInput { input_id, sink } => {
                    run_pipewire(
                        &runner,
                        vec!["move-sink-input", &input_id, &sink],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::LoadModule { name, arguments } => {
                    run_pipewire(
                        &runner,
                        vec!["load-module", &name, &arguments],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::UnloadModule(module_id) => {
                    run_pipewire(
                        &runner,
                        vec!["unload-module", &module_id],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::SetVolume { sink, volume } => {
                    let value = format!("{}%", (volume * 100.0).round() as u8);
                    run_pipewire(
                        &runner,
                        vec!["set-sink-volume", &sink, &value],
                        Duration::from_secs(2),
                    )
                    .await
                }
                PipeWireCommand::SetMute { sink, muted } => {
                    let value = if muted { "1" } else { "0" };
                    run_pipewire(
                        &runner,
                        vec!["set-sink-mute", &sink, value],
                        Duration::from_secs(2),
                    )
                    .await
                }
            }
        })
    }
}

fn run_pipewire<'a>(
    runner: &Arc<dyn ProcessRunner>,
    args: Vec<&'a str>,
    timeout: Duration,
) -> BoxFuture<'static, Result<String, BluetoothError>> {
    let runner = runner.clone();
    let args = args.into_iter().map(str::to_owned).collect();
    Box::pin(async move { runner.run("pactl", args, None, timeout).await })
}
