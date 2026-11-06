# Spotify Connect providers

LumiStripe supports one Spotify provider at a time. Both providers use the
existing dashboard status and control API and send audio to the dedicated
`lumistripe_spotify` PipeWire sink. The sink monitor continues through the
existing speaker routing and music-reactive audio path; the DSP and LED
renderer do not change.

## Select a provider

Set `LUMI_SPOTIFY_BACKEND` in
`/etc/lumistripe/lumistripe-web.env`, or choose it during an administrator
install/repair:

```bash
sudo lumistripe-admin install --spotify-backend librespot
sudo lumistripe-admin install --spotify-backend soloist
sudo lumistripe-admin install --spotify-backend disabled
```

For repair from a fresh trusted checkout, add the same option to
`lumistripe-admin repair --source PATH`. Leaving the option out preserves the
saved setting. Older installations without the setting continue to select
Soloist. Selecting `librespot` or `disabled` stops the Soloist service but
retains its API key file, executable, and data for a later switch back.

| Backend | Runtime | Setup |
| --- | --- | --- |
| `librespot` | In-process Rust Spotify Connect receiver | No Soloist API key or service. Pair from Spotify on the same local network. |
| `soloist` | Existing separate `lumistripe-spotify.service` | Requires the Soloist API key in its protected environment file. |
| `disabled` | No Spotify provider | Spotify controls report unavailable. |

The installer only asks for a Soloist key when `soloist` is selected. The
native receiver can be selected on a fresh install without downloading or
starting Soloist.

## Native pairing and saved state

The native provider advertises the configured device name through librespot's
local Connect discovery. Open Spotify on a phone or computer on the same
network and select LumiStripe under available devices. The Spotify client
completes the local discovery/authentication exchange. LumiStripe does not ask
for an account password, scrape Spotify clients, or import Soloist credentials.

The default state directory is
`$HOME/.local/share/lumistripe/spotify`; override it with
`LUMI_SPOTIFY_LIBRESPOT_DATA_DIR`. LumiStripe creates it with mode `0700` and
keeps credentials, volume, and the stable device ID in private files. The
audio cache is capped at 64 MiB. Do not include this directory in unprotected
backups or diagnostic reports. The REST API and application logs do not expose
cached credentials or access tokens.

Saved credentials restore the session after restart. If credentials expire or
are revoked, the provider removes the cached credential file, reports that
pairing is needed, and continues advertising the receiver. Select LumiStripe
again in Spotify. With no saved credentials, status says authentication is
required while local discovery remains available for pairing. Network loss is
retried with bounded exponential backoff; repeated failures do not start
parallel Connect instances. Changing the device name in **Devices → Startup →
Names** restarts local discovery with the new name while preserving its device
identity and session.

## Controls and audio

The dashboard supports play, pause, previous/next, seek, volume, shuffle, and
repeat off/context/track. Status comes from librespot Connect and player events,
including the active device, track URI, title, artists, album, cover URL,
duration, position, volume, and playback options. Commands are serialized in a
bounded queue and complete only after a matching provider event. A timeout
reports that delivery may still complete; it does not claim success.

Librespot uses its PulseAudio-compatible output backend with the explicit
`lumistripe_spotify` sink. PipeWire supplies that PulseAudio interface on
supported installations and routes it to the selected physical output. With
the native provider, decoded PCM also enters LumiStripe's bounded analysis
queue directly, avoiding a monitor-capture round trip for reactive animation.
The audio worker still runs the existing DSP and renderer. If decoded PCM
fails or stalls while Spotify is playing, LumiStripe falls back to the
PipeWire sink monitor when available. Soloist continues to use monitor
capture. Intentional pause does not trigger fallback. Track changes and seeks
start a new PCM generation; pause/resume preserves the same DSP state. While
monitor fallback is active, the worker probes direct PCM every five seconds
and requires eight contiguous batches before switching back. Probe PCM is
discarded, and the monitor remains the only analysis source until recovery is
confirmed. Returning to direct PCM resets DSP and music-structure state.

The Audio page distinguishes authentication required, disconnected Connect
service, paused playback, direct PCM waiting/healthy/overloaded/stalled,
monitor fallback active/unavailable, DSP stalls, and Spotify sink errors. A
PipeWire volume or mute change does not affect direct PCM analysis; Spotify's
software volume and normalization do. See the [audio guide](audio.md) for the
gain and timestamp boundaries.

Decoded PCM timestamps are based on monotonic queue-submission time and sample
position. They are not Spotify media timestamps or physical speaker
presentation times. The software tests can validate sample order and feature
timestamps but cannot verify speaker-to-LED alignment.

If the receiver connects but is silent, check that the sink and monitor exist
with `pactl list short sinks` and `pactl list short sources`, and check the web
service journal for an output error. For source-path diagnostics, inspect the
Audio page's input transport: `direct_pcm` means decoded Spotify audio is
reaching the engine, while `monitor_fallback` means monitor capture is active.

## Compatibility and policy

Librespot is an unofficial Spotify implementation. Its protocol compatibility
can change, and Spotify can change client or service behavior. Spotify's
published [Developer Terms](https://developer.spotify.com/terms) restrict
reverse engineering and certain derivative uses. How those terms apply to a
particular librespot deployment requires an operator's own policy and legal
review. LumiStripe therefore keeps librespot opt-in and preserves Soloist as a
migration option; it does not make the native backend the default.

The librespot project documents Premium as a requirement for Connect playback
([supported features](https://github.com/librespot-org/librespot#supported-features)).
The native integration is a Connect receiver, not the Spotify Web API. It does
not provide search, browsing, playlist editing, account management, or a
general queue editor. Soloist and librespot use different authentication and
credential formats; switching does not import or delete the other provider's
credentials.

## Manual acceptance after installation

Automated software tests use synthetic state and mock data. They do not verify
Spotify authentication, local network discovery, playback, account policy, or
physical audio routing. After selecting `librespot` on a real appliance, verify
that:

1. LumiStripe appears in Spotify's device list and can be selected.
2. Playback, dashboard controls, track metadata, and speaker output work.
3. The Audio page reports direct PCM during playback, and the Spotify monitor
   remains available for fallback routing.
4. A service restart restores the session without creating a duplicate device.
5. A network interruption recovers, and a renamed device reappears with its new
   name.
6. Re-pairing works after cached credentials are revoked or removed.

Keep the dashboard paired and restricted to trusted clients during setup. The
Connect receiver uses its required local discovery protocol; it does not add a
separate unauthenticated dashboard control route.
