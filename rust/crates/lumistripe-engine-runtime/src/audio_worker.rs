//! Rust-owned capture, DSP, structure analysis, and output-scheduler updates.

use crate::OutputAudioHandle;
use lumistripe_engine_audio::{
    AudioBeatEvent, AudioCapture, AudioConfig, AudioFrame, AudioProcessor, AudioProcessorStats,
    MAX_AUDIO_BEAT_EVENTS, MusicFeatures, MusicStructurePhase, MusicStructureState,
    MusicStructureTracker, enumerate_input_devices, monotonic_time_ns,
};
use lumistripe_engine_protocol::{
    EngineAudioStatus, MAX_RENDER_STRUCTURE_EVENTS, RenderAudioInput, RenderAudioUpdate,
    RenderBeatEvent, RenderEffectFeatures, RenderSelectorFeatures, RenderStructureEvent,
};
use std::collections::VecDeque;
use std::sync::mpsc::{self, Receiver, Sender, SyncSender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

const AUDIO_CONTROL_TIMEOUT: Duration = Duration::from_secs(4);
const AUDIO_WORKER_POLL_INTERVAL: Duration = Duration::from_millis(4);
const AUDIO_EVENT_HISTORY_CAPACITY: usize = 256;
const AUDIO_STALL_TIMEOUT_NS: u64 = 1_500_000_000;
const AUDIO_INITIAL_CALLBACK_TIMEOUT_NS: u64 = 3_000_000_000;
const AUDIO_RECOVERY_DELAY_NS: u64 = 1_000_000_000;

enum AudioCommand {
    Start {
        selector: Option<String>,
        config: AudioConfig,
        reply: SyncSender<Result<bool, String>>,
    },
    Configure {
        config: AudioConfig,
        reply: SyncSender<Result<bool, String>>,
    },
    Stop {
        reply: SyncSender<()>,
    },
    Shutdown {
        reply: SyncSender<()>,
    },
}

/// Control-plane handle for the independent Rust audio worker.
pub(super) struct NativeAudioWorker {
    commands: Sender<AudioCommand>,
    status: Arc<Mutex<EngineAudioStatus>>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl NativeAudioWorker {
    pub(super) fn new(output: OutputAudioHandle) -> Self {
        let (commands, receiver) = mpsc::channel();
        let status = Arc::new(Mutex::new(EngineAudioStatus::default()));
        let worker_status = Arc::clone(&status);
        let thread = thread::Builder::new()
            .name("lumistripe-rust-audio".into())
            .spawn(move || audio_worker_loop(receiver, output, worker_status))
            .expect("Rust audio worker thread could not be created");
        Self {
            commands,
            status,
            thread: Mutex::new(Some(thread)),
        }
    }

    pub(super) fn start(
        &self,
        selector: Option<String>,
        config: AudioConfig,
    ) -> Result<bool, String> {
        config.validate()?;
        let (reply, receive) = mpsc::sync_channel(1);
        self.commands
            .send(AudioCommand::Start {
                selector,
                config,
                reply,
            })
            .map_err(|error| format!("Rust audio worker is unavailable: {error}"))?;
        receive
            .recv_timeout(AUDIO_CONTROL_TIMEOUT)
            .map_err(|error| format!("Rust audio start did not finish: {error}"))?
    }

    pub(super) fn configure(&self, config: AudioConfig) -> Result<bool, String> {
        config.validate()?;
        let (reply, receive) = mpsc::sync_channel(1);
        self.commands
            .send(AudioCommand::Configure { config, reply })
            .map_err(|error| format!("Rust audio worker is unavailable: {error}"))?;
        receive
            .recv_timeout(AUDIO_CONTROL_TIMEOUT)
            .map_err(|error| format!("Rust audio reconfiguration did not finish: {error}"))?
    }

    pub(super) fn stop(&self) -> Result<(), String> {
        let (reply, receive) = mpsc::sync_channel(1);
        self.commands
            .send(AudioCommand::Stop { reply })
            .map_err(|error| format!("Rust audio worker is unavailable: {error}"))?;
        receive
            .recv_timeout(AUDIO_CONTROL_TIMEOUT)
            .map_err(|error| format!("Rust audio stop did not finish: {error}"))
    }

    pub(super) fn devices(&self) -> Result<Vec<lumistripe_engine_audio::InputAudioDevice>, String> {
        enumerate_input_devices().map_err(|error| error.to_string())
    }

    pub(super) fn status(&self) -> EngineAudioStatus {
        self.status
            .lock()
            .expect("Rust audio status poisoned")
            .clone()
    }

    pub(super) fn shutdown(&self) {
        let Some(thread) = self
            .thread
            .lock()
            .expect("Rust audio thread lock poisoned")
            .take()
        else {
            return;
        };
        let (reply, receive) = mpsc::sync_channel(1);
        let _ = self.commands.send(AudioCommand::Shutdown { reply });
        let _ = receive.recv_timeout(AUDIO_CONTROL_TIMEOUT);
        let _ = thread.join();
    }
}

impl Drop for NativeAudioWorker {
    fn drop(&mut self) {
        self.shutdown();
    }
}

struct AudioSession {
    capture: AudioCapture,
    processor: AudioProcessor,
    tracker: MusicStructureTracker,
    frame: AudioFrame,
    features: MusicFeatures,
    structure: MusicStructureState,
    opened_at_ns: u64,
    last_frame_ns: Option<u64>,
}

#[derive(Clone, Copy)]
struct PendingStructureEdge {
    sequence: u64,
    timestamp_ns: u64,
    phase: lumistripe_engine_protocol::RenderMusicStructurePhase,
    confidence: f64,
    buildup_progress: f64,
    drop_event: bool,
    section_change: bool,
    phase_change: bool,
}

#[derive(Clone, Copy)]
struct ObservedFrame {
    frame: AudioFrame,
    features: MusicFeatures,
    structure: MusicStructureState,
    phase_changed: bool,
}

struct WorkerState {
    config: AudioConfig,
    selector: Option<String>,
    desired_active: bool,
    session: Option<AudioSession>,
    retry_at_ns: u64,
    last_error: Option<String>,
    audio_update_sequence: u64,
    feature_sequence: u64,
    beat_event_sequence: u64,
    structure_event_sequence: u64,
    pending_beats: VecDeque<RenderBeatEvent>,
    pending_structure: VecDeque<PendingStructureEdge>,
    dropped_audio_updates: u64,
    dropped_structure_events: u64,
}

impl Default for WorkerState {
    fn default() -> Self {
        Self {
            config: AudioConfig::default(),
            selector: None,
            desired_active: false,
            session: None,
            retry_at_ns: 0,
            last_error: None,
            audio_update_sequence: 0,
            feature_sequence: 0,
            beat_event_sequence: 0,
            structure_event_sequence: 0,
            pending_beats: VecDeque::with_capacity(AUDIO_EVENT_HISTORY_CAPACITY),
            pending_structure: VecDeque::with_capacity(AUDIO_EVENT_HISTORY_CAPACITY),
            dropped_audio_updates: 0,
            dropped_structure_events: 0,
        }
    }
}

fn audio_worker_loop(
    commands: Receiver<AudioCommand>,
    output: OutputAudioHandle,
    status: Arc<Mutex<EngineAudioStatus>>,
) {
    let mut state = WorkerState::default();
    let mut shutdown = false;
    while !shutdown {
        match commands.recv_timeout(AUDIO_WORKER_POLL_INTERVAL) {
            Ok(AudioCommand::Start {
                selector,
                config,
                reply,
            }) => {
                let result = state.start(selector, config);
                write_status(&state, &status);
                let _ = reply.send(result);
            }
            Ok(AudioCommand::Configure { config, reply }) => {
                let result = state.configure(config);
                write_status(&state, &status);
                let _ = reply.send(result);
            }
            Ok(AudioCommand::Stop { reply }) => {
                state.stop();
                write_status(&state, &status);
                let _ = reply.send(());
            }
            Ok(AudioCommand::Shutdown { reply }) => {
                state.stop();
                write_status(&state, &status);
                let _ = reply.send(());
                shutdown = true;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                state.stop();
                shutdown = true;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }

        if !shutdown {
            state.process_available_audio(&output);
            state.recover_stalled_capture();
            write_status(&state, &status);
        }
    }
}

impl WorkerState {
    fn start(&mut self, selector: Option<String>, config: AudioConfig) -> Result<bool, String> {
        config.validate()?;
        self.stop_session();
        self.selector = selector;
        self.config = config;
        self.desired_active = true;
        self.clear_pending_events();
        self.open_session()
    }

    fn configure(&mut self, config: AudioConfig) -> Result<bool, String> {
        config.validate()?;
        self.config = config;
        if !self.desired_active {
            self.publish_error(None);
            return Ok(false);
        }
        self.stop_session();
        self.clear_pending_events();
        self.open_session()
    }

    fn stop(&mut self) {
        self.desired_active = false;
        self.stop_session();
        self.clear_pending_events();
        self.publish_error(None);
    }

    fn stop_session(&mut self) {
        self.session = None;
    }

    fn clear_pending_events(&mut self) {
        self.pending_beats.clear();
        self.pending_structure.clear();
    }

    fn publish_error(&mut self, error: Option<String>) {
        self.last_error = error;
    }

    fn open_session(&mut self) -> Result<bool, String> {
        let result = (|| {
            let capture =
                AudioCapture::open(self.selector.as_deref()).map_err(|error| error.to_string())?;
            let processor = AudioProcessor::new(self.config, capture.sample_rate() as f32)
                .map_err(|error| error.to_string())?;
            Ok::<_, String>(AudioSession {
                capture,
                processor,
                tracker: MusicStructureTracker::new(),
                frame: AudioFrame::default(),
                features: MusicFeatures::default(),
                structure: MusicStructureState::default(),
                opened_at_ns: monotonic_time_ns(),
                last_frame_ns: None,
            })
        })();
        match result {
            Ok(session) => {
                self.session = Some(session);
                self.retry_at_ns = 0;
                self.publish_error(None);
                Ok(true)
            }
            Err(error) => {
                self.session = None;
                self.retry_at_ns = monotonic_time_ns().saturating_add(AUDIO_RECOVERY_DELAY_NS);
                self.publish_error(Some(error.clone()));
                Err(error)
            }
        }
    }

    fn process_available_audio(&mut self, output: &OutputAudioHandle) {
        loop {
            let Some(session) = self.session.as_mut() else {
                return;
            };
            let Some(batch) = session.capture.try_pop() else {
                return;
            };
            self.process_batch(output, batch);
        }
    }

    fn process_batch(
        &mut self,
        output: &OutputAudioHandle,
        batch: lumistripe_engine_audio::CapturedAudioBatch,
    ) {
        let Some(session) = self.session.as_mut() else {
            return;
        };
        let seen = session.processor.stats().samples_seen;
        if batch.sample_position > seen {
            let _ = session
                .processor
                .mark_discontinuity(batch.sample_position - seen);
        }

        let mut observed: [Option<ObservedFrame>; 8] = [None; 8];
        let mut observed_count = 0;
        let mut structure = session.structure;
        let tracker = &mut session.tracker;
        let capture_end_seconds = batch.capture_end_ns as f64 / 1_000_000_000.0;
        let result = session.processor.feed_samples_with_observer(
            &batch.samples[..batch.len],
            capture_end_seconds,
            |frame, features| {
                let update = tracker
                    .update(&features, frame.timestamp)
                    .expect("capture timestamps are monotonic");
                structure = update.state;
                if observed_count < observed.len() {
                    observed[observed_count] = Some(ObservedFrame {
                        frame,
                        features,
                        structure: update.state,
                        phase_changed: update.phase_changed,
                    });
                    observed_count += 1;
                }
            },
        );
        if let Err(error) = result {
            self.publish_error(Some(format!("Rust audio DSP failed: {error}")));
            return;
        }
        let mut beat_events = [AudioBeatEvent::default(); MAX_AUDIO_BEAT_EVENTS];
        let beat_event_count = session.processor.drain_beat_events_into(&mut beat_events);
        for event in beat_events.into_iter().take(beat_event_count) {
            if self.beat_event_sequence == u64::MAX {
                self.dropped_audio_updates = self.dropped_audio_updates.saturating_add(1);
                continue;
            }
            self.beat_event_sequence += 1;
            if self.pending_beats.len() == AUDIO_EVENT_HISTORY_CAPACITY {
                self.pending_beats.pop_front();
                self.dropped_audio_updates = self.dropped_audio_updates.saturating_add(1);
            }
            self.pending_beats.push_back(RenderBeatEvent {
                sequence: self.beat_event_sequence,
                timestamp_ns: seconds_to_ns(event.timestamp),
                strength: event.strength as f64,
            });
        }
        session.structure = structure;

        for observed in observed.into_iter().take(observed_count).flatten() {
            self.feature_sequence = self.feature_sequence.saturating_add(1);
            let mut frame = observed.frame;
            frame.sequence = self.feature_sequence;
            let timestamp_ns = seconds_to_ns(frame.timestamp);
            let structure_edge = observed.features.section_change
                || observed.structure.drop_event
                || observed.phase_changed;
            if structure_edge {
                self.structure_event_sequence = self.structure_event_sequence.saturating_add(1);
                if self.pending_structure.len() == AUDIO_EVENT_HISTORY_CAPACITY {
                    self.pending_structure.pop_front();
                    self.dropped_structure_events = self.dropped_structure_events.saturating_add(1);
                }
                self.pending_structure.push_back(PendingStructureEdge {
                    sequence: self.structure_event_sequence,
                    timestamp_ns,
                    phase: structure_phase(observed.structure.phase),
                    confidence: observed.structure.confidence,
                    buildup_progress: observed.structure.buildup_progress,
                    drop_event: observed.structure.drop_event,
                    section_change: observed.features.section_change,
                    phase_change: observed.phase_changed,
                });
            }
            if let Some(session) = self.session.as_mut() {
                session.frame = frame;
                session.features = observed.features;
                session.structure = observed.structure;
                session.last_frame_ns = Some(timestamp_ns);
            }
            self.submit_audio_frame(output, frame, observed.features);
        }
    }

    fn submit_audio_frame(
        &mut self,
        output: &OutputAudioHandle,
        frame: AudioFrame,
        features: MusicFeatures,
    ) {
        if self.audio_update_sequence == u64::MAX {
            self.dropped_audio_updates = self.dropped_audio_updates.saturating_add(1);
            return;
        }
        self.audio_update_sequence += 1;
        let (output_count, beat_ack, structure_ack) = output.audio_acknowledgments();
        while self
            .pending_beats
            .front()
            .is_some_and(|event| event.sequence <= beat_ack)
        {
            self.pending_beats.pop_front();
        }
        if output_count > 0 && structure_ack.len() >= output_count {
            let acknowledged = structure_ack[..output_count]
                .iter()
                .copied()
                .min()
                .unwrap_or(0);
            while self
                .pending_structure
                .front()
                .is_some_and(|event| event.sequence <= acknowledged)
            {
                self.pending_structure.pop_front();
            }
        }

        let mut structure_events = Vec::new();
        if output_count > 0 {
            let per_output_limit = MAX_RENDER_STRUCTURE_EVENTS / output_count;
            'outputs: for output_index in 0..output_count {
                for event in &self.pending_structure {
                    if structure_events.len() >= MAX_RENDER_STRUCTURE_EVENTS
                        || structure_events.len() >= per_output_limit * output_count
                    {
                        break 'outputs;
                    }
                    structure_events.push(RenderStructureEvent {
                        output_index,
                        sequence: event.sequence,
                        timestamp_ns: event.timestamp_ns,
                        phase: event.phase,
                        confidence: event.confidence,
                        buildup_progress: event.buildup_progress,
                        drop_event: event.drop_event,
                        section_change: event.section_change,
                        phase_change: event.phase_change,
                    });
                }
            }
        }
        let audio = RenderAudioInput {
            rms: frame.rms as f64,
            bands: frame.bands.map(f64::from),
            beat: frame.beat,
            beat_strength: frame.beat_strength as f64,
            feature_timestamp_ns: Some(seconds_to_ns(frame.timestamp)),
            feature_sequence: Some(frame.sequence),
            fallback_bpm: Some(features.bpm as f64),
            beat_events: self.pending_beats.iter().copied().collect(),
            structure_events,
            selector_features: Some(RenderSelectorFeatures {
                energy_level: features.energy_level as f64,
                energy: features.energy as f64,
                volume: features.volume as f64,
                brightness: features.brightness as f64,
                onset_strength: features.onset_strength as f64,
                bass_energy: features.bass_energy as f64,
                bass: features.bass as f64,
                mid_energy: features.mid_energy as f64,
                treble_energy: features.treble_energy as f64,
                beat_confidence: features.beat_confidence as f64,
                bpm: features.bpm as f64,
                beat: features.beat,
                silence: features.silence,
                drop_detected: features.drop_detected,
                section_change: features.section_change,
            }),
            effect_features: Some(RenderEffectFeatures {
                beat_strength: features.beat_strength as f64,
                musical_impact: features.musical_impact as f64,
            }),
            beat_event_stream: true,
        };
        match output.submit_audio_update(RenderAudioUpdate {
            sequence: self.audio_update_sequence,
            timestamp_ns: seconds_to_ns(frame.timestamp),
            audio: Some(audio),
        }) {
            Ok(_) | Err(crate::FrameAckStatus::NotConfigured) => {}
            Err(crate::FrameAckStatus::ShuttingDown) => {}
            Err(crate::FrameAckStatus::InvalidFrame | crate::FrameAckStatus::Accepted) => {
                self.dropped_audio_updates = self.dropped_audio_updates.saturating_add(1);
            }
        }
    }

    fn recover_stalled_capture(&mut self) {
        let now = monotonic_time_ns();
        if let Some(session) = self.session.as_ref() {
            let stats = session.capture.stats();
            let callback_stalled = match stats.last_callback_ns {
                Some(last) => now.saturating_sub(last) > AUDIO_STALL_TIMEOUT_NS,
                None => {
                    now.saturating_sub(session.opened_at_ns) > AUDIO_INITIAL_CALLBACK_TIMEOUT_NS
                }
            };
            if callback_stalled {
                self.session = None;
                self.retry_at_ns = now.saturating_add(AUDIO_RECOVERY_DELAY_NS);
                self.publish_error(Some(
                    "audio capture stopped producing callbacks; retrying".to_owned(),
                ));
            }
        }
        if self.desired_active && self.session.is_none() && now >= self.retry_at_ns {
            if let Err(error) = self.open_session() {
                self.publish_error(Some(format!("audio device recovery failed: {error}")));
            }
        }
    }
}

fn write_status(state: &WorkerState, shared: &Mutex<EngineAudioStatus>) {
    let mut status = shared.lock().expect("Rust audio status poisoned");
    if let Some(session) = state.session.as_ref() {
        status.active = true;
        status.device = Some(session.capture.device.clone());
        status.frame = session.frame;
        status.features = session.features;
        status.structure = session.structure;
        status.processor = session.processor.stats();
        status.capture = session.capture.stats();
        status.last_frame_ns = session.last_frame_ns;
    } else {
        status.active = false;
        status.device = None;
        status.frame = AudioFrame::default();
        status.features = MusicFeatures::default();
        status.structure = MusicStructureState::default();
        status.processor = AudioProcessorStats::default();
        status.capture = Default::default();
        status.last_frame_ns = None;
    }
    status.last_error = state.last_error.clone();
    status.audio_update_sequence = state.audio_update_sequence;
    status.dropped_audio_updates = state.dropped_audio_updates;
    status.dropped_structure_events = state.dropped_structure_events;
}

fn structure_phase(
    phase: MusicStructurePhase,
) -> lumistripe_engine_protocol::RenderMusicStructurePhase {
    match phase {
        MusicStructurePhase::Idle => lumistripe_engine_protocol::RenderMusicStructurePhase::Idle,
        MusicStructurePhase::Breakdown => {
            lumistripe_engine_protocol::RenderMusicStructurePhase::Breakdown
        }
        MusicStructurePhase::Groove => {
            lumistripe_engine_protocol::RenderMusicStructurePhase::Groove
        }
        MusicStructurePhase::Buildup => {
            lumistripe_engine_protocol::RenderMusicStructurePhase::Buildup
        }
        MusicStructurePhase::Drop => lumistripe_engine_protocol::RenderMusicStructurePhase::Drop,
    }
}

fn seconds_to_ns(seconds: f64) -> u64 {
    if !seconds.is_finite() || seconds <= 0.0 {
        return 0;
    }
    (seconds * 1_000_000_000.0).round().min(u64::MAX as f64) as u64
}
