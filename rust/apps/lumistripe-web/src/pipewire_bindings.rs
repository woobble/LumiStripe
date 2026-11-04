//! PipeWire graph discovery and default-device routing through pipewire-rs.

use crate::bluetooth_process::{BluetoothError, ProcessRunner};
use crate::bluetooth_providers::{CommandPipeWireProvider, PipeWireCommand, PipeWireProvider};
use futures_util::future::BoxFuture;
use pipewire as pw;
use pw::types::ObjectType;
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::sync::{Arc, Mutex, Once};
use std::time::{Duration, Instant};
use tokio::sync::Mutex as AsyncMutex;

const GRAPH_TIMEOUT: Duration = Duration::from_secs(3);
const GRAPH_CACHE_AGE: Duration = Duration::from_millis(750);
const MAX_GRAPH_NODES: usize = 4096;
static PIPEWIRE_INIT: Once = Once::new();

#[derive(Clone, Debug)]
struct PipeWireNode {
    id: u32,
    class: String,
    name: String,
    description: String,
}

#[derive(Clone, Debug)]
enum PipeWireOperation {
    Snapshot,
    SetDefault {
        kind: &'static str,
        selector: String,
    },
}

#[derive(Clone, Debug)]
enum PipeWireResponse {
    Snapshot(Vec<PipeWireNode>),
    SetDefault,
}

#[derive(Clone, Debug)]
struct CachedGraph {
    observed_at: Instant,
    nodes: Vec<PipeWireNode>,
}

#[derive(Clone)]
pub(crate) struct PipeWireBindingsProvider {
    runner: Arc<dyn ProcessRunner>,
    graph: Arc<Mutex<Option<CachedGraph>>>,
    refresh_gate: Arc<AsyncMutex<()>>,
}

impl PipeWireBindingsProvider {
    pub(crate) fn new(runner: Arc<dyn ProcessRunner>) -> Self {
        Self {
            runner,
            graph: Arc::new(Mutex::new(None)),
            refresh_gate: Arc::new(AsyncMutex::new(())),
        }
    }

    async fn snapshot(&self) -> Result<Vec<PipeWireNode>, BluetoothError> {
        if let Some(graph) = self.cached_graph() {
            return Ok(graph.nodes);
        }
        let _permit = self.refresh_gate.lock().await;
        if let Some(graph) = self.cached_graph() {
            return Ok(graph.nodes);
        }
        let nodes = tokio::task::spawn_blocking(|| request_pipewire(PipeWireOperation::Snapshot))
            .await
            .map_err(|error| {
                BluetoothError::new("pipewire", format!("PipeWire worker failed: {error}"))
            })??;
        let PipeWireResponse::Snapshot(nodes) = nodes else {
            return Err(BluetoothError::new(
                "pipewire",
                "PipeWire returned an unexpected graph response.",
            ));
        };
        *self.graph.lock().map_err(|_| {
            BluetoothError::new("pipewire", "PipeWire graph cache is unavailable.")
        })? = Some(CachedGraph {
            observed_at: Instant::now(),
            nodes: nodes.clone(),
        });
        Ok(nodes)
    }

    fn cached_graph(&self) -> Option<CachedGraph> {
        self.graph.lock().ok().and_then(|graph| {
            graph
                .as_ref()
                .filter(|graph| graph.observed_at.elapsed() <= GRAPH_CACHE_AGE)
                .cloned()
        })
    }

    async fn set_default(
        &self,
        kind: &'static str,
        selector: String,
    ) -> Result<(), BluetoothError> {
        tokio::task::spawn_blocking(move || {
            request_pipewire(PipeWireOperation::SetDefault { kind, selector })
        })
        .await
        .map_err(|error| {
            BluetoothError::new("pipewire", format!("PipeWire worker failed: {error}"))
        })??;
        if let Ok(mut graph) = self.graph.lock() {
            *graph = None;
        }
        Ok(())
    }
}

