#!/usr/bin/env python3
"""
OpenClaw presentation generation service.

This module provides a production-ready architecture for generating
presentations in multiple formats (PPTX, PDF, HTML). It includes a
template library, theming system, plugin-based renderers, AI-assisted
content generation hooks, data source integrations, caching, and
quality checks.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import datetime as dt
import hashlib
import html
import json
import logging
import os
import re
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

VERSION = "1.0.0"
DEFAULT_SLIDE_SIZE = (13.333, 7.5)

logger = logging.getLogger(__name__)


class PresentationError(Exception):
    """Base error for presentation service."""


class MissingDependencyError(PresentationError):
    """Raised when an optional dependency is missing."""


class DataSourceError(PresentationError):
    """Raised when a data source fails."""


class RenderError(PresentationError):
    """Raised when a renderer fails."""


class QualityIssueLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class TextAlign(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class ImageFit(str, Enum):
    CONTAIN = "contain"
    COVER = "cover"


class ChartType(str, Enum):
    BAR = "bar"
    LINE = "line"
    PIE = "pie"


class ShapeType(str, Enum):
    RECTANGLE = "rectangle"
    OVAL = "oval"


@dataclass(frozen=True)
class Color:
    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        for value in (self.r, self.g, self.b):
            if not 0 <= value <= 255:
                raise ValueError("Color values must be in 0..255")

    def to_hex(self) -> str:
        return "#{:02x}{:02x}{:02x}".format(self.r, self.g, self.b)

    def to_rgb_tuple(self) -> Tuple[int, int, int]:
        return self.r, self.g, self.b

    @staticmethod
    def from_hex(value: str) -> "Color":
        value = value.strip().lstrip("#")
        if len(value) != 6:
            raise ValueError("Hex color must be 6 characters")
        return Color(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


@dataclass(frozen=True)
class FontSpec:
    title: str = "Calibri"
    body: str = "Calibri"
    mono: str = "Consolas"


@dataclass
class TextStyle:
    font_name: str = "Calibri"
    font_size: int = 24
    color: Color = field(default_factory=lambda: Color(0, 0, 0))
    bold: bool = False
    italic: bool = False


@dataclass
class Theme:
    name: str
    palette: Dict[str, Color]
    fonts: FontSpec = field(default_factory=FontSpec)
    background: Color = field(default_factory=lambda: Color(255, 255, 255))
    accent: Color = field(default_factory=lambda: Color(0, 120, 212))
    title_style: TextStyle = field(default_factory=TextStyle)
    body_style: TextStyle = field(default_factory=TextStyle)

    @staticmethod
    def build(
        name: str,
        palette: Dict[str, Color],
        background: Color,
        accent: Color,
        title_color: Optional[Color] = None,
        body_color: Optional[Color] = None,
    ) -> "Theme":
        title_color = title_color or palette.get("primary", Color(0, 0, 0))
        body_color = body_color or palette.get("text", Color(33, 33, 33))
        return Theme(
            name=name,
            palette=palette,
            background=background,
            accent=accent,
            title_style=TextStyle(
                font_name="Calibri",
                font_size=44,
                color=title_color,
                bold=True,
            ),
            body_style=TextStyle(
                font_name="Calibri",
                font_size=24,
                color=body_color,
            ),
        )


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return self.x, self.y, self.width, self.height


@dataclass
class LayoutSpec:
    slide_width: float = DEFAULT_SLIDE_SIZE[0]
    slide_height: float = DEFAULT_SLIDE_SIZE[1]
    margin_left: float = 0.6
    margin_right: float = 0.6
    margin_top: float = 0.5
    margin_bottom: float = 0.5
    gutter: float = 0.2
    columns: int = 1

    def content_width(self) -> float:
        return self.slide_width - self.margin_left - self.margin_right

    def content_height(self) -> float:
        return self.slide_height - self.margin_top - self.margin_bottom


@dataclass
class MasterSlide:
    background_color: Optional[Color] = None
    header_text: Optional[str] = None
    footer_text: Optional[str] = None
    logo_path: Optional[str] = None


@dataclass
class SlideElement:
    element_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    bounds: Optional[Rect] = None
    z_index: int = 0


@dataclass
class TextBlock(SlideElement):
    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)
    align: TextAlign = TextAlign.LEFT


@dataclass
class ImageBlock(SlideElement):
    source: str = ""
    alt_text: str = ""
    fit: ImageFit = ImageFit.CONTAIN
    opacity: float = 1.0


@dataclass
class ChartBlock(SlideElement):
    chart_type: ChartType = ChartType.BAR
    series: List[Dict[str, Any]] = field(default_factory=list)
    title: str = ""
    x_label: str = ""
    y_label: str = ""


@dataclass
class TableBlock(SlideElement):
    headers: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)


@dataclass
class ShapeBlock(SlideElement):
    shape: ShapeType = ShapeType.RECTANGLE
    fill_color: Color = field(default_factory=lambda: Color(255, 255, 255))
    line_color: Optional[Color] = None
    line_width: float = 1.0


@dataclass
class MediaBlock(SlideElement):
    media_type: str = "video"
    source: str = ""
    poster: Optional[str] = None


@dataclass
class InteractiveElement(SlideElement):
    text: str = ""
    target_url: str = ""
    style: TextStyle = field(default_factory=TextStyle)


@dataclass
class Transition:
    name: str = "fade"
    duration_ms: int = 600


@dataclass
class Animation:
    name: str = "fade_in"
    duration_ms: int = 500
    delay_ms: int = 0
    target_id: Optional[str] = None


@dataclass
class Slide:
    title: str = ""
    elements: List[SlideElement] = field(default_factory=list)
    notes: Optional[str] = None
    transition: Optional[Transition] = None
    animations: List[Animation] = field(default_factory=list)
    layout: Optional[LayoutSpec] = None


@dataclass
class PresentationSpec:
    title: str
    author: str = ""
    slides: List[Slide] = field(default_factory=list)
    theme: Theme = field(default_factory=lambda: Theme.build(
        "modern",
        {
            "primary": Color(13, 27, 42),
            "secondary": Color(27, 38, 59),
            "accent": Color(255, 140, 0),
            "text": Color(33, 33, 33),
        },
        background=Color(255, 255, 255),
        accent=Color(255, 140, 0),
    ))
    template_id: str = "modern"
    language: str = "en"
    output_format: str = "pptx"
    layout: LayoutSpec = field(default_factory=LayoutSpec)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "template_id": self.template_id,
            "language": self.language,
            "output_format": self.output_format,
            "layout": dataclasses.asdict(self.layout),
            "theme": {
                "name": self.theme.name,
                "palette": {k: v.to_hex() for k, v in self.theme.palette.items()},
                "background": self.theme.background.to_hex(),
                "accent": self.theme.accent.to_hex(),
                "fonts": dataclasses.asdict(self.theme.fonts),
            },
            "slides": [
                {
                    "title": slide.title,
                    "notes": slide.notes,
                    "elements": [serialize_element(element) for element in slide.elements],
                }
                for slide in self.slides
            ],
            "metadata": self.metadata,
        }


@dataclass
class Template:
    template_id: str
    name: str
    description: str
    theme: Theme
    layout: LayoutSpec
    master: Optional[MasterSlide] = None


class TemplateLibrary:
    def __init__(self) -> None:
        self._templates: Dict[str, Template] = {}

    def register(self, template: Template) -> None:
        self._templates[template.template_id] = template

    def get(self, template_id: str) -> Template:
        if template_id not in self._templates:
            raise PresentationError(f"Unknown template: {template_id}")
        return self._templates[template_id]

    def all(self) -> List[Template]:
        return list(self._templates.values())

    @classmethod
    def default_library(cls) -> "TemplateLibrary":
        library = cls()
        library.register(
            Template(
                template_id="modern",
                name="Modern Professional",
                description="Dark header with accent highlights.",
                theme=Theme.build(
                    "modern",
                    {
                        "primary": Color(13, 27, 42),
                        "secondary": Color(27, 38, 59),
                        "accent": Color(255, 140, 0),
                        "text": Color(33, 33, 33),
                        "muted": Color(120, 120, 120),
                    },
                    background=Color(245, 245, 245),
                    accent=Color(255, 140, 0),
                    title_color=Color(13, 27, 42),
                ),
                layout=LayoutSpec(),
                master=MasterSlide(
                    background_color=Color(245, 245, 245),
                    header_text=None,
                    footer_text=None,
                ),
            )
        )
        library.register(
            Template(
                template_id="minimal",
                name="Minimal Light",
                description="Clean layout with large whitespace.",
                theme=Theme.build(
                    "minimal",
                    {
                        "primary": Color(0, 82, 155),
                        "secondary": Color(230, 230, 230),
                        "accent": Color(0, 120, 212),
                        "text": Color(15, 15, 15),
                    },
                    background=Color(255, 255, 255),
                    accent=Color(0, 120, 212),
                ),
                layout=LayoutSpec(margin_left=0.8, margin_right=0.8, margin_top=0.6, margin_bottom=0.6),
            )
        )
        library.register(
            Template(
                template_id="dark",
                name="Dark Contrast",
                description="High-contrast dark theme.",
                theme=Theme.build(
                    "dark",
                    {
                        "primary": Color(245, 245, 245),
                        "secondary": Color(110, 190, 255),
                        "accent": Color(255, 140, 0),
                        "text": Color(230, 230, 230),
                    },
                    background=Color(20, 20, 20),
                    accent=Color(255, 140, 0),
                    title_color=Color(245, 245, 245),
                    body_color=Color(230, 230, 230),
                ),
                layout=LayoutSpec(),
                master=MasterSlide(background_color=Color(20, 20, 20)),
            )
        )
        return library


def serialize_element(element: SlideElement) -> Dict[str, Any]:
    base = {
        "element_id": element.element_id,
        "bounds": dataclasses.asdict(element.bounds) if element.bounds else None,
        "z_index": element.z_index,
        "type": element.__class__.__name__,
    }
    if isinstance(element, TextBlock):
        base.update(
            {
                "text": element.text,
                "align": element.align.value,
                "style": dataclasses.asdict(element.style),
            }
        )
    elif isinstance(element, ImageBlock):
        base.update(
            {
                "source": element.source,
                "alt_text": element.alt_text,
                "fit": element.fit.value,
                "opacity": element.opacity,
            }
        )
    elif isinstance(element, ChartBlock):
        base.update(
            {
                "chart_type": element.chart_type.value,
                "series": element.series,
                "title": element.title,
                "x_label": element.x_label,
                "y_label": element.y_label,
            }
        )
    elif isinstance(element, TableBlock):
        base.update({"headers": element.headers, "rows": element.rows})
    elif isinstance(element, ShapeBlock):
        base.update(
            {
                "shape": element.shape.value,
                "fill_color": element.fill_color.to_hex(),
                "line_color": element.line_color.to_hex() if element.line_color else None,
                "line_width": element.line_width,
            }
        )
    elif isinstance(element, MediaBlock):
        base.update(
            {"media_type": element.media_type, "source": element.source, "poster": element.poster}
        )
    elif isinstance(element, InteractiveElement):
        base.update(
            {
                "text": element.text,
                "target_url": element.target_url,
                "style": dataclasses.asdict(element.style),
            }
        )
    return base


class LayoutEngine:
    def apply(self, spec: PresentationSpec) -> None:
        for slide in spec.slides:
            layout = slide.layout or spec.layout
            self._layout_slide(slide, layout, spec.theme)

    def _layout_slide(self, slide: Slide, layout: LayoutSpec, theme: Theme) -> None:
        unbounded = [element for element in slide.elements if element.bounds is None]
        if not unbounded:
            return
        available_width = layout.content_width()
        available_height = layout.content_height()
        total = len(unbounded)
        if total == 0:
            return
        height = (available_height - (total - 1) * layout.gutter) / total
        current_y = layout.margin_top
        for element in unbounded:
            element.bounds = Rect(
                layout.margin_left,
                current_y,
                available_width,
                max(height, 0.1),
            )
            current_y += height + layout.gutter


@dataclass
class QualityIssue:
    level: QualityIssueLevel
    message: str
    slide_index: Optional[int] = None


@dataclass
class QualityReport:
    issues: List[QualityIssue] = field(default_factory=list)

    def has_errors(self) -> bool:
        return any(issue.level == QualityIssueLevel.ERROR for issue in self.issues)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issues": [
                {
                    "level": issue.level.value,
                    "message": issue.message,
                    "slide_index": issue.slide_index,
                }
                for issue in self.issues
            ]
        }


class QualityChecker:
    def __init__(self, min_contrast_ratio: float = 4.5) -> None:
        self.min_contrast_ratio = min_contrast_ratio

    def check(self, spec: PresentationSpec) -> QualityReport:
        report = QualityReport()
        for idx, slide in enumerate(spec.slides):
            background = spec.theme.background
            for element in slide.elements:
                if isinstance(element, TextBlock):
                    ratio = contrast_ratio(element.style.color, background)
                    if ratio < self.min_contrast_ratio:
                        report.issues.append(
                            QualityIssue(
                                level=QualityIssueLevel.WARNING,
                                message=(
                                    f"Low contrast ratio ({ratio:.2f}) "
                                    f"for text '{element.text[:40]}'"
                                ),
                                slide_index=idx,
                            )
                        )
                    if len(element.text) > 800:
                        report.issues.append(
                            QualityIssue(
                                level=QualityIssueLevel.INFO,
                                message="High text density in slide text block.",
                                slide_index=idx,
                            )
                        )
        return report


def contrast_ratio(color_a: Color, color_b: Color) -> float:
    def luminance(color: Color) -> float:
        def channel(c: int) -> float:
            c = c / 255.0
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b = color.to_rgb_tuple()
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    l1 = luminance(color_a)
    l2 = luminance(color_b)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


class PresentationCache:
    def __init__(
        self,
        root_dir: Optional[Path] = None,
        ttl_seconds: int = 3600,
        max_entries: int = 200,
    ) -> None:
        self.root_dir = root_dir or Path(os.getenv("OPENCLAW_CACHE_DIR", ".cache/presentations"))
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, key: str) -> Path:
        return self.root_dir / f"{key}.json"

    def get(self, key: str) -> Optional[Path]:
        meta_path = self._meta_path(key)
        if not meta_path.exists():
            return None
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        expires_at = metadata.get("expires_at", 0)
        output_path = metadata.get("output_path")
        if not output_path:
            return None
        if time.time() > expires_at:
            return None
        path = Path(output_path)
        if not path.exists():
            return None
        return path

    def set(self, key: str, output_path: Path, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta_path = self._meta_path(key)
        payload = {
            "created_at": time.time(),
            "expires_at": time.time() + self.ttl_seconds,
            "output_path": str(output_path),
            "metadata": metadata or {},
        }
        meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._prune()

    def _prune(self) -> None:
        entries = sorted(self.root_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for entry in entries[self.max_entries :]:
            try:
                entry.unlink()
            except OSError:
                logger.warning("Failed to prune cache entry: %s", entry)


class ImageDownloader:
    def __init__(self, cache_dir: Optional[Path] = None, timeout: int = 15) -> None:
        self.cache_dir = cache_dir or Path(os.getenv("OPENCLAW_IMAGE_CACHE", ".cache/images"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    def fetch(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        extension = Path(url.split("?")[0]).suffix or ".img"
        target = self.cache_dir / f"{key}{extension}"
        if target.exists():
            return target
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        target.write_bytes(response.content)
        return target


class ContentGenerator:
    def generate_outline(self, topic: str, slide_count: int, language: str) -> List[Dict[str, Any]]:
        raise NotImplementedError


class BasicContentGenerator(ContentGenerator):
    def generate_outline(self, topic: str, slide_count: int, language: str) -> List[Dict[str, Any]]:
        slide_count = max(slide_count, 3)
        topic_clean = topic.strip().title()
        outline = []
        outline.append({"title": topic_clean, "bullets": [f"Overview of {topic_clean}"]})
        outline.append({"title": "Agenda", "bullets": ["Background", "Key points", "Use cases", "Next steps"]})
        remaining = slide_count - 3
        for idx in range(remaining):
            outline.append(
                {
                    "title": f"Section {idx + 1}",
                    "bullets": [
                        f"{topic_clean} insight {idx + 1}",
                        f"Opportunity {idx + 1}",
                        "Supporting data point",
                    ],
                }
            )
        outline.append({"title": "Summary", "bullets": ["Key takeaways", "Action items"]})
        return outline


class LLMContentGenerator(ContentGenerator):
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str = "default",
        timeout: int = 30,
        request_builder: Optional[Callable[[str, int, str], Dict[str, Any]]] = None,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.request_builder = request_builder or self._default_request

    def _default_request(self, topic: str, slide_count: int, language: str) -> Dict[str, Any]:
        prompt = textwrap.dedent(
            f"""
            Create a structured presentation outline with {slide_count} slides.
            Topic: {topic}
            Language: {language}
            Return JSON array with fields: title, bullets, notes.
            """
        ).strip()
        return {"model": self.model, "prompt": prompt}

    def generate_outline(self, topic: str, slide_count: int, language: str) -> List[Dict[str, Any]]:
        payload = self.request_builder(topic, slide_count, language)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        response = requests.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "outline" in data:
            return data["outline"]
        if isinstance(data, list):
            return data
        raise PresentationError("Unexpected outline response from LLM service")


class ImageProvider:
    def search_images(self, query: str, count: int = 1) -> List[str]:
        raise NotImplementedError


class UnsplashProvider(ImageProvider):
    def __init__(self, api_key: str, timeout: int = 15) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search_images(self, query: str, count: int = 1) -> List[str]:
        url = "https://api.unsplash.com/search/photos"
        params = {"query": query, "per_page": count}
        headers = {"Authorization": f"Client-ID {self.api_key}"}
        response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return [item["urls"]["regular"] for item in data.get("results", [])]


class PexelsProvider(ImageProvider):
    def __init__(self, api_key: str, timeout: int = 15) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search_images(self, query: str, count: int = 1) -> List[str]:
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": self.api_key}
        params = {"query": query, "per_page": count}
        response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return [item["src"]["large"] for item in data.get("photos", [])]


class CombinedImageProvider(ImageProvider):
    def __init__(self, providers: Sequence[ImageProvider]) -> None:
        self.providers = list(providers)

    def search_images(self, query: str, count: int = 1) -> List[str]:
        results: List[str] = []
        for provider in self.providers:
            try:
                images = provider.search_images(query, count)
                results.extend(images)
            except Exception as exc:
                logger.warning("Image provider failed: %s", exc)
            if len(results) >= count:
                break
        return results[:count]


class ChartGenerator:
    def create_chart(self, data: List[Dict[str, Any]], chart_type: ChartType, title: str) -> ChartBlock:
        return ChartBlock(chart_type=chart_type, series=data, title=title)


class DataSource:
    def load(self) -> List[Dict[str, Any]]:
        raise NotImplementedError


class CSVDataSource(DataSource):
    def __init__(self, path: Path, encoding: str = "utf-8", delimiter: str = ",") -> None:
        self.path = path
        self.encoding = encoding
        self.delimiter = delimiter

    def load(self) -> List[Dict[str, Any]]:
        try:
            with self.path.open("r", encoding=self.encoding, newline="") as handle:
                reader = csv.DictReader(handle, delimiter=self.delimiter)
                return list(reader)
        except OSError as exc:
            raise DataSourceError(f"Failed to load CSV: {exc}") from exc


class JSONDataSource(DataSource):
    def __init__(self, path: Path, encoding: str = "utf-8") -> None:
        self.path = path
        self.encoding = encoding

    def load(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding=self.encoding))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataSourceError(f"Failed to load JSON: {exc}") from exc
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        raise DataSourceError("Unsupported JSON format")


class MarkdownDataSource(DataSource):
    def __init__(self, text: Optional[str] = None, path: Optional[Path] = None) -> None:
        self.text = text
        self.path = path

    def load(self) -> List[Dict[str, Any]]:
        if self.text is None and self.path is None:
            raise DataSourceError("MarkdownDataSource requires text or path")
        content = self.text
        if content is None and self.path is not None:
            content = self.path.read_text(encoding="utf-8")
        if content is None:
            return []
        slides: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        for line in content.splitlines():
            if re.match(r"^#{1,6}\s+", line):
                title = re.sub(r"^#{1,6}\s+", "", line).strip()
                current = {"title": title, "bullets": []}
                slides.append(current)
            elif re.match(r"^\s*[-*]\s+", line) and current is not None:
                bullet = re.sub(r"^\s*[-*]\s+", "", line).strip()
                current["bullets"].append(bullet)
        return slides


class ExcelDataSource(DataSource):
    def __init__(self, path: Path, sheet_name: Optional[str] = None) -> None:
        self.path = path
        self.sheet_name = sheet_name

    def load(self) -> List[Dict[str, Any]]:
        try:
            import openpyxl  # type: ignore
        except ImportError as exc:
            raise MissingDependencyError("openpyxl is required for ExcelDataSource") from exc
        workbook = openpyxl.load_workbook(self.path, data_only=True)
        sheet = workbook[self.sheet_name] if self.sheet_name else workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(value) for value in rows[0]]
        data = []
        for row in rows[1:]:
            data.append({headers[idx]: value for idx, value in enumerate(row)})
        return data


class GoogleSheetsSource(DataSource):
    def __init__(self, spreadsheet_id: str, range_name: str, api_key: str) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.range_name = range_name
        self.api_key = api_key

    def load(self) -> List[Dict[str, Any]]:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{self.range_name}"
        params = {"key": self.api_key}
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        values = response.json().get("values", [])
        if not values:
            return []
        headers = values[0]
        data = []
        for row in values[1:]:
            data.append({headers[idx]: row[idx] if idx < len(row) else "" for idx in range(len(headers))})
        return data


class NotionSource(DataSource):
    def __init__(self, database_id: str, token: str, notion_version: str = "2022-06-28") -> None:
        self.database_id = database_id
        self.token = token
        self.notion_version = notion_version

    def load(self) -> List[Dict[str, Any]]:
        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.notion_version,
        }
        response = requests.post(url, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data.get("results", []):
            properties = item.get("properties", {})
            row = {}
            for key, prop in properties.items():
                value = extract_notion_value(prop)
                row[key] = value
            results.append(row)
        return results


class AirtableSource(DataSource):
    def __init__(self, base_id: str, table: str, token: str) -> None:
        self.base_id = base_id
        self.table = table
        self.token = token

    def load(self) -> List[Dict[str, Any]]:
        url = f"https://api.airtable.com/v0/{self.base_id}/{self.table}"
        headers = {"Authorization": f"Bearer {self.token}"}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
        return [record.get("fields", {}) for record in data.get("records", [])]


def extract_notion_value(prop: Dict[str, Any]) -> Any:
    prop_type = prop.get("type")
    if prop_type == "title":
        return "".join(text.get("plain_text", "") for text in prop.get("title", []))
    if prop_type == "rich_text":
        return "".join(text.get("plain_text", "") for text in prop.get("rich_text", []))
    if prop_type == "number":
        return prop.get("number")
    if prop_type == "select":
        return prop.get("select", {}).get("name")
    if prop_type == "multi_select":
        return [item.get("name") for item in prop.get("multi_select", [])]
    if prop_type == "date":
        return prop.get("date", {}).get("start")
    if prop_type == "checkbox":
        return prop.get("checkbox")
    return None


class BasePlugin:
    format: str = ""

    def render(self, spec: PresentationSpec, output_path: Path) -> Path:
        raise NotImplementedError

    def supports_feature(self, feature: str) -> bool:
        return False


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: Dict[str, BasePlugin] = {}

    def register(self, plugin: BasePlugin) -> None:
        self._plugins[plugin.format] = plugin

    def get(self, format_name: str) -> BasePlugin:
        if format_name not in self._plugins:
            raise PresentationError(f"No plugin registered for format {format_name}")
        return self._plugins[format_name]

    def supported_formats(self) -> List[str]:
        return list(self._plugins.keys())


class PptxPlugin(BasePlugin):
    format = "pptx"

    def __init__(self, image_downloader: Optional[ImageDownloader] = None) -> None:
        self.image_downloader = image_downloader or ImageDownloader()

    def render(self, spec: PresentationSpec, output_path: Path) -> Path:
        try:
            from pptx import Presentation  # type: ignore
            from pptx.dml.color import RGBColor  # type: ignore
            from pptx.enum.shapes import MSO_SHAPE  # type: ignore
            from pptx.enum.text import PP_ALIGN  # type: ignore
            from pptx.util import Inches, Pt  # type: ignore
        except ImportError as exc:
            raise MissingDependencyError("python-pptx is required for PPTX output") from exc

        prs = Presentation()
        prs.slide_width = Inches(spec.layout.slide_width)
        prs.slide_height = Inches(spec.layout.slide_height)

        for slide in spec.slides:
            ppt_slide = prs.slides.add_slide(prs.slide_layouts[6])
            self._apply_background(ppt_slide, spec.theme, prs.slide_width, prs.slide_height, MSO_SHAPE)
            if slide.title:
                title_box = ppt_slide.shapes.add_textbox(
                    Inches(spec.layout.margin_left),
                    Inches(spec.layout.margin_top),
                    Inches(spec.layout.content_width()),
                    Inches(0.8),
                )
                tf = title_box.text_frame
                tf.text = slide.title
                tf.paragraphs[0].alignment = PP_ALIGN.LEFT
                font = tf.paragraphs[0].font
                font.size = Pt(spec.theme.title_style.font_size)
                font.bold = spec.theme.title_style.bold
                font.name = spec.theme.title_style.font_name
                font.color.rgb = RGBColor(*spec.theme.title_style.color.to_rgb_tuple())

            for element in sorted(slide.elements, key=lambda e: e.z_index):
                if element.bounds is None:
                    continue
                if isinstance(element, TextBlock):
                    box = ppt_slide.shapes.add_textbox(
                        Inches(element.bounds.x),
                        Inches(element.bounds.y),
                        Inches(element.bounds.width),
                        Inches(element.bounds.height),
                    )
                    tf = box.text_frame
                    tf.text = element.text
                    paragraph = tf.paragraphs[0]
                    paragraph.alignment = self._map_alignment(element.align, PP_ALIGN)
                    font = paragraph.font
                    font.size = Pt(element.style.font_size)
                    font.name = element.style.font_name
                    font.bold = element.style.bold
                    font.italic = element.style.italic
                    font.color.rgb = RGBColor(*element.style.color.to_rgb_tuple())
                elif isinstance(element, ImageBlock):
                    try:
                        image_path = self._resolve_image(element.source)
                        ppt_slide.shapes.add_picture(
                            str(image_path),
                            Inches(element.bounds.x),
                            Inches(element.bounds.y),
                            Inches(element.bounds.width),
                            Inches(element.bounds.height),
                        )
                    except Exception as exc:
                        logger.warning("Failed to add image: %s", exc)
                elif isinstance(element, ShapeBlock):
                    shape_type = MSO_SHAPE.RECTANGLE if element.shape == ShapeType.RECTANGLE else MSO_SHAPE.OVAL
                    shape = ppt_slide.shapes.add_shape(
                        shape_type,
                        Inches(element.bounds.x),
                        Inches(element.bounds.y),
                        Inches(element.bounds.width),
                        Inches(element.bounds.height),
                    )
                    shape.fill.solid()
                    shape.fill.fore_color.rgb = RGBColor(*element.fill_color.to_rgb_tuple())
                    if element.line_color:
                        shape.line.color.rgb = RGBColor(*element.line_color.to_rgb_tuple())
                        shape.line.width = Pt(element.line_width)
                    else:
                        shape.line.fill.background()
                elif isinstance(element, TableBlock):
                    rows = max(len(element.rows), 1) + 1
                    cols = max(len(element.headers), 1)
                    table = ppt_slide.shapes.add_table(
                        rows,
                        cols,
                        Inches(element.bounds.x),
                        Inches(element.bounds.y),
                        Inches(element.bounds.width),
                        Inches(element.bounds.height),
                    ).table
                    for col_idx, header in enumerate(element.headers):
                        table.cell(0, col_idx).text = str(header)
                    for row_idx, row in enumerate(element.rows, start=1):
                        for col_idx, value in enumerate(row):
                            if col_idx >= cols:
                                break
                            table.cell(row_idx, col_idx).text = str(value)
                elif isinstance(element, ChartBlock):
                    self._render_chart_placeholder(ppt_slide, element, Inches, Pt)
                elif isinstance(element, MediaBlock):
                    self._render_media(ppt_slide, element, Inches)
                elif isinstance(element, InteractiveElement):
                    box = ppt_slide.shapes.add_textbox(
                        Inches(element.bounds.x),
                        Inches(element.bounds.y),
                        Inches(element.bounds.width),
                        Inches(element.bounds.height),
                    )
                    tf = box.text_frame
                    p = tf.paragraphs[0]
                    run = p.add_run()
                    run.text = element.text
                    run.hyperlink.address = element.target_url
                    font = run.font
                    font.size = Pt(element.style.font_size)
                    font.name = element.style.font_name
                    font.bold = element.style.bold
                    font.color.rgb = RGBColor(*element.style.color.to_rgb_tuple())

            if slide.notes:
                try:
                    notes = ppt_slide.notes_slide.notes_text_frame
                    notes.text = slide.notes
                except Exception as exc:
                    logger.warning("Failed to attach speaker notes: %s", exc)

        output_path = output_path.with_suffix(".pptx")
        prs.save(output_path)
        return output_path

    def supports_feature(self, feature: str) -> bool:
        return feature in {"speaker_notes", "hyperlinks", "images"}

    def _resolve_image(self, source: str) -> Path:
        if source.startswith("http://") or source.startswith("https://"):
            return self.image_downloader.fetch(source)
        return Path(source)

    def _apply_background(self, ppt_slide: Any, theme: Theme, width: Any, height: Any, shape_enum: Any) -> None:
        bg_color = theme.background
        bg = ppt_slide.shapes.add_shape(shape_enum.RECTANGLE, 0, 0, width, height)
        bg.fill.solid()
        bg.fill.fore_color.rgb = self._color_to_rgb(bg_color)
        bg.line.fill.background()

    def _color_to_rgb(self, color: Color) -> Any:
        from pptx.dml.color import RGBColor  # type: ignore

        return RGBColor(*color.to_rgb_tuple())

    def _map_alignment(self, align: TextAlign, pp_align: Any) -> Any:
        return {
            TextAlign.LEFT: pp_align.LEFT,
            TextAlign.CENTER: pp_align.CENTER,
            TextAlign.RIGHT: pp_align.RIGHT,
            TextAlign.JUSTIFY: pp_align.JUSTIFY,
        }[align]

    def _render_chart_placeholder(self, ppt_slide: Any, element: ChartBlock, Inches: Any, Pt: Any) -> None:
        box = ppt_slide.shapes.add_textbox(
            Inches(element.bounds.x),
            Inches(element.bounds.y),
            Inches(element.bounds.width),
            Inches(element.bounds.height),
        )
        tf = box.text_frame
        tf.text = f"{element.title} (chart rendering placeholder)"
        tf.paragraphs[0].font.size = Pt(16)

    def _render_media(self, ppt_slide: Any, element: MediaBlock, Inches: Any) -> None:
        if not hasattr(ppt_slide.shapes, "add_movie"):
            logger.warning("Media embedding not supported in this python-pptx version")
            return
        try:
            ppt_slide.shapes.add_movie(
                element.source,
                Inches(element.bounds.x),
                Inches(element.bounds.y),
                Inches(element.bounds.width),
                Inches(element.bounds.height),
                poster_frame_image=element.poster,
            )
        except Exception as exc:
            logger.warning("Failed to add media: %s", exc)


class HtmlPlugin(BasePlugin):
    format = "html"

    def render(self, spec: PresentationSpec, output_path: Path) -> Path:
        output_path = output_path.with_suffix(".html")
        slides_html = []
        for index, slide in enumerate(spec.slides):
            layout = slide.layout or spec.layout
            slide_content = [f"<h1>{html.escape(slide.title)}</h1>"] if slide.title else []
            for element in slide.elements:
                if element.bounds is None:
                    continue
                styles = self._style_from_bounds(element.bounds, layout)
                if isinstance(element, TextBlock):
                    slide_content.append(
                        f"<div class='text-block' style='{styles}{self._text_style_css(element.style)}'>"
                        f"{html.escape(element.text)}</div>"
                    )
                elif isinstance(element, ImageBlock):
                    slide_content.append(
                        f"<img class='image-block' style='{styles}' src='{html.escape(element.source)}' "
                        f"alt='{html.escape(element.alt_text)}' />"
                    )
                elif isinstance(element, TableBlock):
                    slide_content.append(self._table_html(element, styles))
                elif isinstance(element, ChartBlock):
                    slide_content.append(self._chart_html(element, styles))
                elif isinstance(element, ShapeBlock):
                    slide_content.append(
                        f"<div class='shape-block' style='{styles};"
                        f"background:{element.fill_color.to_hex()}'></div>"
                    )
                elif isinstance(element, InteractiveElement):
                    slide_content.append(
                        f"<a class='interactive' style='{styles}{self._text_style_css(element.style)}' "
                        f"href='{html.escape(element.target_url)}'>"
                        f"{html.escape(element.text)}</a>"
                    )
            notes = f"<aside class='notes'>{html.escape(slide.notes)}</aside>" if slide.notes else ""
            slides_html.append(
                f"<section class='slide' data-index='{index}'>{''.join(slide_content)}{notes}</section>"
            )

        css = self._build_css(spec.theme)
        document = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>{html.escape(spec.title)}</title>
<style>{css}</style>
</head>
<body>
<div class="deck">
{''.join(slides_html)}
</div>
</body>
</html>
"""
        output_path.write_text(document, encoding="utf-8")
        return output_path

    def supports_feature(self, feature: str) -> bool:
        return feature in {"speaker_notes", "hyperlinks", "images", "animations"}

    def _style_from_bounds(self, bounds: Rect, layout: LayoutSpec) -> str:
        left = bounds.x / layout.slide_width * 100
        top = bounds.y / layout.slide_height * 100
        width = bounds.width / layout.slide_width * 100
        height = bounds.height / layout.slide_height * 100
        return f"left:{left:.2f}%;top:{top:.2f}%;width:{width:.2f}%;height:{height:.2f}%;"

    def _build_css(self, theme: Theme) -> str:
        return textwrap.dedent(
            f"""
            body {{
                margin: 0;
                background: {theme.background.to_hex()};
                font-family: {theme.body_style.font_name};
            }}
            .deck {{
                display: flex;
                flex-direction: column;
                gap: 40px;
                padding: 40px;
            }}
            .slide {{
                position: relative;
                width: 960px;
                height: 540px;
                background: {theme.background.to_hex()};
                box-shadow: 0 10px 25px rgba(0,0,0,0.15);
                overflow: hidden;
            }}
            .slide h1 {{
                margin: 24px;
                font-size: 36px;
                color: {theme.title_style.color.to_hex()};
            }}
            .text-block {{
                position: absolute;
                font-size: 18px;
                color: {theme.body_style.color.to_hex()};
                white-space: pre-wrap;
            }}
            .image-block {{
                position: absolute;
                object-fit: cover;
            }}
            .shape-block {{
                position: absolute;
            }}
            .notes {{
                display: none;
            }}
            .interactive {{
                position: absolute;
                color: {theme.accent.to_hex()};
                text-decoration: underline;
            }}
            """
        ).strip()

    def _table_html(self, element: TableBlock, styles: str) -> str:
        headers = "".join(f"<th>{html.escape(str(header))}</th>" for header in element.headers)
        rows = ""
        for row in element.rows:
            row_html = "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
            rows += f"<tr>{row_html}</tr>"
        return f"<table style='position:absolute;{styles}'><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>"

    def _chart_html(self, element: ChartBlock, styles: str) -> str:
        data_points = element.series or []
        lines = [f"{item.get('label', '')}: {item.get('value', '')}" for item in data_points]
        content = "<br>".join(html.escape(line) for line in lines)
        return f"<div class='chart-block' style='position:absolute;{styles}'>{content}</div>"

    def _text_style_css(self, style: TextStyle) -> str:
        weight = "bold" if style.bold else "normal"
        italic = "italic" if style.italic else "normal"
        return (
            f"font-family:{style.font_name};"
            f"font-size:{style.font_size}px;"
            f"color:{style.color.to_hex()};"
            f"font-weight:{weight};"
            f"font-style:{italic};"
        )


