"""Which concrete implementations the routes get.

Every provider choice is made here and nowhere else. Importing a concrete provider
anywhere else is the architecture leaking: import it here instead.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request

from app.application.accounts import AccountService
from app.application.audit import AuditLog
from app.application.briefs import (
    AnswerStage,
    BriefService,
    ScreenedSources,
    StoredBriefCitations,
)
from app.application.documents import (
    DocumentEvidence,
    DocumentProcessor,
    DocumentReviewQueue,
    DocumentService,
)
from app.application.encryption import ScopeEncryption
from app.application.exports import ExportService
from app.application.inference import GuardedInference
from app.application.masking import (
    DocumentsAwaitingReview,
    MaskingRuleService,
    MaskStage,
    PaperMaskStore,
)
from app.application.mfa import MfaService
from app.application.papers import PaperService, PaperStore, ScreenedEvidence
from app.application.pipeline import DiscoverStage, ExportStage, IngestStage
from app.application.planning import PlanService
from app.application.projects import ProjectService
from app.application.queries_stage import QueriesStage, QueryCompiler
from app.application.questions import QuestionService, QuestionWorker
from app.application.reviews import ReviewService
from app.application.runs import RunService
from app.application.screening import ScreenStage
from app.auth.argon2 import Argon2PasswordHasher
from app.auth.base import PasskeyVerifier, PasswordHasher
from app.auth.webauthn import WebAuthnPasskeyVerifier
from app.briefs.bm25 import Bm25Retriever
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider, generate_kek
from app.domain.documents import ExtractionLimits, UploadPolicy
from app.domain.masking import (
    DOCUMENT_ENTITY_TYPES,
    PAPER_ENTITY_TYPES,
    QUESTION_ENTITY_TYPES,
)
from app.domain.mfa import MfaPolicy
from app.domain.plans import PlanSource
from app.domain.screening import MaskedTextSource
from app.email.base import EmailProvider
from app.email.recording import RecordingEmailProvider
from app.email.smtp import SmtpEmailProvider
from app.extraction.base import DocumentExtractor
from app.extraction.isolated import IsolatedDocumentExtractor
from app.files.base import FileStore
from app.files.memory import MemoryFileStore
from app.files.volume import LocalVolumeFileStore
from app.inference.base import InferenceProvider, PromptGuard
from app.inference.demo import DemoInferenceProvider
from app.inference.ollama import OllamaInferenceProvider
from app.inference.openai_compatible import OpenAICompatibleInferenceProvider
from app.malware.base import MalwareScanner
from app.malware.clamd import ClamdAddress, ClamdScanner
from app.malware.skip import NoMalwareScanner
from app.net.base import Destination, HttpFetcher, OutboundPolicy
from app.net.httpx_fetcher import HttpxFetcher
from app.privacy.base import Detector, Sanitizer
from app.privacy.dates import DatePatternDetector
from app.privacy.guard import LeakCheckPromptGuard
from app.privacy.patterns import PatternDetector
from app.privacy.sanitizer import DetectorSanitizer
from app.privacy.spacy_ner import LazySpacyNerDetector, SpacyNerDetector
from app.ratelimit.base import RateLimiter
from app.ratelimit.memory import MemoryRateLimiter
from app.ratelimit.valkey import ValkeyRateLimiter
from app.runs.base import Stage
from app.search import arxiv, demo, europepmc, openalex
from app.search.base import PaperSearchProvider
from app.settings import Settings
from app.signals.base import Signals
from app.signals.memory import MemorySignals
from app.signals.valkey import ValkeySignals
from app.storage.base import StorageProvider, UnitOfWork
from app.storage.memory import MemoryStorage
from app.storage.postgres.storage import PostgresStorage

API_PREFIX = "/api/v1"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Providers:
    """Everything built once per process. Providers hold pools and caches."""

    storage: StorageProvider
    encryption: ScopeEncryption
    audit: AuditLog
    accounts: AccountService
    email: EmailProvider
    rate_limiter: RateLimiter
    projects: ProjectService
    mfa: MfaService
    paper_sources: tuple[PaperSearchProvider, ...]
    inference: GuardedInference
    http_clients: tuple[HttpFetcher, ...]
    signals: Signals
    stages: Mapping[str, Stage]
    reviews: ReviewService
    runs: RunService
    papers: PaperService
    plans: PlanService
    masking_rules: MaskingRuleService
    briefs: BriefService
    questions: QuestionService
    # Run by the worker process beside its runs (`app/worker.py`); idle in the API.
    answers: QuestionWorker
    documents: DocumentService
    # Run by the worker process beside its runs (`app/worker.py`); idle in the API.
    document_processor: DocumentProcessor

    async def close(self) -> None:
        """Release every pool and client: once, at shutdown or at the end of a CLI run."""
        await self.storage.close()
        await self.rate_limiter.close()
        for client in self.http_clients:
            await client.close()
        await self.signals.close()


def _storage(settings: Settings) -> StorageProvider:
    if settings.database_url is None:
        return MemoryStorage()
    return PostgresStorage.from_url(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        pool_timeout_seconds=settings.db_pool_timeout_seconds,
        statement_timeout_ms=settings.db_statement_timeout_ms,
    )


def _key_provider(settings: Settings) -> LocalKeyProvider:
    if settings.key_encryption_keys is None:
        # Only reachable with in-memory storage (Settings enforces it): the data
        # dies with the process, so a key that dies with it loses nothing.
        return LocalKeyProvider.from_config(f"1:{generate_kek()}")
    return LocalKeyProvider.from_config(settings.key_encryption_keys.get_secret_value())


def _rate_limiter(settings: Settings, clock: Callable[[], datetime]) -> RateLimiter:
    if settings.valkey_url is None:
        return MemoryRateLimiter(clock)
    return ValkeyRateLimiter.from_url(settings.valkey_url.get_secret_value(), clock)


def _email(settings: Settings) -> EmailProvider:
    if settings.smtp_host is None:
        return RecordingEmailProvider()
    return SmtpEmailProvider(
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_sender,
        username=settings.smtp_username or None,
        password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
        use_starttls=settings.smtp_starttls,
    )


_PAPER_SOURCE_HOSTS = {
    openalex.SOURCE_ID: openalex.HOST,
    europepmc.SOURCE_ID: europepmc.HOST,
    arxiv.SOURCE_ID: arxiv.HOST,
}


def paper_source_destination(source_id: str, settings: Settings) -> Destination:
    """A paper source's one host: public, https only, and carrying only its own key."""
    key = settings.openalex_api_key
    token = (
        key.get_secret_value() if key is not None and source_id == openalex.SOURCE_ID else None
    )
    return Destination(_PAPER_SOURCE_HOSTS[source_id], bearer_token=token)


