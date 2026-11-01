//! Native Linux SPI/GPIO drivers plus a hardware-free mock output.

use gpio_cdev::{Chip, LineRequestFlags};
use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
use spidev::{SpiModeFlags, Spidev, SpidevOptions};
use std::hint::black_box;
use std::io::{self, Write};
use std::ptr;
use std::time::Instant;

const DEFAULT_SPI_BUFFER_BYTES: usize = 4096;
const OUTPUT_WRITE_ATTEMPTS: usize = 2;
const BCM_GPIOMEM_PATH: &str = "/dev/gpiomem";
const RP1_GPIOMEM_PATH: &str = "/dev/gpiomem0";
const BCM_GPIO_MAP_BYTES: usize = 4096;
const RP1_GPIO_MAP_BYTES: usize = 0x30000;
const GPSET0: usize = 0x1c / 4;
const GPSET1: usize = 0x20 / 4;
const GPCLR0: usize = 0x28 / 4;
const GPCLR1: usize = 0x2c / 4;
const RP1_IO_BANK_OFFSETS: [usize; 3] = [0x0000, 0x4000, 0x8000];
const RP1_RIO_BANK_OFFSETS: [usize; 3] = [0x10000, 0x14000, 0x18000];
const RP1_GPIO_CTRL: usize = 0x04;
const RP1_GPIO_FUNCTION_SELECT_MASK: u32 = 0x1f;
const RP1_GPIO_FUNCTION_SELECT: u32 = 0x05;
const RP1_RIO_OUT: usize = 0x00;
const RP1_RIO_OE: usize = 0x04;
const RP1_REGISTER_SET: usize = 0x2000;
const RP1_REGISTER_CLEAR: usize = 0x3000;
const RP1_GPIO_CTRL_OUTOVER_MASK: u32 = 0x0000_3000;
const RP1_GPIO_CTRL_OEOVER_MASK: u32 = 0x0000_c000;
const RP1_PAD_INPUT_ENABLE: u32 = 0x40;
const RP1_PAD_OUTPUT_DISABLE: u32 = 0x80;

pub struct OutputSet {
    config: EngineConfig,
    outputs: Vec<ManagedOutput>,
    frames_rendered: u64,
    last_transmission_ms: Option<f64>,
    last_sequential_skew_ms: Option<f64>,
}

impl OutputSet {
    pub fn open(config: EngineConfig) -> Result<Self, OutputError> {
        config
            .validate()
            .map_err(|error| OutputError::InvalidConfig(error.to_string()))?;
        let mut outputs = Vec::with_capacity(config.outputs.len());
        for output in &config.outputs {
            let mut managed = ManagedOutput::open(output)?;
            managed.render(&vec![0; output.pixel_count * 4])?;
            outputs.push(managed);
        }
        Ok(Self {
            config,
            outputs,
            frames_rendered: 0,
            last_transmission_ms: None,
            last_sequential_skew_ms: None,
        })
    }

    pub fn render(&mut self, frames: &[Vec<u8>]) -> Result<u64, OutputError> {
        if frames.len() != self.outputs.len() {
            return Err(OutputError::InvalidFrame(format!(
                "frame set has {} outputs; expected {}",
                frames.len(),
                self.outputs.len()
            )));
        }
        let transmission_started_at = Instant::now();
        let mut first_completed_at = None;
        let mut last_completed_at = None;
        for (output, frame) in self.outputs.iter_mut().zip(frames) {
            output.render(frame)?;
            let completed_at = Instant::now();
            first_completed_at.get_or_insert(completed_at);
            last_completed_at = Some(completed_at);
        }
        self.last_sequential_skew_ms = first_completed_at
            .zip(last_completed_at)
            .map(|(first, last)| last.duration_since(first).as_secs_f64() * 1000.0);
        self.last_transmission_ms = Some(transmission_started_at.elapsed().as_secs_f64() * 1000.0);
        self.frames_rendered = self.frames_rendered.saturating_add(1);
        Ok(self.frames_rendered)
    }

