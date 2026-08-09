from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..storage import atomic_write_json, strict_json_loads
from .model import TEMPLATE_EXTENSION, CutTemplate, TemplateFormatError

_WINDOWS_RESERVED_STEMS = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}


@dataclass(slots=True, frozen=True)
class TemplateDiagnostic:
    """One template-library file that could not enter the usable catalog."""

    path: Path
    message: str
    code: str = "invalid-template"
    template_id: str | None = None


@dataclass(slots=True, frozen=True)
class TemplateCatalog:
    """Valid unique templates plus non-fatal file diagnostics from a scan."""

    templates: tuple[CutTemplate, ...]
    diagnostics: tuple[TemplateDiagnostic, ...]

    def get(self, template_id: str) -> CutTemplate | None:
        wanted = str(template_id)
        return next((item for item in self.templates if item.id == wanted), None)


def _safe_stem(value: str, *, fallback: str = "template") -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    stem = re.sub(r"[^a-z0-9_-]+", "-", ascii_text).strip("-_.")[:100]
    if not stem:
        stem = fallback
    if stem.casefold() in _WINDOWS_RESERVED_STEMS:
        stem = f"_{stem}"
    return stem


class TemplateLibrary:
    """A directory-backed, portable library of versioned cutting templates."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _default_filename(self, template: CutTemplate) -> str:
        name = _safe_stem(template.name)
        identity = _safe_stem(template.id, fallback="id")[:32]
        return f"{name}--{identity}{TEMPLATE_EXTENSION}"

    def _destination(self, template: CutTemplate, filename: str | Path | None) -> Path:
        if filename is None:
            safe_name = self._default_filename(template)
        else:
            raw = str(filename)
            candidate = Path(raw)
            if candidate.is_absolute():
                resolved = candidate.expanduser().resolve()
                if resolved.parent != self.root:
                    raise TemplateFormatError("Template path must stay inside the library root")
                raw_stem = resolved.stem
            else:
                if "/" in raw or "\\" in raw or len(candidate.parts) != 1:
                    raise TemplateFormatError("Template filename must not contain a directory")
                raw_stem = candidate.stem if candidate.suffix else candidate.name
            safe_name = f"{_safe_stem(raw_stem)}{TEMPLATE_EXTENSION}"
        destination = (self.root / safe_name).resolve()
        if destination.parent != self.root:
            raise TemplateFormatError("Template path must stay inside the library root")
        return destination

    def _source(self, reference: str | Path) -> Path:
        candidate = Path(reference).expanduser()
        if candidate.is_absolute():
            source = candidate.resolve()
            if source.parent != self.root:
                raise TemplateFormatError("Template path must stay inside the library root")
            return source
        raw = str(reference)
        if "/" in raw or "\\" in raw or len(candidate.parts) != 1:
            raise TemplateFormatError("Template filename must not contain a directory")
        if candidate.suffix.lower() != TEMPLATE_EXTENSION:
            candidate = candidate.with_suffix(TEMPLATE_EXTENSION)
        source = (self.root / candidate.name).resolve()
        if source.parent != self.root:
            raise TemplateFormatError("Template path must stay inside the library root")
        return source

    def save(
        self,
        template: CutTemplate,
        filename: str | Path | None = None,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Validate and atomically save a template, refusing replacement by default."""

        validated = CutTemplate.from_dict(template.to_dict())
        destination = self._destination(validated, filename)
        if destination.exists() and not overwrite:
            raise FileExistsError(f"Template already exists: {destination}")
        atomic_write_json(destination, validated.to_dict())
        return destination

    def replace(
        self,
        template: CutTemplate,
        *,
        expected_modified_at: str | None = None,
    ) -> Path:
        """Atomically replace the one existing file with this persistent ID.

        Replacement deliberately keeps the original path even when the display
        name changes.  This prevents a rename from leaving two files with one
        persistent ID.  Unrelated malformed files do not block replacement,
        but an absent or duplicate target is rejected.
        """

        validated = CutTemplate.from_dict(template.to_dict())
        matches: list[tuple[Path, CutTemplate]] = []
        for path in self._template_paths():
            try:
                current = self.load(path)
            except (OSError, TemplateFormatError):
                continue
            if current.id == validated.id:
                matches.append((path, current))

        if not matches:
            raise FileNotFoundError(
                f"No template with ID {validated.id!r} in {self.root}"
            )
        if len(matches) > 1:
            filenames = ", ".join(path.name for path, _ in matches)
            raise TemplateFormatError(
                f"Cannot replace duplicate template ID {validated.id!r}: {filenames}"
            )

        destination, current = matches[0]
        if validated.created_at != current.created_at:
            raise TemplateFormatError(
                "Replacement must preserve the template creation timestamp"
            )
        if (
            expected_modified_at is not None
            and current.modified_at != str(expected_modified_at)
        ):
            raise TemplateFormatError(
                "Template changed after it was opened; reload it before saving"
            )
        atomic_write_json(destination, validated.to_dict())
        return destination

    def load(self, reference: str | Path) -> CutTemplate:
        """Load by filename/path, falling back to a template ID lookup."""

        source = self._source(reference)
        if not source.exists() and not Path(reference).suffix:
            return self.get(str(reference))
        try:
            raw = strict_json_loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise TemplateFormatError(f"Invalid template JSON in {source}: {exc}") from exc
        return CutTemplate.from_dict(raw)

    def _template_paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        if not self.root.is_dir():
            raise TemplateFormatError(f"Template library root is not a directory: {self.root}")
        return sorted(
            (
                path
                for path in self.root.iterdir()
                if path.is_file() and path.suffix.lower() == TEMPLATE_EXTENSION
            ),
            key=lambda path: path.name.casefold(),
        )

    def list_templates(self) -> list[CutTemplate]:
        """Strictly load every template, rejecting malformed or duplicate IDs."""

        paths = self._template_paths()
        templates = [self.load(path) for path in paths]
        paths_by_id: dict[str, list[Path]] = {}
        for path, template in zip(paths, templates, strict=True):
            paths_by_id.setdefault(template.id, []).append(path)
        duplicates = {
            template_id: paths
            for template_id, paths in paths_by_id.items()
            if len(paths) > 1
        }
        if duplicates:
            details = "; ".join(
                f"{template_id!r} in {', '.join(path.name for path in paths)}"
                for template_id, paths in sorted(duplicates.items())
            )
            raise TemplateFormatError(f"Duplicate template IDs: {details}")
        return sorted(templates, key=lambda item: (item.name.casefold(), item.id))

    def scan(self) -> TemplateCatalog:
        """Return all valid unique templates without one bad file hiding others."""

        try:
            paths = self._template_paths()
        except (OSError, TemplateFormatError) as exc:
            diagnostic = TemplateDiagnostic(
                path=self.root,
                message=str(exc),
                code="library-error",
            )
            return TemplateCatalog(templates=(), diagnostics=(diagnostic,))

        loaded: list[tuple[Path, CutTemplate]] = []
        diagnostics: list[TemplateDiagnostic] = []
        for path in paths:
            try:
                loaded.append((path, self.load(path)))
            except (OSError, TemplateFormatError) as exc:
                diagnostics.append(TemplateDiagnostic(path=path, message=str(exc)))

        rows_by_id: dict[str, list[tuple[Path, CutTemplate]]] = {}
        for row in loaded:
            rows_by_id.setdefault(row[1].id, []).append(row)

        valid: list[CutTemplate] = []
        for template_id, rows in rows_by_id.items():
            if len(rows) == 1:
                valid.append(rows[0][1])
                continue
            filenames = ", ".join(path.name for path, _ in rows)
            message = f"Duplicate template ID {template_id!r} appears in {filenames}"
            diagnostics.extend(
                TemplateDiagnostic(
                    path=path,
                    message=message,
                    code="duplicate-id",
                    template_id=template_id,
                )
                for path, _ in rows
            )

        return TemplateCatalog(
            templates=tuple(sorted(valid, key=lambda item: (item.name.casefold(), item.id))),
            diagnostics=tuple(
                sorted(diagnostics, key=lambda item: (str(item.path).casefold(), item.code))
            ),
        )

    def catalog(self) -> TemplateCatalog:
        """Collection-style alias for the resilient scanner."""

        return self.scan()

    def list(self) -> list[CutTemplate]:
        """Alias for callers that prefer a short collection-style method name."""

        return self.list_templates()

    def get(self, template_id: str) -> CutTemplate:
        """Return the template with an exact persistent ID."""

        wanted = str(template_id)
        for template in self.list_templates():
            if template.id == wanted:
                return template
        raise FileNotFoundError(f"No template with ID {wanted!r} in {self.root}")

    def load_by_id(self, template_id: str) -> CutTemplate:
        return self.get(template_id)

    def find(self, template_id: str) -> CutTemplate | None:
        try:
            return self.get(template_id)
        except FileNotFoundError:
            return None

    def delete(self, template_id: str) -> bool:
        """Delete the file with an exact template ID, returning whether one existed."""

        wanted = str(template_id)
        catalog = self.scan()
        library_error = next(
            (item for item in catalog.diagnostics if item.code == "library-error"),
            None,
        )
        if library_error is not None:
            raise TemplateFormatError(library_error.message)
        duplicates = [
            item
            for item in catalog.diagnostics
            if item.code == "duplicate-id" and item.template_id == wanted
        ]
        if duplicates:
            filenames = ", ".join(item.path.name for item in duplicates)
            raise TemplateFormatError(
                f"Cannot delete duplicate template ID {wanted!r}: {filenames}"
            )
        if catalog.get(wanted) is None:
            return False
        for path in self._template_paths():
            try:
                template = self.load(path)
            except (OSError, TemplateFormatError):
                continue
            if template.id == wanted:
                path.unlink()
                return True
        return False