def _paper_source(
    source_id: str, settings: Settings
) -> tuple[PaperSearchProvider, HttpFetcher | None]:
    if source_id == demo.SOURCE_ID:
        return demo.DemoPaperSearchProvider(), None
    fetcher = HttpxFetcher(
        OutboundPolicy(destinations=(paper_source_destination(source_id, settings),))
    )
    if source_id == openalex.SOURCE_ID:
        pacer = openalex.pacer_for(has_api_key=settings.openalex_api_key is not None)
        return openalex.OpenAlexProvider(
            fetcher, mailto=settings.openalex_mailto, pacer=pacer
        ), fetcher
    if source_id == europepmc.SOURCE_ID:
        return europepmc.EuropePmcProvider(fetcher), fetcher
    return arxiv.ArxivProvider(fetcher), fetcher


def inference_destination(settings: Settings) -> Destination:
    """The model server the operator named, on the terms Settings already validated.

    Its address is not checked against the SSRF block list: the operator chose
    it, and it is normally on the compose network or their own VPN.
    """
    url = urlsplit(settings.inference_url)
    return Destination(
        url.hostname or "",
        allows_private_addresses=True,
        allows_plain_http=url.scheme == "http",
        bearer_token=(
            settings.inference_token.get_secret_value() if settings.inference_token else None
        ),
    )


def _inference(settings: Settings) -> tuple[GuardedInference, HttpFetcher | None]:
    if settings.demo_inference:
        # The guard stays in front of the stand-in, so a demo run is held to the
        # same leak check as a real one.
        return GuardedInference(DemoInferenceProvider(), prompt_guard()), None
    timeout = settings.inference_timeout_seconds
    fetcher = HttpxFetcher(
        OutboundPolicy(
            destinations=(inference_destination(settings),),
            read_timeout_seconds=timeout,
            total_timeout_seconds=timeout,
        )
    )
    return GuardedInference(inference_provider(settings, fetcher), prompt_guard()), fetcher


def inference_provider(settings: Settings, fetcher: HttpFetcher) -> InferenceProvider:
    """The model server's wire format, as the operator set it. Ollama unless told otherwise."""
    if settings.inference_api == "openai":
        return OpenAICompatibleInferenceProvider(
            fetcher, base_url=settings.inference_url, model=settings.inference_model
        )
    return OllamaInferenceProvider(
        fetcher, base_url=settings.inference_url, model=settings.inference_model
    )


