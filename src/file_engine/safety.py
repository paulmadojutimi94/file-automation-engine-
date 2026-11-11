"""Every defence against hostile file names and paths lives here, so each one
can be switched off on its own by the attack eval (evals/attacks.py)."""

from __future__ import annotations

import html
import unicodedata
from pathlib import Path


class UnsafePathError(ValueError):
    """A computed path would land outside the folder we are allowed to write to."""


def is_safe_component(name: str) -> bool:
    """True if `name` can only ever be ONE path segment (no '/', no '..')."""
    return name not in {"", ".", ".."} and "/" not in name and "\\" not in name and "\x00" not in name


def ensure_inside(path: Path, root: Path) -> None:
    """Raise unless `path` really lives under `root` once symlinks are followed.

    `resolve()` follows any symlinked folder on the way, so a planted
    `Documents -> /etc` link is caught here, not after the move.
    """
    if not path.resolve().is_relative_to(root.resolve()):
        raise UnsafePathError(f"{path} resolves outside {root}")


def display(text: str) -> str:
    """Make a file name safe to print: control and invisible format characters
    (ANSI escapes, newlines, right-to-left overrides) become visible escapes."""
    out = []
    for ch in text:
        if unicodedata.category(ch).startswith("C"):  # Cc control, Cf format, Cs, Co, Cn
            code = ord(ch)
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


_MD_SPECIAL = "\\`*_[]#|"


def md_cell(text: str) -> str:
    """Escape a value for a Markdown table cell."""
    text = html.escape(display(text), quote=False)  # < > & cannot open HTML tags
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in text)


def csv_cell(text: str) -> str:
    """Stop spreadsheet apps treating a file name as a formula (CSV injection)."""
    text = display(text)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text
