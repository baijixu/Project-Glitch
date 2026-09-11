# Glitch — Technical Spec

## 0. Overview

Glitch is a 3D AI companion: a VRM avatar rendered on-screen, driven by a Python backend that handles conversation, voice, and behavior. The architecture keeps rendering and "thinking" as two separate components talking over a small, well-defined protocol — this keeps each side simple, swappable, and easy to debug independently.

**Ground rule for whoever (or whatever) builds this: do not deviate from this architecture without writing the deviation down and asking first.** No silent forks, no ad hoc scripting outside what's specified here.

---

## 1. Goals

- Native-feeling desktop app, not "a Python script that also opens a browser tab."
- High-quality VRM rendering: animations, expressions, and spring-bone physics all working smoothly.
- All orchestration logic (LLM calls, STT/TTS, Discord, skills) lives in Python.
- Runs on **Windows, macOS, and Linux** without OS-specific forks of the core logic.
- Renderer and Brain can run on the **same machine or different machines on the LAN** (e.g. Brain on the Mac Studio, Renderer on box1).
- No PowerShell, anywhere, for any reason. If a setup step needs a shell script, it's POSIX `sh`/`bash` for macOS/Linux and a `.bat`/`.py` for Windows — never `.ps1`.

## 2. Non-goals

- Not building the renderer in a Python-native 3D engine (Panda3D, PyOpenGL, etc.). No mature Python VRM renderer matches three-vrm's spring-bone physics, blendshape handling, and humanoid bone mapping.
- Not merging renderer and backend into one process/codebase. They stay separate and only talk over a defined API.
- No persistent memory/recall system in this build. Out of scope for now — may be revisited later as a separate, deliberate addition.

---

## 3. Architecture

Two independent components, one contract between them.

```
┌─────────────────────────────┐         WebSocket + HTTP        ┌──────────────────────────────┐
│         RENDERER             │◄────────────(LAN or local)─────►│           BRAIN               │
│  (three.js + three-vrm,      │                                  │  (Python backend)             │
│   runs inside pywebview      │                                  │                                │
│   or plain browser window)   │                                  │  - LLM orchestration           │
│                               │                                  │  - STT / TTS                   │
│  - Loads/renders .vrm model  │                                  │  - Discord integration          │
│  - Plays animations          │                                  │  - Skills (ComfyUI, etc.)       │
│  - Sets expressions/visemes  │                                  │  - Decides WHAT the model does  │
│  - Reports back state/events │                                  │                                │
└─────────────────────────────┘                                  └──────────────────────────────┘
     lives on: box1 (or wherever                                       lives on: Mac Studio (or
     you want a screen/window)                                         wherever the Brain runs)
```

**Hard rule:** the Renderer never makes decisions. It only executes commands sent to it and reports state/events back. All "thinking" happens in the Brain.

---

## 4. Renderer component

- **Stack:** three.js + `@pixiv/three-vrm`, vanilla JS or a minimal bundler (Vite recommended — fast, simple, well-documented). No framework bloat (no React/Vue needed for this).
- **Host shell:** `pywebview` (Python) wrapping the local HTML/JS app in a native window. This is the *only* Python involved in the Renderer component, and its job is limited to: open a window, load `index.html`, expose OS-level niceties (always-on-top, fullscreen toggle, tray icon) if wanted. It does not contain app logic.
- **Responsibilities:**
  - Load and display the `.vrm` model.
  - Play animation clips / blend expressions on command.
  - Accept a viseme/phoneme stream for lipsync during TTS playback.
  - Connect out to the Brain's WebSocket **server** as a **client** (browser engines can't bind listening sockets, so the direction is fixed: Brain hosts, Renderer connects).
  - Emit events back over that same connection (model loaded, animation finished, error) so the Brain can react.
  - Reconnect/retry if the connection drops — the Brain may restart independently of the Renderer window.
- **Explicitly out of scope for this component:** any LLM/API calls, any Discord code. If Claude Code adds any of that here, stop and flag it.

## 5. Brain component (Python)

- **Stack:** Python 3.11+. LLM client/orchestration, STT/TTS, Discord bot, ComfyUI skill calls.
- **Responsibilities:**
  - Own the conversation loop end-to-end.
  - Decide what expression/animation/viseme stream to send and when.
  - **Host the WebSocket server** the Renderer connects to. This direction is fixed, not a design choice — browser/WebView JS can only open outbound WebSocket connections, it cannot listen for them.
