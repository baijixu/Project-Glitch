"""Shared WebSocket message schema -- the Python-side mirror of protocol.md
(the actual source of truth). Every message type Brain code sends or
expects to receive from the Renderer is built/validated through this
module; nothing constructs a raw message dict inline elsewhere, so
protocol.md and this file can't silently drift apart from the rest of the
codebase.
"""

from dataclasses import asdict, dataclass

# Renderer -> Brain message type strings.
PONG = "pong"
READY = "ready"
ANIMATION_FINISHED = "animation_finished"
ERROR = "error"
USER_TEXT = "user_text"
USER_AUDIO = "user_audio"
SAVE_PROFILE = "save_profile"
LOAD_PROFILE = "load_profile"
GET_PROFILE = "get_profile"
DELETE_PROFILE = "delete_profile"
SAVE_SOUL = "save_soul"
LOAD_SOUL = "load_soul"
GET_SOUL = "get_soul"
DELETE_SOUL = "delete_soul"
SAVE_AVATAR = "save_avatar"
LOAD_AVATAR = "load_avatar"
SET_ROLEPLAY_ACTIVE = "set_roleplay_active"
SAVE_TTS_ENGINE = "save_tts_engine"
LOAD_TTS_ENGINE = "load_tts_engine"
GET_TTS_ENGINE = "get_tts_engine"
DELETE_TTS_ENGINE = "delete_tts_engine"
GET_TTS_VOICES = "get_tts_voices"
SET_TTS_VOICE = "set_tts_voice"
COMBINE_KOKORO_VOICE = "combine_kokoro_voice"
SAVE_LLM_ENGINE = "save_llm_engine"
LOAD_LLM_ENGINE = "load_llm_engine"
GET_LLM_ENGINE = "get_llm_engine"
DELETE_LLM_ENGINE = "delete_llm_engine"
SET_HARNESS_ACTIVE = "set_harness_active"
SELECT_HARNESS = "select_harness"
GET_LLM_MODELS = "get_llm_models"
SAVE_HARNESS = "save_harness"
GET_HARNESS = "get_harness"
DELETE_HARNESS = "delete_harness"
SET_VOICE_ACTIVE = "set_voice_active"
SET_WEB_SEARCH_ACTIVE = "set_web_search_active"
RATE_REPLY = "rate_reply"
SET_LESSONS_ACTIVE = "set_lessons_active"
SET_LESSONS_AUTONOMY = "set_lessons_autonomy"
SAVE_LESSON = "save_lesson"
RETIRE_LESSON = "retire_lesson"
DELETE_LESSON = "delete_lesson"
RESOLVE_LESSON_PROPOSAL = "resolve_lesson_proposal"
SET_CURIOSITY_ACTIVE = "set_curiosity_active"
SET_TRAINING_ACTIVE = "set_training_active"
RESOLVE_MEMORY_PROPOSAL = "resolve_memory_proposal"
SET_MEMORY_ACTIVE = "set_memory_active"
CLEAR_MEMORY = "clear_memory"
GET_MEMORY_CONTENT = "get_memory_content"
SET_MEMORY_PROVIDER = "set_memory_provider"
SAVE_HINDSIGHT_CONFIG = "save_hindsight_config"
GET_SOUL_AND_USER = "get_soul_and_user"
SAVE_SOUL_AND_USER = "save_soul_and_user"
GET_NOTES = "get_notes"
SAVE_NOTES = "save_notes"
REGENERATE_LAST = "regenerate_last"
STOP_REPLY = "stop_reply"
SET_DEBUG_ACTIVE = "set_debug_active"
DEBUG_PING = "debug_ping"
RESTART_BRAIN = "restart_brain"

