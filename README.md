# Liscribe v2

> Work in progress — v2 rewrite. See `main` branch for the stable v1.

100% offline Mac audio recorder and transcriber.
Menu bar resident. No terminal required after install.

## Status

Phase 1 complete — engine layer only. UI layer in progress.

## Engine modules (stable)

- `recorder.py` — audio capture, dual-source BlackHole recording
- `transcriber.py` — faster-whisper integration, multi-model
- `output.py` — markdown output, in:/out: labelling, mic bleed suppression
- `notes.py` — timestamped footnotes
- `config.py` — JSON config
- `platform_setup.py` — BlackHole/PortAudio checks
- `waveform.py` — audio level data
