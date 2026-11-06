use crate::app::{AppState, websocket_origin_allowed};
use crate::audio::AudioStreamContext;
use crate::auth::SESSION_COOKIE;
use crate::control::ControlPlane;
use crate::preview::encode_preview_frame;
use axum::extract::State;
use axum::extract::ws::{CloseCode, CloseFrame, Message, WebSocket, WebSocketUpgrade};
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use futures_util::stream::{SplitSink, SplitStream};
use futures_util::{SinkExt, StreamExt};
use lumistripe_engine_audio::MusicStructurePhase;
use lumistripe_engine_protocol::{EngineAudioStatus, ResponseResult};
use lumistripe_engine_runtime::EngineHandle;
use serde_json::{Value, json};
use std::collections::VecDeque;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tokio::time::{MissedTickBehavior, interval};
use tokio_util::sync::CancellationToken;

pub type EngineHealthReceiver = watch::Receiver<Option<Result<Arc<ResponseResult>, String>>>;
pub type AudioTelemetryReceiver = watch::Receiver<Option<Result<Arc<Value>, String>>>;
pub type PreviewReceiver = watch::Receiver<Option<Result<Arc<Vec<u8>>, String>>>;

pub fn spawn_health_publisher(
    client: EngineHandle,
    cancellation: CancellationToken,
    publisher: watch::Sender<Option<Result<Arc<ResponseResult>, String>>>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let mut poll = interval(Duration::from_secs_f64(1.0 / 30.0));
        poll.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let mut last_error: Option<String> = None;
        loop {
            tokio::select! {
                _ = cancellation.cancelled() => break,
                _ = poll.tick() => {
                    let result = match client.health().await {
                        Ok(health @ ResponseResult::Health { .. }) => Ok(Arc::new(health)),
                        Ok(_) => Err("Rust engine returned an unexpected health response".to_owned()),
                        Err(error) => Err(error.to_string()),
                    };
                    match &result {
                        Ok(_) if last_error.is_some() => {
                            tracing::info!("Rust engine health checks recovered");
                            last_error = None;
                        }
                        Err(error) if last_error.as_ref() != Some(error) => {
                            tracing::warn!(error = %error, "Rust engine health check failed");
                            last_error = Some(error.clone());
                        }
                        _ => {}
                    }
                    publisher.send_replace(Some(result));
                }
            }
        }
    })
}

pub fn spawn_engine_failure_monitor(
    mut health: EngineHealthReceiver,
    cancellation: CancellationToken,
    failure: watch::Sender<Option<String>>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            let snapshot = { health.borrow_and_update().clone() };
            if let Some(Err(error)) = snapshot {
                tracing::error!(error = %error, "in-process Rust engine failed; stopping web process");
                failure.send_replace(Some(error));
                break;
            }
            tokio::select! {
                _ = cancellation.cancelled() => break,
                changed = health.changed() => if changed.is_err() { break; },
            }
        }
    })
}

pub fn spawn_audio_telemetry_publisher(
    client: EngineHandle,
    health: EngineHealthReceiver,
    control: ControlPlane,
    cancellation: CancellationToken,
    publisher: watch::Sender<Option<Result<Arc<Value>, String>>>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let mut poll = interval(Duration::from_secs_f64(1.0 / 15.0));
        poll.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let mut drop_count = 0_u64;
        let mut was_drop = false;
        let mut noise_samples = VecDeque::with_capacity(300);
        let mut last_audio_sequence = None;
        let mut previous_context: Option<AudioStreamContext> = None;
        let mut last_error: Option<String> = None;
        loop {
            tokio::select! {
                _ = cancellation.cancelled() => break,
                _ = poll.tick() => {
                    let result = match client.audio_status().await {
                        Ok(ResponseResult::AudioStatus { status }) => {
                            let health_snapshot = health.borrow().clone();
                            let health_response = health_snapshot
                                .as_ref()
                                .and_then(|result| result.as_ref().ok())
                                .map(Arc::as_ref);
                            let context = control.audio_stream_context();
                            if previous_context
                                .as_ref()
                                .is_some_and(|previous| previous != &context)
                            {
                                noise_samples.clear();
                                last_audio_sequence = None;
                            }
                            previous_context = Some(context.clone());
                            record_noise_sample(
                                &mut noise_samples,
                                &mut last_audio_sequence,
                                &status,
                            );
                            let telemetry = audio_telemetry(
                                &status,
                                health_response,
                                &context,
                                estimated_noise_floor(&noise_samples),
                                &mut drop_count,
                                &mut was_drop,
                            );
                            Ok(Arc::new(telemetry))
                        }
                        Ok(_) => Err("Rust engine returned an unexpected audio-status response".to_owned()),
                        Err(error) => Err(error.to_string()),
                    };
                    match &result {
                        Ok(_) if last_error.is_some() => {
                            tracing::info!("Rust audio status polling recovered");
                            last_error = None;
                        }
                        Err(error) if last_error.as_ref() != Some(error) => {
                            tracing::warn!(error = %error, "Rust audio status polling failed");
                            last_error = Some(error.clone());
                        }
                        _ => {}
                    }
                    publisher.send_replace(Some(result));
                }
            }
        }
    })
}

