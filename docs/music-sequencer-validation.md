# Music structure and sequencer validation

This workflow separates deterministic software results from measurements on a
Raspberry Pi. The generated structure track tests the current feature detector;
its labels are controlled references, not a claim about how arbitrary songs
will be classified.

## Generate structured reference audio

The existing steady sync track remains the generator default. Select the
structured profile explicitly to create the 32-bar progression and its JSON
timeline:

```bash
uv run python scripts/generate_audio_sync_track.py \
  /tmp/lumistripe-structure.wav \
  --profile structure \
  --bpm 128
```

The generator also accepts BPM values from 60 to 200. Use 120, 128, and 150
for the standard sweep. The fixed section layout is 2 bars of silence, 8 bars
of groove, 4 bars of breakdown, 8 bars of build-up, 2 bars of drop, and 8 bars
of high-energy groove. The WAV is mono 16-bit PCM at 48 kHz by default. The
timeline is written beside it as `lumistripe-structure.timeline.json`; pass
`--timeline PATH` to choose another destination.

## Run the offline validation

The runner uses the native audio processor and an in-memory LED stripe. It
does not require GPIO, SPI, a physical LED strip, or audio playback hardware.
Build/install the normal audio development profile if the extension is not
available:

```bash
LUMISTRIPE_BUILD_EXTENSIONS=audio uv sync --locked --all-extras --dev
```

Then run the validation:

```bash
uv run python scripts/validate_music_sequencer.py \
  /tmp/lumistripe-structure.wav \
  /tmp/lumistripe-structure.timeline.json \
  --report /tmp/lumistripe-report.json \
  --trace /tmp/lumistripe-trace.csv
```

The runner replays the same captured DSP snapshots through Dynamic playback
with automatic switching set to off, beat, bar, and phrase. It also runs a
repeating and a one-shot two-animation sequence, with a −85 ms sync offset.
The JSON report summarizes each scenario; the CSV contains timestamped
structure, clock, playback, sequence, and scheduler observations.

Initial report tolerances are ±2 seconds for section/drop boundaries, a 75 ms
median absolute beat-time error, and one audio-processing frame for sequence
transitions. The report includes unmatched and missed events. Bar and phrase
positions are evaluated relative to the first confident beat observed by the
tracker; the report does not treat that estimate as a confirmed downbeat.
Physical synchronization is always labeled unverified by this offline runner.

Run the Python regression tests as well:

```bash
uv run python -m pytest -q packages/lumistripe-core/tests
```

The regression suite covers confidence loss and recovery, tempo changes,
stalled frames, negative offsets, quantized switching, and custom sequence
completion independently of physical hardware.

## Initial offline baseline

On 2026-10-25, the native-DSP runner was exercised at 120, 128, and 150 BPM
with all six playback scenarios:

| BPM | Structure phases | Beat clock median error | Off / beat / bar switching | Phrase switching | Repeating / one-shot sequence |
| ---: | --- | ---: | --- | --- | --- |
| 120 | Fail: breakdown, build-up, drop, and high-energy boundaries missed | 7.17 ms, pass | Pass | Unverified | Pass / pass |
| 128 | Fail: breakdown, build-up, drop, and high-energy boundaries missed | 6.81 ms, pass | Pass | Unverified | Pass / pass |
| 150 | Fail: breakdown, build-up, drop, and high-energy boundaries missed | 7.30 ms, pass | Pass | Pass | Pass / pass |

No false drop events were reported. The failure was consistent across the
sweep: `program_loudness` remained elevated after the short-term signal fell,
and the tracker selected the maximum of those values. This was the pre-Goal 8B
baseline.

These are software measurements using the native DSP and an in-memory stripe;
they do not establish Bluetooth speaker-to-LED synchronization. The JSON and
CSV files are generated locally by the runner and are not checked in as
machine-specific artifacts.

## Goal 8B revalidation

The structure detector now uses native `rolling_loudness` as its short-term
level when available. It keeps instantaneous RMS for transient evidence and
does not use `program_loudness` to classify section energy. The structured test
audio now makes the breakdown clearly quieter than the following build-up; its
section labels and the runner's ±2 second boundary tolerance are unchanged.
The validation CSV includes short-term `energy`, `rolling_loudness`, and
`program_loudness` so transitions can be inspected directly.

Native feature medians show the reason for that selection:

| BPM | Groove rolling / program | Breakdown rolling / program | Build-up rolling, first to last quarter |
| ---: | ---: | ---: | ---: |
| 120 | 0.180 / 0.219 | 0.044 / 0.151 | 0.135 → 0.189 |
| 128 | 0.187 / 0.222 | 0.045 / 0.156 | 0.141 → 0.197 |
| 150 | 0.201 / 0.229 | 0.047 / 0.168 | 0.143 → 0.210 |