- **Config:** a single `.env` or `config.yaml` with the bind address/port the Brain listens on. The Renderer's config just needs to know that same address (`ws://<host>:<port>`) to connect to, so same-machine vs. LAN is just a config value on both sides, not a code branch.

## 6. Control protocol (Renderer ↔ Brain)

**Direction:** Brain hosts the WebSocket server; Renderer is always the client (see §5). This holds true whether they're on the same machine or across the LAN — the Renderer just dials out to whatever address it's configured with.

Keep the message schema small and versioned from day one.

```jsonc
// Brain → Renderer
{ "type": "play_animation", "name": "wave", "loop": false }
{ "type": "set_expression", "name": "happy", "weight": 0.8 }
{ "type": "viseme_stream", "frames": [{"t": 0.0, "shape": "aa", "weight": 0.6}, ...] }
{ "type": "ping" }

// Renderer → Brain
{ "type": "ready", "model": "glitch.vrm" }
{ "type": "animation_finished", "name": "wave" }
{ "type": "error", "message": "..." }
{ "type": "pong" }
```

Define this list exhaustively in the actual codebase (e.g. `protocol.md` or a shared JSON schema) before either side starts sending messages nobody documented.

---

## 7. Cross-platform requirements

| Concern | Windows | macOS | Linux |
|---|---|---|---|
| pywebview backend | Edge WebView2 (bundled w/ Win10/11) | WKWebView (native) | WebKitGTK — needs `python3-gi`, `gir1.2-webkit2-4.1` (or distro equivalent) installed |
| Audio capture/playback | `sounddevice`/`pyaudio` — verify device enumeration works | same libs, verify mic permission prompt handled | same libs, verify PulseAudio/PipeWire compatibility |
| GPU/model backend calls (LM Studio, ComfyUI) | HTTP calls to local/LAN endpoints — no OS branching needed | same | same |
| Setup script | `.bat` or plain Python (`setup.py` / `install.py`), **never `.ps1`** | `.sh` | `.sh` |
| Packaging (later, optional) | PyInstaller | PyInstaller / py2app | PyInstaller / AppImage |

**Rule:** any OS-specific code lives behind a single small abstraction (e.g. `platform_utils.py`) that the rest of the app calls into — never scattered `if sys.platform == ...` checks throughout the codebase.

---

## 8. Directory structure (proposed)

```
glitch/
├── renderer/                # three.js + three-vrm app
│   ├── index.html
│   ├── src/
│   ├── assets/
│   │   └── glitch.vrm
│   ├── package.json
│   └── shell/                # pywebview wrapper only
│       └── launch.py
├── brain/                    # Python backend
│   ├── main.py
│   ├── llm/
│   ├── voice/                # STT/TTS
│   ├── discord_bot/
│   ├── skills/
│   ├── protocol.py            # shared message schema
│   └── platform_utils.py
├── protocol.md                # source of truth for the WS message contract
├── config.example.yaml
├── setup.sh                   # macOS/Linux
├── setup.bat                  # Windows
└── SPEC.md                    # this document, kept in the repo
```

---

## 9. Build order (recommended)

1. **Renderer first, standalone.** Get the `.vrm` loading and animating correctly in a plain browser tab before touching pywebview or Python at all. Verify animation quality here — this is the highest-risk part of the project to get wrong.
2. **Wrap in pywebview.** Confirm the same behavior inside the native window shell.
3. **Define and implement `protocol.md`.** Get a bare-bones WebSocket ping/pong working between a throwaway Python script and the Renderer.
4. **Bring in Brain logic incrementally**, one subsystem at a time (LLM → STT/TTS → Discord → skills), verifying the protocol calls at each step rather than building everything at once.
5. **Cross-platform pass last** — get it fully working on your primary OS first, then verify/fix on the other two.

## 10. Guardrails for whoever builds this

- No new files outside `renderer/` or `brain/` without a reason documented in a commit message.
- No PowerShell. Ever. If something seems to require it, that's a sign to find a Python or POSIX-shell alternative, not to write the `.ps1`.
- Any deviation from this spec gets written down before it's implemented, not after.