pub fn spawn_preview_publisher(
    mut health: EngineHealthReceiver,
    cancellation: CancellationToken,
    publisher: watch::Sender<Option<Result<Arc<Vec<u8>>, String>>>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let mut last_sent_sequence = None;
        loop {
            tokio::select! {
                _ = cancellation.cancelled() => break,
                changed = health.changed() => {
                    if changed.is_err() { break; }
                    let snapshot = health.borrow_and_update().clone();
                    match snapshot {
                        Some(Err(error)) => {
                            publisher.send_replace(Some(Err(error)));
                        }
                        Some(Ok(response)) => match response.as_ref() {
                            ResponseResult::Health {
                                frames_rendered,
                                preview_outputs: Some(outputs),
                                ..
                            } => {
                                let sequence = *frames_rendered;
                                if last_sent_sequence != Some(sequence) {
                                    let encoded = encode_preview_frame(sequence, outputs)
                                        .map(Arc::new)
                                        .map_err(str::to_owned);
                                    publisher.send_replace(Some(encoded));
                                    last_sent_sequence = Some(sequence);
                                }
                            }
                            ResponseResult::Health { .. } => {}
                            _ => {
                                publisher.send_replace(Some(Err(
                                    "Rust engine returned an unexpected preview source".to_owned(),
                                )));
                            }
                        },
                        None => {}
                    }
                }
            }
        }
    })
}