    pub fn blackout(&mut self) -> Result<(), OutputError> {
        let mut first_error = None;
        for output in &mut self.outputs {
            let black = vec![0; output.config.pixel_count * 4];
            if let Err(error) = output.render(&black) {
                if first_error.is_none() {
                    first_error = Some(error);
                }
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    pub fn output_count(&self) -> usize {
        self.outputs.len()
    }

    pub fn pixel_counts(&self) -> Vec<usize> {
        self.config.pixel_counts()
    }

    pub fn total_pixels(&self) -> usize {
        self.config.total_pixels()
    }

    pub fn frames_rendered(&self) -> u64 {
        self.frames_rendered
    }

    pub fn last_sequential_skew_ms(&self) -> Option<f64> {
        self.last_sequential_skew_ms
    }

    pub fn last_transmission_ms(&self) -> Option<f64> {
        self.last_transmission_ms
    }
}

struct ManagedOutput {
    config: OutputConfig,
    driver: Box<dyn OutputDriver>,
}

impl ManagedOutput {
    fn open(config: &OutputConfig) -> Result<Self, OutputError> {
        let driver: Box<dyn OutputDriver> = match &config.backend {
            OutputBackend::Mock => Box::new(MockOutput::default()),
            OutputBackend::MockSpi => Box::new(MockSpiOutput::new(config.pixel_count)),
            OutputBackend::Spi { device, speed_hz } => Box::new(
                SpiOutput::open(device, *speed_hz, config.pixel_count).map_err(|error| {
                    OutputError::Device(format!("cannot open SPI device {device:?}: {error}"))
                })?,
            ),
            OutputBackend::Gpio {
                chip,
                data_pin,
                clock_pin,
            } => Box::new(
                GpioOutput::open(chip, *data_pin, *clock_pin, &config.id).map_err(|error| {
                    OutputError::Device(format!(
                        "cannot configure GPIO output {:?} on {chip:?}: {error}",
                        config.id
                    ))
                })?,
            ),
        };
        Ok(Self {
            config: config.clone(),
            driver,
        })
    }

    fn render(&mut self, rgba: &[u8]) -> Result<(), OutputError> {
        let expected = self.config.pixel_count * 4;
        if rgba.len() != expected {
            return Err(OutputError::InvalidFrame(format!(
                "output {:?} has {} RGBA bytes; expected {expected}",
                self.config.id,
                rgba.len()
            )));
        }
        retry_output_write(|| {
            self.driver
                .write_rgba(rgba, self.config.pixel_count, self.config.reversed)
        })
        .map_err(|error| {
            OutputError::Device(format!("output {:?} write failed: {error}", self.config.id))
        })
    }
}

trait OutputDriver: Send {
    fn write_rgba(&mut self, rgba: &[u8], pixel_count: usize, reversed: bool) -> io::Result<()>;
}

fn retry_output_write(mut write: impl FnMut() -> io::Result<()>) -> io::Result<()> {
    for attempt in 0..OUTPUT_WRITE_ATTEMPTS {
        match write() {
            Ok(()) => return Ok(()),
            Err(error)
                if attempt + 1 < OUTPUT_WRITE_ATTEMPTS
                    && matches!(
                        error.kind(),
                        io::ErrorKind::Interrupted
                            | io::ErrorKind::WouldBlock
                            | io::ErrorKind::TimedOut
                    ) =>
            {
                continue;
            }
            Err(error) => return Err(error),
        }
    }
    unreachable!("output write attempts always include at least one write")
}

#[derive(Default)]
struct MockOutput {
    last_rgba: Vec<u8>,
    writes: u64,
}

impl OutputDriver for MockOutput {
    fn write_rgba(&mut self, rgba: &[u8], pixel_count: usize, reversed: bool) -> io::Result<()> {
        self.last_rgba.clear();
        self.last_rgba.reserve(rgba.len());
        let pixels = rgba.chunks_exact(4);
        if reversed {
            for pixel in pixels.rev() {
                self.last_rgba.extend_from_slice(pixel);
            }
        } else {
            self.last_rgba.extend_from_slice(rgba);
        }
        debug_assert_eq!(self.last_rgba.len(), pixel_count * 4);
        self.writes = self.writes.saturating_add(1);
        Ok(())
    }
}

struct MockSpiOutput {
    pixel_count: usize,
    frame: Vec<u8>,
    writes: u64,
}

impl MockSpiOutput {
    fn new(pixel_count: usize) -> Self {
        Self {
            pixel_count,
            frame: vec![0; encoded_frame_size(pixel_count)],
            writes: 0,
        }
    }
}

impl OutputDriver for MockSpiOutput {
    fn write_rgba(&mut self, rgba: &[u8], pixel_count: usize, reversed: bool) -> io::Result<()> {
        debug_assert_eq!(pixel_count, self.pixel_count);
        encode_sm16716_frame(rgba, self.pixel_count, reversed, &mut self.frame);
        self.writes = self.writes.saturating_add(1);
        Ok(())
    }
}

struct SpiOutput {
    device: Spidev,
    pixel_count: usize,
    max_transfer_bytes: usize,
    frame: Vec<u8>,
}

impl SpiOutput {
    fn open(path: &str, speed_hz: u32, pixel_count: usize) -> io::Result<Self> {
        let mut device = Spidev::open(path)?;
        let mut options = SpidevOptions::new();
        options
            .bits_per_word(8)
            .max_speed_hz(speed_hz)
            .lsb_first(false)
            .mode(SpiModeFlags::SPI_MODE_0 | SpiModeFlags::SPI_NO_CS);
        if let Err(error) = device.configure(&options.build()) {
            // Raspberry Pi 5's DesignWare SPI controller rejects SPI_NO_CS.
            // LumiStripe uses MOSI only, so the default chip select is safe.
            if error.raw_os_error() != Some(libc::EINVAL) {
                return Err(error);
            }
            let mut fallback = SpidevOptions::new();
            fallback
                .bits_per_word(8)
                .max_speed_hz(speed_hz)
                .lsb_first(false)
                .mode(SpiModeFlags::SPI_MODE_0);
            device.configure(&fallback.build())?;
        }
        let max_transfer_bytes = kernel_spi_buffer_size();
        let frame_bytes = encoded_frame_size(pixel_count);
        if frame_bytes > max_transfer_bytes {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!(
                    "encoded SPI frame is {frame_bytes} bytes, exceeding the single-transfer limit of {max_transfer_bytes} bytes; increase the spidev.bufsiz kernel parameter"
                ),
            ));
        }
        Ok(Self {
            device,
            pixel_count,
            max_transfer_bytes,
            frame: vec![0; frame_bytes],
        })
    }
}

impl OutputDriver for SpiOutput {
    fn write_rgba(&mut self, rgba: &[u8], pixel_count: usize, reversed: bool) -> io::Result<()> {
        debug_assert_eq!(pixel_count, self.pixel_count);
        transmit_spi_frame(
            &mut self.device,
            rgba,
            pixel_count,
            reversed,
            &mut self.frame,
            self.max_transfer_bytes,
        )
    }
}

fn transmit_spi_frame<W: Write>(
    writer: &mut W,
    rgba: &[u8],
    pixel_count: usize,
    reversed: bool,
    frame: &mut [u8],
    max_transfer_bytes: usize,
) -> io::Result<()> {
    encode_sm16716_frame(rgba, pixel_count, reversed, frame);
    if frame.len() > max_transfer_bytes {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "encoded SPI frame is {} bytes, exceeding the single-transfer limit of {max_transfer_bytes} bytes",
                frame.len()
            ),
        ));
    }
    let written = writer.write(frame)?;
    if written != frame.len() {
        return Err(io::Error::new(
            io::ErrorKind::WriteZero,
            format!(
                "SPI device accepted {written} of {} frame bytes in one transfer",
                frame.len()
            ),
        ));
    }
    Ok(())
}

