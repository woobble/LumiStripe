//! Rust-owned capture, DSP, structure analysis, and output-scheduler updates.

use crate::OutputAudioHandle;
use lumistripe_engine_audio::{
    AUDIO_SAMPLE_RATE, AudioBeatEvent, AudioCapture, AudioCaptureStats, AudioConfig, AudioFrame,
    AudioPcmBatch, AudioProcessor, AudioProcessorStats, InputAudioDevice, MAX_AUDIO_BEAT_EVENTS,
    MusicFeatures, MusicStructurePhase, MusicStructureState, MusicStructureTracker,
    PcmIngressReceiver, PcmIngressSender, PcmPlaybackState, bounded_pcm_ingress,
    enumerate_input_devices, monotonic_time_ns,
};
use lumistripe_engine_protocol::{
    AudioInputTransport, EngineAudioStatus, MAX_RENDER_STRUCTURE_EVENTS, RenderAudioInput,
    RenderAudioUpdate, RenderBeatEvent, RenderEffectFeatures, RenderSelectorFeatures,
    RenderStructureEvent,
};
use std::collections::VecDeque;
use std::io;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

const AUDIO_CONTROL_TIMEOUT: Duration = Duration::from_secs(4);
const AUDIO_COMMAND_CAPACITY: usize = 16;
const AUDIO_WORKER_POLL_INTERVAL: Duration = Duration::from_millis(4);
const AUDIO_EVENT_HISTORY_CAPACITY: usize = 256;
const AUDIO_STALL_TIMEOUT_NS: u64 = 1_500_000_000;
const AUDIO_INITIAL_CALLBACK_TIMEOUT_NS: u64 = 3_000_000_000;
const AUDIO_RECOVERY_DELAY_NS: u64 = 1_000_000_000;
const DIRECT_PCM_FIRST_BATCH_TIMEOUT_NS: u64 = 3_000_000_000;
const DIRECT_PCM_STALL_TIMEOUT_NS: u64 = 1_500_000_000;
const DSP_TIMING_WINDOW: usize = 64;
const DEMO_FRAME_INTERVAL_NS: u64 = 40_000_000;

