# LumiStripe web UI/UX redesign

This document describes the current web interface after the dashboard redesign.
The interface puts frequent light controls first and keeps setup and diagnostic
work grouped by task.

## Navigation hierarchy

The four primary destinations stay visible throughout the app. On small screens
they appear in a bottom navigation bar; on large screens they move to a left
sidebar.

| Primary area | Route | Purpose |
| --- | --- | --- |
| Lights | `/` | Everyday playback, brightness, power, color, animation, and preview controls. |
| Audio | `/audio` | Current audio source, monitoring state, output selection, and live audio telemetry. |
| Devices | `/setup` | Stripe configuration and device setup. This route opens the Stripes section. |
| System | `/diagnostics` | Controller health and system status, with technical details available on demand. |

Secondary routes are grouped with the task they support:

- **Playback settings** (`/playback`) are linked from Lights. They contain music
  recognition, the idle behavior for music reactive playback, transitions, and
  sequencing.
- **Device setup** has Stripes (`/setup/stripes`), Color (`/setup/color`),
  Audio (`/setup/audio`), Startup (`/setup/startup`), and Network
  (`/setup/network`) sections.
- **Audio setup** is divided into Sources, Devices, Tuning, and Sync under
  `/setup/audio`.
- The older `/calibration` route redirects to Color setup so existing links
  continue to work.

## Page inventory and interaction model

### Lights dashboard

The Lights page is the main operating surface. The status pill communicates
whether the controller is running and whether the selected lights are on. When
stripes have independent playback, the target selector chooses all stripes or
one output; each subsequent control applies to that selection.

The main control sequence is:

1. Adjust brightness with a single, prominent slider.
2. Turn the selected output on or off.
3. Choose Color, Static, Cycle, or Music playback.
4. Set a color or choose an animation for the selected target.
5. Open an optional live preview or use **More settings** for advanced playback.

Animation selection remains available from the dashboard. Music idle behavior,
transitions, and sequencing live on the advanced page to keep the main surface
focused on common changes.

### Playback settings

The advanced page repeats the stripe target selector so a user can configure an
independent output without returning to Lights. It shows music recognition for
static and cycling modes, and offers idle brightness, idle mode, color, or
animation when Music mode is selected. Transition and sequencer controls remain
available below those playback-specific settings. Sequencer timing uses one set
of choices, phrase length appears only for phrase timing, and beat diagnostics
stay collapsed until requested. The selected sequence is summarized above the
sequence library. Transition effect and duration are summarized in a disclosure
and shown in detail only when opened.

### Audio

The Audio page presents source and output controls together with current input
health, visual telemetry, and music structure. Device management remains in
Audio setup. This separates live operation from source configuration while
keeping the current signal visible during use.

### Device setup

Setup pages share a clear section title and a consistent set of destinations.
Stripe rows foreground the output name and pixel count. Wiring, zones,
calibration, and power configuration are grouped under an individual stripe's
advanced disclosure. This keeps common identification visible without removing
the detailed controls.

### System diagnostics

The health summary stays visible on entry. Resource usage, audio internals,
power details, and runtime metrics sit inside a Technical details disclosure.
Host metrics are only requested while that disclosure is open.

## Visual and layout system

- Use a neutral dark background with restrained surface contrast and thin
  borders. Remove ambient purple glow and heavy ring treatments so the content
  and selected states carry the visual hierarchy.
- Use consistent rounded surface cards, compact section labels, and clear
  heading sizes. Primary actions use the existing accent; green indicates an
  active light output.
- Keep the app header compact and sticky. Constrain page content to a readable
  width while allowing dense setup pages to use available space.
- At small widths, stack paired panels and let controls wrap. At wide widths,
  pair related controls, such as source with microphone setup and output with
  Bluetooth devices. Stripe cards use additional columns only when space allows.
- The mobile navigation accounts for the device safe area and reserves bottom
  content space so controls are not hidden behind it. At large widths, content
  shifts beside the sidebar.

## Accessibility and input

- Navigation, mode choices, targets, disclosures, and settings remain operable
  with native links and buttons and have visible keyboard focus styles.
- Icon controls include accessible names; the playback mode choices have
  descriptive labels even when their visible text is abbreviated.
- Power and connection changes use status text that is exposed to assistive
  technology. Brightness values are shown alongside their sliders.
- Navigation labels remain visible at mobile widths rather than relying on
  icons alone. Touch controls retain a minimum 44-pixel height in the redesigned
  navigation and setup tabs.
- Existing reduced-motion handling is preserved.

## Design rationale

Lighting is the most frequent job, so power, brightness, and playback mode are
the first controls on entry. A shared target selector makes independent-strip
behavior explicit before changes are applied. Setup and advanced playback
remain one step away, while diagnostics presents a useful health summary before
revealing lower-frequency technical measurements. The same hierarchy adapts to
touch navigation on phones and persistent section navigation on desktop.