def prompt_guard() -> PromptGuard:
    """The last check before every model call: it fails closed.

    Patterns only: a name model on every prompt would refuse the published
    author and place names that papers legitimately contain.
    """
    return LeakCheckPromptGuard((PatternDetector(),))


def ner_detectors(settings: Settings) -> tuple[Detector, ...]:
    """The local name-and-place detector, if the operator's setting and install allow it.

    Raises `ValueError` when `masking_ner` is `spacy` and the model is not
    installed, and `RemoteModelRefusedError` for a model that is not on this machine.
    """
    if settings.masking_ner == "off":
        return ()
    model = settings.masking_ner_model
    if SpacyNerDetector.is_available(model):
        return (LazySpacyNerDetector(model),)
    if settings.masking_ner == "spacy":
        raise ValueError(
            f"masking_ner is spacy but {model} is not installed: uv sync --extra ner"
        )
    return ()


def _signals(settings: Settings) -> Signals:
    if settings.valkey_url is None:
        return MemorySignals()
    return ValkeySignals.from_url(settings.valkey_url.get_secret_value())


@dataclass(frozen=True, slots=True)
class _Pipeline:
    """What the real pipeline stages share, built once per process."""

    storage: StorageProvider
    store: PaperStore
    plans: PlanSource[UnitOfWork]
    masked: MaskedTextSource[UnitOfWork]
    sources: tuple[PaperSearchProvider, ...]
    inference: GuardedInference
    rules: MaskingRuleService
    audit: AuditLog
    clock: Callable[[], datetime]


def _pipeline_stages(settings: Settings, pipeline: _Pipeline) -> dict[str, Stage]:
    p = pipeline
    return {
        "discover": DiscoverStage(
            storage=p.storage,
            store=p.store,
            plans=p.plans,
            sources=p.sources,
            limit_per_source=settings.papers_per_source,
            clock=p.clock,
        ),
        "ingest": IngestStage(storage=p.storage, store=p.store, clock=p.clock),
        "screen": ScreenStage(
            storage=p.storage,
            store=p.store,
            plans=p.plans,
            inference=p.inference,
            masked=p.masked,
            rules=p.rules,
            audit=p.audit,
            concurrency=settings.screening_concurrency,
            clock=p.clock,
        ),
        "export": ExportStage(storage=p.storage, store=p.store),
    }


_QUERY_COMPILERS: Mapping[str, QueryCompiler] = {
    openalex.SOURCE_ID: openalex.compile_openalex_query,
    europepmc.SOURCE_ID: europepmc.compile_europepmc_query,
    arxiv.SOURCE_ID: arxiv.compile_arxiv_query,
    demo.SOURCE_ID: demo.compile_demo_query,
}


def planning_sanitizer(settings: Settings, ner: tuple[Detector, ...]) -> Sanitizer:
    """Questions, answers and change requests: the question list, places as the operator set."""
    return DetectorSanitizer(
        (PatternDetector(), *ner),
        entity_types=QUESTION_ENTITY_TYPES,
        keeps_countries_and_regions=not settings.mask_countries_and_regions,
    )


def _mask_stage(
    storage: StorageProvider,
    store: PaperStore,
    masks: PaperMaskStore,
    rules: MaskingRuleService,
    ner: tuple[Detector, ...],
    documents: DocumentsAwaitingReview | None = None,
) -> MaskStage:
    # Papers get the narrow list, so the name model is never run on them.
    sanitizer = DetectorSanitizer((PatternDetector(), *ner), entity_types=PAPER_ENTITY_TYPES)
    return MaskStage(
        storage=storage,
        texts=store,
        masks=masks,
        rules=rules,
        sanitizer=sanitizer,
        detector_sources=sanitizer.sources,
        entity_types=tuple(PAPER_ENTITY_TYPES),
        documents=documents,
    )


def _document_sanitizer(ner: tuple[Detector, ...]) -> DetectorSanitizer:
    """The broad list for a team's own documents, dates included (brief rule 3)."""
    return DetectorSanitizer(
        (PatternDetector(), DatePatternDetector(), *ner), entity_types=DOCUMENT_ENTITY_TYPES
    )


def _file_store(settings: Settings) -> FileStore:
    if settings.document_storage_dir is None:
        return MemoryFileStore()
    return LocalVolumeFileStore(settings.document_storage_dir)


def _malware_scanner(settings: Settings) -> MalwareScanner:
    if settings.malware_scanner == "clamd" and settings.clamd_address:
        return ClamdScanner(ClamdAddress.parse(settings.clamd_address))
    # Unset outside production means none; in production an unset scanner has
    # already switched uploads off (`Settings.uploads_disabled_reason`).
    return NoMalwareScanner()


