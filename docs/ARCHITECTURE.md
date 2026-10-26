# Architecture, security, and design notes

TAYO is a thin browser UI over a local analysis service. This document
explains the split, the request flows, the trust model, and what would need to
change for production.

---

## 1. Goals and non-goals

### Goals

- **Local-first:** no account, no cloud inference, no telemetry; media stays on
  the machine.
- **Journalist-friendly:** every conclusion carries evidence links, uncertainty,
  and a recommended next step.
- **Honest:** classifiers are described as signals, not proof. Missing evidence
  produces “Not Sure”.
- **Safe URL triage:** inspect a suspicious link without opening it in the
  user's browser and without fetching the page body.
- **Runnable demo:** CPU-only path, clear setup, no build step for the extension.

### Non-goals

- Forensic verification or a definitive “fake” verdict.
- Unrestricted web search or a general claim-verification oracle.
- Detecting every generator, deepfake, or manipulation.
- Multi-user hosting, authentication, or a hardened public API.
- Chrome Web Store publication (the project documents the path, but the demo is
  loaded unpacked).

---

## 2. Component split

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Browser (Chrome/Edge/Brave)                                              │
│                                                                          │
│  extension/background.js  (MV3 service worker)                           │
│    • creates context menus                                               │
│    • stores a pending request in chrome.storage.session                  │
│    • opens the side panel                                                │
│                                                                          │
│  extension/panel.html + panel.js + panel.css                             │
│    • five tools, forms, result rendering                                 │
│    • fetch() to http://127.0.0.1:8000                                    │
│    • no page DOM access, no remote scripts, no ML                        │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │ JSON over loopback HTTP
┌──────────────────────────────────▼───────────────────────────────────────┐
│ FastAPI backend (backend/app)                                            │
│                                                                          │
│  main.py            routes, CORS, upload limits, temp cleanup            │
│  config.py          env-driven settings                                  │
│  schemas.py         request validation                                   │
│                                                                          │
│  services/factcheck.py       corpus + Google Fact Check + Ollama         │
│  services/text_analysis.py   ModernBERT + stylometry                     │
│  services/image_analysis.py  metadata + Smaug + URL fetch                │
│  services/video_analysis.py  ffmpeg frames + image model                 │
│  services/url_scan.py        offline heuristics + DNS/TLS/RDAP + intel   │
│  services/metadata.py        EXIF/XMP/C2PA hints, ffmpeg helpers         │
│  services/model_registry.py  lazy load, device, fallback, inference      │
│  services/smaug_image_detect.py  vendored Apache-2.0 Smaug wrapper       │
│  services/net_utils.py       URL parsing + SSRF guard                    │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                     ▼
       model cache            bundled ffmpeg      network lookups
       (Smaug; Ateeqq         (imageio-ffmpeg)    DNS/TLS/RDAP/intel
        fallback; ModernBERT)
```

### Why the extension is thin

MV3 service workers are event-driven and short-lived; they are not a reliable
home for a multi-hundred-megabyte PyTorch model or a long ffmpeg job. The side
panel is a normal extension page, but it is still a browser context with CSP,
memory, and store-policy constraints. Keeping models in a local process gives:

- warm model weights and predictable latency,
- access to ffmpeg and raw sockets,
- simple Python evaluation tooling,
- a single place for SSRF controls and API keys.

A pure-extension design would require tiny quantized ONNX models in
WASM/WebGPU and a hosted API for everything else. That is a valid future
direction, not this demo.

---

## 3. Request flows

### 3.1 Fact-check

```text
panel.js ──POST /api/factcheck {claim}──▶ factcheck_claim()
                                           │
                                           ├─ normalize + fuzzy match curated corpus
                                           │    └─ confidence = corpus confidence × match score
                                           ├─ optional Google Fact Check Tools API
                                           ├─ optional Ollama summary (evidence only)
                                           └─ fallback: "Not Sure" + search links
        ◀── {verdict, summary, evidence[], limitations[]} ──