After the fix, the complete six-scenario sweep reports:

| BPM | Structure | Missed / false drops | False phase samples | Beat median error | Off / beat / bar | Phrase | Repeating / one-shot |
| ---: | --- | ---: | ---: | ---: | --- | --- | --- |
| 120 | Pass | 0 / 0 | 0 | 7.32 ms | Pass | Unverified | Pass / pass |
| 128 | Pass | 0 / 0 | 0 | 7.19 ms | Pass | Unverified | Pass / pass |
| 150 | Pass | 0 / 0 | 0 | 7.30 ms | Pass | Pass | Pass / pass |

Section transition errors against the unchanged timeline labels were:

| BPM | Silence | Groove | Breakdown | Build-up | Drop | High-energy groove |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 120 | +0.01 s | +1.25 s | +0.84 s | +1.77 s | +0.24 s | −0.91 s |
| 128 | +0.01 s | +1.24 s | +0.95 s | +1.75 s | +0.22 s | −0.67 s |
| 150 | +0.01 s | +1.25 s | +0.89 s | +1.74 s | +0.20 s | −0.14 s |

All phase boundary errors are within the existing ±2 second tolerance. Each
track produced one confirmed drop, with no missed drops, false drops, or false
phase samples. Automatic off/beat/bar switching and both custom sequence modes
passed. Phrase switching remains unverified at 120 and 128 BPM because those
runs did not produce a scheduled candidate switch. Beat-clock errors remain
well below the 75 ms tolerance and close to the original baseline.

Native regression tests process the generated audio through `AudioState` and
assert the section sequence, energy-feature behavior, drop count, silence
recovery, and resistance to an isolated transient and repeated feature frames.
Existing playback tests continue to cover stale snapshots. These results use
the native DSP and an in-memory stripe; Bluetooth-to-LED synchronization still
requires measurement on the Raspberry Pi.

The native drop path waits for at least eight seconds of confirmed build-up and
a short-term level increase. This rejects ordinary kick transients in the
reference, but shorter real-world build-ups may not produce a confirmed drop;
evaluate that tradeoff against real tracks before tuning it.

Repeat the complete software sweep with:

```bash
for bpm in 120 128 150; do
  uv run python scripts/generate_audio_sync_track.py \
    "/tmp/lumistripe-structure-${bpm}.wav" \
    --profile structure --bpm "$bpm"
  uv run python scripts/validate_music_sequencer.py \
    "/tmp/lumistripe-structure-${bpm}.wav" \
    "/tmp/lumistripe-structure-${bpm}.timeline.json" \
    --report "/tmp/lumistripe-report-${bpm}.json" \
    --trace "/tmp/lumistripe-trace-${bpm}.csv"
done
```

## Raspberry Pi 5 procedure

1. Restart and verify the installed runtime:

   ```bash
   sudo systemctl restart lumistripe-web.service
   sudo systemctl status lumistripe-web.service
   journalctl -u lumistripe-web.service -n 100 --no-pager
   ```

2. Open the paired LumiStripe dashboard. Connect the Bluetooth source device,
   select Bluetooth as the Audio input, and confirm the dashboard reports the
   intended connected input and active audio monitor.
3. Check the saved audio sync offset for that input route in the Audio
   controls. Record its value before the run; do not change calibration as
   part of this test. The offset is route-specific.
4. Create or select a repeating sequence with two eligible animations, each
   lasting two bars. Select Dynamic mode and activate the sequence.
5. Transfer `lumistripe-structure.wav` to the Bluetooth source device and play
   it to the Pi. For a quick sequencing check, the existing steady reference
   can also be generated at 128 BPM:

   ```bash
   uv run python scripts/generate_audio_sync_track.py \
     /tmp/lumistripe-sync.wav --bpm 128 --bars 32
   ```

6. Observe the Audio structure phase, confidence, build-up progress, confirmed
   drops, beat-clock confidence, estimated bar position, current animation,
   sequence step, and transition countdown. At 128 BPM, two bars are about
   3.75 seconds after the initial confident boundary. Record unexpected phase
   changes, missed drops, waiting states, late/dropped/coalesced ticks, and
   animation transition times.
7. Disconnect and reconnect the Bluetooth source. Confirm the same route and
   saved offset are active, then repeat the run and record any differences.

Keep Pi observations separate from offline simulation results. Dashboard
timing and animation changes alone do not establish acoustic-to-light
synchronization accuracy. To claim physical synchronization, measure audible
speaker output and LED transitions on a shared recorder clock, as described in
[`audio-sync-validation.md`](audio-sync-validation.md).
