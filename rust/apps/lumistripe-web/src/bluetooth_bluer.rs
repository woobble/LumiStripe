//! BlueZ provider backed by the official `bluer` D-Bus bindings.

use crate::bluetooth_models::BluetoothDevice;
use crate::bluetooth_process::BluetoothError;
use crate::bluetooth_providers::{BlueZCommand, BlueZProvider, BlueZSnapshot};
use bluer::agent::{Agent, ReqError, RequestAuthorization, RequestConfirmation};
use bluer::{Adapter, Address, Session};
use futures_util::StreamExt;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex;
use tokio::time::timeout;
use uuid::Uuid;

const MAX_DEVICE_COUNT: usize = 256;
const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(5);
const A2DP_SOURCE_UUID: u128 = 0x0000_110a_0000_1000_8000_0080_5f9b_34fb;
const A2DP_SINK_UUID: u128 = 0x0000_110b_0000_1000_8000_0080_5f9b_34fb;

struct BlueZContext {
    session: Session,
    adapter: Adapter,
}

#[derive(Clone, Default)]
pub(crate) struct BluerBlueZProvider {
    context: Arc<Mutex<Option<BlueZContext>>>,
}

impl BluerBlueZProvider {
    async fn session_adapter(&self) -> Result<(Session, Adapter), BluetoothError> {
        let mut context = self.context.lock().await;
        if context.is_none() {
            let session = Session::new().await.map_err(bluez_error)?;
            let adapter = session.default_adapter().await.map_err(bluez_error)?;
            *context = Some(BlueZContext { session, adapter });
        }
        let context = context.as_ref().expect("BlueZ context was initialized");
        Ok((context.session.clone(), context.adapter.clone()))
    }

    async fn snapshot_now(&self) -> Result<BlueZSnapshot, BluetoothError> {
        let (_, adapter) = self.session_adapter().await?;
        let powered = adapter.is_powered().await.map_err(bluez_error)?;
        let alias = adapter
            .alias()
            .await
            .ok()
            .filter(|name| !name.trim().is_empty());
        let scanning = adapter.is_discovering().await.unwrap_or(false);
        let addresses = adapter.device_addresses().await.map_err(bluez_error)?;
        let mut devices = Vec::new();
        for address in addresses.into_iter().take(MAX_DEVICE_COUNT) {
            let Ok(device) = adapter.device(address) else {
                continue;
            };
            let address = device.address().to_string().to_ascii_uppercase();
            let alias = device.alias().await.unwrap_or_default();
            let name = if alias.trim().is_empty() {
                device
                    .name()
                    .await
                    .ok()
                    .flatten()
                    .filter(|name| !name.trim().is_empty())
                    .unwrap_or_else(|| address.clone())
            } else {
                alias
            };
            let uuids = device.uuids().await.ok().flatten().unwrap_or_default();
            let mut roles = Vec::new();
            if uuids.contains(&Uuid::from_u128(A2DP_SOURCE_UUID)) {
                roles.push("input".to_owned());
            }
            if uuids.contains(&Uuid::from_u128(A2DP_SINK_UUID)) {
                roles.push("output".to_owned());
            }
            devices.push(BluetoothDevice {
                address,
                name,
                paired: device.is_paired().await.unwrap_or(false),
                connected: device.is_connected().await.unwrap_or(false),
                roles,
            });
        }
        Ok(BlueZSnapshot {
            powered,
            alias,
            scanning,
            devices,
        })
    }