```

- The corpus (`backend/app/data/factchecks.json`) contains aliases, keywords,
  verdict, explanation, and evidence with source/URL/snippet/relation.
- Matching combines RapidFuzz token-set similarity (45%), Jaccard token overlap
  (35%), and keyword coverage (20%). The threshold is 0.58 for English and
  0.75 for detected Mongolian. Cyrillic and informal galig are folded to a
  comparable Latin key; negated variants cannot inherit a corpus verdict.
- Google ratings are normalized: “false/misleading/hoax/…” → `False`;
  “true/correct/…” → `True`; “half/partly/mixed” or unknown → `Not Sure`.
- Ollama receives only the claim, verdict, and retrieved evidence. The prompt
  forbids adding facts. If it fails, the template summary remains.
- “Not Sure” always includes search links so a journalist can continue
  manually.

### 3.2 Text

```text
panel.js ──POST /api/analyze/text {text}──▶ threadpool ──▶ ModernBERT
                                                        + chunk aggregation
                                                        + stylometry
        ◀── {label, ai_probability, stats, signals, limitations} ──
```

- Text is split into ~1,200-word chunks (max 8) and scored independently.
  The reported probability is the mean over chunks.
- Temperature scaling from `detector-config.json` (1.566…) is applied.
- If the model is disabled or unavailable, a transparent heuristic runs and is
  labeled as weak.
- Stylometry (sentence-length variation, type–token ratio, trigram repetition,
  phrase hits) is displayed for explanation, not combined into a fake
  “certainty”.
- For Mongolian Cyrillic or recognized informal Latin galig, Unicode-aware
  statistics are returned without invoking the English ModernBERT detector.
  `ai_probability` is null because no Mongolian AI-text model has been
  validated for this build. Users can select `mn_latn` manually when informal
  spelling defeats automatic detection.

### 3.3 Image

```text
panel.js ──multipart file──▶ temp file ──▶ EXIF/XMP/C2PA hints
                                       └──▶ Smaug classifier
        ◀── {label, ai_probability, metadata, signals, sha256, limitations} ──

panel.js ──POST image URL──▶ SSRF checks ──▶ backend fetch (size/type caps)
                                          └──▶ same analysis
```

- Images are converted to RGB and passed to the configured model(s). The
  default is Smaug alone; if it cannot load, the registry automatically loads
  the smaller Ateeqq SigLIP model. Enabling the optional third model averages
  probabilities without calibration or weighting.
- Label bands: ≥0.80 “Likely AI”, ≥0.60 “Possibly AI”, ≤0.20 “Likely real”,
  ≤0.40 “Possibly real”, otherwise “Uncertain”. These are UI heuristics, not
  scientifically calibrated thresholds.
- Metadata checks are independent signals: generator names, camera make/model,
  EXIF presence, C2PA/JUMBF marker presence.
- **C2PA is presence-only.** The demo does not verify signatures or trust lists.
  A malicious file can contain the string “c2pa”. For real provenance, use the
  `c2pa-python` SDK, validate the manifest, and check the signer certificate.
- SHA-256 is computed for record-keeping and duplicate detection.

### 3.4 Video

```text
panel.js ──multipart file──▶ per-request temp dir
                              ├─ ffmpeg/ffprobe metadata
                              ├─ 8 evenly spaced frames
                              └─ image model per frame
        ◀── {mean/max probability, per-frame scores, metadata, limitations} ──
```

- This is frame sampling, not a video deepfake model. It can miss short
  manipulations, temporal artifacts, audio fakes, and lip-sync errors.
- Frames are deleted with the per-request temporary directory.
- A mean is reported alongside the max and the count above 80%, because one
  manipulated frame is easy to hide in an average.

### 3.5 URL scan

```text
panel.js ──POST /api/scan/url──▶ parse + resolve host (SSRF guard)
                                 ├─ offline heuristics (scheme, userinfo, IDN,
                                 │  IP literal, port, TLD, brand lookalike, …)
                                 ├─ DNS over HTTPS (A/AAAA/CNAME/MX/NS/TXT/DS)
                                 ├─ TLS handshake + certificate parse
                                 ├─ RDAP domain registration / age
                                 └─ optional Safe Browsing / VirusTotal / URLhaus
        ◀── {risk_score, risk_level, reasons, checks, limitations} ──