fn encoded_frame_size(pixel_count: usize) -> usize {
    (50 + pixel_count * 26 + 7) / 8
}

/// Encode the shared SM16716 GPIO/SPI wire format (50 lead clocks, start bit,
/// RGB MSB first, one trailing clock per pixel, and zero byte padding).
pub fn encode_sm16716_frame(
    rgba: &[u8],
    pixel_count: usize,
    reversed: bool,
    destination: &mut [u8],
) {
    destination.fill(0);
    let pixels = rgba.chunks_exact(4);
    let mut bit_index = 50usize;
    let mut write_pixel = |pixel: &[u8]| {
        write_bit(destination, &mut bit_index, true);
        let alpha = u32::from(pixel[3]);
        for channel in &pixel[..3] {
            let scaled = (u32::from(*channel) * alpha / 255) as u8;
            for shift in (0..8).rev() {
                write_bit(destination, &mut bit_index, (scaled & (1 << shift)) != 0);
            }
        }
    };
    if reversed {
        for pixel in pixels.rev() {
            write_pixel(pixel);
        }
    } else {
        for pixel in pixels {
            write_pixel(pixel);
        }
    }
    debug_assert_eq!(bit_index, 50 + pixel_count * 25);
}

fn write_bit(destination: &mut [u8], bit_index: &mut usize, value: bool) {
    if value {
        destination[*bit_index >> 3] |= 1 << (7 - (*bit_index & 7));
    }
    *bit_index += 1;
}

fn kernel_spi_buffer_size() -> usize {
    std::fs::read_to_string("/sys/module/spidev/parameters/bufsiz")
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_SPI_BUFFER_BYTES)
}

enum GpioLineWriter {
    Gpiomem(GpiomemLineWriter),
    CharacterDevice(GpiodLineWriter),
}

impl GpioLineWriter {
    fn set_values(&mut self, data: bool, clock: bool) -> io::Result<()> {
        match self {
            Self::Gpiomem(writer) => writer.set_values(data, clock),
            Self::CharacterDevice(writer) => writer.set_values(data, clock),
        }
    }

    fn wait_ns(&self, ns: u64) {
        match self {
            Self::Gpiomem(writer) => writer.wait_ns(ns),
            Self::CharacterDevice(_) => std::hint::spin_loop(),
        }
    }
}

trait GpioPulseWriter {
    fn set_values(&mut self, data: bool, clock: bool) -> io::Result<()>;
    fn wait_ns(&self, ns: u64);
}

impl GpioPulseWriter for GpioLineWriter {
    fn set_values(&mut self, data: bool, clock: bool) -> io::Result<()> {
        GpioLineWriter::set_values(self, data, clock)
    }

    fn wait_ns(&self, ns: u64) {
        GpioLineWriter::wait_ns(self, ns)
    }
}

struct GpioOutput {
    writer: GpioLineWriter,
}

impl GpioOutput {
    fn open(chip: &str, data_pin: u32, clock_pin: u32, consumer: &str) -> io::Result<Self> {
        if let Ok(writer) = GpiomemLineWriter::open(data_pin, clock_pin) {
            return Ok(Self {
                writer: GpioLineWriter::Gpiomem(writer),
            });
        }
        GpiodLineWriter::open(chip, data_pin, clock_pin, consumer).map(|writer| Self {
            writer: GpioLineWriter::CharacterDevice(writer),
        })
    }
}

impl OutputDriver for GpioOutput {
    fn write_rgba(&mut self, rgba: &[u8], pixel_count: usize, reversed: bool) -> io::Result<()> {
        transmit_gpio_frame(&mut self.writer, rgba, pixel_count, reversed)
    }
}

