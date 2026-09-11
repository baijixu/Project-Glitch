# Glitch control protocol (Renderer ↔ Brain)

Source of truth for the WebSocket message contract between the Renderer and the Brain (SPEC.md
section 6). Both sides must only send message types listed here — nothing undocumented.

**Direction:** the Brain hosts the WebSocket server; the Renderer is always the client, connecting
out to whatever `ws://<host>:<port>` it's configured with (same-machine or LAN, just a config
value on both sides). Fixed by SPEC.md section 5 — browser/WebView JS can only open outbound
WebSocket connections, it cannot listen for them.

**Transport:** one JSON object per WebSocket text frame. Every message has a `type` field.

**Hard rule:** the Renderer never makes decisions. It only executes commands sent to it and
reports state/events back. All "thinking" happens in the Brain.

## Brain → Renderer

| `type` | Fields | Meaning |
|---|---|---|
| `ping` | — | Liveness check. Renderer replies with `pong`. |
| `play_animation` | `name: string`, `loop: bool` | Play a named animation clip. |
| `set_expression` | `name: string`, `weight: number` (0–1) | Blend a VRM expression preset toward `weight`. |
| `viseme_stream` | `frames: [{t: number, shape: string, weight: number}]` | Lipsync playback: a timed sequence of viseme shape/weight keyframes, `t` in seconds from stream start. |

## Renderer → Brain

| `type` | Fields | Meaning |
|---|---|---|
| `pong` | — | Reply to `ping`. |
| `ready` | `model: string` | Sent once, right after the Renderer connects and the `.vrm` model has finished loading. |
| `animation_finished` | `name: string` | The named `play_animation` clip completed. |
| `error` | `message: string` | Something went wrong Renderer-side (failed to load model, unknown animation name, etc.) — reported, not acted on locally. |

## Adding a new message type

1. Add a row to the table above (this file is the source of truth, not the code).
2. Add it to `brain/protocol.py` (the shared schema Brain-side code builds/validates messages
   against).
3. Add the matching handler on the other side (Renderer's `src/brain_client.js`, or the relevant
   Brain subsystem).

Nothing sends or handles a message type that isn't documented here first.
