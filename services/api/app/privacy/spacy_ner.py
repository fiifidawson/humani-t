"""Names, places and dates from a spaCy pipeline installed on this machine.

A model loads only from an installed package or a local folder. A URL or hub id is
refused, never downloaded, so "fully local" holds at run time too; `spacy download`
is an operator's install step, never called here.
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import importlib.metadata
import importlib.util
import json
import re
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from app.domain.masking import EntityType, MaskSpan, detector_source

# spaCy's OntoNotes labels that are personal data in our setting. ORG, NORP,
# MONEY and CARDINAL are evidence ("UNICEF paid 40 dollars"), not people.
SPACY_LABELS: Mapping[str, EntityType] = MappingProxyType(
    {
        "PERSON": EntityType.PERSON,
        "GPE": EntityType.LOCATION,
        "LOC": EntityType.LOCATION,
        "FAC": EntityType.LOCATION,
        "DATE": EntityType.DATE,
    }
)

# Only the named-entity recogniser and the layer it listens to are needed.
_UNUSED_COMPONENTS = ("parser", "tagger", "attribute_ruler", "lemmatizer", "senter")
_PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


class RemoteModelRefusedError(ValueError):
    """The configured model is not an installed package or an existing local folder."""


class _Entity(Protocol):
    @property
    def start_char(self) -> int: ...

    @property
    def end_char(self) -> int: ...

    @property
    def label_(self) -> str: ...


class _Doc(Protocol):
    @property
    def ents(self) -> Sequence[_Entity]: ...


class Language(Protocol):
    """The part of `spacy.Language` this adapter uses."""

    @property
    def meta(self) -> Mapping[str, Any]: ...

    def __call__(self, text: str) -> _Doc: ...


def _require_local(model: str) -> None:
    if _PACKAGE_NAME.fullmatch(model):
        return
    path = Path(model)
    if "://" in model or not path.is_absolute() or not path.is_dir():
        raise RemoteModelRefusedError(
            "masking_ner_model must be an installed spaCy model package (such as "
            "en_core_web_sm) or an absolute path to a model folder on this machine"
        )


def _source(meta: Mapping[str, Any]) -> str:
    return detector_source(f"spacy-{meta['lang']}_{meta['name']}", str(meta["version"]))


class SpacyNerDetector:
    """Adapts a loaded spaCy pipeline to the `Detector` Protocol."""

    def __init__(self, language: Language) -> None:
        self._language = language
        self._source = _source(language.meta)
        # One pipeline object is not documented as safe to call from several
        # threads at once; calls are serialised rather than risk corrupt output.
        self._lock = threading.Lock()

    @classmethod
    def load(cls, model: str) -> SpacyNerDetector:
        """Load `model` from this machine now. Takes seconds and some hundred MB of memory."""
        _require_local(model)
        spacy = importlib.import_module("spacy")
        return cls(spacy.load(model, exclude=list(_UNUSED_COMPONENTS)))

    @staticmethod
    def is_available(model: str) -> bool:
        """Whether spaCy and `model` are installed here, without loading or fetching anything.

        Raise `RemoteModelRefusedError`, not False, for a model that is not local.
        """
        _require_local(model)
        if importlib.util.find_spec("spacy") is None:
            return False
        if _PACKAGE_NAME.fullmatch(model):
            return importlib.util.find_spec(model) is not None
        return Path(model).is_absolute() and (Path(model) / "meta.json").is_file()

    @property
    def source(self) -> str:
        return self._source

    @property
    def entity_types(self) -> frozenset[EntityType]:
        return frozenset(SPACY_LABELS.values())

    async def detect(self, text: str) -> tuple[MaskSpan, ...]:
        doc = await asyncio.to_thread(self._parse, text)
        return tuple(
            MaskSpan(ent.start_char, ent.end_char, SPACY_LABELS[ent.label_], self._source)
            for ent in doc.ents
            if ent.label_ in SPACY_LABELS
        )

    def _parse(self, text: str) -> _Doc:
        with self._lock:
            return self._language(text)


@functools.cache
def _loaded(model: str) -> SpacyNerDetector:
    return SpacyNerDetector.load(model)


def _installed_meta(model: str) -> Mapping[str, Any]:
    if _PACKAGE_NAME.fullmatch(model):
        lang, _, name = model.partition("_")
        return {"lang": lang, "name": name, "version": importlib.metadata.version(model)}
    meta: Mapping[str, Any] = json.loads((Path(model) / "meta.json").read_text("utf-8"))
    return meta


class LazySpacyNerDetector:
    """A `SpacyNerDetector` that loads its model on first use, once per process.

    Building providers must stay fast (every test does it), and most processes never
    mask a document.
    """

    def __init__(self, model: str) -> None:
        _require_local(model)
        self._model = model
        self._source = _source(_installed_meta(model))

    @property
    def source(self) -> str:
        return self._source

    @property
    def entity_types(self) -> frozenset[EntityType]:
        return frozenset(SPACY_LABELS.values())

    async def detect(self, text: str) -> tuple[MaskSpan, ...]:
        detector = await asyncio.to_thread(_loaded, self._model)
        return await detector.detect(text)
