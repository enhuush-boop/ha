from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import ssl
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography import x509

from ..config import settings
from .net_utils import UnsafeUrlError, parse_http_url, resolve_public_host

SUSPICIOUS_TLDS = {
    "zip",
    "mov",
    "top",
    "xyz",
    "click",
    "link",
    "live",
    "shop",
    "support",
    "review",
    "country",
    "stream",
    "download",
    "racing",
    "win",
    "bid",
    "loan",
    "work",
    "men",
    "gq",
    "tk",
    "ml",
    "ga",
    "cf",
}

SHORTENERS = {
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "goo.gl",
    "ow.ly",
    "is.gd",
    "buff.ly",
    "rebrand.ly",
    "cutt.ly",
    "shorturl.at",
    "rb.gy",
    "tiny.cc",
}

BRANDS = [
    "google",
    "microsoft",
    "apple",
    "amazon",
    "paypal",
    "facebook",
    "instagram",
    "whatsapp",
    "netflix",
    "bankofamerica",
    "wellsfargo",
    "chase",
    "dhl",
    "fedex",
    "usps",
    "linkedin",
    "dropbox",
    "adobe",
    "coinbase",
    "binance",
    "outlook",
    "office365",
    "icloud",
]

PATH_KEYWORDS = (
    "login",
    "signin",
    "sign-in",
    "verify",
    "secure",
    "account",
    "update",
    "password",
    "billing",
    "invoice",
    "wallet",
    "unlock",
    "confirm",
    "support",
    "auth",
)

URL_LIMITATIONS = [
    "The local backend does not fetch the page body, execute JavaScript, or submit forms. When VirusTotal is configured, however, the URL is submitted to VirusTotal for scanning and may be fetched by its scanners.",
    "VirusTotal submissions are added to its dataset and can become accessible to its community. Do not scan URLs containing secrets, personal information, or private tokens.",
    "DNSSEC proves that DNS answers were signed by the domain owner; it does not prove the site is honest or legitimate.",
    "A valid TLS certificate (especially DV) only proves control of the domain, not that the site is trustworthy. Phishing sites routinely get valid certificates.",
    "New domain age, typosquatting, and threat-intel matches are risk indicators, not proof of phishing.",
    "For a definitive page-behavior analysis, use a disposable sandbox (for example urlscan.io or a Playwright container) and never enter credentials.",
]


def _add(
    signals: list[dict],
    name: str,
    triggered: bool,
    weight: int,
    detail: str,
) -> None:
    signals.append(
        {
            "name": name,
            "triggered": bool(triggered),
            "weight": weight if triggered else 0,
            "detail": detail,
        }
    )