fn transmit_gpio_frame<W: GpioPulseWriter>(
    writer: &mut W,
    rgba: &[u8],
    pixel_count: usize,
    reversed: bool,
) -> io::Result<()> {
    for _ in 0..50 {
        gpio_pulse(writer, false)?;
    }
    let pixels = rgba.chunks_exact(4);
    if reversed {
        for pixel in pixels.rev() {
            gpio_transmit_pixel(writer, pixel)?;
        }
    } else {
        for pixel in pixels {
            gpio_transmit_pixel(writer, pixel)?;
        }
    }
    for _ in 0..pixel_count {
        gpio_pulse(writer, false)?;
    }
    Ok(())
}

fn gpio_transmit_pixel<W: GpioPulseWriter>(writer: &mut W, pixel: &[u8]) -> io::Result<()> {
    gpio_pulse(writer, true)?;
    let alpha = u32::from(pixel[3]);
    for channel in &pixel[..3] {
        let scaled = (u32::from(*channel) * alpha / 255) as u8;
        for shift in (0..8).rev() {
            gpio_pulse(writer, (scaled & (1 << shift)) != 0)?;
        }
    }
    Ok(())
}

fn gpio_pulse<W: GpioPulseWriter>(writer: &mut W, data: bool) -> io::Result<()> {
    writer.set_values(data, false)?;
    writer.wait_ns(100);
    writer.set_values(data, true)?;
    writer.wait_ns(500);
    writer.set_values(data, false)?;
    writer.wait_ns(500);
    Ok(())
}

struct GpiodLineWriter {
    request: gpio_cdev::MultiLineHandle,
}

impl GpiodLineWriter {
    fn open(chip_path: &str, data_pin: u32, clock_pin: u32, consumer: &str) -> io::Result<Self> {
        let mut chip = Chip::new(chip_path).map_err(gpiod_error)?;
        let lines = chip
            .get_lines(&[data_pin, clock_pin])
            .map_err(gpiod_error)?;
        let request = lines
            .request(
                LineRequestFlags::OUTPUT,
                &[0, 0],
                &format!("lumistripe-{consumer}"),
            )
            .map_err(gpiod_error)?;
        Ok(Self { request })
    }

    fn set_values(&self, data: bool, clock: bool) -> io::Result<()> {
        self.request
            .set_values(&[u8::from(data), u8::from(clock)])
            .map_err(gpiod_error)
    }
}

fn gpiod_error(error: gpio_cdev::errors::Error) -> io::Error {
    io::Error::other(error.to_string())
}

struct GpiomemLineWriter {
    fd: libc::c_int,
    gpio: *mut u32,
    layout: GpioRegisterLayout,
    mapping_bytes: usize,
    data_pin: u32,
    clock_pin: u32,
    old_data_config: u32,
    old_clock_config: u32,
    old_data_output_enabled: bool,
    old_clock_output_enabled: bool,
    old_data_level: bool,
    old_clock_level: bool,
    old_data_pad_config: u32,
    old_clock_pad_config: u32,
    configured_pins: u8,
    ns_per_iteration: f64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum GpioRegisterLayout {
    Bcm,
    Rp1,
}

impl GpioRegisterLayout {
    fn from_device_tree_compatible(compatible: &[u8]) -> Option<Self> {
        let compatible = compatible
            .split(|byte| *byte == 0)
            .filter(|value| !value.is_empty());
        let mut is_bcm = false;
        for value in compatible {
            match value {
                b"brcm,bcm2712" => return Some(Self::Rp1),
                b"brcm,bcm2708" | b"brcm,bcm2835" | b"brcm,bcm2836" | b"brcm,bcm2837"
                | b"brcm,bcm2710" | b"brcm,bcm2711" => is_bcm = true,
                _ => {}
            }
        }
        is_bcm.then_some(Self::Bcm)
    }

    fn device_path(self) -> &'static str {
        match self {
            Self::Bcm => BCM_GPIOMEM_PATH,
            Self::Rp1 => RP1_GPIOMEM_PATH,
        }
    }

    fn mapping_bytes(self) -> usize {
        match self {
            Self::Bcm => BCM_GPIO_MAP_BYTES,
            Self::Rp1 => RP1_GPIO_MAP_BYTES,
        }
    }
}

// The mapped GPIO registers are exclusively owned by the output worker thread.
unsafe impl Send for GpiomemLineWriter {}

