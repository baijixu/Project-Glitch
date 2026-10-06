"""The WebSocket messages, built here for every one Brain sends -- the Python side of protocol.md,
which says what each field means."""

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
RENAME_AVATAR = "rename_avatar"
GET_CHAT_LOGS = "get_chat_logs"
GET_CHAT_LOG = "get_chat_log"
DELETE_CHAT_LOG = "delete_chat_log"
DELETE_AVATAR = "delete_avatar"
SET_ROLEPLAY_ACTIVE = "set_roleplay_active"
SET_ROLEPLAY_ENGINE = "set_roleplay_engine"
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
TEST_REACH_OUT = "test_reach_out"
SET_SAMPLING_PROFILE = "set_sampling_profile"
SAVE_SAMPLING_PROFILE = "save_sampling_profile"
DELETE_SAMPLING_PROFILE = "delete_sampling_profile"
SET_TRAINING_ACTIVE = "set_training_active"
RESOLVE_MEMORY_PROPOSAL = "resolve_memory_proposal"
SET_MEMORY_ACTIVE = "set_memory_active"
CLEAR_MEMORY = "clear_memory"
GET_MEMORY_CONTENT = "get_memory_content"
SET_MEMORY_PROFILE = "set_memory_profile"
SAVE_MEMORY_PROFILE = "save_memory_profile"
DELETE_MEMORY_PROFILE = "delete_memory_profile"
GET_MEMORY_PROFILE = "get_memory_profile"
GET_SOUL_AND_USER = "get_soul_and_user"
SAVE_SOUL_AND_USER = "save_soul_and_user"
GET_NOTES = "get_notes"
SAVE_NOTES = "save_notes"
REGENERATE_LAST = "regenerate_last"
DELETE_LAST = "delete_last"
CLEAR_CONVERSATION = "clear_conversation"
STOP_REPLY = "stop_reply"
SPEECH_INTERRUPTED = "speech_interrupted"
SET_DEBUG_ACTIVE = "set_debug_active"
DEBUG_PING = "debug_ping"
RESTART_BRAIN = "restart_brain"

# Brain -> Renderer message type strings.
PING = "ping"
PLAY_ANIMATION = "play_animation"
SET_EXPRESSION = "set_expression"
SPEAK_TEXT = "speak_text"
SPEAK_AUDIO = "speak_audio"
PROFILES = "profiles"
PROFILE_CONTENT = "profile_content"
SOULS = "souls"
SOUL_CONTENT = "soul_content"
AVATARS = "avatars"
CHAT_LOGS = "chat_logs"
CHAT_LOG_CONTENT = "chat_log_content"
AVATAR_DATA = "avatar_data"
ROLEPLAY_STATE = "roleplay_state"
ROLEPLAY_ENGINE = "roleplay_engine"
VOICE_STATE = "voice_state"
WEB_SEARCH_STATE = "web_search_state"
CURIOSITY_STATE = "curiosity_state"
CURIOSITY_TIMER = "curiosity_timer"
SAMPLING_STATE = "sampling_state"
CONTEXT_USAGE = "context_usage"
CONVERSATION_CLEARED = "conversation_cleared"
TRAINING_STATE = "training_state"
LESSONS_STATE = "lessons_state"
LESSON_EVENT = "lesson_event"
MEMORY_STATE = "memory_state"
MEMORY_CONTENT = "memory_content"
MEMORY_PROFILES = "memory_profiles"
MEMORY_PROFILE_CONTENT = "memory_profile_content"
SOUL_AND_USER_CONTENT = "soul_and_user_content"
NOTES_CONTENT = "notes_content"
MEMORY_LEARNED = "memory_learned"
NO_REPLY = "no_reply"
LEFT_OFF = "left_off"
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
    """Echoes a debug_ping's `ts` back; the Renderer works out the round trip itself."""
    return {"type": DEBUG_PONG, "ts": ts}


def debug_event(category: str, message: str, ms: float | None = None) -> dict:
    """Diagnostics for the debug log -- timings and errors of her own LLM/TTS/STT/harness calls, never
    conversation content -- only to devices with debugging on. `ms` is left out when there's no duration."""
    payload = {"type": DEBUG_EVENT, "category": category, "message": message}
    if ms is not None:
        payload["ms"] = round(ms, 1)
    return payload


def speak_text(text: str, reach_out: bool = False, partial: bool = False) -> dict:
    """Her reply's text. `partial`: one more sentence of a reply still being written
    (the final speak_text then carries all of it). `reach_out`: she spoke first --
    not the reply a device may be waiting for."""
    flags = {"reach_out": reach_out, "partial": partial}
    return {"type": SPEAK_TEXT, "text": text, **{flag: True for flag, on in flags.items() if on}}


