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
const DIRECT_PCM_RECOVERY_PROBE_INTERVAL_NS: u64 = 5_000_000_000;
const DIRECT_PCM_RECOVERY_PROBE_TIMEOUT_NS: u64 = 3_000_000_000;
const DIRECT_PCM_RECOVERY_CONFIRM_BATCHES: usize = 8;
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
    timestamp_sample_position_end: Option<u64>,
    timestamp_end_ns: Option<u64>,
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
    next_direct_probe_ns: u64,
    direct_probe_generation: Option<u64>,
    direct_probe_started_ns: u64,
    direct_probe_expected_position: Option<u64>,
    direct_probe_contiguous_batches: usize,
    last_direct_playback_state: Option<PcmPlaybackState>,
    direct_playback_started_ns: Option<u64>,
    pcm_ingress: PcmIngressSender,
    pcm_receiver: PcmIngressReceiver,
    capture_factory: Arc<dyn AudioCaptureFactory>,
    dsp_timing_ns: [u64; DSP_TIMING_WINDOW],
    dsp_timing_count: usize,
    dsp_timing_cursor: usize,
    retry_at_ns: u64,
    last_error: Option<String>,
    audio_update_sequence: u64,
    last_audio_update_timestamp_ns: u64,
    last_feature_timestamp_ns: Option<u64>,
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
            next_direct_probe_ns: 0,
            direct_probe_generation: None,
            direct_probe_started_ns: 0,
            direct_probe_expected_position: None,
            direct_probe_contiguous_batches: 0,
            last_direct_playback_state: None,
            direct_playback_started_ns: None,
            pcm_ingress,
            pcm_receiver,
            capture_factory,
            dsp_timing_ns: [0; DSP_TIMING_WINDOW],
            dsp_timing_count: 0,
            dsp_timing_cursor: 0,
            retry_at_ns: 0,
            last_error: None,
            audio_update_sequence: 0,
            last_audio_update_timestamp_ns: 0,
            last_feature_timestamp_ns: None,
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
                state.clear_output_audio(&output);
                write_status(&state, &status);
                let _ = reply.send(result);
            }
            Ok(AudioCommand::StartDirect {
                fallback_selector,
                config,
                reply,
            }) => {
                let result = state.start_direct(fallback_selector, config);
                state.clear_output_audio(&output);
                write_status(&state, &status);
                let _ = reply.send(result);
            }
            Ok(AudioCommand::Configure { config, reply }) => {
                let result = state.configure(config);
                state.clear_output_audio(&output);
                write_status(&state, &status);
                let _ = reply.send(result);
            }
            Ok(AudioCommand::Stop { reply }) => {
                state.stop();
                state.clear_output_audio(&output);
                write_status(&state, &status);
                let _ = reply.send(());
            }
            Ok(AudioCommand::Shutdown { reply }) => {
                state.stop();
                state.clear_output_audio(&output);
                write_status(&state, &status);
                let _ = reply.send(());
                shutdown = true;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                state.stop();
                state.clear_output_audio(&output);
                shutdown = true;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }

        if !shutdown {
            state.process_available_audio(&output);
            state.process_demo_audio(&output);
            state.recover_stalled_capture(&output);
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
        self.reset_direct_probe(0);
        self.last_direct_playback_state = None;
        self.direct_playback_started_ns = None;
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
        self.reset_direct_probe(0);
        self.last_direct_playback_state = None;
        self.direct_playback_started_ns = None;
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
        self.reset_direct_probe(0);
        self.last_direct_playback_state = None;
        self.direct_playback_started_ns = None;
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

    fn clear_output_audio(&mut self, output: &OutputAudioHandle) {
        if self.audio_update_sequence == u64::MAX {
            self.dropped_audio_updates = self.dropped_audio_updates.saturating_add(1);
            return;
        }
        self.audio_update_sequence += 1;
        let timestamp_ns = monotonic_time_ns().max(self.last_audio_update_timestamp_ns);
        self.last_audio_update_timestamp_ns = timestamp_ns;
        self.last_feature_timestamp_ns = Some(timestamp_ns);
        match output.submit_audio_update(RenderAudioUpdate {
            sequence: self.audio_update_sequence,
            timestamp_ns,
            audio: None,
        }) {
            Ok(_) | Err(crate::FrameAckStatus::NotConfigured) => {}
            Err(crate::FrameAckStatus::ShuttingDown) => {}
            Err(crate::FrameAckStatus::InvalidFrame | crate::FrameAckStatus::Accepted) => {
                self.dropped_audio_updates = self.dropped_audio_updates.saturating_add(1);
            }
        }
    }

    fn reset_direct_probe(&mut self, next_probe_ns: u64) {
        self.direct_probe_generation = None;
        self.direct_probe_started_ns = 0;
        self.direct_probe_expected_position = None;
        self.direct_probe_contiguous_batches = 0;
        self.next_direct_probe_ns = next_probe_ns;
    }

    fn publish_error(&mut self, error: Option<String>) {
        self.last_error = error;
    }

    fn open_session(&mut self) -> Result<bool, String> {
        let result = if self.direct_requested && !self.fallback_active {
            self.pcm_ingress.set_enabled(true);
            self.pcm_receiver.discard_queued();
            self.pcm_ingress.begin_generation();
            self.build_direct_session()
        } else {
            self.pcm_ingress.set_enabled(false);
            self.pcm_receiver.discard_queued();
            self.reset_direct_probe(if self.direct_requested {
                monotonic_time_ns().saturating_add(DIRECT_PCM_RECOVERY_PROBE_INTERVAL_NS)
            } else {
                0
            });
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

    fn build_direct_session(&self) -> Result<AudioSession, String> {
        let processor = AudioProcessor::new(self.config, AUDIO_SAMPLE_RATE)
            .map_err(|error| error.to_string())?;
        Ok(AudioSession {
            input: AudioInput::DirectPcm,
            processor,
            tracker: MusicStructureTracker::new(),
            sample_rate: AUDIO_SAMPLE_RATE as u32,
            stream_generation: Some(self.pcm_ingress.generation()),
            sample_position_origin: None,
            timestamp_sample_position_end: None,
            timestamp_end_ns: None,
            last_input_timestamp_ns: None,
            frame: AudioFrame::default(),
            features: MusicFeatures::default(),
            structure: MusicStructureState::default(),
            opened_at_ns: monotonic_time_ns(),
            last_frame_ns: None,
        })
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
            timestamp_sample_position_end: None,
            timestamp_end_ns: None,
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
        let direct_pcm = self
            .session
            .as_ref()
            .is_some_and(|session| matches!(&session.input, AudioInput::DirectPcm));
        if direct_pcm {
            let generation = batch.stream_generation;
            let ingress = self.pcm_ingress.clone();
            if ingress
                .process_current_generation(generation, || self.process_batch_inner(output, batch))
                .is_none()
            {
                self.pending_beats.clear();
                self.pending_structure.clear();
                self.clear_output_audio(output);
            }
            return;
        }
        self.process_batch_inner(output, batch);
    }

    fn process_batch_inner(&mut self, output: &OutputAudioHandle, batch: AudioPcmBatch) {
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
            session.timestamp_sample_position_end = None;
            session.timestamp_end_ns = None;
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
        let direct_pcm = matches!(&session.input, AudioInput::DirectPcm);
        let Some(relative_sample_position) =
            batch.sample_position.checked_sub(sample_position_origin)
        else {
            self.pcm_ingress.record_discontinuity(batch.len as u64);
            return;
        };
        let seen = session.processor.stats().samples_seen;
        if relative_sample_position < seen {
            self.pcm_ingress.record_discontinuity(batch.len as u64);
            return;
        }
        if relative_sample_position > seen {
            if direct_pcm && batch.dropped_samples_before == 0 {
                self.pcm_ingress
                    .record_discontinuity(relative_sample_position - seen);
            }
            self.pending_beats.clear();
            self.pending_structure.clear();
            session.tracker.reset();
            session.structure = MusicStructureState::default();
            if let Err(error) = session
                .processor
                .mark_discontinuity(relative_sample_position - seen)
            {
                self.publish_error(Some(format!(
                    "audio sample gap could not be applied: {error}"
                )));
                return;
            }
        }
        let capture_end_ns = if direct_pcm {
            let Some(batch_end_position) = batch.sample_position.checked_add(batch.len as u64)
            else {
                self.pcm_ingress.record_discontinuity(batch.len as u64);
                return;
            };
            if session
                .timestamp_sample_position_end
                .is_some_and(|previous_end| batch.sample_position < previous_end)
            {
                self.pcm_ingress.record_discontinuity(batch.len as u64);
                return;
            }
            let projected_end_ns = match (
                session.timestamp_sample_position_end,
                session.timestamp_end_ns,
            ) {
                (Some(previous_end_position), Some(previous_end_ns)) => {
                    let elapsed_ns = ((batch_end_position - previous_end_position) as u128
                        * 1_000_000_000_u128
                        / u128::from(batch.sample_rate))
                    .min(u64::MAX as u128) as u64;
                    previous_end_ns.saturating_add(elapsed_ns)
                }
                _ => batch.capture_end_ns,
            };
            let capture_end_ns = batch
                .capture_end_ns
                .max(projected_end_ns)
                .max(session.last_input_timestamp_ns.unwrap_or_default());
            session.timestamp_sample_position_end = Some(batch_end_position);
            session.timestamp_end_ns = Some(capture_end_ns);
            capture_end_ns
        } else {
            batch
                .capture_end_ns
                .max(session.last_input_timestamp_ns.unwrap_or_default())
        };

        let mut observed: [Option<ObservedFrame>; 8] = [None; 8];
        let mut observed_count = 0;
        let mut structure = session.structure;
        let tracker = &mut session.tracker;
        session.last_input_timestamp_ns = Some(capture_end_ns);
        let capture_end_seconds = capture_end_ns as f64 / 1_000_000_000.0;
        let mut tracker_error = None;
        let processing_started = std::time::Instant::now();
        let result = session.processor.feed_samples_with_observer(
            &batch.samples[..batch.len],
            capture_end_seconds,
            |frame, features| {
                if tracker_error.is_some() {
                    return;
                }
                let update = match tracker.update(&features, frame.timestamp) {
                    Ok(update) => update,
                    Err(error) => {
                        tracker_error = Some(error.to_string());
                        return;
                    }
                };
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
        if let Some(error) = tracker_error {
            self.pending_beats.clear();
            self.pending_structure.clear();
            let mut ignored_events = [AudioBeatEvent::default(); MAX_AUDIO_BEAT_EVENTS];
            let _ = session
                .processor
                .drain_beat_events_into(&mut ignored_events);
            session.tracker.reset();
            session.structure = MusicStructureState::default();
            self.publish_error(Some(format!(
                "Music structure timestamps regressed; tracker reset: {error}"
            )));
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
            let event_timestamp_ns = seconds_to_ns(event.timestamp)
                .max(self.last_feature_timestamp_ns.unwrap_or_default());
            if self.pending_beats.len() == AUDIO_EVENT_HISTORY_CAPACITY {
                self.pending_beats.pop_front();
                self.dropped_audio_updates = self.dropped_audio_updates.saturating_add(1);
            }
            self.pending_beats.push_back(RenderBeatEvent {
                sequence: self.beat_event_sequence,
                timestamp_ns: event_timestamp_ns,
                strength: event.strength as f64,
            });
        }
        session.structure = structure;

        for observed in observed.into_iter().take(observed_count).flatten() {
            self.feature_sequence = self.feature_sequence.saturating_add(1);
            let mut frame = observed.frame;
            frame.sequence = self.feature_sequence;
            let timestamp_ns = self.last_feature_timestamp_ns.map_or_else(
                || seconds_to_ns(frame.timestamp),
                |last| seconds_to_ns(frame.timestamp).max(last.saturating_add(1)),
            );
            frame.timestamp = timestamp_ns as f64 / 1_000_000_000.0;
            self.last_feature_timestamp_ns = Some(timestamp_ns);
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
        let timestamp_ns = seconds_to_ns(frame.timestamp).max(self.last_audio_update_timestamp_ns);
        self.last_audio_update_timestamp_ns = timestamp_ns;
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
            feature_timestamp_ns: Some(timestamp_ns),
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
            timestamp_ns,
            audio: Some(audio),
        }) {
            Ok(_) | Err(crate::FrameAckStatus::NotConfigured) => {}
            Err(crate::FrameAckStatus::ShuttingDown) => {}
            Err(crate::FrameAckStatus::InvalidFrame | crate::FrameAckStatus::Accepted) => {
                self.dropped_audio_updates = self.dropped_audio_updates.saturating_add(1);
            }
        }
    }

    fn recover_stalled_capture(&mut self, output: &OutputAudioHandle) {
        self.recover_stalled_capture_at(output, monotonic_time_ns());
    }

    fn recover_stalled_capture_at(&mut self, output: &OutputAudioHandle, now: u64) {
        if self.session.as_ref().is_some_and(|session| {
            matches!(&session.input, AudioInput::DirectPcm)
                && session.stream_generation != Some(self.pcm_ingress.generation())
        }) {
            self.pending_beats.clear();
            self.pending_structure.clear();
            match self.build_direct_session() {
                Ok(session) => {
                    self.session = Some(session);
                    self.publish_error(None);
                    self.clear_output_audio(output);
                }
                Err(error) => {
                    self.session = None;
                    self.publish_error(Some(format!(
                        "Spotify decoded PCM session could not restart: {error}"
                    )));
                }
            }
        }

        let playback = self.pcm_ingress.stats().playback_state;
        if self.direct_requested {
            if playback == PcmPlaybackState::Playing
                && self.last_direct_playback_state != Some(PcmPlaybackState::Playing)
            {
                self.direct_playback_started_ns = Some(now);
            } else if playback != PcmPlaybackState::Playing {
                self.direct_playback_started_ns = None;
            }
        }
        if self.direct_requested
            && matches!(playback, PcmPlaybackState::Paused | PcmPlaybackState::Idle)
            && self.last_direct_playback_state != Some(playback)
        {
            self.pending_beats.clear();
            self.pending_structure.clear();
            self.clear_output_audio(output);
        }
        self.last_direct_playback_state = Some(playback);

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
            let playback_started = self
                .direct_playback_started_ns
                .unwrap_or(session.opened_at_ns);
            let recent_input = pcm
                .last_input_timestamp_ns
                .filter(|timestamp| *timestamp >= playback_started);
            // Give resumed playback a first-batch window; pre-pause timestamps are stale.
            let (reference_timestamp, timeout) = recent_input.map_or(
                (playback_started, DIRECT_PCM_FIRST_BATCH_TIMEOUT_NS),
                |timestamp| (timestamp, DIRECT_PCM_STALL_TIMEOUT_NS),
            );
            now.saturating_sub(reference_timestamp) > timeout
        });
        if direct_stalled && now >= self.retry_at_ns {
            self.activate_monitor_fallback(now, output);
        }

        let capture_stalled = self.session.as_ref().and_then(|session| {
            session.input.capture().map(|capture| {
                let stats = capture.stats();
                match stats.last_callback_ns {
                    Some(last) => now.saturating_sub(last) > AUDIO_STALL_TIMEOUT_NS,
                    None => {
                        now.saturating_sub(session.opened_at_ns) > AUDIO_INITIAL_CALLBACK_TIMEOUT_NS
                    }
                }
            })
        });
        if capture_stalled == Some(true) {
            let monitor_fallback = self
                .session
                .as_ref()
                .is_some_and(|session| matches!(&session.input, AudioInput::MonitorFallback(_)));
            self.session = None;
            self.pending_beats.clear();
            self.pending_structure.clear();
            self.retry_at_ns = now.saturating_add(AUDIO_RECOVERY_DELAY_NS);
            self.publish_error(Some(if monitor_fallback {
                "Spotify monitor fallback stopped producing callbacks; retrying".to_owned()
            } else {
                "audio capture stopped producing callbacks; retrying".to_owned()
            }));
            self.clear_output_audio(output);
        }

        if self.direct_requested && self.fallback_active {
            self.probe_direct_recovery(now, output);
        }

        let waiting_for_probe = self.direct_probe_generation.is_some();
        if self.desired_active
            && !self.demo_active
            && self.session.is_none()
            && now >= self.retry_at_ns
            && !waiting_for_probe
        {
            if let Err(error) = self.open_session() {
                self.publish_error(Some(format!("audio device recovery failed: {error}")));
            }
        }
    }

    fn probe_direct_recovery(&mut self, now: u64, output: &OutputAudioHandle) {
        let playback = self.pcm_ingress.stats().playback_state;
        if playback != PcmPlaybackState::Playing {
            if self.direct_probe_generation.is_some() {
                self.pcm_ingress.set_enabled(false);
                self.pcm_receiver.discard_queued();
                self.reset_direct_probe(now.saturating_add(DIRECT_PCM_RECOVERY_PROBE_INTERVAL_NS));
            }
            return;
        }

        if self.direct_probe_generation.is_none() && now >= self.next_direct_probe_ns {
            self.pcm_ingress.set_enabled(true);
            self.pcm_receiver.discard_queued();
            self.direct_probe_generation = Some(self.pcm_ingress.generation());
            self.direct_probe_started_ns = now;
            self.direct_probe_expected_position = None;
            self.direct_probe_contiguous_batches = 0;
        }

        let Some(probe_generation) = self.direct_probe_generation else {
            return;
        };
        if probe_generation != self.pcm_ingress.generation() {
            self.direct_probe_generation = Some(self.pcm_ingress.generation());
            self.direct_probe_started_ns = now;
            self.direct_probe_expected_position = None;
            self.direct_probe_contiguous_batches = 0;
        }

        while let Some(batch) = self.pcm_receiver.try_pop() {
            if batch.stream_generation != self.pcm_ingress.generation() {
                continue;
            }
            let contiguous = batch.dropped_samples_before == 0
                && self
                    .direct_probe_expected_position
                    .is_none_or(|expected| expected == batch.sample_position);
            let Some(end_position) = batch.sample_position.checked_add(batch.len as u64) else {
                self.direct_probe_expected_position = None;
                self.direct_probe_contiguous_batches = 0;
                continue;
            };
            self.direct_probe_contiguous_batches = if contiguous {
                self.direct_probe_contiguous_batches.saturating_add(1)
            } else {
                1
            };
            self.direct_probe_expected_position = Some(end_position);
            if self.direct_probe_contiguous_batches >= DIRECT_PCM_RECOVERY_CONFIRM_BATCHES {
                self.activate_direct_after_probe(now, output);
                return;
            }
        }

        let stats = self.pcm_ingress.stats();
        let last_input = stats
            .last_input_timestamp_ns
            .unwrap_or(self.direct_probe_started_ns);
        let input_is_current = last_input >= self.direct_probe_started_ns;
        let probe_failed = now.saturating_sub(self.direct_probe_started_ns)
            > DIRECT_PCM_RECOVERY_PROBE_TIMEOUT_NS
            || (input_is_current && now.saturating_sub(last_input) > DIRECT_PCM_STALL_TIMEOUT_NS);
        if probe_failed {
            self.pcm_ingress.set_enabled(false);
            self.pcm_receiver.discard_queued();
            self.reset_direct_probe(now.saturating_add(DIRECT_PCM_RECOVERY_PROBE_INTERVAL_NS));
            if self.session.is_none() {
                self.retry_at_ns = now;
            }
        }
    }

    fn activate_direct_after_probe(&mut self, now: u64, output: &OutputAudioHandle) {
        self.pcm_ingress.begin_generation();
        self.pcm_receiver.discard_queued();
        self.pending_beats.clear();
        self.pending_structure.clear();
        match self.build_direct_session() {
            Ok(session) => {
                self.session = Some(session);
                self.fallback_active = false;
                self.retry_at_ns = 0;
                self.reset_direct_probe(now.saturating_add(DIRECT_PCM_RECOVERY_PROBE_INTERVAL_NS));
                self.publish_error(None);
                self.clear_output_audio(output);
            }
            Err(error) => {
                self.pcm_ingress.set_enabled(false);
                self.session = None;
                self.retry_at_ns = now.saturating_add(AUDIO_RECOVERY_DELAY_NS);
                self.reset_direct_probe(now.saturating_add(DIRECT_PCM_RECOVERY_PROBE_INTERVAL_NS));
                self.publish_error(Some(format!(
                    "Spotify decoded PCM recovery failed: {error}"
                )));
            }
        }
    }

    fn activate_monitor_fallback(&mut self, now_ns: u64, output: &OutputAudioHandle) {
        self.pcm_ingress.set_enabled(false);
        self.pcm_receiver.discard_queued();
        self.pending_beats.clear();
        self.pending_structure.clear();
        self.fallback_active = true;
        self.reset_direct_probe(now_ns.saturating_add(DIRECT_PCM_RECOVERY_PROBE_INTERVAL_NS));
        self.clear_output_audio(output);
        match self.build_capture_session(AudioInputTransport::MonitorFallback) {
            Ok(session) => {
                self.session = Some(session);
                self.retry_at_ns = 0;
                self.publish_error(Some(
                    "Spotify decoded PCM is unavailable; using PipeWire monitor capture."
                        .to_owned(),
                ));
            }
            Err(error) => {
                self.session = None;
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
        AudioBatchSource, AudioCaptureFactory, AudioInput, PendingStructureEdge, WorkerState,
        demo_snapshot, write_status,
    };
    use crate::OutputWorker;
    use lumistripe_engine_audio::{
        AudioCaptureStats, AudioConfig, AudioPcmBatch, InputAudioDevice, PcmIngressReceiver,
        PcmIngressSender, PcmPlaybackState, bounded_pcm_ingress, monotonic_time_ns,
    };
    use lumistripe_engine_protocol::{
        AudioInputTransport, EngineAudioStatus, RenderBeatEvent, RenderMusicStructurePhase,
    };
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
        let output = OutputWorker::new().unwrap();
        let output_audio = output.audio_handle();
        state.recover_stalled_capture(&output_audio);

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
        let _ = output.shutdown();
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
        let output = OutputWorker::new().unwrap();
        let output_audio = output.audio_handle();
        state.recover_stalled_capture(&output_audio);

        assert!(!state.fallback_active);
        assert_eq!(
            state.session.as_ref().unwrap().input.transport(),
            AudioInputTransport::DirectPcm
        );
        assert!(sender.stats().enabled);
        let paused_generation = sender.generation();
        sender.set_playback_state(PcmPlaybackState::Playing);
        state.recover_stalled_capture_at(&output_audio, monotonic_time_ns());
        assert_eq!(sender.generation(), paused_generation);
        assert!(!state.fallback_active);
        assert!(matches!(
            state.session.as_ref().unwrap().input,
            AudioInput::DirectPcm
        ));
        let _ = output.shutdown();
    }

    #[test]
    fn resumed_direct_input_gets_first_batch_timeout_after_a_long_pause() {
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
        let output = OutputWorker::new().unwrap();
        let output_audio = output.audio_handle();
        let initial_playback_start = monotonic_time_ns();

        sender.set_playback_state(PcmPlaybackState::Playing);
        state.recover_stalled_capture_at(&output_audio, initial_playback_start);
        let generation = sender.generation();
        assert!(sender.submit_interleaved_f64_at(
            &[0.2; 512],
            44_100,
            1,
            generation,
            0,
            initial_playback_start + 1,
        ));
        state.process_available_audio(&output_audio);

        let resumed_playback_start = initial_playback_start + 60_000_000_000;
        sender.set_playback_state(PcmPlaybackState::Paused);
        state.recover_stalled_capture_at(&output_audio, resumed_playback_start - 10_000_000_000);
        sender.set_playback_state(PcmPlaybackState::Playing);
        state.recover_stalled_capture_at(&output_audio, resumed_playback_start);

        state.recover_stalled_capture_at(
            &output_audio,
            resumed_playback_start + super::DIRECT_PCM_FIRST_BATCH_TIMEOUT_NS - 1,
        );
        assert!(!state.fallback_active);
        assert!(matches!(
            state.session.as_ref().unwrap().input,
            AudioInput::DirectPcm
        ));

        state.recover_stalled_capture_at(
            &output_audio,
            resumed_playback_start + super::DIRECT_PCM_FIRST_BATCH_TIMEOUT_NS + 1,
        );
        assert!(state.fallback_active);
        let _ = output.shutdown();
    }

    #[test]
    fn switching_to_capture_and_back_invalidates_old_direct_pcm() {
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
        let old_generation = sender.generation();
        assert!(sender.submit_interleaved_f64_at(
            &[0.25, 0.25],
            44_100,
            2,
            old_generation,
            0,
            monotonic_time_ns(),
        ));

        assert!(
            state
                .start(Some("mock:microphone".to_owned()), AudioConfig::default())
                .unwrap()
        );
        assert!(!state.direct_requested);
        assert!(!sender.stats().enabled);
        assert_eq!(
            state.session.as_ref().unwrap().input.transport(),
            AudioInputTransport::CaptureDevice
        );
        assert!(sender.stats().stale_samples >= 1);

        assert!(
            state
                .start_direct(
                    Some("mock:spotify-monitor".to_owned()),
                    AudioConfig::default()
                )
                .unwrap()
        );
        assert!(sender.generation() > old_generation);
        assert!(!sender.submit_interleaved_f64_at(
            &[0.5, 0.5],
            44_100,
            2,
            old_generation,
            1,
            monotonic_time_ns().saturating_add(1),
        ));
        assert_eq!(sender.stats().queue_occupancy, 0);
        assert_eq!(
            state
                .session
                .as_ref()
                .unwrap()
                .processor
                .stats()
                .samples_seen,
            0
        );
    }

    #[test]
    fn direct_pcm_overlap_is_dropped_and_forward_gap_is_counted_once() {
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
        let output = OutputWorker::new().unwrap();
        let output_audio = output.audio_handle();
        let generation = sender.generation();
        let packet = [0.2_f64; 512];
        let timestamp = monotonic_time_ns();

        assert!(sender.submit_interleaved_f64_at(&packet, 44_100, 1, generation, 0, timestamp,));
        state.process_available_audio(&output_audio);
        assert_eq!(
            state
                .session
                .as_ref()
                .unwrap()
                .processor
                .stats()
                .samples_seen,
            512
        );

        assert!(
            sender.submit_interleaved_f64_at(&packet, 44_100, 1, generation, 0, timestamp + 1,)
        );
        state.process_available_audio(&output_audio);
        assert_eq!(
            state
                .session
                .as_ref()
                .unwrap()
                .processor
                .stats()
                .samples_seen,
            512
        );

        assert!(sender.submit_interleaved_f64_at(
            &packet,
            44_100,
            1,
            generation,
            1_024,
            timestamp + 2,
        ));
        state.process_available_audio(&output_audio);

        assert_eq!(
            state
                .session
                .as_ref()
                .unwrap()
                .processor
                .stats()
                .samples_seen,
            1_536
        );
        assert_eq!(sender.stats().dropped_samples, 512);
        assert_eq!(sender.stats().discontinuities, 1);
        assert_eq!(sender.stats().queue_occupancy, 0);
        let _ = output.shutdown();
    }

    #[test]
    fn monitor_fallback_probes_direct_pcm_and_clears_transition_events() {
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
        let output = OutputWorker::new().unwrap();
        let output_audio = output.audio_handle();
        state.pending_beats.push_back(RenderBeatEvent {
            sequence: 1,
            timestamp_ns: 1,
            strength: 1.0,
        });
        state.pending_structure.push_back(PendingStructureEdge {
            sequence: 1,
            timestamp_ns: 1,
            phase: RenderMusicStructurePhase::Drop,
            confidence: 1.0,
            buildup_progress: 0.0,
            drop_event: true,
            section_change: false,
            phase_change: true,
        });

        let now = monotonic_time_ns();
        sender.set_playback_state(PcmPlaybackState::Failed);
        state.recover_stalled_capture_at(&output_audio, now);
        assert!(state.fallback_active);
        assert!(state.pending_beats.is_empty());
        assert!(state.pending_structure.is_empty());
        assert_eq!(state.audio_update_sequence, 1);
        assert!(matches!(
            state.session.as_ref().unwrap().input,
            AudioInput::MonitorFallback(_)
        ));

        sender.set_playback_state(PcmPlaybackState::Playing);
        state.next_direct_probe_ns = now;
        state.recover_stalled_capture_at(&output_audio, now);
        assert!(state.direct_probe_generation.is_some());
        let probe_generation = sender.generation();
        let probe_packet = [0.2_f64; 512];
        for batch_index in 0..super::DIRECT_PCM_RECOVERY_CONFIRM_BATCHES {
            assert!(sender.submit_interleaved_f64_at(
                &probe_packet,
                44_100,
                1,
                probe_generation,
                (batch_index * probe_packet.len()) as u64,
                now + batch_index as u64 + 1,
            ));
        }
        state.recover_stalled_capture_at(&output_audio, now + 100_000_000);

        assert!(!state.fallback_active);
        assert!(state.direct_probe_generation.is_none());
        assert!(matches!(
            state.session.as_ref().unwrap().input,
            AudioInput::DirectPcm
        ));
        assert_eq!(
            state
                .session
                .as_ref()
                .unwrap()
                .processor
                .stats()
                .samples_seen,
            0
        );
        assert!(state.pending_beats.is_empty());
        assert!(state.pending_structure.is_empty());
        assert_eq!(state.audio_update_sequence, 2);
        assert_eq!(sender.stats().queue_occupancy, 0);

        let generation = sender.generation();
        assert!(sender.submit_interleaved_f64_at(
            &probe_packet,
            44_100,
            1,
            generation,
            0,
            now + 200_000_000,
        ));
        state.process_available_audio(&output_audio);
        assert_eq!(
            state
                .session
                .as_ref()
                .unwrap()
                .processor
                .stats()
                .samples_seen,
            512
        );
        let _ = output.shutdown();
    }

    #[test]
    fn failed_recovery_probe_keeps_monitor_as_the_only_analysis_source() {
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
        let output = OutputWorker::new().unwrap();
        let output_audio = output.audio_handle();
        let now = monotonic_time_ns();
        sender.set_playback_state(PcmPlaybackState::Failed);
        state.recover_stalled_capture_at(&output_audio, now);
        sender.set_playback_state(PcmPlaybackState::Playing);
        state.next_direct_probe_ns = now;
        state.recover_stalled_capture_at(&output_audio, now);

        state.direct_probe_started_ns =
            now.saturating_sub(super::DIRECT_PCM_RECOVERY_PROBE_TIMEOUT_NS + 1);
        state.recover_stalled_capture_at(&output_audio, now);

        assert!(state.fallback_active);
        assert!(state.direct_probe_generation.is_none());
        assert!(matches!(
            state.session.as_ref().unwrap().input,
            AudioInput::MonitorFallback(_)
        ));
        assert!(!sender.stats().enabled);
        assert!(
            state
                .last_error
                .as_deref()
                .is_some_and(|error| { error.contains("using PipeWire monitor capture") })
        );
        let _ = output.shutdown();
    }
}