impl GpiomemLineWriter {
    fn open(data_pin: u32, clock_pin: u32) -> io::Result<Self> {
        if data_pin > 53 || clock_pin > 53 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "GPIO pins must be between 0 and 53",
            ));
        }
        let compatible = std::fs::read("/proc/device-tree/compatible")?;
        let layout =
            GpioRegisterLayout::from_device_tree_compatible(&compatible).ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::Unsupported,
                    "cannot safely select a GPIO MMIO layout for this device tree",
                )
            })?;
        let mapping_bytes = layout.mapping_bytes();
        let path =
            std::ffi::CString::new(layout.device_path()).expect("static path has no NUL bytes");
        // SAFETY: path is a valid C string and the descriptor is closed in Drop.
        let fd = unsafe { libc::open(path.as_ptr(), libc::O_RDWR | libc::O_CLOEXEC) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: the device tree selects the GPIO register layout and mapping size.
        let mapping = unsafe {
            libc::mmap(
                ptr::null_mut(),
                mapping_bytes,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                fd,
                0,
            )
        };
        if mapping == libc::MAP_FAILED {
            let error = io::Error::last_os_error();
            // SAFETY: fd was opened above and has not been closed.
            unsafe { libc::close(fd) };
            return Err(error);
        }
        let mut writer = Self {
            fd,
            gpio: mapping.cast(),
            layout,
            mapping_bytes,
            data_pin,
            clock_pin,
            old_data_config: 0,
            old_clock_config: 0,
            old_data_output_enabled: false,
            old_clock_output_enabled: false,
            old_data_level: false,
            old_clock_level: false,
            old_data_pad_config: 0,
            old_clock_pad_config: 0,
            configured_pins: 0,
            ns_per_iteration: 6.0,
        };
        writer.configure_pin(data_pin, true)?;
        writer.configured_pins |= 0b01;
        writer.configure_pin(clock_pin, false)?;
        writer.configured_pins |= 0b10;
        writer.ns_per_iteration = calibrate_busy_loop();
        Ok(writer)
    }

    fn set_values(&mut self, data: bool, clock: bool) -> io::Result<()> {
        self.set_pin(self.data_pin, data);
        self.set_pin(self.clock_pin, clock);
        Ok(())
    }

    fn wait_ns(&self, ns: u64) {
        let iterations = ((ns as f64 / self.ns_per_iteration).round() as usize).max(1);
        for iteration in 0..iterations {
            black_box(iteration);
        }
    }

    fn set_pin(&self, pin: u32, value: bool) {
        match self.layout {
            GpioRegisterLayout::Bcm => {
                let bit = 1u32 << (pin % 32);
                let register = match (value, pin < 32) {
                    (true, true) => GPSET0,
                    (true, false) => GPSET1,
                    (false, true) => GPCLR0,
                    (false, false) => GPCLR1,
                };
                // SAFETY: the mapping covers the BCM GPIO registers and pin range is checked.
                unsafe { ptr::write_volatile(self.gpio.add(register), bit) };
            }
            GpioRegisterLayout::Rp1 => {
                let (_, rio_offset, bit, _, _) =
                    rp1_pin_registers(pin).expect("GPIO pin was range checked before MMIO setup");
                let register = rio_offset
                    + RP1_RIO_OUT
                    + if value {
                        RP1_REGISTER_SET
                    } else {
                        RP1_REGISTER_CLEAR
                    };
                self.write_register(register, bit);
            }
        }
    }

    fn configure_pin(&mut self, pin: u32, is_data_pin: bool) -> io::Result<()> {
        match self.layout {
            GpioRegisterLayout::Bcm => {
                let old_config = self.bcm_get_fsel(pin);
                if is_data_pin {
                    self.old_data_config = old_config;
                } else {
                    self.old_clock_config = old_config;
                }
                self.set_pin(pin, false);
                self.bcm_set_fsel(pin, 1);
            }
            GpioRegisterLayout::Rp1 => {
                let (_, rio_offset, bit, control_offset, pad_offset) = rp1_pin_registers(pin)
                    .ok_or_else(|| {
                        io::Error::new(io::ErrorKind::InvalidInput, "invalid RP1 GPIO pin")
                    })?;
                let old_config = self.read_register(control_offset);
                let output_enable = self.read_register(rio_offset + RP1_RIO_OE);
                let output_level = self.read_register(rio_offset + RP1_RIO_OUT);
                let old_pad_config = self.read_register(pad_offset);
                if is_data_pin {
                    self.old_data_config = old_config;
                    self.old_data_output_enabled = output_enable & bit != 0;
                    self.old_data_level = output_level & bit != 0;
                    self.old_data_pad_config = old_pad_config;
                } else {
                    self.old_clock_config = old_config;
                    self.old_clock_output_enabled = output_enable & bit != 0;
                    self.old_clock_level = output_level & bit != 0;
                    self.old_clock_pad_config = old_pad_config;
                }

                // Set the output latch before enabling the pin to avoid a startup pulse.
                self.write_register(rio_offset + RP1_RIO_OUT + RP1_REGISTER_CLEAR, bit);
                self.write_register(
                    pad_offset,
                    (old_pad_config | RP1_PAD_INPUT_ENABLE) & !RP1_PAD_OUTPUT_DISABLE,
                );
                self.write_register(control_offset, rp1_gpio_output_control(old_config));
                self.write_register(rio_offset + RP1_RIO_OE + RP1_REGISTER_SET, bit);
            }
        }
        Ok(())
    }

    fn restore_pin(
        &self,
        pin: u32,
        old_config: u32,
        output_enabled: bool,
        output_level: bool,
        old_pad_config: u32,
    ) {
        match self.layout {
            GpioRegisterLayout::Bcm => self.bcm_set_fsel(pin, old_config),
            GpioRegisterLayout::Rp1 => {
                let (_, rio_offset, bit, control_offset, pad_offset) =
                    rp1_pin_registers(pin).expect("GPIO pin was range checked before MMIO setup");
                let level_register = rio_offset
                    + RP1_RIO_OUT
                    + if output_level {
                        RP1_REGISTER_SET
                    } else {
                        RP1_REGISTER_CLEAR
                    };
                let enable_register = rio_offset
                    + RP1_RIO_OE
                    + if output_enabled {
                        RP1_REGISTER_SET
                    } else {
                        RP1_REGISTER_CLEAR
                    };
                self.write_register(level_register, bit);
                self.write_register(enable_register, bit);
                self.write_register(control_offset, old_config);
                self.write_register(pad_offset, old_pad_config);
            }
        }
    }

    fn bcm_get_fsel(&self, pin: u32) -> u32 {
        let register = (pin / 10) as usize;
        let shift = (pin % 10) * 3;
        // SAFETY: BCM function-select registers are within the mapped GPIO page.
        (unsafe { ptr::read_volatile(self.gpio.add(register)) } >> shift) & 7
    }

    fn bcm_set_fsel(&self, pin: u32, mode: u32) {
        let register = (pin / 10) as usize;
        let shift = (pin % 10) * 3;
        // SAFETY: BCM function-select registers are within the mapped GPIO page.
        unsafe {
            let current = ptr::read_volatile(self.gpio.add(register));
            let next = (current & !(7 << shift)) | ((mode & 7) << shift);
            ptr::write_volatile(self.gpio.add(register), next);
        }
    }

    fn read_register(&self, byte_offset: usize) -> u32 {
        // SAFETY: every caller uses an offset inside the selected GPIO mapping.
        unsafe { ptr::read_volatile(self.gpio.add(byte_offset / std::mem::size_of::<u32>())) }
    }

    fn write_register(&self, byte_offset: usize, value: u32) {
        // SAFETY: every caller uses an offset inside the selected GPIO mapping.
        unsafe {
            ptr::write_volatile(
                self.gpio.add(byte_offset / std::mem::size_of::<u32>()),
                value,
            )
        }
    }
}

