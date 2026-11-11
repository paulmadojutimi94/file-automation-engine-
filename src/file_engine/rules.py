"""Which folder does a file belong in? Decided by its extension only."""

from __future__ import annotations

import json
from pathlib import Path

from . import safety

# fmt: off
DEFAULT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Images": (".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".webp", ".bmp", ".tif", ".tiff",
               ".svg", ".raw", ".cr2", ".nef"),
    "Documents": (".pdf", ".doc", ".docx", ".txt", ".md", ".markdown", ".rtf", ".odt", ".pages", ".epub",
                  ".mobi", ".azw3", ".djvu", ".tex", ".log"),
    "Spreadsheets": (".xls", ".xlsx", ".xlsm", ".csv", ".tsv", ".numbers", ".ods"),
    "Presentations": (".ppt", ".pptx", ".key", ".odp"),
    "Audio": (".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".aiff", ".opus"),
    "Video": (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv"),
    "Archives": (".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar", ".lz", ".lzma", ".cab",
                 ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"),
    "Code": (".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".html", ".htm", ".css", ".scss", ".sass", ".less",
             ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".sh", ".bat", ".ps1", ".ipynb", ".sql",
             ".swift", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".r",
             ".lua", ".pl"),
    "Installers": (".dmg", ".pkg", ".exe", ".msi", ".deb", ".rpm", ".apk", ".appimage"),
    "Fonts": (".ttf", ".otf", ".woff", ".woff2"),
}
# fmt: on
OTHER = "Other"

# Files people recognise by their whole name, not an extension.
KNOWN_NAMES: dict[str, str] = {
    name: "Code" for name in ("dockerfile", "makefile", "gemfile", "rakefile", "procfile", "vagrantfile", "jenkinsfile")
}


class Rules:
    """Maps an extension (".jpg") to a category folder name ("Images")."""

    def __init__(self, categories: dict[str, tuple[str, ...] | list[str]] | None = None):
        categories = DEFAULT_CATEGORIES if categories is None else categories
        self.by_ext: dict[str, str] = {}
        self.categories = set(categories)
        for category, exts in categories.items():
            if not safety.is_safe_component(category) or category.startswith("."):
                raise ValueError(f"category name {category!r} must be a plain folder name")
            for ext in exts:
                ext = ext.lower() if ext.startswith(".") else "." + ext.lower()
                if self.by_ext.get(ext, category) != category:
                    raise ValueError(f"{ext} is listed under both {self.by_ext[ext]} and {category}")
                self.by_ext[ext] = category
        # multi-part extensions (".tar.gz") are checked before single ones (".gz")
        self.compound = sorted((e for e in self.by_ext if e.count(".") > 1), key=len, reverse=True)

    @classmethod
    def from_json(cls, path: Path | str) -> Rules:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, list) and all(isinstance(e, str) for e in v) for k, v in data.items()
        ):
            raise ValueError('rules file must look like {"Images": [".jpg", ".png"], ...}')
        return cls(data)

    def extension(self, name: str) -> str:
        """Lower-cased extension, including known compound ones: 'A.TAR.GZ' -> '.tar.gz'."""
        lower = name.lower()
        for ext in self.compound:
            if lower.endswith(ext) and len(lower) > len(ext):
                return ext
        dot = lower.rfind(".")
        if dot <= 0 or dot == len(lower) - 1:  # no dot, ".bashrc", or "name."
            return ""
        return lower[dot:]

    def category(self, name: str) -> str:
        known = KNOWN_NAMES.get(name.lower())
        if known in self.categories:  # custom rules without a Code folder ignore this table
            return known
        return self.by_ext.get(self.extension(name), OTHER)