fn audio_telemetry(
    status: &EngineAudioStatus,
    health: Option<&ResponseResult>,
    context: &AudioStreamContext,
    estimated_noise_floor: f64,
    drop_count: &mut u64,
    was_drop: &mut bool,
) -> Value {
    let frame = status.frame;
    let features = status.features;
    let processor = status.processor;
    let structure = status.structure;
    let drop_phase = structure.phase == MusicStructurePhase::Drop;
    if drop_phase && !*was_drop {
        *drop_count = drop_count.saturating_add(1);
    }
    *was_drop = drop_phase;
    let clock = health.and_then(|response| match response {
        ResponseResult::Health { beat_clocks, .. } => beat_clocks.first(),
        _ => None,
    });
    let scheduler = health.and_then(|response| match response {
        ResponseResult::Health {
            effect_schedulers, ..
        } => effect_schedulers.first(),
        _ => None,
    });
    let next_beat_in_ms = clock
        .and_then(|clock| clock.next_beat_at_ns)
        .map(|next| next.saturating_sub(crate::state::monotonic_time_ns()) as f64 / 1_000_000.0);
    let bands = frame.bands.map(clamp_unit_f32);
    let active = status.active;
    let phase = phase_name(structure.phase);
    let scheduler_late_dropped = scheduler.map_or(0, |state| state.late_ticks_dropped);
    let scheduler_late_coalesced = scheduler.map_or(0, |state| state.late_ticks_coalesced);
    let scheduler_lateness_ms =
        scheduler.map_or(0.0, |state| state.beat_lateness_p95_ns as f64 / 1_000_000.0);
    let spectral_balance = {
        let total = features.bass_energy + features.mid_energy + features.treble_energy;
        if total > 0.0 {
            clamp_unit_f32(features.treble_energy / total)
        } else {
            0.0
        }
    };
    json!({
        "input_peak": clamp_unit_f32(processor.input_peak),
        "clipping_fraction": clamp_unit_f32(processor.clipping_fraction),
        "microphone_gate_open": processor.microphone_gate_open,
        "configured_input_noise_floor": context.configured_input_noise_floor,
        "sequence": frame.sequence,
        "fresh": frame.fresh,
        "input_level": clamp_unit_f32(processor.input_rms),
        "processed_level": clamp_unit_f32(frame.rms),
        "bands": bands,
        "beat": frame.beat || features.beat,
        "beat_strength": clamp_unit_f32(frame.beat_strength.max(features.beat_strength)),
        "bpm": f64::from(features.bpm.max(0.0)),
        "bpm_confidence": clamp_unit_f32(features.bpm_confidence),
        "beat_phase": clock.map_or(0.0, |value| value.phase.clamp(0.0, 0.999_999)),
        "beat_index": clock.map_or(0, |value| value.beat_index),
        "next_beat_in_ms": next_beat_in_ms,
        "beat_event_sequence": clock.map_or(0, |value| value.last_event_sequence),
        "dropped_beat_events": processor.native_dropped_beat_events.saturating_add(processor.queued_dropped_beat_events),
        "pending_beat_events": 0,
        "beat_scheduler_late_ticks_dropped": scheduler_late_dropped,
        "beat_scheduler_late_ticks_coalesced": scheduler_late_coalesced,
        "beat_scheduler_lateness_p95_ms": scheduler_lateness_ms,
        "calibration_output_callback_count": 0,
        "calibration_output_sample_rate": 0.0,
        "calibration_output_block_duration_ms": 0.0,
        "calibration_output_callback_p50_ms": 0.0,
        "calibration_output_callback_p95_ms": 0.0,
        "calibration_output_callback_p99_ms": 0.0,
        "calibration_output_interval_jitter_p95_ms": 0.0,
        "calibration_output_underflow_count": 0,
        "calibration_output_overflow_count": 0,
        "calibration_output_callback_error_count": 0,
        "sync_offset_ms": context.sync_offset_ms,
        "sync_route": context.sync_route,
        "input_transport": status.input_transport,
        "input_sample_rate": status.device.as_ref().map_or(status.pcm_ingress.sample_rate, |device| device.sample_rate),
        "input_channels": status.device.as_ref().map_or(status.pcm_ingress.channels, |device| device.channels),
        "pcm_ingress": status.pcm_ingress,
        "dsp_processing_p50_ns": status.dsp_processing_p50_ns,
        "dsp_processing_p95_ns": status.dsp_processing_p95_ns,
        "last_feature_timestamp_ns": status.last_frame_ns,
        "estimated_noise_floor": estimated_noise_floor,
        "configured_noise_floor": context.configured_noise_floor,
        "normalization_gain": f64::from(processor.normalization_gain.max(0.0)),
        "hardware_gain_value": Value::Null,
        "program_loudness": clamp_unit_f32(processor.program_loudness),
        "musical_impact": clamp_unit_f32(processor.musical_impact),
        "gate": if active { "active" } else { "idle" },
        "gate_preview": true,
        "gate_energy": clamp_unit_f32(features.energy_level),
        "gate_onset": clamp_unit_f32(features.onset_strength),
        "gate_beat_density": clamp_unit_f32(features.beat_confidence),
        "gate_brightness": clamp_unit_f32(features.brightness),
        "gate_spectral_balance": spectral_balance,
        "gate_reason": if active && context.source == "demo" { "demo_audio_active" } else if active && status.input_transport == lumistripe_engine_protocol::AudioInputTransport::DirectPcm { "spotify_direct_pcm_active" } else if active && status.input_transport == lumistripe_engine_protocol::AudioInputTransport::MonitorFallback { "spotify_monitor_fallback_active" } else if active { "rust_audio_capture_active" } else { "no_audio_input" },
        "gate_checks": [],
        "music_structure_phase": phase,
        "music_structure_confidence": structure.confidence.clamp(0.0, 1.0),
        "music_structure_buildup_progress": structure.buildup_progress.clamp(0.0, 1.0),
        "music_structure_drop_count": drop_count,
        "music_structure_active": structure.phase != MusicStructurePhase::Idle,
        "music_structure_fresh": frame.fresh,
        "drop_accent_status": scheduler.map_or("idle", |state| state.drop_accent_status.as_str()),
        "drop_accent_reason": scheduler.and_then(|state| state.drop_accent_reason.as_deref()),
        "health": if active { "ok" } else if status.last_error.is_some() { "unavailable" } else { "inactive" },
    })
}