def speak_audio(audio_b64: str, sample_rate: int, text: str, frames: list["VisemeFrame"]) -> dict:
    """One clip of her voice -- a sentence, played after the one before -- with the
    text it says (its subtitle) and its mouth shapes over time."""
    return {"type": SPEAK_AUDIO, "audio_b64": audio_b64, "sample_rate": sample_rate, "text": text, "frames": [asdict(f) for f in frames]}


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
    """`avatars` is avatars.list_avatars()'s [{"name", "kind"}], so the picker knows a .vrm from a .png."""
    return {"type": AVATARS, "avatars": avatars}


def chat_logs(mode: str, days: list[dict]) -> dict:
    """The saved chat logs for one mode ("main" or "roleplay"): [{"date", "size"}], newest first."""
    return {"type": CHAT_LOGS, "mode": mode, "days": days}


def chat_log_content(mode: str, date: str, content: str, error: str = "") -> dict:
    return {"type": CHAT_LOG_CONTENT, "mode": mode, "date": date, "content": content, "error": error}


def avatars_changed(avatars: list[dict], renamed: dict | None = None, deleted: str = "") -> dict:
    """The avatar list after a rename or delete, to every device. `renamed` is
    {"from", "to"} (a device showing it just updates the name, no reload);
    `deleted` is the removed name (a device showing it goes back to the
    built-in "Glitch")."""
    return {"type": AVATARS, "avatars": avatars, "renamed": renamed, "deleted": deleted}


def avatar_data(name: str, data_b64: str, kind: str) -> dict:
    return {"type": AVATAR_DATA, "name": name, "data_b64": data_b64, "kind": kind}


def roleplay_state(active: bool) -> dict:
    return {"type": ROLEPLAY_STATE, "active": active}


def roleplay_engine(name: str) -> dict:
    """The engine role-play switches to, or "" to stay on the current one."""
    return {"type": ROLEPLAY_ENGINE, "name": name}


def voice_state(active: bool) -> dict:
    """Whether her voice (TTS) is on (voice_settings.py)."""
    return {"type": VOICE_STATE, "active": active}


def web_search_state(active: bool) -> dict:
    """Whether her own LLM path can search the web; always False with no SearXNG configured."""
    return {"type": WEB_SEARCH_STATE, "active": active}


def training_state(available: bool, active: bool, pending: list, error: str = "") -> dict:
    """Settings -> Memory training: `available` (needs a Hindsight server), on/off, the proposals waiting for
    review ({id, fact, source, created}, oldest first), and `error` when an approval couldn't be saved."""
    return {"type": TRAINING_STATE, "available": available, "active": active, "pending": pending, "error": error}


def conversation_cleared() -> dict:
    """Sent to every device after clear_conversation: she's on a fresh
    conversation, so each device clears its chat panel too."""
    return {"type": CONVERSATION_CLEARED}


def context_usage(used: int, window: int | None, conversation: int | None = None, keep: int | None = None) -> dict:
    """How full her context is, for the Settings meter: `used` of the model's `window` tokens after the latest
    reply (window None if the server doesn't say), and the running `conversation` (estimated) of the `keep`
    she holds before the oldest part is dropped."""
    return {"type": CONTEXT_USAGE, "used": used, "window": window, "conversation": conversation, "keep": keep}


def curiosity_timer(state: str, due_at: float | None, error: str = "") -> dict:
    """Settings -> Curiosity's countdown: `state` "counting" (`due_at`: epoch seconds she may reach out),
    "waiting" (for a reply to her), "off", "roleplay" or "harness". With `error`, only to the device whose
    test_reach_out couldn't run."""
    return {"type": CURIOSITY_TIMER, "state": state, "due_at": due_at, "error": error}


def curiosity_state(active: bool) -> dict:
    """Whether curiosity (curiosity.py) is on."""
    return {"type": CURIOSITY_STATE, "active": active}


def sampling_state(active: str, profiles: dict, builtin: list[str], error: str = "") -> dict:
    """Her sampling profiles by name, the one in use, and the built-in ones (can't be saved over or deleted);
    `error` only to the device whose save/delete was refused."""
    return {"type": SAMPLING_STATE, "active": active, "profiles": profiles, "builtin": builtin, "error": error}


def memory_state(active: bool) -> dict:
    """Whether her own memory (memory.py) is on."""
    return {"type": MEMORY_STATE, "active": active}