# Brain -> Renderer message type strings.
PING = "ping"
PLAY_ANIMATION = "play_animation"
SET_EXPRESSION = "set_expression"
VISEME_STREAM = "viseme_stream"
SPEAK_TEXT = "speak_text"
SPEAK_AUDIO = "speak_audio"
PROFILES = "profiles"
PROFILE_CONTENT = "profile_content"
SOULS = "souls"
SOUL_CONTENT = "soul_content"
AVATARS = "avatars"
AVATAR_DATA = "avatar_data"
ROLEPLAY_STATE = "roleplay_state"
VOICE_STATE = "voice_state"
WEB_SEARCH_STATE = "web_search_state"
CURIOSITY_STATE = "curiosity_state"
TRAINING_STATE = "training_state"
LESSONS_STATE = "lessons_state"
LESSON_EVENT = "lesson_event"
MEMORY_STATE = "memory_state"
MEMORY_CONTENT = "memory_content"
MEMORY_PROVIDER_STATE = "memory_provider_state"
HINDSIGHT_CONFIG = "hindsight_config"
SOUL_AND_USER_CONTENT = "soul_and_user_content"
NOTES_CONTENT = "notes_content"
MEMORY_LEARNED = "memory_learned"
NO_REPLY = "no_reply"
TTS_ENGINES = "tts_engines"
TTS_ENGINE_CONTENT = "tts_engine_content"
TTS_VOICES = "tts_voices"
LLM_ENGINES = "llm_engines"
LLM_ENGINE_CONTENT = "llm_engine_content"
HARNESS_STATE = "harness_state"
HARNESS_CONTENT = "harness_content"
LLM_MODELS = "llm_models"
DEBUG_PONG = "debug_pong"
DEBUG_EVENT = "debug_event"
USER_TRANSCRIPT = "user_transcript"
HARNESS_HEALTH = "harness_health"
TTS_HEALTH = "tts_health"


def ping() -> dict:
    return {"type": PING}


def debug_pong(ts) -> dict:
    """Echoes `ts` (a debug_ping's own timestamp, opaque to Brain) straight
    back -- the Renderer computes round-trip time itself from
    now-minus-ts, so Brain doesn't need to know or care what clock/units
    `ts` is even in.
    """
    return {"type": DEBUG_PONG, "ts": ts}


def debug_event(category: str, message: str, ms: float | None = None) -> dict:
    """Diagnostic-only: connection/timing/error info for the debug log
    (brain_client.js's Debugging toggle) -- never conversation content.
    Only sent to a connection that's turned debugging on (see main.py's
    _DEBUG_CONNECTIONS), and only ever describes *that Brain instance's*
    own external calls (LLM/TTS/STT/harness), not anything said. `ms` is
    omitted (not 0 or null) when a category has no meaningful duration
    (e.g. a refusal that never made a call at all).
    """
    payload = {"type": DEBUG_EVENT, "category": category, "message": message}
    if ms is not None:
        payload["ms"] = round(ms, 1)
    return payload


def speak_text(text: str) -> dict:
    return {"type": SPEAK_TEXT, "text": text}


def speak_audio(audio_b64: str, sample_rate: int) -> dict:
    return {"type": SPEAK_AUDIO, "audio_b64": audio_b64, "sample_rate": sample_rate}


def play_animation(name: str, loop: bool = False) -> dict:
    return {"type": PLAY_ANIMATION, "name": name, "loop": loop}


def set_expression(name: str, weight: float) -> dict:
    return {"type": SET_EXPRESSION, "name": name, "weight": weight}


def profiles(names: list[str], active: str) -> dict:
    return {"type": PROFILES, "names": names, "active": active}


def profile_content(name: str, content: str) -> dict:
    return {"type": PROFILE_CONTENT, "name": name, "content": content}


def souls(names: list[str], active: str) -> dict:
    return {"type": SOULS, "names": names, "active": active}


def soul_content(name: str, description: str, examples: str) -> dict:
    return {"type": SOUL_CONTENT, "name": name, "description": description, "examples": examples}


def avatars(avatars: list[dict]) -> dict:
    """`avatars` is avatars.list_avatars()'s own [{"name", "kind"}, ...]
    shape -- passed straight through, not just names, so the Renderer's
    picker knows whether each entry is a 3D .vrm or a flat .png without a
    round trip per entry.
    """
    return {"type": AVATARS, "avatars": avatars}