```

- **No page body is fetched, no JavaScript runs, no forms are submitted.**
- Risk weights are additive and capped at 100. Examples: threat-intel match +60,
  invalid TLS +30, domain <30 days +25, brand lookalike +28, raw IP +25.
- The score is an explainable triage heuristic, not a probability.
- DNS/TLS are hygiene signals. Phishing sites routinely use valid DV
  certificates and DNSSEC-enabled domains.

---

## 4. Backend modules

| Module | Responsibility |
|---|---|
| `main.py` | Routes, CORS, upload streaming/limits, temp cleanup, request logging. |
| `config.py` | Loads `backend/.env`; model IDs, device, keys, limits, CORS. |
| `schemas.py` | Pydantic request models and length constraints. |
| `services/model_registry.py` | Lazy image/text model loading, Smaug adapter and automatic fallback, label mapping, temperature scaling, singleton caches, status. |
| `services/smaug_image_detect.py` | Vendored Apache-2.0 wrapper for Smaug so no hub-downloaded Python executes at runtime. |
| `services/factcheck.py` | Corpus matching, Google API, rating normalization, Ollama summaries, no-match search links. |
| `services/text_analysis.py` | Chunking, ModernBERT inference, stylometry, labels, limitations. |
| `services/image_analysis.py` | Metadata + classifiers ensemble, labels/confidence, image URL fetch with SSRF checks. |
| `services/video_analysis.py` | Frame scoring, aggregation, video-specific uncertainty. |
| `services/url_scan.py` | Offline signals, DoH, TLS certificate inspection, RDAP, threat intel, scoring. |
| `services/metadata.py` | Pillow EXIF/info, C2PA/software markers, ffmpeg/ffprobe metadata, frame extraction. |
| `services/net_utils.py` | URL parsing and private/loopback/reserved address blocking. |

### Concurrency and caching

- FastAPI runs inference in a threadpool so the event loop stays responsive.
- Models are process-wide singletons protected by locks. The registry loads on
  first use, not at startup, so fact-checking and URL scanning work even if
  model download fails.
- There is no request queue. Concurrent image/video analyses compete for CPU
  and memory. The demo assumes a single journalist.
- Temp uploads use `tempfile` and are deleted in `finally` blocks; video frames
  live inside a `TemporaryDirectory`.

---

## 5. Extension design

### Manifest V3

- **Permissions:** `contextMenus`, `storage`, `sidePanel`.
- **Host permissions:** `http://127.0.0.1:8000/*` and
  `http://localhost:8000/*`.
- **No `scripting`, `tabs`, or `<all_urls>`.** The extension does not read page
  content. Text comes from `selectionText`; image/link URLs come from the
  context-menu payload.
- **Side panel** is declared statically and opened on toolbar click via
  `setPanelBehavior`.
- **Pending requests** use `chrome.storage.session`, so a request is not lost
  if the panel is not ready. Session storage is cleared when the browser
  closes and is not exposed to content scripts by default.

### Rendering safety

- All backend-provided text is rendered with `textContent` and DOM APIs.
- Evidence links are created as anchors only when the URL starts with
  `http://` or `https://`, with `rel="noreferrer noopener"`.
- There is no `innerHTML` with remote data, no `eval`, no remote script, and no
  third-party CDN.
- CSP is the extension default (MV3 disallows remote code). The panel does not
  need inline scripts.

### Settings

- Backend URL is stored in `chrome.storage.local`.
- The default localhost origins are authorized statically. Changing the origin
  requires a manifest edit; this is a deliberate MV3 constraint, not a bug.
- “Clear saved data” clears local and session storage and resets the URL.

---

## 6. Security and privacy

### Threat model (demo scope)

| Threat | Mitigation |
|---|---|
| Malicious page injects content into the panel | No content scripts; textContent rendering; no HTML injection. |
| SSRF via image URL / URL input | Reject non-HTTP(S), block private/loopback/link-local/reserved IPs, revalidate redirects, cap redirects, size, and content type. |
| DoS via huge upload | Streamed upload with `MAX_UPLOAD_MB` cap and 413 response. |
| Path traversal via filename | Server-generated temp names; only a short sanitized suffix is reused. |
| XSS via claim/evidence | Safe DOM APIs; links allow only http(s). |
| Extension fetches arbitrary origins | Static host permissions limited to localhost; backend URL must be allowed by manifest. |
| Secret leakage to extension | API keys live only in backend `.env`; `/api/config` exposes booleans, not values. |
| Model pickle risk | Prefer safetensors checkpoints; production should pin revisions and verify hashes. |

### Network behavior

| Action | Destination | When |
|---|---|---|
| Health/config | localhost | Panel open/retry. |
| Claim check | localhost; Google Fact Check only with key | User submits claim. |
| Text/image/video | localhost only | User submits media. |
| Image URL | Target public host (fetched by backend) | User explicitly submits an image URL. |
| URL scan | Google DoH (host), RDAP (domain), TLS target; threat intel only with keys | User submits URL. |
| Ollama summary | localhost Ollama | Fact-check with `OLLAMA_URL` set. |

