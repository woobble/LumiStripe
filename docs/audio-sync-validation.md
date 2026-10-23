# Audio-to-LED sync validation

This procedure measures the physical path from the audio source through the
capture and beat scheduler to the LEDs. The calibration cue is generated on the
Pi through the PipeWire/Pulse default sink. Its PortAudio DAC clock is mapped to
the Pi monotonic clock, so browser clock offset and browser speaker latency are
not part of the comparison.

## Reproducible DSP workload

Generate a deterministic 120 BPM four-on-the-floor track with bass kicks and
offbeat hi-hats:

```bash
uv run python scripts/generate_audio_sync_track.py /tmp/lumistripe-sync.wav
```

The default file is 32 bars (64 seconds). The BPM, bar count, and sample rate can
be changed with `--bpm`, `--bars`, and `--sample-rate`.

Measure processor headroom and call latency on the same Pi before and after a
DSP change:

```bash
uv run python scripts/benchmark_audio_dsp.py --iterations 4096
```

The report includes median, p95, and p99 feed-call time for standard 512-sample
capture blocks and 4-hop/16-hop batches, plus real-time headroom. Run the same
command at least three times under idle system load and compare medians and p95
values. Keep the Pi model, cooling, power supply, Python version, sample rate,
and native build profile the same. The 512-sample result represents the normal
capture callback workload; larger batches cover integrations that submit more
than one FFT hop at a time.

Measure the real PipeWire output callback on the Raspberry Pi and confirm the
stream is connected to the named active sync sink:

```bash
uv run python scripts/benchmark_audio_sync_output.py \
  --sink alsa_output.usb-speakers --duration 20
```

The command plays the nonuniform calibration cue and reports the sample rate,
PortAudio block duration, callback p50/p95/p99 processing time, p95 callback
interval jitter, underflow/overflow counts, and callback errors. It exits with a clear
error if PipeWire cannot identify the calibration stream or if that stream is
connected to a different sink. Run it at least three times with idle system
load, then repeat under representative CPU and LED output load. Compare the
callback p95/p99 with the PortAudio block duration and record interval jitter
and status counts.

During live playback, audio telemetry reports beat scheduler p95 lateness,
dropped stale ticks, and coalesced ticks. A stall must not cause old beat
flashes to burst on later frames. A tick is eligible only when it is at most
50 ms late and no later than half a configured subdivision interval.

## Physical audio and LED measurement

Use one recorder or oscilloscope with two synchronized channels: a measurement
microphone fixed near the selected speaker, and a photodiode fixed against one
LED. Record at 48 kHz or higher. Keep gain, speaker level, microphone position,
strip brightness, and recorder settings fixed across baseline and corrected
runs.

1. Build the audio extension and start LumiStripe on the Raspberry Pi. Use
   `pactl list short sinks` to record the configured PipeWire sink identifiers.
2. Play the generated file to the test input. For the microphone route, play it
   from a separate speaker at a fixed level. For the Bluetooth input route,
   send the same file from the paired source device. Set LumiStripe to Dynamic
   mode and verify its audio monitor is active.
3. For each input route and each configured PipeWire output sink, set the saved
   sync offset to 0 ms. Record at least 100 beats on the two-channel recorder.
   Record source name, input selector/address, output sink, BPM, sample rate,
   configured offset, and Pi temperature with the capture.
4. Detect each audible kick onset from the microphone channel and each LED
   transition from the photodiode channel. Pair transitions to the nearest kick
   within one half beat. Calculate `LED transition time - audible onset time`
   per beat. Report signed median, median absolute deviation, p95 and p99
   absolute error, and missed/unpaired beats. Record scheduler stale drops,
   coalesced ticks, and scheduler p95 lateness from audio telemetry.
5. Start **Audio-to-LED sync** calibration for that route. The Pi plays a
   repeating nonuniform cue while the strip displays the matching flash pattern.
   Adjust the offset until the speaker cue and flash coincide, save it, then
   record another 100 beats using the same settings.
6. Repeat for every microphone/Bluetooth input and every configured PipeWire
   sink. Route offsets are stored separately by LumiStripe. Include both the
   uncorrected and corrected measurements in the report.

There is no fixed pass threshold in this procedure. Establish a threshold after
the baseline measurements and choose it for the target Pi, speaker, LEDs, and
installation. Use the same threshold for the corrected matrix.

| Input route | PipeWire sink | Offset | Beats | Median delay | Median abs. deviation | p95 abs. error | p99 abs. error | Missed beats | Scheduler dropped / coalesced | Scheduler p95 lateness |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Microphone |  | 0 ms |  |  |  |  |  |  |  |  |
| Microphone |  |  |  |  |  |  |  |  |  |  |
| Bluetooth |  | 0 ms |  |  |  |  |  |  |  |  |
| Bluetooth |  |  |  |  |  |  |  |  |  |  |

The shared recorder clock measures audible output and physical LED transitions
directly. The calibration UI provides an operator adjustment; it does not
replace these end-to-end measurements.