impl Drop for GpiomemLineWriter {
    fn drop(&mut self) {
        if self.gpio.is_null() {
            return;
        }
        if self.configured_pins & 0b01 != 0 {
            self.restore_pin(
                self.data_pin,
                self.old_data_config,
                self.old_data_output_enabled,
                self.old_data_level,
                self.old_data_pad_config,
            );
        }
        if self.configured_pins & 0b10 != 0 {
            self.restore_pin(
                self.clock_pin,
                self.old_clock_config,
                self.old_clock_output_enabled,
                self.old_clock_level,
                self.old_clock_pad_config,
            );
        }
        // SAFETY: mapping and descriptor are released exactly once here.
        unsafe {
            libc::munmap(self.gpio.cast(), self.mapping_bytes);
            libc::close(self.fd);
        }
        self.gpio = ptr::null_mut();
        self.fd = -1;
    }
}

fn rp1_pin_registers(pin: u32) -> Option<(usize, usize, u32, usize, usize)> {
    let (bank, local_pin) = match pin {
        0..=27 => (0, pin),
        28..=33 => (1, pin - 28),
        34..=53 => (2, pin - 34),
        _ => return None,
    };
    Some((
        RP1_IO_BANK_OFFSETS[bank],
        RP1_RIO_BANK_OFFSETS[bank],
        1u32 << local_pin,
        RP1_IO_BANK_OFFSETS[bank] + local_pin as usize * 8 + RP1_GPIO_CTRL,
        0x20000 + RP1_IO_BANK_OFFSETS[bank] + 0x04 + local_pin as usize * 4,
    ))
}

fn rp1_gpio_output_control(old_config: u32) -> u32 {
    (old_config
        & !(RP1_GPIO_FUNCTION_SELECT_MASK | RP1_GPIO_CTRL_OUTOVER_MASK | RP1_GPIO_CTRL_OEOVER_MASK))
        | RP1_GPIO_FUNCTION_SELECT
}

fn calibrate_busy_loop() -> f64 {
    let iterations = 1_000_000usize;
    let started = Instant::now();
    for iteration in 0..iterations {
        black_box(iteration);
    }
    let elapsed_ns = started.elapsed().as_nanos() as f64;
    (elapsed_ns / iterations as f64).max(1.0)
}

#[derive(Debug)]
pub enum OutputError {
    InvalidConfig(String),
    InvalidFrame(String),
    Device(String),
}

impl std::fmt::Display for OutputError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidConfig(message) | Self::InvalidFrame(message) | Self::Device(message) => {
                f.write_str(message)
            }
        }
    }
}

impl std::error::Error for OutputError {}

#[cfg(test)]
mod tests {
    use super::{
        GpioPulseWriter, GpioRegisterLayout, MockSpiOutput, OutputDriver, OutputError, OutputSet,
        encode_sm16716_frame, encoded_frame_size, retry_output_write, rp1_pin_registers,
        transmit_gpio_frame, transmit_spi_frame,
    };
    use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
    use std::io::{self, Write};

    #[test]
    fn retries_transient_output_write_once() {
        let mut attempts = 0;
        retry_output_write(|| {
            attempts += 1;
            if attempts == 1 {
                Err(io::Error::from(io::ErrorKind::Interrupted))
            } else {
                Ok(())
            }
        })
        .unwrap();

        assert_eq!(attempts, 2);
    }

    #[test]
    fn output_write_retries_are_bounded_and_permanent_errors_are_not_retried() {
        let mut transient_attempts = 0;
        let transient = retry_output_write(|| {
            transient_attempts += 1;
            Err(io::Error::from(io::ErrorKind::WouldBlock))
        })
        .unwrap_err();
        assert_eq!(transient.kind(), io::ErrorKind::WouldBlock);
        assert_eq!(transient_attempts, 2);

        let mut permanent_attempts = 0;
        let permanent = retry_output_write(|| {
            permanent_attempts += 1;
            Err(io::Error::from(io::ErrorKind::PermissionDenied))
        })
        .unwrap_err();
        assert_eq!(permanent.kind(), io::ErrorKind::PermissionDenied);
        assert_eq!(permanent_attempts, 1);
    }

