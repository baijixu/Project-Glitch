"""The user's saved role-play profiles: one freeform markdown file each in profiles/
(character and scenario together, straight into her prompt).

Two separate files, never mixed up (like souls.py's soul.md/rp_soul.md):

- user.md is the user's MAIN file -- who they are, in their own words. Only written
  by hand (Settings' editor, or the file itself); in her prompt while role-play is off.
- rp_user.md is the selected ROLE-PLAY profile: loading a saved profile copies it
  here, and it's her persona while role-play is on.

Kept apart so a role-play profile can never overwrite his own file. rp_user.md is on
disk, so the active profile survives a restart.
"""

from pathlib import Path

from store import Choice, NamedStore, Toggle

USER_MD_PATH = Path(__file__).parent / "user.md"  # the user's main file -- see the module docstring; written only by write_main_user
RP_USER_MD_PATH = Path(__file__).parent / "rp_user.md"  # the selected role-play profile -- everything here that "loads" a profile writes this
# Whether role-play is on, apart from which profile is selected. Off by default, so a fresh
# install doesn't start in role-play with memory, lessons and curiosity paused.
ROLEPLAY_ACTIVE = Toggle(Path(__file__).parent / "roleplay_active.txt", default=False)

# "No profile": not a file, always offered, can't be deleted. Selecting it clears
# rp_user.md; it's what the selection falls back to when the active profile is deleted.
DEFAULT_PROFILE_NAME = "Default"

# One freeform .md per saved profile. DEFAULT_PROFILE_NAME is reserved, so it can't
# be saved as a real profiles/Default.md -- a second, indistinguishable "Default".
STORE = NamedStore(Path(__file__).parent / "profiles", kind="profile", reserved=DEFAULT_PROFILE_NAME, suffix=".md")
# Which one was last loaded, so the dropdown shows it again on reconnect and
# characters._profile_deleted can tell whether it's deleting the one in effect.
ACTIVE = Choice(Path(__file__).parent / "active_profile_name.txt", default=DEFAULT_PROFILE_NAME)

list_profiles = STORE.names
# Not the active-name bookkeeping, even for the active one -- characters._profile_deleted
# decides whether to fall back to DEFAULT_PROFILE_NAME (it knows if role-play is on).
delete_profile = STORE.delete
read_active_profile_name = ACTIVE.read


def save_profile(name: str, content: str) -> None:
    STORE.write_text(name, content)


def read_profile(name: str) -> dict:
    """{"content"}: the editor's Edit (get_profile), without activating it."""
    return {"content": STORE.read_text(name)}


def load_profile(name: str) -> str:
    """Copies the named profile into rp_user.md (selecting it, role-play on or not) and
    returns its content -- "" for DEFAULT_PROFILE_NAME. Never touches user.md."""
    content = "" if name == DEFAULT_PROFILE_NAME else STORE.read_text(name)
    RP_USER_MD_PATH.write_text(content, encoding="utf-8")
    ACTIVE.write(name)
    return content


def write_main_user(content: str) -> None:
    """Overwrites user.md -- the only writer of it, called only by Settings' editor."""
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