No analytics, crash reporting, accounts, or cloud model calls are built in.
Hugging Face is contacted only to download model weights.

### SSRF details and residual risk

`resolve_public_host()` rejects hostnames ending in `.local`, `.internal`,
`.localhost`, `.home.arpa`, and any resolved address that is private, loopback,
link-local, multicast, reserved, unspecified, or not global. Redirects in
`fetch_image_from_url()` are followed manually, max 5, and each hop is
revalidated. The URL scanner itself never fetches the URL.

Residual risks:

- **DNS rebinding / TOCTOU:** resolution is checked first, then httpx resolves
  again when connecting. A production system must pin the validated IP in the
  transport (or use an egress proxy).
- **Proxy environment variables:** httpx may honor proxies; a production
  deployment should disable or control them.
- **IPv4-mapped IPv6 and unusual encodings:** Python's `ipaddress` covers the
  common cases, but fuzzing is warranted.
- **Redirects to internal services** are rechecked in the image fetcher; the
  URL scanner does not follow redirects at all.
- **Model/media parser vulnerabilities:** Pillow/ffmpeg run in-process. A
  hostile file could exploit a decoder. Production should sandbox media parsing
  in a separate, resource-limited worker/container.

### CORS

The backend defaults to `ALLOWED_ORIGINS=*` with credentials disabled, which is
appropriate only for a loopback demo. Extension pages send `Origin:
chrome-extension://<id>`. For a shared deployment, set an explicit allowlist and
add authentication.

### Data retention

- Uploads and extracted frames are deleted per request.
- The backend logs method, path, status, latency, and request ID — not claim
  text or media contents.
- Model cache and `chrome.storage` settings persist locally until deleted.
- No database. Fact-check corpus is a JSON file.

---

## 7. Production hardening checklist

If this moves beyond a local demo:

1. **Authentication and authorization.** API tokens or mTLS; per-user quotas;
   audit logs without sensitive content.
2. **Network isolation.** Run analysis workers in containers with restricted
   egress; use an egress proxy with DNS pinning; block link-local/metadata
   endpoints at the network layer too.
3. **Sandbox media decoders.** Separate worker with seccomp/AppArmor, memory and
   CPU limits, no network.
4. **Request queue and backpressure.** One model per worker; bounded queue;
   cancellation; per-request timeouts.
5. **Model supply chain.** Pin revisions, verify checksums, prefer safetensors,
   scan artifacts, record model versions in every result for reproducibility.
6. **Calibration and evaluation.** Build a licensed, diverse test set; report
   confidence intervals; calibrate thresholds per model; monitor drift. Never
   ship a threshold tuned on the same small set used to report accuracy.
7. **C2PA verification.** Use the official SDK and a trust list; distinguish
   “manifest present” from “signature valid and signer trusted”.
8. **Provenance and reverse image search.** Integrate approved services; keep
   human review in the loop.
9. **Evidence retrieval.** Add a trusted-source index with retrieval,
   entailment/stance classification, source-reliability metadata, and
   date-aware answers. Do not let an LLM invent evidence.
10. **Extension distribution.** Privacy policy, permission justifications,
    reproducible builds, CSP review, no remote code, store screenshots.
11. **Abuse and privacy.** Rate limits, retention policy, redaction, user
    consent for external API calls, and a clear “no accusation” policy.
12. **Accessibility and i18n.** Keyboard navigation, screen-reader labels,
    localization, right-to-left layouts.

---

## 8. Known limitations by design

- Curated fact-check corpus: two claims. “Not Sure” is common.
- No unrestricted web search or live news retrieval.
- Image/video classifiers are trained for specific data; they do not generalize
  to all generators or editing pipelines. Smaug is strong on current Google
  models but still missed 3/100 Nano Banana images (and 3/50 after stress
  recompression) in our evaluation.
- C2PA signatures are not verified.
- URL risk scoring is heuristic and can be gamed; absence of signals is not
  proof of safety.
- No audio analysis, no lip-sync detection, no temporal video model.
- CPU inference is fast enough for a demo but not for batch newsroom work.
- The extension is Chromium-only; Firefox MV3 sidebars differ and are untested.