def _upload_policy(settings: Settings) -> UploadPolicy:
    return UploadPolicy(
        max_file_bytes=settings.upload_max_bytes,
        max_documents=settings.documents_per_project,
        max_project_bytes=settings.document_bytes_per_project,
        uploads_per_hour=settings.uploads_per_hour,
        disabled_reason=settings.uploads_disabled_reason,
    )


def _extraction_limits(settings: Settings) -> ExtractionLimits:
    return ExtractionLimits(
        max_pages=settings.extraction_max_pages,
        max_characters=settings.extraction_max_characters,
        timeout_seconds=settings.extraction_timeout_seconds,
    )


def build_providers(
    settings: Settings,
    *,
    clock: Callable[[], datetime] = _utc_now,
    password_hasher: PasswordHasher | None = None,
    storage: StorageProvider | None = None,
    email: EmailProvider | None = None,
    rate_limiter: RateLimiter | None = None,
    mfa_policy: MfaPolicy | None = None,
    file_store: FileStore | None = None,
    malware_scanner: MalwareScanner | None = None,
    document_extractor: DocumentExtractor | None = None,
) -> Providers:
    """Turn configuration into implementations: the one place that does.

    The keyword arguments are for tests. MFA is required unless `mfa_policy` says
    otherwise, and that is not an operator setting.
    """
    storage = storage or _storage(settings)
    email = email or _email(settings)
    limiter = rate_limiter or _rate_limiter(settings, clock)
    passkeys: PasskeyVerifier = WebAuthnPasskeyVerifier(public_origin=settings.public_origin)
    encryption = ScopeEncryption(_key_provider(settings), AesGcmCipher(), clock)
    audit = AuditLog(encryption, clock)
    sources = [_paper_source(source_id, settings) for source_id in settings.paper_source_ids]
    inference, inference_client = _inference(settings)
    signals = _signals(settings)
    ner = ner_detectors(settings)
    masking_rules = MaskingRuleService(
        storage=storage, encryption=encryption, audit=audit, clock=clock
    )
    masks = PaperMaskStore(encryption, clock)
    files = file_store or _file_store(settings)
    document_evidence = DocumentEvidence(encryption)
    plans = PlanService(
        storage=storage,
        encryption=encryption,
        audit=audit,
        inference=inference,
        sanitizer=planning_sanitizer(settings, ner),
        rules=masking_rules,
        clock=clock,
    )
    pipeline = _Pipeline(
        storage=storage,
        store=PaperStore(encryption, clock),
        plans=plans,
        masked=masks,
        sources=tuple(provider for provider, _ in sources),
        inference=inference,
        rules=masking_rules,
        audit=audit,
        clock=clock,
    )
    compilers = {s: _QUERY_COMPILERS[s] for s in settings.paper_source_ids}
    stages = {
        "queries": QueriesStage(plans, compilers),
        "mask": _mask_stage(
            storage, pipeline.store, masks, masking_rules, ner, DocumentReviewQueue(storage)
        ),
        **_pipeline_stages(settings, pipeline),
        "answer": AnswerStage(
            storage=storage,
            encryption=encryption,
            audit=audit,
            inference=inference,
            retriever=Bm25Retriever(),
            evidence=ScreenedEvidence(pipeline.store, pipeline.masked, document_evidence),
            catalogue=ScreenedSources(pipeline.store),
            sanitizer=planning_sanitizer(settings, ner),
            rules=masking_rules,
            max_rounds=settings.brief_max_rounds,
            clock=clock,
        ),
    }
    runs = RunService(
        storage=storage,
        encryption=encryption,
        audit=audit,
        signals=signals,
        available_stages=tuple(stages),
        rules=masking_rules,
        clock=clock,
    )
    document_sanitizer = _document_sanitizer(ner)
    return Providers(
        storage=storage,
        encryption=encryption,
        audit=audit,
        accounts=AccountService(
            storage=storage,
            hasher=password_hasher or Argon2PasswordHasher(),
            email=email,
            limiter=limiter,
            audit=audit,
            public_origin=settings.public_origin,
            mfa_policy=mfa_policy or MfaPolicy(),
            clock=clock,
        ),
        email=email,
        rate_limiter=limiter,
        projects=ProjectService(
            storage=storage, encryption=encryption, audit=audit, clock=clock
        ),
        mfa=MfaService(
            storage=storage,
            encryption=encryption,
            audit=audit,
            email=email,
            limiter=limiter,
            passkeys=passkeys,
            clock=clock,
        ),
        paper_sources=tuple(provider for provider, _ in sources),
        inference=inference,
        http_clients=tuple(
            client for client in (*(c for _, c in sources), inference_client) if client
        ),
        signals=signals,
        stages=stages,
        reviews=ReviewService(storage=storage, encryption=encryption, audit=audit, clock=clock),
        runs=runs,
        papers=PaperService(
            storage=storage,
            store=pipeline.store,
            masked=pipeline.masked,
            encryption=encryption,
            citations=StoredBriefCitations(encryption),
        ),
        plans=plans,
        masking_rules=masking_rules,
        briefs=BriefService(storage=storage, encryption=encryption),
        questions=QuestionService(
            storage=storage,
            encryption=encryption,
            audit=audit,
            sanitizer=planning_sanitizer(settings, ner),
            rules=masking_rules,
            signals=signals,
            clock=clock,
        ),
        answers=QuestionWorker(
            storage=storage,
            encryption=encryption,
            inference=inference,
            retriever=Bm25Retriever(),
            evidence=ScreenedEvidence(pipeline.store, pipeline.masked, document_evidence),
            rules=masking_rules,
            worker_id=f"answers-{uuid.uuid4().hex[:12]}",
            signals=signals,
            clock=clock,
        ),
        documents=DocumentService(
            storage=storage,
            encryption=encryption,
            audit=audit,
            files=files,
            limiter=limiter,
            signals=signals,
            rules=masking_rules,
            policy=_upload_policy(settings),
            runs=runs,
            clock=clock,
        ),
        document_processor=DocumentProcessor(
            storage=storage,
            encryption=encryption,
            audit=audit,
            files=files,
            scanner=malware_scanner or _malware_scanner(settings),
            extractor=document_extractor or IsolatedDocumentExtractor(),
            sanitizer=document_sanitizer,
            detector_sources=document_sanitizer.sources,
            rules=masking_rules,
            signals=signals,
            limits=_extraction_limits(settings),
            worker_id=f"documents-{uuid.uuid4().hex[:12]}",
            clock=clock,
        ),
    )


