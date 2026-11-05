use gpio_cdev::{Chip, EventRequestFlags, EventType, LineEvent, LineEventHandle, LineRequestFlags};
use std::collections::HashSet;
use std::os::fd::AsRawFd;

const BUTTON_DEBOUNCE_NS: u64 = 150_000_000;
const GPIO_EDGE_DEBOUNCE_NS: u64 = 1_000_000;
// Linux GPIO v1 UAPI defines BIAS_PULL_UP as bit 5; gpio-cdev 0.6 does not name it.
const GPIOHANDLE_REQUEST_BIAS_PULL_UP: u32 = 1 << 5;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct EncoderPins {
    pub a: u32,
    pub b: u32,
    pub button: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum EncoderEvent {
    Rotate { encoder: u8, step: i32 },
    Press { encoder: u8 },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LineRole {
    A,
    B,
    Button,
}

struct InputLine {
    offset: u32,
    encoder: u8,
    role: LineRole,
    handle: LineEventHandle,
    last_event_ns: u64,
}

#[derive(Clone, Copy, Debug, Default)]
struct EncoderState {
    previous_state: u8,
    accumulated_steps: i8,
    last_button_press_ns: u64,
}

pub(crate) struct EncoderBackend {
    lines: Vec<InputLine>,
    states: [Option<EncoderState>; 2],
}

impl EncoderBackend {
    pub fn open(
        chip_path: &str,
        encoder1: Option<EncoderPins>,
        encoder2: Option<EncoderPins>,
    ) -> Result<Self, String> {
        let mut offsets = HashSet::new();
        for pins in [encoder1, encoder2].into_iter().flatten() {
            for offset in [pins.a, pins.b, pins.button] {
                if !offsets.insert(offset) {
                    return Err(format!(
                        "GPIO encoder pin {offset} is assigned more than once"
                    ));
                }
            }
        }
        let Some(_) = encoder1.or(encoder2) else {
            return Ok(Self {
                lines: Vec::new(),
                states: [None, None],
            });
        };

        let mut chip = Chip::new(chip_path)
            .map_err(|error| format!("open GPIO encoder chip {chip_path}: {error}"))?;
        let mut lines = Vec::with_capacity(offsets.len());
        for (encoder, pins) in [(0, encoder1), (1, encoder2)] {
            let Some(pins) = pins else { continue };
            for (offset, role) in [
                (pins.a, LineRole::A),
                (pins.b, LineRole::B),
                (pins.button, LineRole::Button),
            ] {
                let line = chip
                    .get_line(offset)
                    .map_err(|error| format!("open GPIO encoder pin {offset}: {error}"))?;
                let flags = LineRequestFlags::INPUT
                    | LineRequestFlags::from_bits_retain(GPIOHANDLE_REQUEST_BIAS_PULL_UP);
                let handle = line
                    .events(flags, EventRequestFlags::BOTH_EDGES, "lumistripe_encoder")
                    .map_err(|error| format!("request GPIO encoder pin {offset}: {error}"))?;
                lines.push(InputLine {
                    offset,
                    encoder,
                    role,
                    handle,
                    last_event_ns: 0,
                });
            }
        }

        let mut states = [None, None];
        for encoder in 0..2 {
            if lines.iter().any(|line| line.encoder == encoder) {
                let a = read_pin(&lines, encoder, LineRole::A)?;
                let b = read_pin(&lines, encoder, LineRole::B)?;
                states[usize::from(encoder)] = Some(EncoderState {
                    previous_state: (a << 1) | b,
                    ..EncoderState::default()
                });
            }
        }
        Ok(Self { lines, states })
    }

    pub fn is_enabled(&self) -> bool {
        !self.lines.is_empty()
    }

    pub fn read_events(&mut self) -> Result<Vec<EncoderEvent>, String> {
        let mut events = Vec::new();
        loop {
            let mut pending = Vec::new();
            for line_index in 0..self.lines.len() {
                let mut poll = libc::pollfd {
                    fd: self.lines[line_index].handle.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                };
                let ready = unsafe { libc::poll(&mut poll, 1, 0) };
                if ready < 0 {
                    let error = std::io::Error::last_os_error();
                    if error.kind() == std::io::ErrorKind::Interrupted {
                        break;
                    }
                    return Err(format!(
                        "poll GPIO encoder pin {}: {error}",
                        self.lines[line_index].offset
                    ));
                }
                if ready == 0 {
                    continue;
                }
                if poll.revents & libc::POLLIN == 0 {
                    return Err(format!(
                        "GPIO encoder pin {} reported an unexpected event",
                        self.lines[line_index].offset
                    ));
                }
                let event = self.lines[line_index].handle.get_event().map_err(|error| {
                    format!(
                        "read GPIO encoder pin {}: {error}",
                        self.lines[line_index].offset
                    )
                })?;
                if accept_gpio_edge(&mut self.lines[line_index].last_event_ns, event.timestamp()) {
                    pending.push((event.timestamp(), line_index, event));
                }
            }
            if pending.is_empty() {
                break;
            }
            pending.sort_by_key(|(timestamp, _, _)| *timestamp);
            for (_, line_index, event) in pending {
                if let Some(decoded) = self.decode_event(line_index, event)? {
                    events.push(decoded);
                }
            }
        }
        Ok(events)
    }

    fn decode_event(
        &mut self,
        line_index: usize,
        event: LineEvent,
    ) -> Result<Option<EncoderEvent>, String> {
        let line = &self.lines[line_index];
        let encoder = line.encoder;
        let role = line.role;
        if role == LineRole::Button {
            let state = self.states[usize::from(encoder)]
                .as_mut()
                .expect("a configured encoder has decoder state");
            let accepted = accept_button_press(
                state,
                event.timestamp(),
                event.event_type() == EventType::FallingEdge,
            );
            return Ok(accepted.then_some(EncoderEvent::Press { encoder }));
        }

        let a = read_pin(&self.lines, encoder, LineRole::A)?;
        let b = read_pin(&self.lines, encoder, LineRole::B)?;
        let current = (a << 1) | b;
        let state = self.states[usize::from(encoder)]
            .as_mut()
            .expect("a configured encoder has decoder state");
        if current == state.previous_state {
            return Ok(None);
        }
        let previous = state.previous_state;
        state.previous_state = current;
        let delta = transition(previous, current);
        if delta == 0 {
            state.accumulated_steps = 0;
            return Ok(None);
        }
        state.accumulated_steps += delta;
        if state.accumulated_steps.abs() < 4 {
            return Ok(None);
        }
        let step = i32::from(state.accumulated_steps.signum());
        state.accumulated_steps = 0;
        Ok(Some(EncoderEvent::Rotate { encoder, step }))
    }
}

fn read_pin(lines: &[InputLine], encoder: u8, role: LineRole) -> Result<u8, String> {
    let line = lines
        .iter()
        .find(|line| line.encoder == encoder && line.role == role)
        .expect("configured encoder includes all three input pins");
    line.handle
        .get_value()
        .map_err(|error| format!("read GPIO encoder pin {}: {error}", line.offset))
}

fn transition(previous: u8, current: u8) -> i8 {
    match (previous, current) {
        (0, 1) | (1, 3) | (3, 2) | (2, 0) => 1,
        (0, 2) | (2, 3) | (3, 1) | (1, 0) => -1,
        _ => 0,
    }
}

fn accept_button_press(state: &mut EncoderState, timestamp_ns: u64, falling_edge: bool) -> bool {
    if !falling_edge || timestamp_ns.saturating_sub(state.last_button_press_ns) < BUTTON_DEBOUNCE_NS
    {
        return false;
    }
    state.last_button_press_ns = timestamp_ns;
    true
}

fn accept_gpio_edge(last_event_ns: &mut u64, timestamp_ns: u64) -> bool {
    if timestamp_ns.saturating_sub(*last_event_ns) < GPIO_EDGE_DEBOUNCE_NS {
        return false;
    }
    *last_event_ns = timestamp_ns;
    true
}

#[cfg(test)]
mod tests {
    use super::{EncoderState, accept_button_press, accept_gpio_edge, transition};

    fn decode_rotation(state: &mut EncoderState, current: u8) -> Option<i32> {
        if current == state.previous_state {
            return None;
        }
        let previous = state.previous_state;
        state.previous_state = current;
        let delta = transition(previous, current);
        if delta == 0 {
            state.accumulated_steps = 0;
            return None;
        }
        state.accumulated_steps += delta;
        if state.accumulated_steps.abs() < 4 {
            return None;
        }
        let step = i32::from(state.accumulated_steps.signum());
        state.accumulated_steps = 0;
        Some(step)
    }

    #[test]
    fn quadrature_decoder_emits_one_step_per_complete_detent() {
        let mut state = EncoderState::default();
        let clockwise = [1, 3, 2, 0];
        let events = clockwise
            .into_iter()
            .filter_map(|next| decode_rotation(&mut state, next))
            .collect::<Vec<_>>();
        assert_eq!(events, [1]);

        let counter_clockwise = [2, 3, 1, 0];
        let events = counter_clockwise
            .into_iter()
            .filter_map(|next| decode_rotation(&mut state, next))
            .collect::<Vec<_>>();
        assert_eq!(events, [-1]);
    }

    #[test]
    fn invalid_quadrature_transition_resets_partial_steps() {
        let mut state = EncoderState::default();
        assert_eq!(decode_rotation(&mut state, 1), None);
        assert_eq!(decode_rotation(&mut state, 2), None);
        assert_eq!(state.accumulated_steps, 0);
    }

    #[test]
    fn button_press_requires_falling_edge_and_debounces_repeated_events() {
        let mut state = EncoderState::default();
        assert!(!accept_button_press(&mut state, 1_000_000_000, false));
        assert!(accept_button_press(&mut state, 1_000_000_000, true));
        assert!(!accept_button_press(&mut state, 1_100_000_000, true));
        assert!(accept_button_press(&mut state, 1_150_000_000, true));
    }

    #[test]
    fn gpio_edges_use_the_same_one_millisecond_debounce_window() {
        let mut last = 2_000_000_000;
        assert!(!accept_gpio_edge(&mut last, 2_000_500_000));
        assert_eq!(last, 2_000_000_000);
        assert!(accept_gpio_edge(&mut last, 2_001_000_000));
        assert_eq!(last, 2_001_000_000);
    }
}
