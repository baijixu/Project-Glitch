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
| `set_expression` | `name: string`, `weight: number` (0–1) | Blend a VRM expression preset toward `weight`. Brain sends this once per reply for mood (`happy`/`angry`/`sad`/`relaxed`/`surprised`/`neutral` -- extracted from a `[mood: ...]` tag the LLM is prompted to end every reply with, stripped before it reaches `speak_text`/TTS). The Renderer treats these five as mutually exclusive (setting one fades the others to 0) and `neutral` as "fade all of them to 0" rather than a settable expression of its own. |
| `viseme_stream` | `frames: [{t: number, shape: string, weight: number}]` | Lipsync playback: a timed sequence of viseme shape/weight keyframes, `t` in seconds from stream start. |
| `speak_text` | `text: string` | The LLM's reply, to display as an on-screen subtitle. Sent alongside `speak_audio`/`viseme_stream` once TTS is in the loop -- audio playback is the real "she's speaking" signal, this is just the subtitle companion to it. |
| `speak_audio` | `audio_b64: string`, `sample_rate: number` | TTS output for the current reply -- base64-encoded WAV/PCM audio to play. Paired with a `viseme_stream` sent alongside it for lipsync timed to this same clip. |
| `profiles` | `names: [string]` | The current list of saved role-play profile names (from `brain/profiles/*.md`). Sent once right after `ready`, and again after every `save_profile`. |
| `profile_content` | `name: string`, `content: string` | The content of the named profile, in reply to `get_profile` -- used to pre-fill the profile editor when the user clicks Edit. |

## Renderer → Brain

| `type` | Fields | Meaning |
|---|---|---|
| `pong` | — | Reply to `ping`. |
| `ready` | `model: string` | Sent once, right after the Renderer connects and the `.vrm` model has finished loading. |
| `animation_finished` | `name: string` | The named `play_animation` clip completed. |
| `error` | `message: string` | Something went wrong Renderer-side (failed to load model, unknown animation name, etc.) — reported, not acted on locally. |
| `user_text` | `text: string` | The user typed a message (chat box). |
| `user_audio` | `audio_b64: string`, `mime_type: string` | Push-to-talk mic recording -- transcribed Brain-side (STT) and handled exactly like `user_text` once transcribed. |
| `save_profile` | `name: string`, `content: string` | Create/overwrite a saved role-play profile -- `content` is one freeform markdown blob (character + scenario together), written to `brain/profiles/<name>.md`. Brain replies with an updated `profiles` list. |
| `load_profile` | `name: string` | Make the named saved profile the active one: its content is copied into `brain/user.md` (the single file Brain's LLM reads its persona from) and folded into the system prompt for every subsequent reply. Resets conversation history -- a new profile shouldn't continue an old exchange under the previous one's premise. |
| `get_profile` | `name: string` | Request the named profile's content, to pre-fill the profile editor for the Edit button (`save_profile` under the same name overwrites it once the user saves). Brain replies with `profile_content`. |

## Adding a new message type

1. Add a row to the table above (this file is the source of truth, not the code).
2. Add it to `brain/protocol.py` (the shared schema Brain-side code builds/validates messages
   against).
3. Add the matching handler on the other side (Renderer's `src/brain_client.js`, or the relevant
   Brain subsystem).

Nothing sends or handles a message type that isn't documented here first.