    async fn execute_now(&self, command: BlueZCommand) -> Result<(), BluetoothError> {
        let deadline = match &command {
            BlueZCommand::Scan => Duration::from_secs(64),
            BlueZCommand::Pair(_) => Duration::from_secs(40),
            BlueZCommand::Connect { .. } => Duration::from_secs(18),
            BlueZCommand::SetPower(_) | BlueZCommand::SetAlias(_) => Duration::from_secs(8),
            BlueZCommand::Forget(_) | BlueZCommand::Disconnect(_) => Duration::from_secs(8),
        };
        let operation = async {
            let (session, adapter) = self.session_adapter().await?;
            match command {
                BlueZCommand::Scan => {
                    adapter.set_powered(true).await.map_err(bluez_error)?;
                    let mut events = adapter.discover_devices().await.map_err(bluez_error)?;
                    let _ = timeout(Duration::from_secs(60), async {
                        while events.next().await.is_some() {}
                    })
                    .await;
                }
                BlueZCommand::SetPower(powered) => {
                    adapter.set_powered(powered).await.map_err(bluez_error)?;
                }
                BlueZCommand::SetAlias(alias) => {
                    adapter.set_alias(alias).await.map_err(bluez_error)?;
                }
                BlueZCommand::Pair(address) => {
                    let address = parse_address(&address)?;
                    adapter.set_powered(true).await.map_err(bluez_error)?;
                    let was_pairable = adapter.is_pairable().await.map_err(bluez_error)?;
                    let mut pairability = PairabilityRestore::new(adapter.clone(), was_pairable);
                    let pair_result = async {
                        adapter.set_pairable(true).await.map_err(bluez_error)?;
                        let agent = pairing_agent(&address);
                        let _agent = session.register_agent(agent).await.map_err(bluez_error)?;
                        let device = adapter.device(address).map_err(bluez_error)?;
                        device.pair().await.map_err(bluez_error)?;
                        device.set_trusted(true).await.map_err(bluez_error)
                    }
                    .await;
                    let restore_result = pairability.restore().await;
                    match (pair_result, restore_result) {
                        (Ok(()), Ok(())) => {}
                        (Err(pair_error), Ok(())) => return Err(pair_error),
                        (Ok(()), Err(restore_error)) => return Err(restore_error),
                        (Err(pair_error), Err(restore_error)) => {
                            tracing::warn!(
                                error = %restore_error,
                                "could not restore Bluetooth pairable state after pairing failed"
                            );
                            return Err(pair_error);
                        }
                    }
                }
                BlueZCommand::Connect { address, role } => {
                    let address = parse_address(&address)?;
                    adapter.set_powered(true).await.map_err(bluez_error)?;
                    let device = adapter.device(address).map_err(bluez_error)?;
                    let profile = if role == "output" {
                        A2DP_SINK_UUID
                    } else {
                        A2DP_SOURCE_UUID
                    };
                    device
                        .connect_profile(&Uuid::from_u128(profile))
                        .await
                        .map_err(bluez_error)?;
                }
                BlueZCommand::Forget(address) => {
                    adapter
                        .remove_device(parse_address(&address)?)
                        .await
                        .map_err(bluez_error)?;
                }
                BlueZCommand::Disconnect(address) => {
                    adapter
                        .device(parse_address(&address)?)
                        .map_err(bluez_error)?
                        .disconnect()
                        .await
                        .map_err(bluez_error)?;
                }
            }
            Ok(())
        };
        timeout(deadline, operation)
            .await
            .map_err(|_| BluetoothError::new("timeout", "BlueZ operation timed out."))?
    }
}

impl BlueZProvider for BluerBlueZProvider {
    fn snapshot(
        &self,
    ) -> futures_util::future::BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
        let provider = self.clone();
        Box::pin(async move {
            timeout(SNAPSHOT_TIMEOUT, provider.snapshot_now())
                .await
                .map_err(|_| BluetoothError::new("timeout", "BlueZ status request timed out."))?
        })
    }

    fn execute(
        &self,
        command: BlueZCommand,
    ) -> futures_util::future::BoxFuture<'static, Result<(), BluetoothError>> {
        let provider = self.clone();
        Box::pin(async move { provider.execute_now(command).await })
    }
}

struct PairabilityRestore {
    adapter: Adapter,
    previous: bool,
    armed: bool,
}

impl PairabilityRestore {
    fn new(adapter: Adapter, previous: bool) -> Self {
        Self {
            adapter,
            previous,
            armed: true,
        }
    }

    async fn restore(&mut self) -> Result<(), BluetoothError> {
        timeout(
            Duration::from_secs(5),
            self.adapter.set_pairable(self.previous),
        )
        .await
        .map_err(|_| {
            BluetoothError::new("timeout", "Restoring Bluetooth pairable state timed out.")
        })?
        .map_err(bluez_error)?;
        self.armed = false;
        Ok(())
    }
}