impl PipeWireProvider for PipeWireBindingsProvider {
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
        let provider = self.clone();
        Box::pin(async move {
            match command {
                PipeWireCommand::Sources => {
                    let nodes = provider.snapshot().await?;
                    Ok(format_nodes(&nodes, "Audio/Source"))
                }
                PipeWireCommand::Sinks => {
                    let nodes = provider.snapshot().await?;
                    Ok(format_nodes(&nodes, "Audio/Sink"))
                }
                PipeWireCommand::SetDefaultSource(selector) => {
                    provider.set_default("source", selector).await?;
                    Ok(String::new())
                }
                PipeWireCommand::SetDefaultSink(selector) => {
                    provider.set_default("sink", selector).await?;
                    Ok(String::new())
                }
                command => {
                    CommandPipeWireProvider::new(provider.runner.clone())
                        .execute(command)
                        .await
                }
            }
        })
    }
}

fn format_nodes(nodes: &[PipeWireNode], media_class: &str) -> String {
    let mut nodes = nodes
        .iter()
        .filter(|node| node.class == media_class)
        .collect::<Vec<_>>();
    nodes.sort_by_key(|node| node.id);
    nodes
        .into_iter()
        .map(|node| {
            format!(
                "{}\t{}\t{}",
                node.id,
                node.name,
                node.description.replace(['\n', '\r'], " ")
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn request_pipewire(operation: PipeWireOperation) -> Result<PipeWireResponse, BluetoothError> {
    let (reply, response) = std::sync::mpsc::sync_channel(1);
    std::thread::Builder::new()
        .name("lumistripe-pipewire-query".to_owned())
        .spawn(move || run_pipewire_loop(operation, reply))
        .map_err(|error| {
            BluetoothError::new(
                "pipewire",
                format!("Could not start PipeWire query: {error}"),
            )
        })?;
    response
        .recv_timeout(GRAPH_TIMEOUT + Duration::from_millis(250))
        .map_err(|_| BluetoothError::new("timeout", "PipeWire request timed out."))?
}

fn run_pipewire_loop(
    operation: PipeWireOperation,
    reply: std::sync::mpsc::SyncSender<Result<PipeWireResponse, BluetoothError>>,
) {
    PIPEWIRE_INIT.call_once(pw::init);
    let result: Result<PipeWireResponse, BluetoothError> = (|| {
        let mainloop = pw::main_loop::MainLoopRc::new(None).map_err(|error| {
            BluetoothError::new(
                "pipewire",
                format!("Could not create PipeWire loop: {error}"),
            )
        })?;
        let context = pw::context::ContextRc::new(&mainloop, None).map_err(|error| {
            BluetoothError::new(
                "pipewire",
                format!("Could not create PipeWire context: {error}"),
            )
        })?;
        let core = context.connect_rc(None).map_err(|error| {
            BluetoothError::new(
                "pipewire",
                format!("Could not connect to PipeWire: {error}"),
            )
        })?;
        let registry = core.get_registry_rc().map_err(|error| {
            BluetoothError::new(
                "pipewire",
                format!("Could not read PipeWire registry: {error}"),
            )
        })?;
        let nodes = Rc::new(RefCell::new(Vec::<PipeWireNode>::new()));
        let default_metadata = Rc::new(RefCell::new(None::<pw::metadata::Metadata>));
        let metadata = default_metadata.clone();
        let nodes_for_registry = nodes.clone();
        let registry_for_bind = registry.clone();
        let _registry_listener = registry
            .add_listener_local()
            .global(move |global| match global.type_ {
                ObjectType::Node => {
                    let Some(props) = global.props.as_ref() else {
                        return;
                    };
                    let Some(class) = props.get("media.class") else {
                        return;
                    };
                    if class != "Audio/Source" && class != "Audio/Sink" {
                        return;
                    }
                    let Some(name) = props.get("node.name") else {
                        return;
                    };
                    let description = props.get("node.description").unwrap_or(name);
                    let mut nodes = nodes_for_registry.borrow_mut();
                    if nodes.len() < MAX_GRAPH_NODES {
                        nodes.push(PipeWireNode {
                            id: global.id,
                            class: class.to_owned(),
                            name: name.to_owned(),
                            description: description.to_owned(),
                        });
                    }
                }
                ObjectType::Metadata => {
                    let is_default_metadata = global
                        .props
                        .as_ref()
                        .and_then(|props| props.get("metadata.name"))
                        == Some("default");
                    if is_default_metadata && metadata.borrow().is_none() {
                        if let Ok(proxy) =
                            registry_for_bind.bind::<pw::metadata::Metadata, _>(global)
                        {
                            *metadata.borrow_mut() = Some(proxy);
                        }
                    }
                }
                _ => {}
            })
            .register();

        let completion = Rc::new(Cell::new(false));
        let completion_for_done = completion.clone();
        let nodes_for_done = nodes.clone();
        let metadata_for_done = default_metadata.clone();
        let core_for_done = core.clone();
        let mainloop_for_done = mainloop.clone();
        let result_for_done = Rc::new(RefCell::new(
            None::<Result<PipeWireResponse, BluetoothError>>,
        ));
        let result_from_done = result_for_done.clone();
        let _core_listener = core
            .add_listener_local()
            .done(move |_, _| {
                if completion_for_done.get() {
                    *result_from_done.borrow_mut() = Some(Ok(PipeWireResponse::SetDefault));
                    mainloop_for_done.quit();
                    return;
                }
                completion_for_done.set(true);
                match &operation {
                    PipeWireOperation::Snapshot => {
                        *result_from_done.borrow_mut() = Some(Ok(PipeWireResponse::Snapshot(
                            nodes_for_done.borrow().clone(),
                        )));
                        mainloop_for_done.quit();
                    }
                    PipeWireOperation::SetDefault { kind, selector } => {
                        let metadata_available = {
                            let metadata_guard = metadata_for_done.borrow();
                            if let Some(metadata) = metadata_guard.as_ref() {
                                let runtime_key = format!("default.audio.{kind}");
                                let configured_key = format!("default.configured.audio.{kind}");
                                let value = serde_json::json!({ "name": selector }).to_string();
                                metadata.set_property(
                                    0,
                                    &runtime_key,
                                    Some("Spa:String:JSON"),
                                    Some(&value),
                                );
                                metadata.set_property(
                                    0,
                                    &configured_key,
                                    Some("Spa:String:JSON"),
                                    Some(&value),
                                );
                                true
                            } else {
                                false
                            }
                        };
                        if !metadata_available {
                            *result_from_done.borrow_mut() = Some(Err(BluetoothError::new(
                                "unavailable",
                                "PipeWire default metadata is unavailable.",
                            )));
                            mainloop_for_done.quit();
                            return;
                        }
                        if let Err(error) = core_for_done.sync(0) {
                            *result_from_done.borrow_mut() = Some(Err(BluetoothError::new(
                                "pipewire",
                                format!("PipeWire could not apply the default route: {error}"),
                            )));
                            mainloop_for_done.quit();
                        }
                    }
                }
            })
            .register();

        core.sync(0).map_err(|error| {
            BluetoothError::new(
                "pipewire",
                format!("Could not enumerate PipeWire globals: {error}"),
            )
        })?;
        let timeout_loop = mainloop.clone();
        let timer = mainloop.loop_().add_timer(move |_| timeout_loop.quit());
        let _ = timer.update_timer(Some(GRAPH_TIMEOUT), None);
        mainloop.run();
        result_for_done.borrow_mut().take().unwrap_or_else(|| {
            Err(BluetoothError::new(
                "timeout",
                "PipeWire request timed out.",
            ))
        })
    })();
    let _ = reply.send(result);
}

#[cfg(test)]
mod tests {
    use super::{PipeWireOperation, PipeWireResponse, request_pipewire};

    #[test]
    #[ignore = "requires a running user PipeWire daemon"]
    fn live_pipewire_registry_snapshot() {
        let PipeWireResponse::Snapshot(nodes) =
            request_pipewire(PipeWireOperation::Snapshot).expect("PipeWire query should succeed")
        else {
            panic!("PipeWire returned an unexpected response");
        };
        assert!(
            nodes.len() <= 4096,
            "PipeWire audio graph should be bounded"
        );
        assert!(
            nodes
                .iter()
                .any(|node| matches!(node.class.as_str(), "Audio/Source" | "Audio/Sink")),
            "PipeWire registry should expose at least one audio source or sink"
        );
    }
}
