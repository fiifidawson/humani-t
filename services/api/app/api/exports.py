"""Turn an export's content into the bytes of a file.

Files are built from the same response models the screens read, so a download says
what the screen says, masked where the screen is masked. Text from papers and the
model is data: CSV cells are neutralised against formulas and HTML is escaped. The
brief's "PDF" is print-ready HTML; no PDF engine runs on the server.
"""

from __future__ import annotations

import csv
import html
import io
from collections.abc import Iterable, Sequence
from typing import Any

from app.api.schemas.briefs import BriefOut, ClaimOut
from app.api.schemas.papers import ManifestOut, PaperOut, PrismaOut
from app.application.exports import ExportContent
from app.domain.exports import ExportName, neutralise_cell

_CSV_COLUMNS = (
    "tag",
    "position",
    "title",
    "year",
    "doi",
    "url",
    "source_id",
    "found_by",
    "is_open_access",
    "label",
    "tier",
    "label_set_by",
    "rules_version",
    "priority",
    "score",
    "rationale",
    "criteria_matched",
    "exclusion_reasons",
    "is_unreadable",
    "model_id",
    "prompt_version",
    "criteria_version",
    "evidence_kind",
    "decided_at",
)

_STRENGTH_WORDS = {
    "strong": "Strong evidence",
    "moderate": "Moderate evidence",
    "weak": "Weak evidence",
    "insufficient": "Not enough evidence",
}
_CONFLICT_WORDS = {
    "hierarchy": "Stronger and weaker studies disagree",
    "context": "Different settings, different results",
    "noise": "Small or unclear studies",
}
_REASONING = "reasoning, not a source"


def render(content: ExportContent) -> bytes:
    """The file's bytes, UTF-8."""
    name = content.name
    if name is ExportName.DATASET_CSV:
        return _dataset_csv(_papers(content)).encode()
    if name is ExportName.DATASET_JSONL:
        return "".join(p.model_dump_json() + "\n" for p in _papers(content)).encode()
    if name is ExportName.INCLUDED_RIS:
        return _ris(_papers(content)).encode()
    if name is ExportName.PRISMA_MD:
        assert content.prisma is not None  # noqa: S101 — the service sets it for this name
        return _prisma_md(PrismaOut.from_view(content.prisma)).encode()
    if name is ExportName.MANIFEST_JSON:
        assert content.manifest is not None  # noqa: S101
        return ManifestOut.from_domain(content.manifest).model_dump_json(indent=2).encode()
    assert content.brief is not None  # noqa: S101
    brief = BriefOut.from_domain(content.brief)
    if name is ExportName.BRIEF_JSON:
        return brief.model_dump_json(indent=2).encode()
    if name is ExportName.BRIEF_MD:
        return _brief_md(brief).encode()
    return _brief_html(brief).encode()


def _papers(content: ExportContent) -> list[PaperOut]:
    return [PaperOut.from_row(row) for row in content.rows]


