"""The operator's upload settings: a scanner is a decision, never a silent default.

Production refuses uploads unless a scanner, or explicitly `none`, is set, and a database
without a file volume switches uploads off.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.dependencies import build_providers
from app.crypto.local_key import generate_kek
from app.extraction.isolated import IsolatedDocumentExtractor
from app.files.volume import LocalVolumeFileStore
from app.malware.clamd import ClamdScanner
from app.malware.skip import NoMalwareScanner
from app.settings import Environment, Settings


def _production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.PRODUCTION,
        "public_origin": "https://humani-t.example.org",
        "database_url": "postgresql+asyncpg://app:secret@db/humani_t",
        "key_encryption_keys": f"1:{generate_kek()}",
        "valkey_url": "redis://:secret@valkey:6379/0",
        "smtp_host": "smtp.example.org",
        "inference_url": "http://ollama:11434",
        "document_storage_dir": "/var/lib/humani-t/documents",
    }
    values.update(overrides)
    return Settings.model_validate(values)


class TestScannerChoice:
    def test_production_without_a_scanner_switches_uploads_off_and_says_why(self):
        reason = _production().uploads_disabled_reason

        assert reason is not None
        assert "malware scanner" in reason

    def test_production_with_scanner_none_written_explicitly_allows_uploads(self):
        assert _production(malware_scanner="none").uploads_disabled_reason is None

    def test_production_with_clamd_allows_uploads(self):
        settings = _production(malware_scanner="clamd", clamd_address="tcp://clamav:3310")

        assert settings.uploads_disabled_reason is None

    def test_clamd_needs_an_address(self):
        with pytest.raises(ValidationError, match="clamd_address"):
            Settings(environment=Environment.TEST, malware_scanner="clamd")

    def test_an_address_without_clamd_chosen_is_refused(self):
        with pytest.raises(ValidationError, match="clamd_address"):
            Settings(environment=Environment.TEST, clamd_address="tcp://clamav:3310")

    def test_development_without_a_scanner_allows_uploads_unscanned(self):
        assert Settings(environment=Environment.DEVELOPMENT).uploads_disabled_reason is None


class TestStorageChoice:
    def test_a_database_without_a_document_directory_switches_uploads_off(self):
        reason = _production(
            malware_scanner="none", document_storage_dir=""
        ).uploads_disabled_reason

        assert reason is not None
        assert "storage directory" in reason

    def test_a_blank_directory_means_unset(self):
        settings = Settings.model_validate(
            {"environment": Environment.TEST, "document_storage_dir": ""}
        )

        assert settings.document_storage_dir is None

    def test_the_project_quota_must_hold_at_least_one_file(self):
        with pytest.raises(ValidationError, match="document_bytes_per_project"):
            Settings(
                environment=Environment.TEST,
                upload_max_bytes=10_000,
                document_bytes_per_project=5_000,
            )


class TestWiring:
    def test_the_chosen_implementations_are_the_production_ones(self, tmp_path: Path):
        settings = Settings(
            environment=Environment.TEST,
            document_storage_dir=tmp_path,
            malware_scanner="clamd",
            clamd_address="tcp://clamav:3310",
            masking_ner="off",
        )

        processor = build_providers(settings).document_processor

        assert isinstance(processor._files, LocalVolumeFileStore)
        assert isinstance(processor._scanner, ClamdScanner)
        assert isinstance(processor._extractor, IsolatedDocumentExtractor)

    def test_no_scanner_is_recorded_as_none(self):
        settings = Settings(environment=Environment.TEST, masking_ner="off")

        processor = build_providers(settings).document_processor

        assert isinstance(processor._scanner, NoMalwareScanner)

    def test_documents_are_masked_with_the_broad_list_including_dates(self):
        settings = Settings(environment=Environment.TEST, masking_ner="off")

        processor = build_providers(settings).document_processor

        assert "dates@1" in processor._detector_sources
        assert "patterns@1" in processor._detector_sources
