"""On-demand search of large biomedical citation databases.

The registry contains source organizations; this module searches publication
records at query time. A citation is a research lead, not a truth verdict.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx

from .mongolian import detect_language, match_key

EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
HEALTH_CUES = {
    "health", "medical", "medicine", "disease", "illness", "symptom", "treatment",
    "vaccine", "vaccination", "drug", "medication", "virus", "cancer", "diabetes",
    "pregnancy", "infection", "clinical", "doctor", "hospital", "therapy", "nutrition",
    "эрүүл", "эм", "вакцин", "өвчин", "эмчилгээ", "вирус", "хавдар", "чихрийн", "шижин",
    "eruul", "mend", "vaksin", "vaktsin", "ovchin", "emchilgee", "havdar",
}


def _search_terms(claim: str) -> str:
    # Europe PMC searches biomedical titles and abstracts. Keep a handful of
    # substantive terms so a full social post does not become a brittle phrase.
    tokens = [t for t in match_key(claim).split() if len(t) > 3]
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return " ".join(seen[:8])


async def search_health_literature(claim: str, limit: int = 6) -> dict:
    """Return citation leads from Europe PMC, which includes PubMed records."""
    normalized = match_key(claim)
    if not (set(normalized.split()) & HEALTH_CUES):
        return {"available": False, "reason": "No health-related search terms detected.", "results": []}
    if detect_language(claim) in {"mn_cyrl", "mn_latn"}:
        return {"available": False, "reason": "Health literature search needs an English query; Mongolian-to-English translation is not configured.", "results": []}
    terms = _search_terms(claim)
    # Do not feed Cyrillic or Mongolian Latin transliteration to a biomedical
    # English-language index as if it had been translated.
    if not terms or re.search(r"[А-Яа-яЁёӨөҮү]", claim):
        return {"available": False, "reason": "English query needed for biomedical database search.", "results": []}
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True,
                                     headers={"User-Agent": "TAYO/0.1 (health literature lookup)"}) as client:
            response = await client.get(EUROPE_PMC, params={
                "query": f'TITLE_ABS:({terms}) AND (SRC:MED OR SRC:PMC)',
                "format": "json", "resultType": "core", "pageSize": max(1, min(limit, 10)),
                "sort": "RELEVANCE",
            })
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return {"available": False, "reason": f"Biomedical literature search unavailable: {str(exc)[:120]}", "results": []}

    results = []
    for record in payload.get("resultList", {}).get("result", []):
        title = (record.get("title") or "").strip()
        source = record.get("journalTitle") or record.get("authorString") or "Europe PMC record"
        pmid = record.get("pmid")
        doi = record.get("doi")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else (
            f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{record.get('source', 'MED')}/{record.get('id', '')}"
        )
        abstract = re.sub(r"<[^>]*>", " ", record.get("abstractText") or "")
        abstract = re.sub(r"\s+", " ", abstract).strip()
        results.append({
            "title": title,
            "source": f"{source} · Europe PMC / PubMed",
            "url": url,
            "snippet": abstract[:420] or "Citation record; abstract unavailable.",
            "published": str(record.get("firstPublicationDate") or record.get("pubYear") or ""),
            "tier": "research",
            "relation": "related",
            "published_verdict": None,
            "pmid": pmid,
            "doi": doi,
            "publication_types": record.get("pubTypeList", {}).get("pubType", []),
            "open_access": record.get("isOpenAccess") == "Y",
        })
    return {"available": True, "database": "Europe PMC (including PubMed citations)",
            "record_count": int(payload.get("hitCount", 0)), "results": results,
            "note": "Research citations are leads, not proof. Check study design, publication type, corrections/retractions, date, and whether the population and outcome match the claim."}