def _offline_signals(parsed) -> tuple[list[dict], str]:
    host = (parsed.hostname or "").lower().rstrip(".")
    host_no_www = host[4:] if host.startswith("www.") else host
    path = parsed.path or ""
    query = parsed.query or ""
    signals: list[dict] = []

    _add(
        signals,
        "https",
        parsed.scheme == "https",
        10 if parsed.scheme != "https" else 0,
        "HTTPS is not used." if parsed.scheme != "https" else "HTTPS is used.",
    )
    _add(
        signals,
        "userinfo_in_url",
        bool(parsed.username or parsed.password),
        25,
        "The URL contains a username/password component, a common obfuscation trick.",
    )

    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    _add(
        signals,
        "ip_literal_host",
        is_ip,
        25,
        "The host is a raw IP address instead of a domain name.",
    )

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except Exception:
        ascii_host = host
    _add(
        signals,
        "punycode_or_idn",
        "xn--" in host or ascii_host != host,
        20,
        "The hostname uses punycode/internationalized characters. Check for homoglyph impersonation.",
    )

    _add(
        signals,
        "non_standard_port",
        parsed.port not in (None, 80, 443),
        12,
        f"Non-standard port: {parsed.port}.",
    )

    parts = host_no_www.split(".")
    suffix = parts[-1] if parts else ""
    _add(
        signals,
        "suspicious_tld",
        suffix in SUSPICIOUS_TLDS,
        8,
        f"TLD .{suffix} is sometimes abused, but many legitimate sites use it too.",
    )
    _add(
        signals,
        "url_shortener",
        host_no_www in SHORTENERS,
        12,
        "The link is a URL shortener; expand it before judging the destination.",
    )
    _add(
        signals,
        "many_subdomains",
        len(parts) >= 4,
        8,
        f"{len(parts)} labels in the hostname. Deep subdomains can hide the registered domain.",
    )
    _add(
        signals,
        "host_hyphens",
        host_no_www.count("-") >= 3,
        5,
        "The hostname contains several hyphens.",
    )
    _add(
        signals,
        "host_digits",
        len(re.findall(r"\d", host_no_www)) >= 3,
        5,
        "The hostname contains several digits.",
    )
    _add(
        signals,
        "long_url",
        len(str(parsed.geturl())) > 110,
        5,
        "The URL is unusually long.",
    )

    candidate = parts[0] if parts else host_no_www
    lookalike_detail = None
    for brand in BRANDS:
        if candidate == brand:
            lookalike_detail = None
            break
        if brand in candidate and candidate != brand:
            lookalike_detail = f"Host label '{candidate}' contains the brand '{brand}' with extra text."
            break
        # Cheap edit-distance check for common typosquats.
        if abs(len(candidate) - len(brand)) <= 2 and _edit_distance(candidate, brand) <= 2:
            lookalike_detail = f"Host label '{candidate}' is very close to the brand '{brand}'."
            break
    _add(
        signals,
        "brand_lookalike",
        bool(lookalike_detail),
        28,
        lookalike_detail or "No simple brand-lookalike pattern detected.",
    )

    path_hits = [word for word in PATH_KEYWORDS if word in path.lower()]
    _add(
        signals,
        "sensitive_path_words",
        bool(path_hits),
        6,
        f"Path contains: {', '.join(path_hits)}." if path_hits else "No sensitive path words.",
    )
    _add(
        signals,
        "credential_query_terms",
        any(term in query.lower() for term in ("password", "token", "email", "account", "verify")),
        5,
        "Query string contains credential/account-related terms.",
    )

    return signals, host_no_www


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            replace = previous[j - 1] + (char_a != char_b)
            current.append(min(insert, delete, replace))
        previous = current
    return previous[-1]


async def _doh(client: httpx.AsyncClient, name: str, rtype: str) -> dict:
    try:
        response = await client.get(
            "https://dns.google/resolve",
            params={"name": name, "type": rtype, "do": "1"},
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        return {"error": str(exc), "Status": -1}


async def _dns_checks(host: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        types = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "DS"]
        results = await asyncio.gather(
            *[_doh(client, host, rtype) for rtype in types]
        )
    by_type = dict(zip(types, results))
    a = by_type.get("A", {})
    ds = by_type.get("DS", {})
    answers: dict[str, list] = {}
    for rtype, data in by_type.items():
        answers[rtype] = data.get("Answer", []) if isinstance(data, dict) else []
    return {
        "status": a.get("Status"),
        "dnssec": {
            "validated": bool(a.get("AD")),
            "ds_present": bool(ds.get("Answer")),
            "note": (
                "AD=true means the recursive resolver validated DNSSEC. A false value can also mean the resolver did not validate; absence of DNSSEC is not proof of malice."
            ),
        },
        "answers": answers,
        "errors": {
            rtype: data.get("error")
            for rtype, data in by_type.items()
            if isinstance(data, dict) and data.get("error")
        },
    }


def _certificate_info(der: bytes) -> dict:
    info: dict[str, Any] = {}
    try:
        cert = x509.load_der_x509_certificate(der)
        info.update(
            {
                "subject": cert.subject.rfc4514_string(),
                "issuer": cert.issuer.rfc4514_string(),
                "serial_number": format(cert.serial_number, "x"),
                "not_before": cert.not_valid_before_utc.isoformat(),
                "not_after": cert.not_valid_after_utc.isoformat(),
                "days_until_expiry": (
                    cert.not_valid_after_utc - datetime.now(timezone.utc)
                ).days,
                "signature_algorithm": str(cert.signature_algorithm_oid),
            }
        )
        try:
            san = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
            info["san"] = san.get_values_for_type(x509.DNSName)
        except Exception:
            info["san"] = []
        try:
            policies = cert.extensions.get_extension_for_class(
                x509.CertificatePolicies
            ).value
            oids = [policy.policy_identifier.dotted_string for policy in policies]
            info["policy_oids"] = oids
            if "2.23.140.1.1" in oids:
                info["validation_type"] = "EV"
            elif "2.23.140.1.2.2" in oids:
                info["validation_type"] = "OV"
            elif "2.23.140.1.2.1" in oids:
                info["validation_type"] = "DV"
            else:
                info["validation_type"] = "unknown"
        except Exception:
            info["validation_type"] = "unknown"
    except Exception as exc:
        info["parse_error"] = str(exc)
    return info


def _inspect_tls(host: str, port: int = 443) -> dict:
    result: dict[str, Any] = {
        "host": host,
        "port": port,
        "connected": False,
        "valid": None,
    }
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=8) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                result.update(
                    {
                        "connected": True,
                        "valid": True,
                        "tls_version": tls.version(),
                        "cipher": tls.cipher()[0] if tls.cipher() else None,
                    }
                )
                der = tls.getpeercert(binary_form=True)
                result["certificate"] = _certificate_info(der)
    except ssl.SSLCertVerificationError as exc:
        result.update({"connected": True, "valid": False, "error": str(exc)})
        try:
            with socket.create_connection((host, port), timeout=8) as sock:
                with ssl._create_unverified_context().wrap_socket(
                    sock, server_hostname=host
                ) as tls:
                    der = tls.getpeercert(binary_form=True)
                    result["certificate"] = _certificate_info(der)
        except Exception as inner:
            result["inspect_error"] = str(inner)
    except Exception as exc:
        result["error"] = str(exc)
    return result