class PdfPlugin(BasePlugin):
    format = "pdf"

    def __init__(self, image_downloader: Optional[ImageDownloader] = None) -> None:
        self.image_downloader = image_downloader or ImageDownloader()

    def render(self, spec: PresentationSpec, output_path: Path) -> Path:
        try:
            from reportlab.lib.pagesizes import landscape  # type: ignore
            from reportlab.pdfgen import canvas  # type: ignore
        except ImportError as exc:
            raise MissingDependencyError("reportlab is required for PDF output") from exc

        output_path = output_path.with_suffix(".pdf")
        width = spec.layout.slide_width * 72
        height = spec.layout.slide_height * 72
        pdf = canvas.Canvas(str(output_path), pagesize=landscape((width, height)))
        for slide in spec.slides:
            pdf.setFillColorRGB(*to_pdf_color(spec.theme.background))
            pdf.rect(0, 0, width, height, fill=1, stroke=0)
            pdf.setFont(spec.theme.title_style.font_name, 32)
            pdf.setFillColorRGB(*to_pdf_color(spec.theme.title_style.color))
            pdf.drawString(36, height - 48, slide.title)
            for element in slide.elements:
                if element.bounds is None:
                    continue
                x = element.bounds.x * 72
                y = height - (element.bounds.y + element.bounds.height) * 72
                w = element.bounds.width * 72
                h = element.bounds.height * 72
                if isinstance(element, TextBlock):
                    pdf.setFont(element.style.font_name, element.style.font_size)
                    pdf.setFillColorRGB(*to_pdf_color(element.style.color))
                    text_obj = pdf.beginText(x, y + h - 18)
                    for line in element.text.splitlines():
                        text_obj.textLine(line)
                    pdf.drawText(text_obj)
                elif isinstance(element, ImageBlock):
                    try:
                        source = element.source
                        if source.startswith("http://") or source.startswith("https://"):
                            source = str(self.image_downloader.fetch(source))
                        pdf.drawImage(source, x, y, width=w, height=h, preserveAspectRatio=True)
                    except Exception as exc:
                        logger.warning("Failed to render image in PDF: %s", exc)
                elif isinstance(element, ShapeBlock):
                    pdf.setFillColorRGB(*to_pdf_color(element.fill_color))
                    pdf.rect(x, y, w, h, fill=1, stroke=0)
            pdf.showPage()
        pdf.save()
        return output_path

    def supports_feature(self, feature: str) -> bool:
        return feature in {"images", "hyperlinks"}


