"""Parse a debug reference map and load only model-selected planning guidance."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_SELECTION_KEYS = {
    "category_references",
    "feature_references",
    "deferred_references",
}


@dataclass(frozen=True)
class DebugReferenceCatalog:
    directory: str
    reference_map_text: str
    required_references: tuple[str, ...]
    category_references: tuple[str, ...]
    feature_references: tuple[str, ...]
    deferred_references: tuple[str, ...]
    visual_references: tuple[tuple[str, tuple[str, ...]], ...]
    sha256: str

    def response_schema(self, *, allow_deferred: bool = False) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": sorted(_SELECTION_KEYS),
            "properties": {
                "category_references": _selection_array(
                    self.category_references, min_items=1
                ),
                "feature_references": _selection_array(
                    self.feature_references, min_items=1
                ),
                "deferred_references": _selection_array(
                    self.deferred_references if allow_deferred else (), min_items=0
                ),
            },
        }

    def normalize_selection(
        self, selection: Mapping[str, Any]
    ) -> dict[str, tuple[str, ...]]:
        if not isinstance(selection, Mapping) or set(selection) != _SELECTION_KEYS:
            raise ValueError(
                "debug reference selection must contain exactly category_references, "
                "feature_references and deferred_references"
            )
        normalized = {
            "category_references": _validate_selected_group(
                selection["category_references"],
                self.category_references,
                "category_references",
                require_one=True,
            ),
            "feature_references": _validate_selected_group(
                selection["feature_references"],
                self.feature_references,
                "feature_references",
                require_one=True,
            ),
            "deferred_references": _validate_selected_group(
                selection["deferred_references"],
                self.deferred_references,
                "deferred_references",
                require_one=False,
            ),
        }
        return normalized

    def selected_visual_references(
        self, selection: Mapping[str, tuple[str, ...]]
    ) -> tuple[str, ...]:
        selected = {
            path
            for group in _SELECTION_KEYS
            for path in selection[group]
        }
        return tuple(
            image
            for reference, images in self.visual_references
            if reference in selected
            for image in images
        )


@dataclass(frozen=True)
class DebugPromptImage:
    relative_path: str
    path: str
    sha256: str
    media_type: str


@dataclass(frozen=True)
class DebugPromptSource:
    directory: str
    files: tuple[str, ...]
    text: str
    sha256: str
    selection: dict[str, tuple[str, ...]]
    images: tuple[DebugPromptImage, ...]


def load_debug_reference_catalog(directory: str) -> DebugReferenceCatalog:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"debug prompt directory does not exist: {directory}")

    skill = _safe_markdown_path(root, root / "skill.md")
    if not skill.is_file():
        raise ValueError("debug prompt directory must contain skill.md")
    reference_map = _safe_markdown_path(root, root / "references" / "reference-map.md")
    if not reference_map.is_file():
        raise ValueError(
            "selective debug prompt directory must contain references/reference-map.md"
        )

    map_bytes = reference_map.read_bytes()
    map_text = map_bytes.decode("utf-8-sig")
    groups: dict[str, list[str]] = {
        "required": [],
        "category": [],
        "feature": [],
        "deferred": [],
    }
    visual_references: list[tuple[str, tuple[str, ...]]] = []
    active_group: str | None = None
    for line in map_text.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading == "基础资料":
                active_group = "required"
            elif heading.startswith("第二步"):
                active_group = "category"
            elif heading.startswith("第三步"):
                active_group = "feature"
            elif heading == "默认不启用":
                active_group = "deferred"
            else:
                active_group = None
            continue
        if active_group is None:
            continue
        markdown: list[str] = []
        images: list[str] = []
        for target in _LINK_RE.findall(line):
            reference = _map_local_reference(root, reference_map.parent, target)
            if reference is None:
                continue
            relative, kind = reference
            if kind == "markdown":
                markdown.append(relative)
                groups[active_group].append(relative)
            elif kind == "image":
                images.append(relative)
        if images:
            if len(markdown) != 1:
                raise ValueError(
                    "each reference-map.md visual row must link exactly one Markdown rule"
                )
            visual_references.append((markdown[0], tuple(images)))

    if any(not groups[name] for name in ("required", "category", "feature")):
        raise ValueError(
            "reference-map.md must define required, category and feature Markdown references"
        )
    all_references = [path for paths in groups.values() for path in paths]
    if len(set(all_references)) != len(all_references):
        raise ValueError("reference-map.md lists one Markdown file more than once")
    all_images = [
        image for _reference, images in visual_references for image in images
    ]
    if len(set(all_images)) != len(all_images):
        raise ValueError("reference-map.md lists one visual reference more than once")

    digest = hashlib.sha256()
    digest.update(b"references/reference-map.md\0")
    digest.update(map_bytes)
    digest.update(b"\0")
    return DebugReferenceCatalog(
        directory=str(root),
        reference_map_text=map_text,
        required_references=tuple(groups["required"]),
        category_references=tuple(groups["category"]),
        feature_references=tuple(groups["feature"]),
        deferred_references=tuple(groups["deferred"]),
        visual_references=tuple(visual_references),
        sha256=digest.hexdigest(),
    )


def load_debug_prompt_directory(
    directory: str, selection: Mapping[str, Any]
) -> DebugPromptSource:
    catalog = load_debug_reference_catalog(directory)
    normalized = catalog.normalize_selection(selection)
    selected_images = catalog.selected_visual_references(normalized)
    root = Path(catalog.directory)
    names = (
        "skill.md",
        "references/reference-map.md",
        *catalog.required_references,
        *(
            path
            for group in (
                "category_references",
                "feature_references",
                "deferred_references",
            )
            for path in normalized[group]
        ),
    )

    chunks: list[str] = []
    digest = hashlib.sha256()
    for relative in names:
        path = _safe_markdown_path(root, root / Path(relative))
        if not path.is_file():
            raise ValueError(f"selected debug prompt reference does not exist: {relative}")
        content = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
        chunks.append(f"\n## {relative}\n\n{content.decode('utf-8-sig')}\n")

    images: list[DebugPromptImage] = []
    if selected_images:
        chunks.append(
            "\n## Selected visual references\n\n"
            + "\n".join(
                f"- {relative} (attached as a verified debug reference image)"
                for relative in selected_images
            )
            + "\n"
        )
    for relative in selected_images:
        path = _safe_path(root, root / Path(relative))
        if not path.is_file():
            raise ValueError(f"selected debug prompt image does not exist: {relative}")
        content = path.read_bytes()
        media_type = _image_media_type(path)
        _validate_image_signature(content, media_type, relative)
        image_sha256 = hashlib.sha256(content).hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
        images.append(
            DebugPromptImage(
                relative_path=relative,
                path=str(path),
                sha256=image_sha256,
                media_type=media_type,
            )
        )

    return DebugPromptSource(
        directory=str(root),
        files=tuple(names),
        text="".join(chunks).strip(),
        sha256=digest.hexdigest(),
        selection=normalized,
        images=tuple(images),
    )


def _selection_array(values: tuple[str, ...], *, min_items: int) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": min_items,
        "maxItems": len(values),
        "uniqueItems": True,
        "items": (
            {"type": "string", "enum": list(values)} if values else False
        ),
    }


def _validate_selected_group(
    value: Any,
    allowed: tuple[str, ...],
    label: str,
    *,
    require_one: bool,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"{label} must be an array of reference-map paths")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must not contain duplicate paths")
    if require_one and not value:
        raise ValueError(f"{label} must select at least one reference")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{label} contains paths outside reference-map.md: {unknown}")
    selected = set(value)
    return tuple(path for path in allowed if path in selected)


def _map_local_reference(
    root: Path, map_directory: Path, target: str
) -> tuple[str, str] | None:
    clean = target.strip()
    if not clean or clean.startswith(("#", "http://", "https://")):
        return None
    if "#" in clean or "?" in clean:
        raise ValueError(f"reference-map.md link must be a plain relative path: {target}")
    path = _safe_path(root, map_directory / Path(clean))
    suffix = path.suffix.lower()
    if suffix not in {".md", ".png", ".jpg", ".jpeg"}:
        return None
    if not path.is_file():
        raise ValueError(f"reference-map.md target does not exist: {target}")
    kind = "markdown" if suffix == ".md" else "image"
    return path.relative_to(root).as_posix(), kind


def _image_media_type(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def _validate_image_signature(content: bytes, media_type: str, relative: str) -> None:
    valid = (
        content.startswith(b"\x89PNG\r\n\x1a\n")
        if media_type == "image/png"
        else content.startswith(b"\xff\xd8")
    )
    if not valid:
        raise ValueError(
            f"debug reference image content does not match its media type: {relative}"
        )


def _safe_markdown_path(root: Path, path: Path) -> Path:
    resolved = _safe_path(root, path)
    if resolved.suffix.lower() != ".md":
        raise ValueError(f"debug prompt text must be Markdown: {path}")
    return resolved


def _safe_path(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if resolved.parent != root and root not in resolved.parents:
        raise ValueError(f"debug prompt file escapes its directory: {path}")
    return resolved
