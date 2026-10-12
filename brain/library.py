"""Her library (Settings -> Library): books the user uploads -- TXT, Markdown, PDF, EPUB,
Word -- that she reads on her own time. Each is kept as plain text (library/<name>.txt);
library.json holds where she is in each, which one she's reading, and her note after
her last pages. The reading itself is a Heartbeat turn (heartbeat.read_pages): she
picks the next book herself, reads PAGE_CHARS at a time and writes a short note. Her
current book and that note reach her when a conversation opens, like her diary.
"""

import base64
import html.parser
import io
import json
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import websockets

import hub
import protocol
from hub import Brain
from store import NamedStore

STORE = NamedStore(Path(__file__).parent / "library", kind="book", suffix=".txt")
STATE_PATH = Path(__file__).parent / "library.json"
FORMATS = (".txt", ".md", ".pdf", ".epub", ".docx")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # under main.py's 100 MB message cap once base64-encoded
PAGE_CHARS = 6000  # about a thousand words a sitting


def extract(filename: str, data: bytes) -> str:
    """The book's text, by file type. Raises ValueError when there's none to be had."""
    suffix = Path(filename).suffix.lower()
    if suffix in (".txt", ".md"):
        text = data.decode("utf-8", errors="replace")
    elif suffix == ".pdf":
        from pypdf import PdfReader

        text = "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    elif suffix == ".epub":
        text = _epub_text(data)
    elif suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as docx:
            body = ET.fromstring(docx.read("word/document.xml"))
        w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        text = "\n\n".join("".join(t.text or "" for t in p.iter(f"{w}t")) for p in body.iter(f"{w}p"))
    else:
        raise ValueError(f"she can read {', '.join(FORMATS)} files")
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    if len(text) < 200:
        raise ValueError("no text in it to read -- a scanned PDF is pictures of pages")
    return text


class HtmlText(html.parser.HTMLParser):
    """The words of an HTML page or XHTML chapter, a blank line after each paragraph or heading;
    no script or style (heartbeat.py reads web pages with it too)."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        self._skipping += tag in ("script", "style", "noscript")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skipping = max(0, self._skipping - 1)
        elif tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n\n")


def _epub_text(data: bytes) -> str:
    """An EPUB's chapters in reading order: container.xml names the package file, whose
    spine lists the chapters by manifest id."""
    with zipfile.ZipFile(io.BytesIO(data)) as epub:
        container = ET.fromstring(epub.read("META-INF/container.xml"))
        opf_path = next(e.get("full-path") for e in container.iter() if e.tag.endswith("rootfile"))
        opf = ET.fromstring(epub.read(opf_path))
        manifest = {e.get("id"): e.get("href") for e in opf.iter() if e.tag.endswith("}item")}
        chapters = []
        for ref in (e for e in opf.iter() if e.tag.endswith("}itemref")):
            parser = HtmlText()
            parser.feed(epub.read(posixpath.join(posixpath.dirname(opf_path), manifest[ref.get("idref")])).decode("utf-8", errors="replace"))
            chapters.append("".join(parser.parts))
    return "\n\n".join(chapters)


def read_state() -> dict:
    """{"reading": name | None, "last": name | None, "books": {name: {"title", "pos", "chars", "note", "finished"}}}"""
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("reading", None)
    state.setdefault("last", None)
    state.setdefault("books", {})
    return state


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def add_book(filename: str, data: bytes) -> str:
    text = extract(filename, data)
    name = STORE.write_text(Path(filename).stem, text)
    state = read_state()
    state["books"][name] = {"title": Path(filename).stem, "pos": 0, "chars": len(text), "note": "", "finished": False}
    save_state(state)
    return name


def delete_book(name: str) -> None:
    STORE.delete(name)
    state = read_state()
    state["books"].pop(name, None)
    state["reading"] = None if state["reading"] == name else state["reading"]
    state["last"] = None if state["last"] == name else state["last"]
    save_state(state)


def unread(state: dict) -> list[str]:
    return [name for name, book in state["books"].items() if not book["finished"]]


def next_pages(text: str, pos: int) -> tuple[str, int]:
    """About PAGE_CHARS from `pos`, ending at a paragraph (or at least a sentence) break."""
    end = pos + PAGE_CHARS
    if end >= len(text):
        return text[pos:].strip(), len(text)
    cut = text.rfind("\n\n", pos + PAGE_CHARS // 2, end)
    if cut < 0:
        cut = text.rfind(". ", pos + PAGE_CHARS // 2, end) + 1 or end
    return text[pos:cut].strip(), cut


def percent(book: dict) -> int:
    return 100 if book["finished"] else min(99, round(100 * book["pos"] / max(book["chars"], 1)))


def reading_line() -> str:
    """What reaches her as a conversation opens: the book she's on (or just finished) and her latest note."""
    state = read_state()
    book = state["books"].get(state["last"] or "")
    if not book or not book["note"]:
        return ""
    where = "you just finished it" if book["finished"] else f"you're {percent(book)}% in"
    return f"The book you're reading on your own time: {book['title']} ({where}). Your note after your last pages:\n{book['note']}"


def library_message(error: str = "") -> dict:
    state = read_state()
    books = [
        {"name": name, "title": book["title"], "percent": percent(book), "reading": name == state["reading"]}
        for name, book in state["books"].items()
    ]
    return protocol.library(books, error)


@hub.handles(protocol.ADD_BOOK, protocol.DELETE_BOOK)
async def _book(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    filename = str(data.get("name") or "")
    try:
        if data["type"] == protocol.DELETE_BOOK:
            delete_book(filename)
        else:
            raw = base64.b64decode(str(data.get("data_b64") or ""), validate=True)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise ValueError(f"that file is over {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
            add_book(filename, raw)
    except Exception as exc:  # a broken file of any kind is just a book she can't read
        print(f"[library] couldn't {data['type']} {filename!r}: {exc!r}")
        return await hub.send(websocket, library_message(f"Couldn't add {filename}: {exc}" if data["type"] == protocol.ADD_BOOK else str(exc)))
    await hub.broadcast(library_message())