def get_providers(request: Request) -> Providers:
    providers: Providers = request.app.state.providers
    return providers


ProvidersDep = Annotated[Providers, Depends(get_providers)]


def get_storage(providers: ProvidersDep) -> StorageProvider:
    return providers.storage


StorageDep = Annotated[StorageProvider, Depends(get_storage)]


def get_accounts(providers: ProvidersDep) -> AccountService:
    return providers.accounts


AccountsDep = Annotated[AccountService, Depends(get_accounts)]


def get_mfa(providers: ProvidersDep) -> MfaService:
    return providers.mfa


MfaDep = Annotated[MfaService, Depends(get_mfa)]


def get_projects(providers: ProvidersDep) -> ProjectService:
    return providers.projects


ProjectsDep = Annotated[ProjectService, Depends(get_projects)]


def get_reviews(providers: ProvidersDep) -> ReviewService:
    return providers.reviews


ReviewsDep = Annotated[ReviewService, Depends(get_reviews)]


def get_runs(providers: ProvidersDep) -> RunService:
    return providers.runs


RunsDep = Annotated[RunService, Depends(get_runs)]


def get_papers(providers: ProvidersDep) -> PaperService:
    return providers.papers


PapersDep = Annotated[PaperService, Depends(get_papers)]


def get_plans(providers: ProvidersDep) -> PlanService:
    return providers.plans


PlansDep = Annotated[PlanService, Depends(get_plans)]


def get_masking_rules(providers: ProvidersDep) -> MaskingRuleService:
    return providers.masking_rules


MaskingRulesDep = Annotated[MaskingRuleService, Depends(get_masking_rules)]


def get_briefs(providers: ProvidersDep) -> BriefService:
    return providers.briefs


BriefsDep = Annotated[BriefService, Depends(get_briefs)]


def get_exports(providers: ProvidersDep) -> ExportService:
    # Stateless, so built per request over the same services the screens use.
    return ExportService(
        storage=providers.storage,
        papers=providers.papers,
        briefs=providers.briefs,
        audit=providers.audit,
    )


ExportsDep = Annotated[ExportService, Depends(get_exports)]


def get_questions(providers: ProvidersDep) -> QuestionService:
    return providers.questions


QuestionsDep = Annotated[QuestionService, Depends(get_questions)]


def get_documents(providers: ProvidersDep) -> DocumentService:
    return providers.documents


DocumentsDep = Annotated[DocumentService, Depends(get_documents)]