enum AudioCommand {
    Start {
        selector: Option<String>,
        config: AudioConfig,
        reply: SyncSender<Result<bool, String>>,
    },
    StartDirect {
        fallback_selector: Option<String>,
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
    commands: SyncSender<AudioCommand>,
    status: Arc<Mutex<EngineAudioStatus>>,
    pcm_ingress: PcmIngressSender,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl NativeAudioWorker {
    pub(super) fn new(output: OutputAudioHandle) -> io::Result<Self> {
        let (commands, receiver) = mpsc::sync_channel(AUDIO_COMMAND_CAPACITY);
        let (pcm_ingress, pcm_receiver) = bounded_pcm_ingress();
        let status = Arc::new(Mutex::new(EngineAudioStatus::default()));
        let worker_status = Arc::clone(&status);
        let worker_ingress = pcm_ingress.clone();
        let thread = thread::Builder::new()
            .name("lumistripe-rust-audio".into())
            .spawn(move || {
                audio_worker_loop(
                    receiver,
                    output,
                    worker_status,
                    worker_ingress,
                    pcm_receiver,
                )
            })?;
        Ok(Self {
            commands,
            status,
            pcm_ingress,
            thread: Mutex::new(Some(thread)),
        })
    }

    pub(super) fn start_direct(
        &self,
        fallback_selector: Option<String>,
        config: AudioConfig,
    ) -> Result<bool, String> {
        config.validate()?;
        let (reply, receive) = mpsc::sync_channel(1);
        self.commands
            .send(AudioCommand::StartDirect {
                fallback_selector,
                config,
                reply,
            })
            .map_err(|error| format!("Rust audio worker is unavailable: {error}"))?;
        receive
            .recv_timeout(AUDIO_CONTROL_TIMEOUT)
            .map_err(|error| format!("Rust direct PCM start did not finish: {error}"))?
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

    pub(super) fn pcm_ingress(&self) -> PcmIngressSender {
        self.pcm_ingress.clone()
    }

    pub(super) fn worker_failure(&self) -> Option<String> {
        let stopped = self
            .thread
            .lock()
            .expect("Rust audio thread lock poisoned")
            .as_ref()
            .is_some_and(JoinHandle::is_finished);
        stopped.then(|| "audio capture and DSP worker terminated unexpectedly".to_owned())
    }

    #[cfg(test)]
    pub(super) fn is_stopped(&self) -> bool {
        self.thread
            .lock()
            .expect("Rust audio thread lock poisoned")
            .is_none()
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
        if let Err(payload) = catch_unwind(AssertUnwindSafe(|| self.shutdown())) {
            let message = payload
                .downcast_ref::<String>()
                .cloned()
                .or_else(|| {
                    payload
                        .downcast_ref::<&'static str>()
                        .map(|message| (*message).to_owned())
                })
                .unwrap_or_else(|| "unknown panic".to_owned());
            eprintln!("Rust audio worker cleanup panicked: {message}");
        }
    }
}

struct AudioSession {
    input: AudioInput,
    processor: AudioProcessor,
    tracker: MusicStructureTracker,
    sample_rate: u32,
    stream_generation: Option<u64>,
    sample_position_origin: Option<u64>,
    last_input_timestamp_ns: Option<u64>,
    frame: AudioFrame,
    features: MusicFeatures,
    structure: MusicStructureState,
    opened_at_ns: u64,
    last_frame_ns: Option<u64>,
}

enum AudioInput {
    CaptureDevice(Box<dyn AudioBatchSource>),
    DirectPcm,
    MonitorFallback(Box<dyn AudioBatchSource>),
}

impl AudioInput {
    fn transport(&self) -> AudioInputTransport {
        match self {
            Self::CaptureDevice(_) => AudioInputTransport::CaptureDevice,
            Self::DirectPcm => AudioInputTransport::DirectPcm,
            Self::MonitorFallback(_) => AudioInputTransport::MonitorFallback,
        }
    }

    fn capture(&self) -> Option<&dyn AudioBatchSource> {
        match self {
            Self::CaptureDevice(capture) | Self::MonitorFallback(capture) => Some(capture.as_ref()),
            Self::DirectPcm => None,
        }
    }
}

trait AudioBatchSource: Send {
    fn try_pop(&mut self) -> Option<AudioPcmBatch>;
    fn sample_rate(&self) -> u32;
    fn device(&self) -> InputAudioDevice;
    fn stats(&self) -> AudioCaptureStats;
}

struct CpalAudioSource(AudioCapture);

impl AudioBatchSource for CpalAudioSource {
    fn try_pop(&mut self) -> Option<AudioPcmBatch> {
        self.0.try_pop()
    }

    fn sample_rate(&self) -> u32 {
        self.0.sample_rate()
    }

    fn device(&self) -> InputAudioDevice {
        self.0.device.clone()
    }

    fn stats(&self) -> AudioCaptureStats {
        self.0.stats()
    }
}

trait AudioCaptureFactory: Send + Sync {
    fn open(&self, selector: Option<&str>) -> Result<Box<dyn AudioBatchSource>, String>;
}

struct SystemAudioCaptureFactory;

impl AudioCaptureFactory for SystemAudioCaptureFactory {
    fn open(&self, selector: Option<&str>) -> Result<Box<dyn AudioBatchSource>, String> {
        AudioCapture::open(selector)
            .map(|capture| Box::new(CpalAudioSource(capture)) as Box<dyn AudioBatchSource>)
            .map_err(|error| error.to_string())
    }
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
    demo_active: bool,
    next_demo_frame_ns: u64,
    demo_frame: AudioFrame,
    demo_features: MusicFeatures,
    session: Option<AudioSession>,
    direct_requested: bool,
    fallback_active: bool,
    fallback_selector: Option<String>,
    pcm_ingress: PcmIngressSender,
    pcm_receiver: PcmIngressReceiver,
    capture_factory: Arc<dyn AudioCaptureFactory>,
    dsp_timing_ns: [u64; DSP_TIMING_WINDOW],
    dsp_timing_count: usize,
    dsp_timing_cursor: usize,
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
        let (pcm_ingress, pcm_receiver) = bounded_pcm_ingress();
        Self::with_pcm_ingress(pcm_ingress, pcm_receiver)
    }
}

impl WorkerState {
    fn with_pcm_ingress(pcm_ingress: PcmIngressSender, pcm_receiver: PcmIngressReceiver) -> Self {
        Self::with_capture_factory(
            pcm_ingress,
            pcm_receiver,
            Arc::new(SystemAudioCaptureFactory),
        )
    }

    fn with_capture_factory(
        pcm_ingress: PcmIngressSender,
        pcm_receiver: PcmIngressReceiver,
        capture_factory: Arc<dyn AudioCaptureFactory>,
    ) -> Self {
        Self {
            config: AudioConfig::default(),
            selector: None,
            desired_active: false,
            demo_active: false,
            next_demo_frame_ns: 0,
            demo_frame: AudioFrame::default(),
            demo_features: MusicFeatures::default(),
            session: None,
            direct_requested: false,
            fallback_active: false,
            fallback_selector: None,
            pcm_ingress,
            pcm_receiver,
            capture_factory,
            dsp_timing_ns: [0; DSP_TIMING_WINDOW],
            dsp_timing_count: 0,
            dsp_timing_cursor: 0,
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
    pcm_ingress: PcmIngressSender,
    pcm_receiver: PcmIngressReceiver,
) {
    let mut state = WorkerState::with_pcm_ingress(pcm_ingress, pcm_receiver);
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
            Ok(AudioCommand::StartDirect {
                fallback_selector,
                config,
                reply,
            }) => {
                let result = state.start_direct(fallback_selector, config);
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
            state.process_demo_audio(&output);
            state.recover_stalled_capture();
            write_status(&state, &status);
        }
    }
}

impl WorkerState {
    fn start(&mut self, selector: Option<String>, config: AudioConfig) -> Result<bool, String> {
        config.validate()?;
        self.stop_session();
        self.pcm_ingress.set_enabled(false);
        self.pcm_receiver.discard_queued();
        self.direct_requested = false;
        self.fallback_active = false;
        self.fallback_selector = None;
        self.demo_active = selector.as_deref() == Some("demo");
        self.selector = if self.demo_active { None } else { selector };
        self.config = config;
        self.desired_active = true;
        self.clear_pending_events();
        if self.demo_active {
            self.next_demo_frame_ns = 0;
            self.demo_frame = AudioFrame::default();
            self.demo_features = MusicFeatures::default();
            self.retry_at_ns = 0;
            self.publish_error(None);
            return Ok(true);
        }
        self.open_session()
    }

    fn start_direct(
        &mut self,
        fallback_selector: Option<String>,
        config: AudioConfig,
    ) -> Result<bool, String> {
        config.validate()?;
        self.stop_session();
        self.pcm_ingress.set_enabled(false);
        self.pcm_receiver.discard_queued();
        self.demo_active = false;
        self.direct_requested = true;
        self.fallback_active = false;
        self.fallback_selector = fallback_selector.clone();
        self.selector = fallback_selector;
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
        if self.demo_active {
            self.publish_error(None);
            return Ok(true);
        }
        self.stop_session();
        self.clear_pending_events();
        self.open_session()
    }

    fn stop(&mut self) {
        self.desired_active = false;
        self.demo_active = false;
        self.direct_requested = false;
        self.fallback_active = false;
        self.fallback_selector = None;
        self.pcm_ingress.set_enabled(false);
        self.pcm_receiver.discard_queued();
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
        let result = if self.direct_requested && !self.fallback_active {
            self.pcm_ingress.set_enabled(true);
            self.pcm_receiver.discard_queued();
            self.pcm_ingress.begin_generation();
            AudioProcessor::new(self.config, AUDIO_SAMPLE_RATE)
                .map(|processor| AudioSession {
                    input: AudioInput::DirectPcm,
                    processor,
                    tracker: MusicStructureTracker::new(),
                    sample_rate: AUDIO_SAMPLE_RATE as u32,
                    stream_generation: None,
                    sample_position_origin: None,
                    last_input_timestamp_ns: None,
                    frame: AudioFrame::default(),
                    features: MusicFeatures::default(),
                    structure: MusicStructureState::default(),
                    opened_at_ns: monotonic_time_ns(),
                    last_frame_ns: None,
                })
                .map_err(|error| error.to_string())
        } else {
            self.pcm_ingress.set_enabled(false);
            self.pcm_receiver.discard_queued();
            self.build_capture_session(if self.direct_requested {
                AudioInputTransport::MonitorFallback
            } else {
                AudioInputTransport::CaptureDevice
            })
        };
        match result {
            Ok(session) => {
                self.session = Some(session);
                self.retry_at_ns = 0;
                if self.direct_requested && self.fallback_active {
                    self.publish_error(Some(
                        "Spotify decoded PCM is unavailable; using PipeWire monitor capture."
                            .to_owned(),
                    ));
                } else {
                    self.publish_error(None);
                }
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

    fn build_capture_session(
        &self,
        transport: AudioInputTransport,
    ) -> Result<AudioSession, String> {
        let capture = self.capture_factory.open(self.selector.as_deref())?;
        let sample_rate = capture.sample_rate();
        let processor = AudioProcessor::new(self.config, sample_rate as f32)
            .map_err(|error| error.to_string())?;
        let input = match transport {
            AudioInputTransport::MonitorFallback => AudioInput::MonitorFallback(capture),
            _ => AudioInput::CaptureDevice(capture),
        };
        Ok(AudioSession {
            input,
            processor,
            tracker: MusicStructureTracker::new(),
            sample_rate,
            stream_generation: None,
            sample_position_origin: None,
            last_input_timestamp_ns: None,
            frame: AudioFrame::default(),
            features: MusicFeatures::default(),
            structure: MusicStructureState::default(),
            opened_at_ns: monotonic_time_ns(),
            last_frame_ns: None,
        })
    }

    fn process_available_audio(&mut self, output: &OutputAudioHandle) {
        loop {
            let Some(batch) = (match self.session.as_mut() {
                Some(session) => match &mut session.input {
                    AudioInput::CaptureDevice(capture) | AudioInput::MonitorFallback(capture) => {
                        capture.try_pop()
                    }
                    AudioInput::DirectPcm => self.pcm_receiver.try_pop(),
                },
                None => None,
            }) else {
                return;
            };
            self.process_batch(output, batch);
        }
    }

    fn process_demo_audio(&mut self, output: &OutputAudioHandle) {
        if !self.demo_active {
            return;
        }
        let now_ns = monotonic_time_ns();
        if now_ns < self.next_demo_frame_ns {
            return;
        }
        self.next_demo_frame_ns = now_ns.saturating_add(DEMO_FRAME_INTERVAL_NS);
        self.feature_sequence = self.feature_sequence.saturating_add(1);
        let (mut frame, features) = demo_snapshot(self.feature_sequence, now_ns);
        frame.sequence = self.feature_sequence;
        self.demo_frame = frame;
        self.demo_features = features;
        self.submit_audio_frame(output, frame, features);
    }

    fn process_batch(&mut self, output: &OutputAudioHandle, batch: AudioPcmBatch) {
        if batch.sample_rate == 0 {
            self.publish_error(Some("audio input reported a zero sample rate".to_owned()));
            return;
        }
        let stream_changed = self.session.as_ref().is_some_and(|session| {
            session.sample_rate != batch.sample_rate
                || session
                    .stream_generation
                    .is_some_and(|generation| generation != batch.stream_generation)
        });
        if stream_changed {
            self.clear_pending_events();
        }
        let config = self.config;
        let Some(session) = self.session.as_mut() else {
            return;
        };
        if stream_changed {
            let processor = match AudioProcessor::new(config, batch.sample_rate as f32) {
                Ok(processor) => processor,
                Err(error) => {
                    self.publish_error(Some(format!(
                        "Rust audio DSP could not reinitialize for input format: {error}"
                    )));
                    return;
                }
            };
            session.processor = processor;
            session.tracker.reset();
            session.sample_rate = batch.sample_rate;
            session.stream_generation = Some(batch.stream_generation);
            session.sample_position_origin = Some(batch.sample_position);
            session.last_input_timestamp_ns = None;
            session.frame = AudioFrame::default();
            session.features = MusicFeatures::default();
            session.structure = MusicStructureState::default();
            session.last_frame_ns = None;
        } else if session.stream_generation.is_none() {
            session.stream_generation = Some(batch.stream_generation);
        }
        let sample_position_origin = *session
            .sample_position_origin
            .get_or_insert(batch.sample_position);
        let relative_sample_position = batch.sample_position.saturating_sub(sample_position_origin);
        let seen = session.processor.stats().samples_seen;
        if relative_sample_position > seen {
            let _ = session
                .processor
                .mark_discontinuity(relative_sample_position - seen);
        }

        let mut observed: [Option<ObservedFrame>; 8] = [None; 8];
        let mut observed_count = 0;
        let mut structure = session.structure;
        let tracker = &mut session.tracker;
        let capture_end_ns = batch
            .capture_end_ns
            .max(session.last_input_timestamp_ns.unwrap_or_default());
        session.last_input_timestamp_ns = Some(capture_end_ns);
        let capture_end_seconds = capture_end_ns as f64 / 1_000_000_000.0;
        let processing_started = std::time::Instant::now();
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
        let processing_ns = processing_started
            .elapsed()
            .as_nanos()
            .min(u64::MAX as u128) as u64;
        self.dsp_timing_ns[self.dsp_timing_cursor] = processing_ns;
        self.dsp_timing_cursor = (self.dsp_timing_cursor + 1) % DSP_TIMING_WINDOW;
        self.dsp_timing_count = (self.dsp_timing_count + 1).min(DSP_TIMING_WINDOW);
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
        let direct_stalled = self.session.as_ref().is_some_and(|session| {
            if !matches!(&session.input, AudioInput::DirectPcm) {
                return false;
            }
            let pcm = self.pcm_ingress.stats();
            if pcm.playback_state == PcmPlaybackState::Failed {
                return true;
            }
            if pcm.playback_state != PcmPlaybackState::Playing {
                return false;
            }
            let last_input = pcm.last_input_timestamp_ns.unwrap_or(session.opened_at_ns);
            let timeout = if pcm.last_input_timestamp_ns.is_some() {
                DIRECT_PCM_STALL_TIMEOUT_NS
            } else {
                DIRECT_PCM_FIRST_BATCH_TIMEOUT_NS
            };
            now.saturating_sub(last_input) > timeout
        });
        if direct_stalled && now >= self.retry_at_ns {
            self.activate_monitor_fallback(now);
        }

        let capture_stalled = self.session.as_ref().and_then(|session| {
            session.input.capture().map(|capture| {
                let stats = capture.stats();
                let callback_stalled = match stats.last_callback_ns {
                    Some(last) => now.saturating_sub(last) > AUDIO_STALL_TIMEOUT_NS,
                    None => {
                        now.saturating_sub(session.opened_at_ns) > AUDIO_INITIAL_CALLBACK_TIMEOUT_NS
                    }
                };
                callback_stalled
            })
        });
        if capture_stalled == Some(true) {
            self.session = None;
            self.retry_at_ns = now.saturating_add(AUDIO_RECOVERY_DELAY_NS);
            self.publish_error(Some(
                "audio capture stopped producing callbacks; retrying".to_owned(),
            ));
        }
        if self.desired_active
            && !self.demo_active
            && self.session.is_none()
            && now >= self.retry_at_ns
        {
            if let Err(error) = self.open_session() {
                self.publish_error(Some(format!("audio device recovery failed: {error}")));
            }
        }
    }

    fn activate_monitor_fallback(&mut self, now_ns: u64) {
        self.pcm_ingress.set_enabled(false);
        self.pcm_receiver.discard_queued();
        match self.build_capture_session(AudioInputTransport::MonitorFallback) {
            Ok(session) => {
                self.session = Some(session);
                self.fallback_active = true;
                self.retry_at_ns = 0;
                self.publish_error(Some(
                    "Spotify decoded PCM is unavailable; using PipeWire monitor capture."
                        .to_owned(),
                ));
            }
            Err(error) => {
                self.pcm_ingress.set_enabled(true);
                self.pcm_receiver.discard_queued();
                self.retry_at_ns = now_ns.saturating_add(AUDIO_RECOVERY_DELAY_NS);
                self.publish_error(Some(format!(
                    "Spotify decoded PCM failed and monitor fallback could not open: {error}"
                )));
            }
        }
    }
}

fn write_status(state: &WorkerState, shared: &Mutex<EngineAudioStatus>) {
    let mut status = shared.lock().expect("Rust audio status poisoned");
    status.input_transport = if state.demo_active {
        AudioInputTransport::Demo
    } else {
        state
            .session
            .as_ref()
            .map_or(AudioInputTransport::Off, |session| {
                session.input.transport()
            })
    };
    status.pcm_ingress = state.pcm_ingress.stats();
    status.dsp_processing_p50_ns = timing_percentile(state, 50);
    status.dsp_processing_p95_ns = timing_percentile(state, 95);
    if let Some(session) = state.session.as_ref() {
        status.active = true;
        status.device = session.input.capture().map(AudioBatchSource::device);
        status.frame = session.frame;
        status.features = session.features;
        status.structure = session.structure;
        status.processor = session.processor.stats();
        status.capture = session
            .input
            .capture()
            .map_or_else(Default::default, AudioBatchSource::stats);
        status.last_frame_ns = session.last_frame_ns;
    } else if state.demo_active {
        status.active = true;
        status.device = None;
        status.frame = state.demo_frame;
        status.features = state.demo_features;
        status.structure = MusicStructureState::default();
        status.processor = AudioProcessorStats::default();
        status.capture = Default::default();
        status.last_frame_ns =
            (state.demo_frame.sequence > 0).then(|| seconds_to_ns(state.demo_frame.timestamp));
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

fn timing_percentile(state: &WorkerState, percentile: usize) -> u64 {
    if state.dsp_timing_count == 0 {
        return 0;
    }
    let mut values = [0_u64; DSP_TIMING_WINDOW];
    values[..state.dsp_timing_count]
        .copy_from_slice(&state.dsp_timing_ns[..state.dsp_timing_count]);
    values[..state.dsp_timing_count].sort_unstable();
    let index = (state.dsp_timing_count - 1) * percentile / 100;
    values[index]
}

fn demo_snapshot(sequence: u64, timestamp_ns: u64) -> (AudioFrame, MusicFeatures) {
    let frame_index = sequence.saturating_sub(1);
    let measure_position = frame_index % 100;
    let beat_index = measure_position / 25;
    let phase = (measure_position % 25) as f32 / 25.0;
    let decay = (-phase * 5.0).exp();
    let fast = (-phase * 12.0).exp();
    let slow = (-phase * 2.0).exp();
    let kick = if matches!(beat_index, 0 | 2) {
        fast
    } else {
        0.001
    };
    let snare = if matches!(beat_index, 1 | 3) {
        decay * 0.8
    } else {
        0.001
    };
    let bass_scale = [1.0, 1.0, 1.0, 0.75][(frame_index / 100 % 4) as usize];
    let bass = slow * 0.6 * bass_scale;
    let beat_position = measure_position % 25;
    let hat = if beat_position < 2 || (12..14).contains(&beat_position) {
        0.35
    } else {
        0.001
    };
    let accent = if (62..64).contains(&measure_position) {
        0.2
    } else {
        0.0
    };
    let bands = [
        kick.min(1.0),
        bass.min(1.0),
        (snare * 0.3 + kick * 0.15).min(1.0),
        snare.min(1.0),
        (snare * 0.5 + hat * 0.3).min(1.0),
        (hat + accent).min(1.0),
        ((hat + accent) * 0.4).min(1.0),
        ((hat + accent) * 0.15).min(1.0),
    ];
    let rms = (bands.iter().sum::<f32>() / bands.len() as f32)
        .sqrt()
        .min(1.0);
    let beat = beat_index == 0;
    let beat_strength = if beat { 0.7 + kick * 0.3 } else { 0.0 };
    let low = ((bands[0] + bands[1]) * 0.5).clamp(0.0, 1.0);
    let mid = ((bands[2] + bands[3] + bands[4]) / 3.0).clamp(0.0, 1.0);
    let high = ((bands[5] + bands[6] + bands[7]) / 3.0).clamp(0.0, 1.0);
    let brightness = (high * 0.55 + rms * 0.25 + (1.0 - low) * high * 0.2).clamp(0.0, 1.0);
    let onset = if beat { beat_strength } else { rms * 0.18 };
    let silence = rms <= 0.003 && bands.iter().copied().fold(0.0_f32, f32::max) <= 0.003;
    let frame = AudioFrame {
        rms,
        bands,
        beat,
        beat_strength,
        sequence,
        timestamp: timestamp_ns as f64 / 1_000_000_000.0,
        fresh: true,
    };
    let features = MusicFeatures {
        energy: rms,
        volume: rms,
        energy_level: rms,
        bass: low,
        brightness,
        onset_strength: onset,
        dynamic_range: beat_strength,
        beat,
        beat_strength,
        bands,
        bass_energy: low,
        mid_energy: mid,
        treble_energy: high,
        spectral_centroid: brightness,
        spectral_flux: onset,
        beat_confidence: beat_strength,
        rolling_loudness: rms,
        program_loudness: rms,
        musical_impact: (rms * 1.5).min(1.0),
        silence,
        drop_detected: beat && low > 0.75 && onset > 0.55,
        ..MusicFeatures::default()
    };
    (frame, features)
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

#[cfg(test)]
mod tests {
    use super::{
        AudioBatchSource, AudioCaptureFactory, AudioInput, WorkerState, demo_snapshot, write_status,
    };
    use crate::OutputWorker;
    use lumistripe_engine_audio::{
        AudioCaptureStats, AudioConfig, AudioPcmBatch, InputAudioDevice, PcmIngressReceiver,
        PcmIngressSender, PcmPlaybackState, bounded_pcm_ingress, monotonic_time_ns,
    };
    use lumistripe_engine_protocol::{AudioInputTransport, EngineAudioStatus};
    use std::collections::VecDeque;
    use std::sync::{Arc, Mutex};

    struct MockCaptureFactory {
        batches: Vec<AudioPcmBatch>,
    }

    impl AudioCaptureFactory for MockCaptureFactory {
        fn open(&self, _selector: Option<&str>) -> Result<Box<dyn AudioBatchSource>, String> {
            Ok(Box::new(MockAudioSource {
                batches: self.batches.iter().copied().collect(),
                device: InputAudioDevice {
                    selector: "mock:spotify-monitor".to_owned(),
                    host: "mock".to_owned(),
                    name: "Spotify monitor".to_owned(),
                    is_default: false,
                    sample_rate: 44_100,
                    channels: 2,
                },
            }))
        }
    }

    struct MockAudioSource {
        batches: VecDeque<AudioPcmBatch>,
        device: InputAudioDevice,
    }

    impl AudioBatchSource for MockAudioSource {
        fn try_pop(&mut self) -> Option<AudioPcmBatch> {
            self.batches.pop_front()
        }

        fn sample_rate(&self) -> u32 {
            self.device.sample_rate
        }

        fn device(&self) -> InputAudioDevice {
            self.device.clone()
        }

        fn stats(&self) -> AudioCaptureStats {
            AudioCaptureStats {
                callback_count: 1,
                last_callback_ns: Some(monotonic_time_ns()),
                ..Default::default()
            }
        }
    }

    fn audio_batch(samples: &[f32], timestamp_ns: u64) -> AudioPcmBatch {
        let mut batch = AudioPcmBatch {
            samples: [0.0; lumistripe_engine_audio::MAX_CAPTURE_BATCH_SAMPLES],
            len: samples.len(),
            sample_rate: 44_100,
            channels: 2,
            capture_end_ns: timestamp_ns,
            submitted_at_ns: timestamp_ns,
            sample_position: 0,
            stream_generation: 0,
            dropped_samples_before: 0,
        };
        batch.samples[..samples.len()].copy_from_slice(samples);
        batch
    }

    fn worker_state(
        sender: PcmIngressSender,
        receiver: PcmIngressReceiver,
        batches: Vec<AudioPcmBatch>,
    ) -> WorkerState {
        WorkerState::with_capture_factory(
            sender,
            receiver,
            Arc::new(MockCaptureFactory { batches }),
        )
    }

    #[test]
    fn demo_source_starts_without_opening_a_capture_device() {
        let mut state = WorkerState::default();

        assert!(
            state
                .start(Some("demo".to_owned()), AudioConfig::default())
                .unwrap()
        );
        assert!(state.demo_active);
        assert!(state.session.is_none());

        let status = Mutex::new(EngineAudioStatus::default());
        write_status(&state, &status);
        let status = status.lock().unwrap();
        assert!(status.active);
        assert!(status.device.is_none());
    }

    #[test]
    fn demo_snapshot_emits_beat_and_audio_features() {
        let (kick, kick_features) = demo_snapshot(1, 1_000_000_000);
        let (snare, _) = demo_snapshot(26, 2_000_000_000);

        assert!(kick.beat);
        assert!(!snare.beat);
        assert_eq!(kick.sequence, 1);
        assert!(kick.fresh);
        assert!(kick.rms > 0.0);
        assert!(kick_features.musical_impact > 0.0);
        assert_eq!(kick_features.bpm, 120.0);
    }

    #[test]
    fn direct_pcm_and_monitor_batches_use_the_same_dsp_and_render_sequence() {
        let mono = (0..2_048)
            .map(|index| {
                let time = index as f32 / 44_100.0;
                0.32 * (std::f32::consts::TAU * 80.0 * time).sin()
                    + 0.08 * (std::f32::consts::TAU * 1_600.0 * time).sin()
            })
            .collect::<Vec<_>>();
        let stereo = mono
            .iter()
            .flat_map(|sample| [f64::from(*sample), f64::from(*sample)])
            .collect::<Vec<_>>();
        let timestamp_ns = 2_000_000_000;
        let (direct_sender, direct_receiver) = bounded_pcm_ingress();
        let mut direct = worker_state(direct_sender.clone(), direct_receiver, Vec::new());
        assert!(
            direct
                .start_direct(
                    Some("mock:spotify-monitor".to_owned()),
                    AudioConfig::default()
                )
                .unwrap()
        );
        let generation = direct_sender.generation();
        assert!(direct_sender.submit_interleaved_f64_at(
            &stereo,
            44_100,
            2,
            generation,
            0,
            timestamp_ns,
        ));

        let (capture_sender, capture_receiver) = bounded_pcm_ingress();
        let mut capture = worker_state(
            capture_sender,
            capture_receiver,
            vec![audio_batch(&mono, timestamp_ns)],
        );
        assert!(
            capture
                .start(
                    Some("mock:spotify-monitor".to_owned()),
                    AudioConfig::default()
                )
                .unwrap()
        );

        let output = OutputWorker::new().unwrap();
        let output_audio = output.audio_handle();
        direct.process_available_audio(&output_audio);
        capture.process_available_audio(&output_audio);

        let direct_session = direct.session.as_ref().unwrap();
        let capture_session = capture.session.as_ref().unwrap();
        assert_eq!(direct_session.processor.stats().samples_seen, 2_048);
        assert_eq!(capture_session.processor.stats().samples_seen, 2_048);
        assert_eq!(
            direct_session.processor.frame(),
            capture_session.processor.frame()
        );
        assert_eq!(
            direct_session.processor.features(),
            capture_session.processor.features()
        );
        assert_eq!(direct.feature_sequence, capture.feature_sequence);
        assert_eq!(direct.audio_update_sequence, capture.audio_update_sequence);
        assert_eq!(direct.pcm_ingress.stats().samples_received, 2_048);
        assert_eq!(direct.pcm_receiver.stats().queue_occupancy, 0);

        let status = Mutex::new(EngineAudioStatus::default());
        write_status(&direct, &status);
        let status = status.lock().unwrap();
        assert!(status.active);
        assert_eq!(status.input_transport, AudioInputTransport::DirectPcm);
        assert_eq!(status.device, None);
        assert_eq!(status.pcm_ingress.sample_rate, 44_100);
        assert_eq!(status.pcm_ingress.channels, 2);
        assert!(status.dsp_processing_p95_ns >= status.dsp_processing_p50_ns);
        drop(status);
        let _ = output.shutdown();
    }

    #[test]
    fn failed_direct_input_uses_monitor_fallback_without_a_second_worker() {
        let (sender, receiver) = bounded_pcm_ingress();
        let mut state = worker_state(sender.clone(), receiver, Vec::new());
        assert!(
            state
                .start_direct(
                    Some("mock:spotify-monitor".to_owned()),
                    AudioConfig::default()
                )
                .unwrap()
        );
        sender.set_playback_state(PcmPlaybackState::Failed);
        state.recover_stalled_capture();

        assert!(state.fallback_active);
        assert!(matches!(
            state.session.as_ref().unwrap().input,
            AudioInput::MonitorFallback(_)
        ));
        assert!(!sender.stats().enabled);
        assert_eq!(
            state.session.as_ref().unwrap().input.transport(),
            AudioInputTransport::MonitorFallback
        );
    }

    #[test]
    fn paused_direct_input_does_not_trigger_monitor_fallback() {
        let (sender, receiver) = bounded_pcm_ingress();
        let mut state = worker_state(sender.clone(), receiver, Vec::new());
        assert!(
            state
                .start_direct(
                    Some("mock:spotify-monitor".to_owned()),
                    AudioConfig::default()
                )
                .unwrap()
        );
        sender.set_playback_state(PcmPlaybackState::Paused);
        state.recover_stalled_capture();

        assert!(!state.fallback_active);
        assert_eq!(
            state.session.as_ref().unwrap().input.transport(),
            AudioInputTransport::DirectPcm
        );
        assert!(sender.stats().enabled);
    }
}