    fn mock_output(id: &str, pixel_count: usize, reversed: bool) -> OutputConfig {
        OutputConfig {
            id: id.into(),
            pixel_count,
            reversed,
            backend: OutputBackend::Mock,
        }
    }

    #[test]
    fn sm16716_frame_matches_alpha_scaled_msb_first_reference() {
        let pixels = [255, 0, 16, 127, 2, 128, 64, 255];
        let mut frame = vec![0; encoded_frame_size(2)];
        encode_sm16716_frame(&pixels, 2, false, &mut frame);
        let bits: Vec<u8> = frame
            .iter()
            .flat_map(|byte| (0..8).rev().map(move |bit| (byte >> bit) & 1))
            .collect();
        let mut expected = vec![0; 50];
        for (red, green, blue) in [(127u8, 0u8, 7u8), (2, 128, 64)] {
            expected.push(1);
            for channel in [red, green, blue] {
                expected.extend((0..8).rev().map(|bit| (channel >> bit) & 1));
            }
        }
        expected.extend([0; 2]);
        expected.resize(frame.len() * 8, 0);
        assert_eq!(bits, expected);
        let actual_hex = frame
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        assert_eq!(
            actual_hex,
            include_str!("../../../test-fixtures/protocol-v2/sm16716-reference.hex").trim()
        );
    }

    #[test]
    fn mock_spi_backend_encodes_frames_without_allocating_a_device() {
        let pixels = [255, 0, 16, 127, 2, 128, 64, 255];
        let mut output = MockSpiOutput::new(2);
        let frame_address = output.frame.as_ptr();
        output.write_rgba(&pixels, 2, true).unwrap();

        let mut expected = vec![0; encoded_frame_size(2)];
        encode_sm16716_frame(&pixels, 2, true, &mut expected);
        assert_eq!(output.frame, expected);
        assert_eq!(output.writes, 1);

        output.write_rgba(&pixels, 2, false).unwrap();
        assert_eq!(output.frame.as_ptr(), frame_address);
        assert_eq!(output.writes, 2);
    }

    #[test]
    fn sm16716_encoder_reverses_pixels_and_clears_padding() {
        let pixels = [1, 2, 3, 255, 4, 5, 6, 255];
        let mut forward = vec![0xff; encoded_frame_size(2)];
        let mut reversed = vec![0xff; encoded_frame_size(2)];
        encode_sm16716_frame(&pixels, 2, false, &mut forward);
        encode_sm16716_frame(&pixels, 2, true, &mut reversed);
        assert_ne!(forward, reversed);
        assert_eq!(reversed[0], 0);
        assert_eq!(reversed.last().unwrap() & 1, 0);
    }

    #[derive(Default)]
    struct MockSpiWriter {
        transfers: Vec<Vec<u8>>,
        short_write: bool,
    }

    impl Write for MockSpiWriter {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.transfers.push(bytes.to_vec());
            Ok(if self.short_write {
                bytes.len().saturating_sub(1)
            } else {
                bytes.len()
            })
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn spi_mock_writes_one_alpha_scaled_reversed_transfer() {
        let pixels = [
            255, 0, 16, 127, // alpha scales to (127, 0, 7)
            2, 128, 64, 255,
        ];
        let mut frame = vec![0; encoded_frame_size(2)];
        let mut writer = MockSpiWriter::default();
        transmit_spi_frame(&mut writer, &pixels, 2, true, &mut frame, 4096).unwrap();

        assert_eq!(writer.transfers, vec![frame]);
        assert_eq!(writer.transfers.len(), 1);
        assert_eq!(writer.transfers[0].len(), encoded_frame_size(2));
        let mut forward = vec![0; encoded_frame_size(2)];
        encode_sm16716_frame(&pixels, 2, false, &mut forward);
        assert_ne!(writer.transfers[0], forward);
    }

    #[test]
    fn spi_mock_rejects_frames_over_the_single_transfer_limit() {
        let mut writer = MockSpiWriter::default();
        let mut frame = vec![0; encoded_frame_size(1)];
        let transfer_limit = frame.len() - 1;
        let error = transmit_spi_frame(
            &mut writer,
            &[1, 2, 3, 255],
            1,
            false,
            &mut frame,
            transfer_limit,
        )
        .unwrap_err();

        assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
        assert!(writer.transfers.is_empty());
    }

    #[test]
    fn spi_mock_does_not_split_a_short_single_transfer() {
        let mut writer = MockSpiWriter {
            short_write: true,
            ..MockSpiWriter::default()
        };
        let mut frame = vec![0; encoded_frame_size(1)];
        let error = transmit_spi_frame(&mut writer, &[1, 2, 3, 255], 1, false, &mut frame, 4096)
            .unwrap_err();

        assert_eq!(error.kind(), io::ErrorKind::WriteZero);
        assert_eq!(writer.transfers.len(), 1);
    }