fn record_noise_sample(
    samples: &mut VecDeque<f64>,
    last_sequence: &mut Option<u64>,
    status: &EngineAudioStatus,
) {
    let frame = status.frame;
    if !frame.fresh || *last_sequence == Some(frame.sequence) {
        return;
    }
    if last_sequence.is_some_and(|previous| frame.sequence < previous) {
        samples.clear();
    }
    samples.push_back(f64::from(status.processor.input_rms.clamp(0.0, 1.0)));
    if samples.len() > 300 {
        samples.pop_front();
    }
    *last_sequence = Some(frame.sequence);
}

fn estimated_noise_floor(samples: &VecDeque<f64>) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    let mut ordered = samples.iter().copied().collect::<Vec<_>>();
    ordered.sort_by(f64::total_cmp);
    ordered[(ordered.len() - 1) / 5]
}

fn clamp_unit_f32(value: f32) -> f64 {
    f64::from(value.clamp(0.0, 1.0))
}

fn phase_name(phase: MusicStructurePhase) -> &'static str {
    match phase {
        MusicStructurePhase::Idle => "idle",
        MusicStructurePhase::Breakdown => "breakdown",
        MusicStructurePhase::Groove => "groove",
        MusicStructurePhase::Buildup => "buildup",
        MusicStructurePhase::Drop => "drop",
    }
}

pub async fn websocket_state(
    State(state): State<AppState>,
    headers: HeaderMap,
    websocket: WebSocketUpgrade,
) -> Response {
    websocket
        .max_message_size(64 * 1024)
        .max_frame_size(64 * 1024)
        .on_upgrade(move |socket| async move {
            let token = session_from_headers(&headers);
            if !websocket_origin_allowed(&headers) {
                close_socket(socket, 1008, "invalid origin").await;
                return;
            }
            if !state.access.authenticated(token.as_deref()) {
                close_socket(socket, 4401, "pairing required").await;
                return;
            }
            let receiver = state.dashboard_state.clone();
            let _ = send_state_watch(socket, receiver, state.access, token).await;
        })
        .into_response()
}

pub async fn websocket_audio(
    State(state): State<AppState>,
    headers: HeaderMap,
    websocket: WebSocketUpgrade,
) -> Response {
    websocket
        .max_message_size(64 * 1024)
        .max_frame_size(64 * 1024)
        .on_upgrade(move |socket| async move {
            let token = session_from_headers(&headers);
            if !websocket_origin_allowed(&headers) {
                close_socket(socket, 1008, "invalid origin").await;
                return;
            }
            if !state.access.authenticated(token.as_deref()) {
                close_socket(socket, 4401, "pairing required").await;
                return;
            }
            let _ = send_watch_json(
                socket,
                state.audio_telemetry,
                state.access,
                token,
                Duration::from_secs_f64(1.0 / 15.0),
                false,
            )
            .await;
        })
        .into_response()
}

pub async fn websocket_preview(
    State(state): State<AppState>,
    headers: HeaderMap,
    websocket: WebSocketUpgrade,
) -> Response {
    websocket
        .max_message_size(64 * 1024)
        .max_frame_size(64 * 1024)
        .on_upgrade(move |socket| async move {
            let token = session_from_headers(&headers);
            if !websocket_origin_allowed(&headers) {
                close_socket(socket, 1008, "invalid origin").await;
                return;
            }
            if !state.access.authenticated(token.as_deref()) {
                close_socket(socket, 4401, "pairing required").await;
                return;
            }
            let _ = send_watch_binary(
                socket,
                state.preview,
                state.access,
                token,
                Duration::from_secs_f64(1.0 / 30.0),
            )
            .await;
        })
        .into_response()
}