def to_pdf_color(color: Color) -> Tuple[float, float, float]:
    return color.r / 255.0, color.g / 255.0, color.b / 255.0


def create_default_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.register(PptxPlugin())
    registry.register(HtmlPlugin())
    registry.register(PdfPlugin())
    return registry


@dataclass
class BatchTask:
    spec: PresentationSpec
    output_path: Path
    use_cache: bool = True


class PresentationService:
    def __init__(
        self,
        registry: Optional[PluginRegistry] = None,
        templates: Optional[TemplateLibrary] = None,
        content_generator: Optional[ContentGenerator] = None,
        image_provider: Optional[ImageProvider] = None,
        chart_generator: Optional[ChartGenerator] = None,
        cache: Optional[PresentationCache] = None,
        layout_engine: Optional[LayoutEngine] = None,
        quality_checker: Optional[QualityChecker] = None,
    ) -> None:
        self.registry = registry or create_default_registry()
        self.templates = templates or TemplateLibrary.default_library()
        self.content_generator = content_generator or BasicContentGenerator()
        self.image_provider = image_provider
        self.chart_generator = chart_generator or ChartGenerator()
        self.cache = cache or PresentationCache()
        self.layout_engine = layout_engine or LayoutEngine()
        self.quality_checker = quality_checker or QualityChecker()

    def generate(self, spec: PresentationSpec, output_path: Path, use_cache: bool = True) -> Path:
        spec = self._apply_template(spec)
        self.layout_engine.apply(spec)
        if self.quality_checker:
            report = self.quality_checker.check(spec)
            for issue in report.issues:
                logger.warning("Quality issue: %s", issue.message)
        cache_key = self._cache_key(spec)
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached
        plugin = self.registry.get(spec.output_format)
        rendered = plugin.render(spec, output_path)
        if use_cache:
            self.cache.set(cache_key, rendered, {"format": spec.output_format})
        return rendered

    async def async_generate(self, spec: PresentationSpec, output_path: Path, use_cache: bool = True) -> Path:
        return await asyncio.to_thread(self.generate, spec, output_path, use_cache)

    async def batch_generate(self, tasks: Sequence[BatchTask], concurrency: int = 4) -> List[Path]:
        semaphore = asyncio.Semaphore(concurrency)

        async def _run(task: BatchTask) -> Path:
            async with semaphore:
                return await self.async_generate(task.spec, task.output_path, task.use_cache)

        return await asyncio.gather(*[_run(task) for task in tasks])

    def generate_from_topic(
        self,
        topic: str,
        output_path: Path,
        slide_count: int = 8,
        output_format: str = "pptx",
        template_id: str = "modern",
        include_images: bool = True,
        language: str = "en",
    ) -> Path:
        outline = self.content_generator.generate_outline(topic, slide_count, language)
        slides: List[Slide] = []
        for item in outline:
            elements: List[SlideElement] = []
            bullets = item.get("bullets", [])
            if bullets:
                text = "\n".join(f"- {bullet}" for bullet in bullets)
                elements.append(TextBlock(text=text, style=self.templates.get(template_id).theme.body_style))
            if include_images and self.image_provider:
                query = item.get("image_query") or item.get("title", topic)
                images = self.image_provider.search_images(query, count=1)
                if images:
                    elements.append(ImageBlock(source=images[0]))
            slides.append(Slide(title=item.get("title", ""), elements=elements, notes=item.get("notes")))

        spec = PresentationSpec(
            title=topic,
            slides=slides,
            template_id=template_id,
            output_format=output_format,
            language=language,
        )
        return self.generate(spec, output_path)

    def generate_from_markdown(
        self,
        markdown_text: str,
        output_path: Path,
        output_format: str = "pptx",
        template_id: str = "minimal",
    ) -> Path:
        data = MarkdownDataSource(text=markdown_text).load()
        slides: List[Slide] = []
        for block in data:
            bullets = block.get("bullets", [])
            text = "\n".join(f"- {bullet}" for bullet in bullets)
            elements = [TextBlock(text=text, style=self.templates.get(template_id).theme.body_style)]
            slides.append(Slide(title=block.get("title", ""), elements=elements))
        spec = PresentationSpec(
            title="Markdown Presentation",
            slides=slides,
            template_id=template_id,
            output_format=output_format,
        )
        return self.generate(spec, output_path)

    def generate_from_data(
        self,
        data: List[Dict[str, Any]],
        output_path: Path,
        output_format: str = "pptx",
        template_id: str = "minimal",
        chart_type: ChartType = ChartType.BAR,
    ) -> Path:
        if not data:
            raise PresentationError("generate_from_data requires non-empty data")
        chart = self.chart_generator.create_chart(data, chart_type, "Data Overview")
        slide = Slide(title="Data Overview", elements=[chart, TableBlock(headers=list(data[0].keys()), rows=[list(item.values()) for item in data])])
        spec = PresentationSpec(
            title="Data Presentation",
            slides=[slide],
            template_id=template_id,
            output_format=output_format,
        )
        return self.generate(spec, output_path)

    def import_data(self, source: DataSource) -> List[Dict[str, Any]]:
        return source.load()

    def _apply_template(self, spec: PresentationSpec) -> PresentationSpec:
        template = self.templates.get(spec.template_id)
        spec.theme = template.theme
        spec.layout = template.layout
        return spec

    def _cache_key(self, spec: PresentationSpec) -> str:
        payload = json.dumps(spec.to_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def create_service_from_env() -> PresentationService:
    templates = TemplateLibrary.default_library()
    registry = create_default_registry()
    image_provider = None
    unsplash_key = os.getenv("UNSPLASH_API_KEY")
    pexels_key = os.getenv("PEXELS_API_KEY")
    providers: List[ImageProvider] = []
    if unsplash_key:
        providers.append(UnsplashProvider(unsplash_key))
    if pexels_key:
        providers.append(PexelsProvider(pexels_key))
    if providers:
        image_provider = CombinedImageProvider(providers)
    service = PresentationService(
        registry=registry,
        templates=templates,
        image_provider=image_provider,
    )
    return service


def example_usage() -> None:
    service = create_service_from_env()
    topic = "OpenClaw Family Assistant"
    output_path = Path("openclaw_presentation.pptx")
    service.generate_from_topic(topic, output_path, slide_count=6, output_format="pptx")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    example_usage()