def avatar_data(name: str, data_b64: str, kind: str) -> dict:
    return {"type": AVATAR_DATA, "name": name, "data_b64": data_b64, "kind": kind}


def roleplay_state(active: bool) -> dict:
    return {"type": ROLEPLAY_STATE, "active": active}


def voice_state(active: bool) -> dict:
    """Whether Glitch's spoken voice (TTS) is currently turned on -- see
    voice_settings.py. Sent on `ready` so the settings toggle reflects the
    persisted value, same as roleplay_state.
    """
    return {"type": VOICE_STATE, "active": active}


def web_search_state(active: bool) -> dict:
    """Whether Glitch's own LLM path can search the web (brain/web_search.py)
    -- separate from anything a Hermes harness already does with its own
    web/session search when active. Sent on `ready`, same pattern as
    voice_state; always False if no SearXNG instance was ever configured
    (config.yaml's brain.web_search block), regardless of what was saved.
    """
    return {"type": WEB_SEARCH_STATE, "active": active}


def training_state(available: bool, active: bool, pending: list, error: str = "") -> dict:
    """Everything the Settings panel's Memory training section shows
    (brain/training.py) -- sent on `ready`, and to every connected device after
    any change. `available` is False unless the memory provider is a configured
    Hindsight server. `pending` are proposed memories waiting for review, oldest
    first, each {id, fact, source, created}; `error` is "" unless the last
    approval couldn't be saved (the proposal then stays in the list).
    """
    return {"type": TRAINING_STATE, "available": available, "active": active, "pending": pending, "error": error}


def curiosity_state(active: bool) -> dict:
    """Whether curiosity (brain/curiosity.py -- she asks the odd follow-up question) is
    on. Sent on `ready`, same pattern as voice_state.
    """
    return {"type": CURIOSITY_STATE, "active": active}


def memory_state(active: bool) -> dict:
    """Whether Glitch's own native memory of the user (brain/memory.py) is
    currently turned on -- separate from anything Hermes does with its own
    memory when the harness is active. Sent on `ready`, same pattern as
    voice_state.
    """
    return {"type": MEMORY_STATE, "active": active}


def memory_content(entries: list[str]) -> dict:
    """Reply to get_memory_content -- the raw fact list (not the "- "
    prefixed block LocalLLM.set_memory receives), so the Renderer can
    format it however it wants (currently: a plain-text download).
    """
    return {"type": MEMORY_CONTENT, "entries": entries}


def memory_learned(fact: str) -> dict:
    """Sent once, right when brain/memory.py's "local" provider actually
    gains a new fact -- not on every extraction attempt (see main.py's
    _maybe_retain_memory), only when something new was genuinely added.
    Never sent for the "hindsight" provider: its own retain() decides
    what's worth keeping server-side and doesn't hand back the specific
    extracted fact synchronously the way local extraction does. Lets the
    Renderer surface a brief toast + a chat-history entry so it's not a
    silent background process. Unlike debug_event, this is always sent
    regardless of the Debugging toggle -- it's a real user-facing
    feature, not diagnostics.
    """
    return {"type": MEMORY_LEARNED, "fact": fact}


def memory_provider_state(provider: str) -> dict:
    """Which memory backend is currently active ("local" or "hindsight",
    see brain/memory.py) -- sent on `ready`, and again after every
    set_memory_provider, to every connected device (brain.llm is shared
    across all of them, so this isn't a per-connection preference).
    """
    return {"type": MEMORY_PROVIDER_STATE, "provider": provider}


def hindsight_config(api_url: str, api_key: str, bank_id: str) -> dict:
    """The saved Hindsight connection details (brain/memory.py) -- sent on
    `ready`, and again after every save_hindsight_config, to pre-fill the
    Settings panel's Memory Server fields. api_key is echoed back the same
    way a saved TTS/LLM/harness engine's api_key already is.
    """
    return {"type": HINDSIGHT_CONFIG, "api_url": api_url, "api_key": api_key, "bank_id": bank_id}