async fn send_watch_json(
    socket: WebSocket,
    mut receiver: watch::Receiver<Option<Result<Arc<Value>, String>>>,
    access: crate::auth::PairingAuth,
    token: Option<String>,
    cadence: Duration,
    close_when_runtime_unavailable: bool,
) -> Result<(), ()> {
    let (mut sink, incoming) = socket.split();
    let (disconnect_tx, mut disconnect_rx) = tokio::sync::oneshot::channel();
    let reader = tokio::spawn(read_until_disconnect(incoming, disconnect_tx));
    let mut tick = interval(cadence);
    tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
    let initial = receiver.borrow_and_update().clone();
    if let Some(Ok(value)) = initial {
        if send_json(&mut sink, value.as_ref()).await.is_err() {
            send_close(&mut sink, 1013, "slow client").await;
            reader.abort();
            return Err(());
        }
        if close_when_runtime_unavailable
            && value.get("running").and_then(Value::as_bool) == Some(false)
        {
            send_close(&mut sink, 1011, "runtime unavailable").await;
            reader.abort();
            return Ok(());
        }
    }
    loop {
        tokio::select! {
            _ = &mut disconnect_rx => break,
            changed = receiver.changed() => {
                if changed.is_err() { break; }
                let current = receiver.borrow_and_update().clone();
                match current {
                    Some(Ok(value)) => {
                        if send_json(&mut sink, value.as_ref()).await.is_err() {
                            send_close(&mut sink, 1013, "slow client").await;
                            break;
                        }
                        if close_when_runtime_unavailable && value.get("running").and_then(Value::as_bool) == Some(false) {
                            send_close(&mut sink, 1011, "runtime unavailable").await;
                            break;
                        }
                    }
                    Some(Err(_)) => {
                        send_close(&mut sink, 1011, "runtime unavailable").await;
                        break;
                    }
                    None => {}
                }
            }
            _ = tick.tick() => {
                if !access.authenticated(token.as_deref()) {
                    send_close(&mut sink, 4401, "pairing required").await;
                    break;
                }
                if close_when_runtime_unavailable {
                    let current = receiver.borrow().clone();
                    if current.as_ref().and_then(|result| result.as_ref().ok()).is_some_and(|value| {
                        value.get("running").and_then(Value::as_bool) == Some(false)
                    }) {
                        send_close(&mut sink, 1011, "runtime unavailable").await;
                        break;
                    }
                }
            }
        }
    }
    reader.abort();
    Ok(())
}

async fn send_watch_binary(
    socket: WebSocket,
    mut receiver: PreviewReceiver,
    access: crate::auth::PairingAuth,
    token: Option<String>,
    cadence: Duration,
) -> Result<(), ()> {
    let (mut sink, incoming) = socket.split();
    let (disconnect_tx, mut disconnect_rx) = tokio::sync::oneshot::channel();
    let reader = tokio::spawn(read_until_disconnect(incoming, disconnect_tx));
    let mut tick = interval(cadence);
    tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
    let initial = receiver.borrow_and_update().clone();
    if let Some(Ok(packet)) = initial
        && send_binary(&mut sink, packet.as_ref()).await.is_err()
    {
        send_close(&mut sink, 1013, "slow client").await;
        reader.abort();
        return Err(());
    }
    loop {
        tokio::select! {
            _ = &mut disconnect_rx => break,
            changed = receiver.changed() => {
                if changed.is_err() { break; }
                let current = receiver.borrow_and_update().clone();
                match current {
                    Some(Ok(packet)) => if send_binary(&mut sink, packet.as_ref()).await.is_err() {
                        send_close(&mut sink, 1013, "slow client").await;
                        break;
                    },
                    Some(Err(_)) => {
                        send_close(&mut sink, 1011, "runtime unavailable").await;
                        break;
                    }
                    None => {}
                }
            }
            _ = tick.tick() => {
                if !access.authenticated(token.as_deref()) {
                    send_close(&mut sink, 4401, "pairing required").await;
                    break;
                }
            }
        }
    }
    reader.abort();
    Ok(())
}

async fn read_until_disconnect(
    mut incoming: SplitStream<WebSocket>,
    disconnect: tokio::sync::oneshot::Sender<()>,
) {
    while let Some(message) = incoming.next().await {
        if matches!(message, Ok(Message::Close(_)) | Err(_)) {
            break;
        }
    }
    let _ = disconnect.send(());
}