    #[test]
    fn mock_output_accepts_a_complete_multistrip_frame_set() {
        let config = EngineConfig::new(vec![
            mock_output("main", 2, false),
            mock_output("secondary", 1, true),
        ])
        .unwrap();
        let mut outputs = OutputSet::open(config).unwrap();
        let sequence = outputs
            .render(&[vec![1, 2, 3, 255, 4, 5, 6, 255], vec![7, 8, 9, 255]])
            .unwrap();
        assert_eq!(sequence, 1);
        assert_eq!(outputs.output_count(), 2);
        assert_eq!(outputs.pixel_counts(), vec![2, 1]);
        assert_eq!(outputs.total_pixels(), 3);
        assert_eq!(outputs.frames_rendered(), 1);
        assert!(outputs.last_transmission_ms().is_some());
        assert!(outputs.last_sequential_skew_ms().is_some());
    }

    #[test]
    fn rejects_incomplete_or_mis_sized_output_sets() {
        let config = EngineConfig::new(vec![mock_output("main", 1, false)]).unwrap();
        let mut outputs = OutputSet::open(config).unwrap();
        assert!(matches!(
            outputs.render(&[]),
            Err(OutputError::InvalidFrame(_))
        ));
        assert!(matches!(
            outputs.render(&[vec![1, 2, 3]]),
            Err(OutputError::InvalidFrame(_))
        ));
    }

    #[test]
    fn empty_output_set_is_supported_for_runtime_lifecycle() {
        let mut outputs = OutputSet::open(EngineConfig { outputs: vec![] }).unwrap();
        assert_eq!(outputs.render(&[]).unwrap(), 1);
        outputs.blackout().unwrap();
        assert_eq!(outputs.total_pixels(), 0);
    }

    #[derive(Default)]
    struct MockGpioWriter {
        writes: Vec<(bool, bool)>,
    }

    impl GpioPulseWriter for MockGpioWriter {
        fn set_values(&mut self, data: bool, clock: bool) -> io::Result<()> {
            self.writes.push((data, clock));
            Ok(())
        }

        fn wait_ns(&self, _ns: u64) {}
    }

    #[test]
    fn gpio_mock_pulses_match_the_spi_encoder_with_alpha_and_reversal() {
        let pixels = [
            255, 0, 16, 127, // alpha scales to (127, 0, 7)
            2, 128, 64, 255,
        ];
        let mut writer = MockGpioWriter::default();
        transmit_gpio_frame(&mut writer, &pixels, 2, true).unwrap();
        let pulse_bits: Vec<u8> = writer
            .writes
            .iter()
            .step_by(3)
            .map(|(data, _)| u8::from(*data))
            .collect();
        let mut encoded = vec![0; encoded_frame_size(2)];
        encode_sm16716_frame(&pixels, 2, true, &mut encoded);
        let encoded_bits: Vec<u8> = encoded
            .iter()
            .flat_map(|byte| (0..8).rev().map(move |bit| (byte >> bit) & 1))
            .collect();
        assert_eq!(pulse_bits.len(), 50 + 26 * 2);
        assert_eq!(&encoded_bits[..pulse_bits.len()], pulse_bits);
        assert!(encoded_bits[pulse_bits.len()..].iter().all(|bit| *bit == 0));
    }

    #[test]
    fn gpio_mmio_layout_selection_matches_supported_device_tree_compatibles() {
        assert_eq!(
            GpioRegisterLayout::from_device_tree_compatible(
                b"raspberrypi,5-model-b\0brcm,bcm2712\0"
            ),
            Some(GpioRegisterLayout::Rp1)
        );
        assert_eq!(
            GpioRegisterLayout::from_device_tree_compatible(
                b"raspberrypi,4-model-b\0brcm,bcm2711\0"
            ),
            Some(GpioRegisterLayout::Bcm)
        );
        assert_eq!(
            GpioRegisterLayout::from_device_tree_compatible(b"generic,unknown-board\0"),
            None
        );
    }

    #[test]
    fn rp1_gpio_register_offsets_cover_all_three_banks() {
        assert_eq!(
            rp1_pin_registers(0),
            Some((0x0000, 0x10000, 1, 0x0004, 0x20004))
        );
        assert_eq!(
            rp1_pin_registers(27),
            Some((0x0000, 0x10000, 1 << 27, 0x00dc, 0x20070))
        );
        assert_eq!(
            rp1_pin_registers(28),
            Some((0x4000, 0x14000, 1, 0x4004, 0x24004))
        );
        assert_eq!(
            rp1_pin_registers(33),
            Some((0x4000, 0x14000, 1 << 5, 0x402c, 0x24018))
        );
        assert_eq!(
            rp1_pin_registers(34),
            Some((0x8000, 0x18000, 1, 0x8004, 0x28004))
        );
        assert_eq!(
            rp1_pin_registers(53),
            Some((0x8000, 0x18000, 1 << 19, 0x809c, 0x28050))
        );
        assert_eq!(rp1_pin_registers(54), None);
    }

    #[test]
    fn rp1_gpio_output_control_selects_gpio_and_disables_overrides() {
        let previous = u32::MAX;
        let configured = super::rp1_gpio_output_control(previous);
        assert_eq!(configured & 0x1f, 0x05);
        assert_eq!(configured & 0x0000_f000, 0);
        assert_eq!(configured & !0x0000_ffff, previous & !0x0000_ffff);
    }
}
