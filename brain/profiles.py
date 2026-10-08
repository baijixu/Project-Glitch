"""Manages the user's saved role-play profiles (a Renderer settings-panel
feature). Each profile is one freeform markdown file in
profiles/ -- character description and scenario combined into a single
blob, not separate structured fields, since it's meant to be loaded
straight into an LLM prompt rather than parsed.

Two separate files, never mixed up (the same split souls.py makes for
soul.md/rp_soul.md):

- user.md is the user's MAIN file -- who they actually are, in their own
  words (interests, what they do and don't care about). Only ever written by
  hand (the Settings manual editor, or the file itself); nothing in this
  module can write it. main.py adds it to her prompt every turn while
  role-play is off (LocalLLM.set_user_info).
- rp_user.md is the currently-selected ROLE-PLAY profile. Loading a saved
  profile copies it here, and this is the only file that copying, saving,
  editing or deleting a saved profile ever touches. It's what LocalLLM's
  persona is read from while role-play is on.

They used to be one file (user.md), so loading a role-play profile silently
overwrote whatever the user had put there themselves.

rp_user.md persisting on disk (not just in Brain's memory) means the active
profile survives a Brain restart -- main.py reads it once at startup and
primes the LLM with it, rather than relying on the Renderer to resend it
on every reconnect the way the first version of this feature did.
"""

from pathlib import Path

from store import Choice, NamedStore, Toggle

USER_MD_PATH = Path(__file__).parent / "user.md"  # the user's main file -- see the module docstring; written only by write_main_user
RP_USER_MD_PATH = Path(__file__).parent / "rp_user.md"  # the selected role-play profile -- everything here that "loads" a profile writes this
# Whether the selected profile is layered into her prompt, apart from which one is selected. Off by default:
# it used to default on, so a fresh install started in role-play with memory, lessons and curiosity paused.
ROLEPLAY_ACTIVE = Toggle(Path(__file__).parent / "roleplay_active.txt", default=False)

# Reserved name for "no profile" -- never a real file in profiles/, always
# offered by the Renderer's dropdown (main.js/brain_client.js prepend it
# client-side, same pattern as avatars.py's DEFAULT_AVATAR_NAME), and
# can't be deleted. Selecting it clears rp_user.md (never user.md), so it's what the active
# selection falls back to if the profile that *was* active gets deleted --
# there always has to be something selected, and this is the one entry
# guaranteed to still exist.
DEFAULT_PROFILE_NAME = "Default"

# One freeform .md per saved profile. DEFAULT_PROFILE_NAME is reserved, so it can't
# be saved as a real profiles/Default.md -- a second, indistinguishable "Default".
STORE = NamedStore(Path(__file__).parent / "profiles", kind="profile", reserved=DEFAULT_PROFILE_NAME, suffix=".md")
# Which one was last loaded, so the dropdown shows it again on reconnect and
# characters._delete_profile can tell whether it's deleting the one in effect.
ACTIVE = Choice(Path(__file__).parent / "active_profile_name.txt", default=DEFAULT_PROFILE_NAME)

list_profiles = STORE.names
save_profile = STORE.write_text
read_profile = STORE.read_text  # pre-fills the editor's Edit (get_profile) without activating it
# Not the active-name bookkeeping, even for the active one -- characters._delete_profile
# decides whether to fall back to DEFAULT_PROFILE_NAME (it knows if role-play is on).
delete_profile = STORE.delete
read_active_profile_name = ACTIVE.read


def load_profile(name: str) -> str:
    """Copies the named profile's content into rp_user.md (making it the
    selected one) and returns that content. Never touches user.md -- the
    user's main file. Always writes rp_user.md
    regardless of the role-play toggle (set_roleplay_active) -- selecting
    a profile while role-play is off just queues it for whenever the user
    turns it back on; characters.load_profile is what actually
    decides whether to apply it to the LLM right now.

    DEFAULT_PROFILE_NAME is special-cased to empty content rather than a
    file read -- it's never a real file (see its own docstring above).
    """
    content = "" if name == DEFAULT_PROFILE_NAME else read_profile(name)
    RP_USER_MD_PATH.write_text(content, encoding="utf-8")
    ACTIVE.write(name)
    return content


def write_main_user(content: str) -> None:
    """Overwrites user.md -- the user's main file -- with raw content. The
    ONLY function that writes that file, and only the Settings manual editor
    calls it: nothing about creating, editing, saving, loading or deleting a
    saved role-play profile can reach it.
    """
    USER_MD_PATH.write_text(content, encoding="utf-8")


def read_main_user() -> str:
    """The content of user.md, or "" if there's none yet."""
    if USER_MD_PATH.exists():
        return USER_MD_PATH.read_text(encoding="utf-8")
    return ""


def read_active_profile() -> str:
    """The selected ROLE-PLAY profile's content (rp_user.md), or "" if none
    has ever been loaded. Not the user's main file -- see read_main_user.
    """
    if RP_USER_MD_PATH.exists():
        return RP_USER_MD_PATH.read_text(encoding="utf-8")
    return ""


set_roleplay_active = ROLEPLAY_ACTIVE.write
read_roleplay_active = ROLEPLAY_ACTIVE.read