def _flat(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        return "; ".join(str(v) for v in value)
    return str(value)


def _dataset_csv(papers: Sequence[PaperOut]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_CSV_COLUMNS)
    for paper in papers:
        values = paper.model_dump(mode="json")
        decision = values.pop("decision") or {}
        merged = {**values, **decision}
        writer.writerow([neutralise_cell(_flat(merged.get(c))) for c in _CSV_COLUMNS])
    return buffer.getvalue()


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _ris(papers: Sequence[PaperOut]) -> str:
    lines: list[str] = []
    for paper in papers:
        lines += ["TY  - JOUR", f"ID  - {paper.tag}", f"TI  - {_one_line(paper.title)}"]
        if paper.year is not None:
            lines.append(f"PY  - {paper.year}")
        if paper.doi:
            lines.append(f"DO  - {_one_line(paper.doi)}")
        if paper.url:
            lines.append(f"UR  - {_one_line(paper.url)}")
        lines.append("ER  - ")
        lines.append("")
    return "\r\n".join(lines)


def _cell(text: object) -> str:
    """A Markdown table cell: one line, and no `|` that would end it early."""
    return _one_line(str(text)).replace("|", "\\|")


def _prisma_md(prisma: PrismaOut) -> str:
    rows = [
        ("Records identified", prisma.identified),
        *((f"Records identified from {s}", n) for s, n in prisma.identified_by_source.items()),
        ("Duplicates removed", prisma.duplicates_removed),
        ("Records after duplicates removed", prisma.after_deduplication),
        ("Records screened", prisma.screened),
        ("Not screened", prisma.not_screened),
        ("Included", prisma.included),
        ("Excluded", prisma.excluded),
        ("Uncertain", prisma.uncertain),
        *((f"Excluded by {c}", n) for c, n in prisma.excluded_by_reason.items()),
    ]
    lines = [
        "# PRISMA flow",
        "",
        f"Run `{prisma.run_id}`.",
        "",
        "| Step | Records |",
        "| --- | --- |",
        *(f"| {_cell(step)} | {count} |" for step, count in rows),
    ]
    if prisma.failed_sources:
        lines += [
            "",
            f"Sources that could not be searched: {', '.join(prisma.failed_sources)}.",
        ]
    return "\n".join(lines) + "\n"


def _claim_md(claim: ClaimOut) -> str:
    if claim.citations:
        return f"{_one_line(claim.text)} " + " ".join(f"[{c.tag}]" for c in claim.citations)
    return f"*{_one_line(claim.text)}* ({_REASONING})"


def _brief_md(brief: BriefOut) -> str:
    out = [
        f"# Decision brief, version {brief.version}",
        "",
        f"Model {brief.model_id} · round {brief.kept_round} of {brief.rounds} · "
        f"stopped: {brief.stopped_because.value} · {brief.created_at.isoformat()}",
        "",
        f"**Question (as sent, masked):** {_one_line(brief.question)}",
        "",
        f"> {brief.caveat}",
        "",
        "## Bottom line",
        "",
        " ".join(_claim_md(c) for c in brief.bottom_line),
    ]
    if brief.warnings:
        out += ["", "**Checks still failing:**", ""]
        out += [f"- {_one_line(w.message)}" for w in brief.warnings]
    out += ["", "## Options", ""]
    for option in brief.options:
        out += [f"### {_one_line(option.name)}", ""]
        out += [
            f"{_STRENGTH_WORDS[option.strength.value]}. {_one_line(option.description)}",
            "",
        ]
        out += ["Benefits:", "", *(f"- + {_claim_md(c)}" for c in option.benefits), ""]
        out += ["Risks:", "", *(f"- - {_claim_md(c)}" for c in option.risks), ""]
    names = [o.name for o in brief.options]
    out += ["## Trade-offs", "", "| Criterion | " + " | ".join(_cell(n) for n in names) + " |"]
    out.append("| --- " * (len(names) + 1) + "|")
    for row in brief.trade_offs:
        label = f"{row.criterion} (humanitarian)" if row.is_humanitarian else row.criterion
        cells = {a.option: _claim_md(a.claim) for a in row.assessments}
        out.append(
            f"| {_cell(label)} | " + " | ".join(_cell(cells.get(n, "")) for n in names) + " |"
        )
    out += ["", "## Where the evidence disagrees", ""]
    for conflict in brief.conflicts:
        out += [
            f"**{_one_line(conflict.question)}** "
            f"({_CONFLICT_WORDS[conflict.conflict_type.value]})",
            "",
        ]
        out += [f"- {_claim_md(p)}" for p in conflict.positions]
        if conflict.meaning is not None:
            out += [f"- What this means: {_claim_md(conflict.meaning)}"]
        out.append("")
    out += ["## If this matters most", ""]
    out += [
        f"- {_one_line(c.criterion)} → {_one_line(c.option)}, because {_claim_md(c.because)}"
        for c in brief.considerations
    ]
    out += ["", "## Gaps in the evidence", "", *(f"- {_one_line(g)}" for g in brief.gaps), ""]
    out += ["## Sources", ""]
    for source in brief.sources:
        details = ", ".join(
            _one_line(str(v)) for v in (source.title, source.year, source.doi) if v
        )
        cited = f"cited {source.citations} times"
        out.append(f"- [{source.tag}] {details or 'no passage'} ({cited})")
    out += ["", "---", "", brief.caveat, ""]
    return "\n".join(out)


def _claim_html(claim: ClaimOut) -> str:
    text = html.escape(_one_line(claim.text))
    if claim.citations:
        tags = " ".join(f"[{html.escape(c.tag)}]" for c in claim.citations)
        return f"{text} <span class=tags>{tags}</span>"
    return f"<em>{text}</em> <span class=reasoning>({_REASONING})</span>"


def _items(claims: Iterable[ClaimOut], mark: str) -> str:
    return "".join(f"<li>{mark} {_claim_html(c)}</li>" for c in claims)


_PRINT_CSS = """
@page { margin: 18mm 16mm 24mm; }
body { font: 11pt/1.45 system-ui, sans-serif; color: #111; margin: 0 0 20mm; }
h1 { font-size: 18pt; } h2 { font-size: 14pt; margin-top: 1.4em; } h3 { font-size: 12pt; }
.caveat { border: 1px solid #b45309; padding: 6pt 8pt; }
.reasoning { color: #555; } .tags { color: #1d4ed8; }
table { border-collapse: collapse; width: 100%; } th, td { border: 1px solid #999;
  padding: 4pt; vertical-align: top; text-align: left; }
footer { position: fixed; bottom: 0; left: 0; right: 0; font-size: 8.5pt;
  border-top: 1px solid #999; padding-top: 3pt; background: #fff; }
"""


def _brief_html(brief: BriefOut) -> str:
    e = html.escape
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>Decision brief, version {brief.version}</title>",
        f"<style>{_PRINT_CSS}</style></head><body>",
        f"<footer>{e(brief.caveat)}</footer>",
        f"<h1>Decision brief, version {brief.version}</h1>",
        f"<p>Model {e(brief.model_id)} · round {brief.kept_round} of {brief.rounds} · "
        f"stopped: {e(brief.stopped_because.value)} · {e(brief.created_at.isoformat())}</p>",
        f"<p><strong>Question (as sent, masked):</strong> {e(brief.question)}</p>",
        f'<p class="caveat">{e(brief.caveat)}</p>',
        "<h2>Bottom line</h2>",
        "<p>" + " ".join(_claim_html(c) for c in brief.bottom_line) + "</p>",
    ]
    if brief.warnings:
        parts.append("<p><strong>Checks still failing:</strong></p><ul>")
        parts += [f"<li>{e(w.message)}</li>" for w in brief.warnings]
        parts.append("</ul>")
    parts.append("<h2>Options</h2>")
    for option in brief.options:
        parts += [
            f"<h3>{e(option.name)}</h3>",
            f"<p>{_STRENGTH_WORDS[option.strength.value]}. {e(option.description)}</p>",
            f"<p>Benefits</p><ul>{_items(option.benefits, '+')}</ul>",
            f"<p>Risks</p><ul>{_items(option.risks, '-')}</ul>",
        ]
    names = [o.name for o in brief.options]
    parts.append("<h2>Trade-offs</h2><table><tr><th>Criterion</th>")
    parts += [f"<th>{e(n)}</th>" for n in names]
    parts.append("</tr>")
    for row in brief.trade_offs:
        label = f"{row.criterion} (humanitarian)" if row.is_humanitarian else row.criterion
        cells = {a.option: _claim_html(a.claim) for a in row.assessments}
        parts.append(f"<tr><th>{e(label)}</th>")
        parts += [f"<td>{cells.get(n, '')}</td>" for n in names]
        parts.append("</tr>")
    parts.append("</table><h2>Where the evidence disagrees</h2>")
    for conflict in brief.conflicts:
        words = _CONFLICT_WORDS[conflict.conflict_type.value]
        parts.append(f"<p><strong>{e(conflict.question)}</strong> ({words})</p>")
        parts.append(f"<ul>{_items(conflict.positions, '')}</ul>")
        if conflict.meaning is not None:
            parts.append(f"<p>What this means: {_claim_html(conflict.meaning)}</p>")
    parts.append("<h2>If this matters most</h2><ul>")
    parts += [
        f"<li>{e(c.criterion)} → {e(c.option)}, because {_claim_html(c.because)}</li>"
        for c in brief.considerations
    ]
    parts.append("</ul><h2>Gaps in the evidence</h2><ul>")
    parts += [f"<li>{e(g)}</li>" for g in brief.gaps]
    parts.append("</ul><h2>Sources</h2><ul>")
    for source in brief.sources:
        details = ", ".join(str(v) for v in (source.title, source.year, source.doi) if v)
        parts.append(
            f"<li>[{e(source.tag)}] {e(details or 'no passage')} "
            f"(cited {source.citations} times)</li>"
        )
    parts.append("</ul></body></html>")
    return "\n".join(parts) + "\n"