def memory_content(entries: list[str]) -> dict:
    """The raw fact list, for Download Memory."""
    return {"type": MEMORY_CONTENT, "entries": entries}


def memory_learned(fact: str) -> dict:
    """A fact the local memory file just gained, for a toast and a chat-history entry. Never sent for a memory
    server: it doesn't hand back what it extracted."""
    return {"type": MEMORY_LEARNED, "fact": fact}


def memory_profiles(profiles: list[dict], active: str, types: list[dict], error: str = "") -> dict:
    """Settings -> Memory: the saved backends [{name, type}], the active one ("Local file" is built in), and
    the kinds that can be added [{type, label, space_label, default_space}]; `error` only to the device whose
    request failed."""
    return {"type": MEMORY_PROFILES, "profiles": profiles, "active": active, "types": types, "error": error}


def memory_profile_content(name: str, profile: dict) -> dict:
    """One saved memory profile for the ✏️ editor ({} if there's none), api_key included."""
    return {"type": MEMORY_PROFILE_CONTENT, "name": name, "profile": profile}


def left_off(note: str) -> dict:
    """Her note on where the conversation just cleared left off -- the Renderer shows it
    in the chat history, so a wrong one can be seen."""
    return {"type": LEFT_OFF, "note": note}


def no_reply() -> dict:
    """Nothing to reply to (empty text, a silent voice message): lets the Renderer re-enable its buttons
    without a blank chat bubble."""
    return {"type": NO_REPLY}


def soul_and_user_content(soul: str, user: str) -> dict:
    """Her main soul.md and user.md (not the role-play ones), for the manual-edit modal."""
    return {"type": SOUL_AND_USER_CONTENT, "soul": soul, "user": user}


def notes_content(content: str) -> dict:
    """notes.md, for Settings' Notes editor."""
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
    """`name` is the connected harness ("" if none). `selected` is what the dropdown shows: the connected one,
    or the last one turned on, so turning a harness off doesn't forget the pick."""
    return {
        "type": HARNESS_STATE,
        "active": active,
        "name": name,
        "selected": selected,
        "available": available,
    }


def harness_content(name: str, endpoint: str, model: str, api_key: str) -> dict:
    """Reply to get_harness: pre-fills the harness editor, like llm_engine_content."""
    return {"type": HARNESS_CONTENT, "name": name, "endpoint": endpoint, "model": model, "api_key": api_key}


def llm_models(endpoint: str, models: list[str]) -> dict:
    return {"type": LLM_MODELS, "endpoint": endpoint, "models": models}


@dataclass
class VisemeFrame:
    t: float
    shape: str
    weight: float


def user_transcript(text: str) -> dict:
    """What STT heard in a voice message, replacing its "🎤 (voice message)" placeholder bubble. Not sent for
    silence -- that's no_reply."""
    return {"type": USER_TRANSCRIPT, "text": text}


def harness_health(name: str, reachable: bool | None) -> dict:
    """Whether `name`'s endpoint answers a TCP check (health.py), for its status light, sent to every device
    when it changes. None if it has no endpoint at all: a different light from False (set up but down)."""
    return {"type": HARNESS_HEALTH, "name": name, "reachable": reachable}


def tts_health(name: str, reachable: bool | None) -> dict:
    """Same as harness_health, for a saved speech engine (never "None")."""
    return {"type": TTS_HEALTH, "name": name, "reachable": reachable}


def tts_voices(name: str, voices: list[str], active_voice: str, can_create_voice: bool, error: str = "") -> dict:
    """The custom (blended) voices a speech engine has, its default, and whether it can make more (voices_dir
    set) -- all empty for "None" or an unknown name. `error` only when combine_kokoro_voice was refused."""
    return {
        "type": TTS_VOICES,
        "name": name,
        "voices": voices,
        "active_voice": active_voice,
        "can_create_voice": can_create_voice,
        "error": error,
    }


def lessons_state(available: bool, active: bool, autonomy: str, lessons: list, pending: list, error: str) -> dict:
    """Settings -> Behavior learning: `available` (needs a Hindsight server), on/off, autonomy, the active
    `lessons` ({id, name, content, priority}, strongest first), the `pending` proposals ({id, action,
    target_id, target_name, name, content, reason}), and `error` if the store couldn't be reached."""
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
    """A one-line chat-history notice after a rating: `kind` "applied", "proposed" (waiting in Settings) or
    "noted" (seen once)."""
    return {"type": LESSON_EVENT, "kind": kind, "text": text}