async fn send_json<T: serde::Serialize>(
    sink: &mut SplitSink<WebSocket, Message>,
    value: &T,
) -> Result<(), ()> {
    let text = serde_json::to_string(value).map_err(|_| ())?;
    tokio::time::timeout(
        Duration::from_secs(2),
        sink.send(Message::Text(text.into())),
    )
    .await
    .map_err(|_| ())?
    .map_err(|_| ())
}

async fn send_binary(sink: &mut SplitSink<WebSocket, Message>, packet: &[u8]) -> Result<(), ()> {
    tokio::time::timeout(
        Duration::from_secs(2),
        sink.send(Message::Binary(packet.to_vec().into())),
    )
    .await
    .map_err(|_| ())?
    .map_err(|_| ())
}

async fn send_close(sink: &mut SplitSink<WebSocket, Message>, code: u16, reason: &'static str) {
    let _ = tokio::time::timeout(
        Duration::from_millis(250),
        sink.send(close_message(code, reason)),
    )
    .await;
}

async fn close_socket(mut socket: WebSocket, code: u16, reason: &'static str) {
    let _ = tokio::time::timeout(
        Duration::from_millis(250),
        socket.send(close_message(code, reason)),
    )
    .await;
}

fn close_message(code: u16, reason: &'static str) -> Message {
    Message::Close(Some(CloseFrame {
        code: CloseCode::from(code),
        reason: reason.into(),
    }))
}

fn session_from_headers(headers: &axum::http::HeaderMap) -> Option<String> {
    headers
        .get(axum::http::header::COOKIE)?
        .to_str()
        .ok()?
        .split(';')
        .filter_map(|part| part.trim().split_once('='))
        .find_map(|(name, value)| (name == SESSION_COOKIE).then(|| value.to_owned()))
}

