# NanoTune

NanoTune is a lightweight, 100% offline Windows music player and high-FPS lyrics engine developed by **mirakwgnr** (`@mirakwgnr` on Instagram). Built for instant library indexing and smooth high-refresh-rate rendering across both modern and legacy PC hardware, it pairs adaptive cover-art theming with a threaded real-time audio FX rack and a word-by-word karaoke lyrics view[cite: 3, 5, 11].

## Features

* **Audio Playback:** Plays local FLAC (16/24-bit), WAV, MP3, M4A, OGG, and OPUS files across multiple music directories with optional subfolder scanning and metadata caching[cite: 3, 5].
* **Adaptive Cover Theming:** Extracts theme accents dynamically from album artwork—shifting the UI, active controls, and Star icon to match each cover, with a neutral silver-grey fallback for monochrome/B&W artwork[cite: 3, 5, 11].
* **Real-Time Audio FX Rack:** Adjust playback speed and pitch (with optional pitch-speed linking), warm 155 Hz sub-bass boost, hall reverb, and an independent 1400 Hz Lo-Fi Muffle filter, plus quick presets (`Normal`, `Lo-Fi Muffle`, `Slowed + Reverb`, `Warm Bass`, `Sped Up`)[cite: 3, 5, 11].
* **Synced & Word-by-Word Lyrics:** Supports `.ELRC` word timestamps, standard `.LRC` files, and embedded FLAC / ID3 / MP4 lyrics, complete with click-to-seek lines and a built-in editor with live timestamp stamping[cite: 3, 5, 10].
* **Hardware Renderer Auto-Detection:** Probes DirectX 10.1/10.0 and OpenGL 2.1/2.0 capabilities on startup, with manual switching between DirectX, OpenGL, and CPU Software Rendering in **Settings**[cite: 3, 4, 5].
* **Windows Shell & Controls:** Includes Windows 10/11 SMTC media transport/thumbnail integration, system tray controls, a collapsible sidebar, `Space` to play/pause, and `F11` for fullscreen[cite: 3, 5, 6].

## System Requirements

| Component | Minimum | Recommended |
| :--- | :--- | :--- |
| **OS** | Windows 7 SP1 / 8 / 8.1 (64-bit)* | Windows 10 (1809+) / 11 (64-bit) |
| **Processor** | Intel Core i5-650 (3.2 GHz) or AMD Phenom II X4 955 / Athlon II X2 260 (or equivalent x86-64 CPU) | Intel Core i5-6500 (3.2 GHz) or AMD Ryzen 3 1200 (3.1 GHz) |
| **Memory** | 2 GB RAM (512 MB available) | 4 GB RAM or higher (for large FLAC + DSP libraries) |
| **Graphics** | DirectX 10.0 or OpenGL 2.0 compatible GPU (e.g., GeForce 8600 GT / Radeon HD 2600 XT) or CPU Rasterizer | DirectX 10.1 or OpenGL 2.1 compatible GPU (e.g., GTX 750 Ti / Radeon R7 260X) with 60Hz+ output |
| **Storage** | 150 MB available disk space | 250 MB+ SSD (for cover cache) |
| **Audio** | DirectSound / WASAPI compatible sound device | WASAPI Exclusive-capable DAC or onboard audio chipset |

*\*Legacy Windows 7/8/8.1 environments require compatible runtime builds and omit the Windows 10/11 SMTC lockscreen/volume overlay[cite: 5].*

## Installation & Quick Start

1. Download and run the NanoTune installer (close any running instances first)[cite: 5].
2. On first launch, choose one or more music directories[cite: 4]. You can add or remove folders anytime under **Settings**[cite: 3, 4].
3. Browse your library via **Tracks**, **Albums**, or **Now Playing & Lyrics** from the sidebar[cite: 4].
4. Click **FX** on the bottom player bar to open the audio effects drawer, or click **Enter / Edit Lyrics** on the lyrics screen to add timestamps and save `.LRC`/`.ELRC` files or embed tags[cite: 3, 4, 10].

## User Data & Diagnostics

* **Config & Cached Covers:** `%APPDATA%\NanoTune\config.json` and `%APPDATA%\NanoTune\covers`[cite: 4]
* **Crash Log:** `%APPDATA%\NanoTune\crash.log`[cite: 4]
* **Registry Key:** `HKEY_CURRENT_USER\Software\NanoTune`[cite: 4]
* **Uninstall:** Remove anytime via Windows **Installed Apps**[cite: 4].

## Privacy & License

NanoTune operates strictly offline as a local desktop audio player with no telemetry, analytics, or external network calls[cite: 5, 6].

Licensed under the **GNU General Public License v2.0 (GPL-2.0)**[cite: 6]. See `LICENCE.txt` or the [full GPL-2.0 license text](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt) for details[cite: 6].