async def _rdap(domain: str) -> dict:
    try:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout, follow_redirects=True
        ) as client:
            response = await client.get(f"https://rdap.org/domain/{domain}")
            if response.status_code == 404:
                return {"found": False, "status_code": 404}
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return {"found": False, "error": str(exc)}

    events = {
        event.get("eventAction"): event.get("eventDate")
        for event in data.get("events", [])
        if isinstance(event, dict)
    }
    created = events.get("registration")
    expires = events.get("expiration")
    age_days = None
    if created:
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created_dt).days
        except Exception:
            pass

    registrar = None
    for entity in data.get("entities", []):
        if "registrar" in (entity.get("roles") or []):
            vcard = entity.get("vcardArray", [None, []])
            for field in vcard[1] if len(vcard) > 1 else []:
                if field and field[0] == "fn":
                    registrar = field[3]
                    break
            break

    return {
        "found": True,
        "handle": data.get("handle"),
        "name": data.get("ldhName"),
        "registrar": registrar,
        "status": data.get("status", []),
        "events": events,
        "created": created,
        "expires": expires,
        "age_days": age_days,
        "nameservers": [
            ns.get("ldhName") for ns in data.get("nameservers", []) if ns.get("ldhName")
        ],
    }


async def _immediate(value: dict) -> dict:
    """Return a value from an awaitable without sleeping."""
    return value