async fn send_state_watch(
    socket: WebSocket,
    mut receiver: watch::Receiver<Arc<Value>>,
    access: crate::auth::PairingAuth,
    token: Option<String>,
) -> Result<(), ()> {
    let (mut sink, incoming) = socket.split();
    let (disconnect_tx, mut disconnect_rx) = tokio::sync::oneshot::channel();
    let reader = tokio::spawn(read_until_disconnect(incoming, disconnect_tx));
    let mut tick = interval(Duration::from_millis(250));
    tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
    let initial = receiver.borrow_and_update().as_ref().clone();
    if send_json(&mut sink, &initial).await.is_err() {
        send_close(&mut sink, 1013, "slow client").await;
        reader.abort();
        return Err(());
    }
    if initial.get("running").and_then(Value::as_bool) == Some(false) {
        send_close(&mut sink, 1011, "runtime unavailable").await;
        reader.abort();
        return Ok(());
    }
    loop {
        tokio::select! {
            _ = &mut disconnect_rx => break,
            changed = receiver.changed() => {
                if changed.is_err() { break; }
                let current = receiver.borrow_and_update().as_ref().clone();
                if send_json(&mut sink, &current).await.is_err() {
                    send_close(&mut sink, 1013, "slow client").await;
                    break;
                }
                if current.get("running").and_then(Value::as_bool) == Some(false) {
                    send_close(&mut sink, 1011, "runtime unavailable").await;
                    break;
                }
            }
            _ = tick.tick() => {
                if !access.authenticated(token.as_deref()) {
                    send_close(&mut sink, 4401, "pairing required").await;
                    break;
                }
            }
        }
    }
    reader.abort();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        audio_telemetry, estimated_noise_floor, record_noise_sample, spawn_engine_failure_monitor,
    };
    use crate::audio::AudioStreamContext;
    use lumistripe_engine_audio::{AudioFrame, AudioProcessorStats};
    use lumistripe_engine_protocol::{AudioInputTransport, EngineAudioStatus};
    use std::collections::VecDeque;
    use std::sync::Arc;
    use tokio::sync::watch;
    use tokio_util::sync::CancellationToken;

    #[tokio::test]
    async fn engine_failure_monitor_publishes_a_terminal_health_error() {
        let (health_sender, health) = watch::channel::<
            Option<Result<Arc<lumistripe_engine_protocol::ResponseResult>, String>>,
        >(None);
        let (failure_sender, mut failure) = watch::channel(None);
        let cancellation = CancellationToken::new();
        let monitor = spawn_engine_failure_monitor(health, cancellation.clone(), failure_sender);

        health_sender.send_replace(Some(Err("output worker terminated".to_owned())));
        tokio::time::timeout(std::time::Duration::from_secs(1), failure.changed())
            .await
            .expect("failure monitor should publish promptly")
            .expect("failure snapshot should remain connected");
        assert_eq!(
            failure.borrow().as_deref(),
            Some("output worker terminated")
        );

        cancellation.cancel();
        monitor.await.unwrap();
    }

    fn status(sequence: u64, fresh: bool, input_rms: f32) -> EngineAudioStatus {
        EngineAudioStatus {
            frame: AudioFrame {
                sequence,
                fresh,
                ..Default::default()
            },
            processor: AudioProcessorStats {
                input_rms,
                ..Default::default()
            },
            ..Default::default()
        }
    }

    #[test]
    fn noise_floor_uses_recent_unique_fresh_engine_frames() {
        let mut samples = VecDeque::new();
        let mut last_sequence = None;
        for (sequence, level) in [0.3, 0.1, 0.2, 0.4, 0.05, 0.6].into_iter().enumerate() {
            record_noise_sample(
                &mut samples,
                &mut last_sequence,
                &status(sequence as u64 + 1, true, level),
            );
        }
        record_noise_sample(&mut samples, &mut last_sequence, &status(6, true, 0.9));
        record_noise_sample(&mut samples, &mut last_sequence, &status(7, false, 0.7));

        assert_eq!(samples.len(), 6);
        assert_eq!(estimated_noise_floor(&samples), f64::from(0.1_f32));

        record_noise_sample(&mut samples, &mut last_sequence, &status(1, true, 0.25));
        assert_eq!(samples.len(), 1);
        assert_eq!(estimated_noise_floor(&samples), 0.25);
    }

    #[test]
    fn audio_telemetry_reports_profile_noise_floors_and_route_settings() {
        let status = EngineAudioStatus {
            input_transport: AudioInputTransport::DirectPcm,
            pcm_ingress: lumistripe_engine_audio::PcmIngressStats {
                enabled: true,
                playback_state: lumistripe_engine_audio::PcmPlaybackState::Playing,
                sample_rate: 44_100,
                channels: 2,
                queue_occupancy: 1,
                queue_capacity: 8,
                dropped_samples: 12,
                ..Default::default()
            },
            dsp_processing_p50_ns: 200_000,
            dsp_processing_p95_ns: 650_000,
            last_frame_ns: Some(1_234_000_000),
            ..status(3, true, 0.12)
        };
        let context = AudioStreamContext {
            source: "auto".to_owned(),
            sync_route: "spotify_direct|librespot decoded PCM|default".to_owned(),
            sync_offset_ms: -24.5,
            configured_input_noise_floor: Some(0.02),
            configured_noise_floor: 0.04,
        };
        let mut drop_count = 0;
        let mut was_drop = false;

        let telemetry = audio_telemetry(
            &status,
            None,
            &context,
            0.03,
            &mut drop_count,
            &mut was_drop,
        );

        assert_eq!(telemetry["configured_input_noise_floor"], 0.02);
        assert_eq!(telemetry["configured_noise_floor"], 0.04);
        assert_eq!(telemetry["estimated_noise_floor"], 0.03);
        assert_eq!(
            telemetry["sync_route"],
            "spotify_direct|librespot decoded PCM|default"
        );
        assert_eq!(telemetry["sync_offset_ms"], -24.5);
        assert_eq!(telemetry["input_transport"], "direct_pcm");
        assert_eq!(telemetry["input_sample_rate"], 44_100);
        assert_eq!(telemetry["input_channels"], 2);
        assert_eq!(telemetry["pcm_ingress"]["queue_occupancy"], 1);
        assert_eq!(telemetry["pcm_ingress"]["dropped_samples"], 12);
        assert_eq!(telemetry["dsp_processing_p50_ns"], 200_000);
        assert_eq!(telemetry["dsp_processing_p95_ns"], 650_000);
        assert_eq!(telemetry["last_feature_timestamp_ns"], 1_234_000_000_u64);
    }
}