def no_reply() -> dict:
    """Sent when Brain decided there's genuinely nothing to reply to --
    empty text, no image, and (for user_audio) a transcription that came
    back blank (e.g. a voice message that was silence). Without this, the
    Renderer has nothing to clear its "awaiting a reply" state on (only
    speak_text does that) and the Send/camera/desktop/mic buttons stay
    grayed out forever -- confirmed live as a real stuck-UI bug, not
    hypothetical. Deliberately its own message rather than an empty
    speak_text, so the Renderer doesn't add a blank chat-history bubble.
    """
    return {"type": NO_REPLY}


def soul_and_user_content(soul: str, user: str) -> dict:
    """Reply to get_soul_and_user -- the raw content of her permanent main soul.md
    (NOT the selected role-play soul, which lives in rp_soul.md) and main user.md
    (NOT the selected role-play profile, which lives in rp_user.md), for
    the manual-edit Settings modal (separate from soul_content/profile_content,
    which are keyed by a saved name)."""
    return {"type": SOUL_AND_USER_CONTENT, "soul": soul, "user": user}


def notes_content(content: str) -> dict:
    """Reply to get_notes -- the raw current content of notes.md for the
    Settings' Notes editor."""
    return {"type": NOTES_CONTENT, "content": content}


def tts_engines(names: list[str], active: str) -> dict:
    return {"type": TTS_ENGINES, "names": names, "active": active}


def tts_engine_content(name: str, endpoint: str, api_key: str, voice: str, voices_dir: str, model: str) -> dict:
    return {
        "type": TTS_ENGINE_CONTENT,
        "name": name,
        "endpoint": endpoint,
        "api_key": api_key,
        "voice": voice,
        "voices_dir": voices_dir,
        "model": model,
    }


def llm_engines(names: list[str], active: str) -> dict:
    return {"type": LLM_ENGINES, "names": names, "active": active}


def llm_engine_content(name: str, endpoint: str, model: str, api_key: str, provider: str = "openai", think: bool = False) -> dict:
    return {
        "type": LLM_ENGINE_CONTENT,
        "name": name,
        "endpoint": endpoint,
        "model": model,
        "api_key": api_key,
        "provider": provider,
        "think": think,
    }


def harness_state(active: bool, name: str, selected: str, available: list[str]) -> dict:
    """`name` is the actually-connected harness ("" if not active) --
    `active`/`name` together are what gate _build_harness_llm-derived
    behavior. `selected` is what the dropdown should show regardless of
    active: the connected harness while active, or the last one that was
    ever successfully turned on while it isn't (harness.py's
    read_selected_harness_name) -- distinct from `name` specifically so
    turning a harness off doesn't also make the Renderer forget which one
    was picked (see harness.py's set_selected_harness_name docstring).
    """
    return {
        "type": HARNESS_STATE,
        "active": active,
        "name": name,
        "selected": selected,
        "available": available,
    }


def harness_content(name: str, endpoint: str, model: str, api_key: str) -> dict:
    """Reply to get_harness -- pre-fills the harness editor's Edit flow,
    same pattern as llm_engine_content/tts_engine_content."""
    return {"type": HARNESS_CONTENT, "name": name, "endpoint": endpoint, "model": model, "api_key": api_key}


def llm_models(endpoint: str, models: list[str]) -> dict:
    return {"type": LLM_MODELS, "endpoint": endpoint, "models": models}


@dataclass
class VisemeFrame:
    t: float
    shape: str
    weight: float


def viseme_stream(frames: list[VisemeFrame]) -> dict:
    return {"type": VISEME_STREAM, "frames": [asdict(f) for f in frames]}