async def _safe_browsing(url: str, client: httpx.AsyncClient) -> dict:
    key = settings.google_safe_browsing_api_key
    if not key:
        return {"enabled": False, "note": "No GOOGLE_SAFE_BROWSING_API_KEY configured."}
    payload = {
        "client": {"clientId": "tayo-demo", "clientVersion": "0.1.0"},
        "threatInfo": {
            "threatTypes": [
                "MALWARE",
                "SOCIAL_ENGINEERING",
                "UNWANTED_SOFTWARE",
                "POTENTIALLY_HARMFUL_APPLICATION",
            ],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}],
        },
    }
    try:
        response = await client.post(
            "https://safebrowsing.googleapis.com/v4/threatMatches:find",
            params={"key": key},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        matches = data.get("matches", [])
        return {
            "enabled": True,
            "matched": bool(matches),
            "matches": [
                {
                    "threatType": item.get("threatType"),
                    "platformType": item.get("platformType"),
                }
                for item in matches
            ],
        }
    except Exception as exc:
        return {"enabled": True, "error": str(exc)}


async def validate_virustotal_key(api_key: str) -> dict:
    """Validate a user-supplied key without returning or persisting it."""
    key = api_key.strip()
    if len(key) < 16 or len(key) > 256 or any(char.isspace() for char in key):
        return {"valid": False, "message": "That does not look like a VirusTotal API key."}
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.get(
                "https://www.virustotal.com/api/v3/metadata",
                headers={"accept": "application/json", "x-apikey": key},
            )
        if response.status_code in {401, 403}:
            return {"valid": False, "message": "VirusTotal rejected this API key."}
        if response.status_code == 429:
            return {
                "valid": True,
                "message": "Key accepted, but its VirusTotal request quota is currently exhausted.",
                "quota_exhausted": True,
            }
        response.raise_for_status()
        return {"valid": True, "message": "VirusTotal connected successfully."}
    except httpx.HTTPError:
        return {
            "valid": False,
            "message": "Could not verify the key with VirusTotal. Check your connection and try again.",
        }


async def _virustotal(
    url: str,
    client: httpx.AsyncClient,
    api_key: str | None = None,
) -> dict:
    key = (api_key or settings.virustotal_api_key).strip()
    if not key:
        return {"enabled": False, "note": "No VIRUSTOTAL_API_KEY configured."}

    headers = {"accept": "application/json", "x-apikey": key}
    submitted = False
    try:
        # POSTing starts a fresh analysis and returns its ID. VirusTotal warns
        # that submitted URLs enter its community-visible dataset, which is why
        # the UI and documentation explicitly call this out.
        response = await client.post(
            "https://www.virustotal.com/api/v3/urls",
            headers=headers,
            data={"url": url},
        )
        response.raise_for_status()
        submitted = True
        analysis_id = response.json().get("data", {}).get("id")
        if not analysis_id:
            return {
                "enabled": True,
                "submitted": True,
                "status": "unknown",
                "error": "VirusTotal accepted the URL but returned no analysis ID.",
            }

        # A public key is commonly limited to four requests per minute. Keep
        # this to the submission plus at most two status requests, leaving room
        # for the one-time key validation during guided setup.
        attributes: dict[str, Any] = {}
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(2)
            analysis = await client.get(
                f"https://www.virustotal.com/api/v3/analyses/{analysis_id}",
                headers=headers,
            )
            analysis.raise_for_status()
            attributes = analysis.json().get("data", {}).get("attributes", {})
            if attributes.get("status") == "completed":
                break

        status = attributes.get("status", "queued")
        stats = attributes.get("stats", {})
        results = attributes.get("results", {})
        detections = [
            {
                "engine": value.get("engine_name") or name,
                "category": value.get("category"),
                "result": value.get("result"),
            }
            for name, value in results.items()
            if isinstance(value, dict)
            and value.get("category") in {"malicious", "suspicious"}
        ]
        result = {
            "enabled": True,
            "submitted": True,
            "found": status == "completed",
            "status": status,
            "analysis_id": analysis_id,
            "stats": stats,
            "detections": detections,
        }
        if status != "completed":
            result["note"] = (
                "VirusTotal accepted the URL, but its analysis is still queued. "
                "Scan the link again shortly to retrieve a new result."
            )
        return result
    except Exception as exc:
        return {"enabled": True, "submitted": submitted, "error": str(exc)}


async def _urlhaus(url: str, client: httpx.AsyncClient) -> dict:
    key = settings.urlhaus_auth_key
    if not key:
        return {"enabled": False, "note": "No URLHAUS_AUTH_KEY configured."}
    try:
        response = await client.post(
            "https://urlhaus-api.abuse.ch/v1/url/",
            data={"url": url},
            headers={"Auth-Key": key},
        )
        response.raise_for_status()
        data = response.json()
        return {
            "enabled": True,
            "query_status": data.get("query_status"),
            "threat": data.get("threat"),
            "tags": data.get("tags"),
            "urlhaus_reference": data.get("urlhaus_reference"),
        }
    except Exception as exc:
        return {"enabled": True, "error": str(exc)}


def _risk_level(score: int, dns_ok: bool, tls_connected: bool) -> str:
    if score >= 60:
        return "High risk"
    if score >= 30:
        return "Medium risk"
    if not dns_ok and not tls_connected:
        return "Unknown"
    return "Low risk"


async def scan_url(raw_url: str, virustotal_api_key: str | None = None) -> dict:
    parsed = parse_http_url(raw_url)
    host = (parsed.hostname or "").lower()

    # An unresolved domain is a useful signal (NXDOMAIN / dead link), not an
    # error. Only reject if it resolves to a private, loopback, or reserved IP.
    resolution_error: str | None = None
    try:
        resolve_public_host(host, parsed.port or 443)
    except UnsafeUrlError as exc:
        if str(exc).startswith("DNS resolution failed"):
            resolution_error = str(exc)
        else:
            raise

    signals, host_no_www = _offline_signals(parsed)

    dns_task = _dns_checks(host)
    tls_task = (
        asyncio.to_thread(_inspect_tls, host, parsed.port or 443)
        if parsed.scheme == "https" and not resolution_error
        else _immediate(
            {
                "connected": False,
                "valid": None,
                "error": (
                    "Domain did not resolve; no TLS handshake performed."
                    if resolution_error
                    else "URL is HTTP; no TLS handshake performed."
                ),
            }
        )
    )
    is_ip = False
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        pass
    domain = host_no_www
    rdap_task = (
        _immediate({"found": False, "note": "RDAP skipped for IP literal."})
        if is_ip
        else _rdap(domain)
    )

    dns_data, tls_data, rdap_data = await asyncio.gather(
        dns_task, tls_task, rdap_task
    )

    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        threat_data = await asyncio.gather(
            _safe_browsing(str(parsed.geturl()), client),
            _virustotal(str(parsed.geturl()), client, virustotal_api_key),
            _urlhaus(str(parsed.geturl()), client),
        )
    safe_browsing, virustotal, urlhaus = threat_data

    score = sum(signal["weight"] for signal in signals if signal["triggered"])
    risk_reasons = [
        s["detail"] for s in signals if s["triggered"] and s["weight"] > 0
    ]

    dns_ok = dns_data.get("status") == 0 and bool(
        dns_data.get("answers", {}).get("A")
        or dns_data.get("answers", {}).get("AAAA")
    )
    if dns_data.get("status") == 3:
        score += 35
        risk_reasons.append("DNS returned NXDOMAIN (the domain does not exist).")
    elif not dns_ok:
        score += 20
        risk_reasons.append("No A/AAAA DNS answer was returned.")

    tls_connected = bool(tls_data.get("connected"))
    if parsed.scheme == "https" and not tls_connected and not resolution_error:
        score += 25
        risk_reasons.append("TLS handshake failed for an HTTPS URL.")
    elif tls_data.get("valid") is False:
        score += 30
        risk_reasons.append("The TLS certificate did not validate.")

    age_days = rdap_data.get("age_days")
    if isinstance(age_days, int):
        if age_days < 30:
            score += 25
            risk_reasons.append(f"The domain is only {age_days} days old.")
        elif age_days < 90:
            score += 12
            risk_reasons.append(f"The domain is {age_days} days old.")

    if safe_browsing.get("matched"):
        score += 60
        risk_reasons.append("Google Safe Browsing reported a threat match.")
    stats = virustotal.get("stats") or {}
    malicious = int(stats.get("malicious", 0) or 0)
    suspicious = int(stats.get("suspicious", 0) or 0)
    if malicious:
        score += 60
        risk_reasons.append(f"VirusTotal reports {malicious} malicious engine(s).")
    elif suspicious >= 2:
        score += 20
        risk_reasons.append(f"VirusTotal reports {suspicious} suspicious engine(s).")
    if urlhaus.get("query_status") == "is_listed":
        score += 60
        risk_reasons.append("URLhaus lists this URL as a malware/phishing URL.")

    score = min(100, score)
    risk_level = _risk_level(score, dns_ok, tls_connected)

    unique_reasons = list(dict.fromkeys(risk_reasons))
    if unique_reasons:
        summary = (
            f"Risk level: {risk_level} ({score}/100). Notable indicators: "
            + "; ".join(unique_reasons[:5])
            + ". This is not proof of phishing; verify with a sandbox and trusted threat intelligence."
        )
    else:
        summary = (
            f"Risk level: {risk_level} ({score}/100). No high-signal indicators were found "
            "by these passive checks. A low score does not guarantee that a site is legitimate."
        )

    return {
        "kind": "url",
        "url": str(parsed.geturl()),
        "host": host,
        "registered_domain": domain,
        "risk_score": score,
        "risk_level": risk_level,
        "summary": summary,
        "reasons": unique_reasons,
        "checks": {
            "offline": signals,
            "dns": dns_data,
            "tls": tls_data,
            "rdap": rdap_data,
            "threat_intel": {
                "safe_browsing": safe_browsing,
                "virustotal": virustotal,
                "urlhaus": urlhaus,
            },
        },
        "limitations": URL_LIMITATIONS,
        "recommended_action": (
            "Do not enter credentials or download files. If you need to see the page, open it "
            "in a disposable sandbox or submit it to urlscan.io. Treat DNSSEC and TLS as "
            "hygiene signals, not proof of legitimacy."
        ),
    }