impl Drop for PairabilityRestore {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        let adapter = self.adapter.clone();
        let previous = self.previous;
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            tracing::error!("cannot restore Bluetooth pairable state outside a Tokio runtime");
            return;
        };
        runtime.spawn(async move {
            match timeout(Duration::from_secs(5), adapter.set_pairable(previous)).await {
                Ok(Ok(())) => {}
                Ok(Err(error)) => {
                    tracing::warn!(%error, "could not restore Bluetooth pairable state")
                }
                Err(_) => tracing::warn!("restoring Bluetooth pairable state timed out"),
            }
        });
    }
}

fn pairing_agent(address: &Address) -> Agent {
    let address = address.to_string();
    let confirmation_address = address.clone();
    let authorization_address = address.clone();
    Agent {
        request_default: true,
        request_confirmation: Some(Box::new(move |request: RequestConfirmation| {
            let approved = request
                .device
                .to_string()
                .eq_ignore_ascii_case(&confirmation_address);
            Box::pin(async move {
                if approved {
                    Ok(())
                } else {
                    Err(ReqError::Rejected)
                }
            }) as Pin<Box<dyn Future<Output = Result<(), ReqError>> + Send>>
        })),
        request_authorization: Some(Box::new(move |request: RequestAuthorization| {
            let approved = request
                .device
                .to_string()
                .eq_ignore_ascii_case(&authorization_address);
            Box::pin(async move {
                if approved {
                    Ok(())
                } else {
                    Err(ReqError::Rejected)
                }
            }) as Pin<Box<dyn Future<Output = Result<(), ReqError>> + Send>>
        })),
        ..Agent::default()
    }
}

fn parse_address(address: &str) -> Result<Address, BluetoothError> {
    address
        .parse::<Address>()
        .map_err(|_| BluetoothError::new("invalid", "Bluetooth address is invalid."))
}

fn bluez_error(error: impl std::fmt::Display) -> BluetoothError {
    BluetoothError::new("bluez", format!("BlueZ request failed: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{BluerBlueZProvider, MAX_DEVICE_COUNT, pairing_agent, parse_address};
    use crate::bluetooth_providers::{BlueZCommand, BlueZProvider};
    use std::time::Duration;
    use tokio::time::sleep;

    #[test]
    fn pairing_agent_is_default_and_handles_numeric_confirmation() {
        let address = parse_address("F4:39:A6:8F:81:3D").expect("test address should parse");
        let agent = pairing_agent(&address);

        assert!(agent.request_default);
        assert!(agent.request_confirmation.is_some());
        assert!(agent.request_authorization.is_some());
    }

    #[tokio::test]
    #[ignore = "requires the BlueZ system D-Bus service and an adapter"]
    async fn live_bluez_snapshot() {
        let provider = BluerBlueZProvider::default();
        let snapshot = provider
            .snapshot()
            .await
            .expect("BlueZ should provide an adapter snapshot");
        assert!(snapshot.devices.len() <= MAX_DEVICE_COUNT);
    }

    #[tokio::test]
    #[ignore = "performs a 60-second discovery on the live Bluetooth adapter"]
    async fn live_bluez_scan_completes_and_releases_discovery() {
        let provider = BluerBlueZProvider::default();
        let before = provider
            .snapshot()
            .await
            .expect("BlueZ should provide an adapter snapshot");
        assert!(before.powered, "the live adapter must already be powered");
        assert!(
            !before.scanning,
            "the live adapter must not already be scanning"
        );

        provider
            .execute(BlueZCommand::Scan)
            .await
            .expect("BlueZ discovery should complete without a provider error");

        let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
        loop {
            let after = provider
                .snapshot()
                .await
                .expect("BlueZ should still provide an adapter snapshot");
            assert!(after.powered, "scanning must not power off the adapter");
            assert!(after.devices.len() <= MAX_DEVICE_COUNT);
            if !after.scanning {
                break;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "the completed scan should release its discovery session"
            );
            sleep(Duration::from_millis(100)).await;
        }
    }
}