def user_transcript(text: str) -> dict:
    """Sent right after STT finishes transcribing a user_audio message --
    the Renderer has no other way to know what was actually heard (it
    only ever sends raw audio, never text, for a voice message), so its
    chat-history bubble starts as a placeholder ("🎤 (voice message)") and
    swaps in this text once it arrives. Not sent for a blank/silent
    transcription -- that path already goes straight to no_reply.
    """
    return {"type": USER_TRANSCRIPT, "text": text}


def harness_health(name: str, reachable: bool | None) -> dict:
    """Whether `name`'s configured endpoint answered a lightweight TCP
    reachability check -- distinct from harness_state's `active` (Glitch
    is actually plugged into it right now): a harness can be reachable but
    not active (available, not currently in use) or active without this
    having reconfirmed reachability yet (still counts as reachable -- an
    active connection already proves it). Sent periodically to every
    connected Renderer (see main.py's _health_check_loop), not just on
    request, so the indicator light updates even with no settings panel
    open to trigger it.

    reachable is None specifically when `name` has no endpoint configured
    at all (its saved harness file is missing/unreadable, or its endpoint
    is empty) -- deliberately distinct from False (configured but down
    right now), which the indicator light renders as a different color:
    nothing to even check is not the same fact as "checked and it's
    unreachable".
    """
    return {"type": HARNESS_HEALTH, "name": name, "reachable": reachable}


def tts_health(name: str, reachable: bool | None) -> dict:
    """Same as harness_health, for one saved speech engine (tts_engines.py)
    -- never sent for tts_engines.NONE_NAME, which has no network endpoint
    to check at all.
    """
    return {"type": TTS_HEALTH, "name": name, "reachable": reachable}


def tts_voices(name: str, voices: list[str], active_voice: str, can_create_voice: bool, error: str = "") -> dict:
    """Which custom voices (kokoro_voices.py) have been created (by
    blending existing ones) for the named speech engine, which one it
    currently defaults to, and whether creating another is even possible
    for it (voices_dir configured) -- lets the Renderer show the voice
    picker/create-voice button, or neither, without needing the engine's
    full saved content (get_tts_engine's job) just to decide that.
    `voices`/`active_voice` are empty and `can_create_voice` is False for
    tts_engines.NONE_NAME or any name that isn't a real saved engine --
    not an error, just nothing to show. Sent in reply to get_tts_voices,
    and again after every combine_kokoro_voice/set_tts_voice for the
    affected engine.

    `error` is "" on every normal reply -- only set when
    combine_kokoro_voice was refused, so the Renderer has something
    concrete to show instead of an attempt that silently appeared to do
    nothing (this used to be a server-console-only print with no reply
    sent at all).
    """
    return {
        "type": TTS_VOICES,
        "name": name,
        "voices": voices,
        "active_voice": active_voice,
        "can_create_voice": can_create_voice,
        "error": error,
    }


def lessons_state(available: bool, active: bool, autonomy: str, lessons: list, pending: list, error: str) -> dict:
    """Everything the Settings panel's Behavior learning section shows
    (brain/lessons.py) -- sent on `ready`, and again to every connected device
    after any change to lessons, proposals or the two settings.

    `available` is False while the memory provider isn't a configured
    Hindsight server (lessons are stored there), in which case the section
    just explains that. `lessons` are the active ones, strongest first, each
    {id, name, content, priority}; `pending` are proposed changes waiting for
    approval, each {id, action, target_id, target_name, name, content, reason};
    `error` is "" unless the lessons store couldn't be reached.
    """
    return {
        "type": LESSONS_STATE,
        "available": available,
        "active": active,
        "autonomy": autonomy,
        "lessons": lessons,
        "pending": pending,
        "error": error,
    }


def lesson_event(kind: str, text: str) -> dict:
    """A one-line notice that something happened to her lessons because of a
    rating -- kind is "applied", "proposed" (waiting for approval in Settings)
    or "noted" (a possible lesson seen once). The Renderer shows it as a
    system entry in the chat history, same as memory_learned.
    """
    return {"type": LESSON_EVENT, "kind": kind, "text": text}
