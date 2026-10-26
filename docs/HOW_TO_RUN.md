# How to run TAYO

This guide takes you from a clean checkout to a working side panel, then covers
configuration, a demo script, and troubleshooting.

---

## 1. Prerequisites

| Requirement | Why |
|---|---|
| Python **3.12** + [`uv`](https://docs.astral.sh/uv/) | Backend and reproducible virtualenv. |
| Chromium browser **116+** (Chrome, Edge, Brave) | MV3 side panel and `chrome.sidePanel.open()`. |
| ~4 GB free disk | venv, models (~1.2 GB), benchmark samples, caches. |
| 8 GB RAM minimum; 16 GB recommended | Smaug is a 439M-parameter model (~2 GB in float32) plus ModernBERT. Close other apps on 8 GB. |
| Optional: NVIDIA GPU + CUDA PyTorch | Faster image inference. CPU works. |
| Optional: Ollama | Local summary rewriting only. |

`ffmpeg` does **not** need to be installed system-wide. The backend uses the
binary bundled with `imageio-ffmpeg`. `ffprobe` is optional; if absent, video
metadata falls back to `ffmpeg -i` parsing.

### WSL2 notes

This project was tested under WSL2 with 7.7 GB RAM visible and an RTX 2060
(6 GB). CPU inference works. CUDA is optional but must be installed as a
**matching torch/torchvision pair** from the same PyTorch index. Do not mix the
CPU torch wheel with the CUDA torchvision wheel from PyPI; that causes
`operator torchvision::nms does not exist` at import time.

```bash
source .venv/bin/activate
# Check https://pytorch.org/get-started/locally/ for the command matching
# your driver, then verify both imports:
uv pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision
.venv/bin/python -c "import torch, torchvision; print(torch.__version__, torchvision.__version__, torch.cuda.is_available())"
```

On CPU, `scripts/setup_backend.sh` already installs the matched CPU pair.

Keep the project and model cache on the Linux filesystem (for example
`~/repos/...`), not under `/mnt/c`, to avoid slow model loading.

### Install `uv` if needed

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# restart the shell, or add ~/.local/bin to PATH
```

---

## 2. Backend setup

From the repository root:

```bash
./scripts/setup_backend.sh
```

The script:

1. Creates `.venv` with Python 3.12.
2. Installs the matched CPU PyTorch + torchvision pair.
3. Installs `backend/requirements.txt` (including `sentence-transformers`).
4. Copies `backend/.env.example` to `backend/.env` if it does not exist.

If `uv` is unavailable, the manual equivalent is:

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install --index-url https://download.pytorch.org/whl/cpu \
  torch==2.14.1 torchvision==0.29.1
uv pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

### Pre-download the models (recommended before a demo)

```bash
.venv/bin/python scripts/warmup_models.py
```

This downloads and loads ~1.2 GB:

- `abacusai/Smaug-AI-Image-Detector` (~870 MB)
- `rasbt/ai-text-detector-modernbert` (~299 MB)

The smaller Ateeqq fallback (~371 MB) is downloaded only if Smaug cannot load.
The cache lives in `backend/.model_cache/` by default. First requests otherwise
download lazily, which makes the UI look stuck for a few minutes.

To use a Hugging Face token (higher rate limits, gated models):

```bash
export HF_TOKEN=hf_...
```

Put it in your shell or `backend/.env`; `huggingface-hub` reads `HF_TOKEN`.

### Start the server

```bash
./scripts/run_backend.sh
```

Defaults:

- API: <http://127.0.0.1:8000>
- Swagger UI: <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/api/health>

Use a different port if 8000 is busy:

```bash
PORT=8010 ./scripts/run_backend.sh
```

If you change the port, update the extension settings **and** the
`host_permissions` entry in `extension/manifest.json` (see below).

For auto-reload during backend development:

```bash
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Verify the backend

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool

.venv/bin/python scripts/smoke_test.py
.venv/bin/python scripts/smoke_test.py --skip-models   # fast
```

The full smoke test downloads models on first run and exercises fact-checking,
URL scanning, SSRF blocking, text, image, and video endpoints.

---

## 3. Load the extension

1. Open `chrome://extensions` (Edge: `edge://extensions`; Brave:
   `brave://extensions`).
2. Enable **Developer mode** (top-right).
3. Click **Load unpacked**.
4. Select the `extension/` directory — **not** the repository root.
5. Pin the extension via the puzzle-piece menu.
6. Click the TAYO icon. A side panel opens on the right.

At the top of the panel, the status bar should read **Local server ready**.
If it says offline, start the backend and press **Retry**.

### Context-menu features

- **Selected text → Fact-check selected text**
- **Link → Scan link for phishing risk**
- **Image → Analyze image for AI generation**

The user gesture is required by Chrome. The service worker stores the request
in session storage and asks the side panel to open. If Chrome refuses to open
the panel automatically (version/enterprise policy), open it once from the
toolbar; the pending request appears and runs.

### Reloading after changes

| Change | Action |
|---|---|
| Extension HTML/CSS/JS/manifest | Click the reload icon on the extension card, close and reopen the side panel. |
| Backend Python | Restart the server (or use `--reload`). |
| `backend/.env` | Restart the server. |
| Backend URL/port | Settings gear in the panel + `host_permissions` in `manifest.json`, then reload. |

### Change the backend URL

Click the gear icon in the panel and enter e.g. `http://127.0.0.1:8010`.
This is stored in `chrome.storage.local`.

Because MV3 host permissions are static, you must also edit
`extension/manifest.json`:

```json
"host_permissions": [
  "http://127.0.0.1:8000/*",
  "http://localhost:8000/*",
  "http://127.0.0.1:8010/*"
]
```

Reload the extension. If you use a LAN IP or HTTPS host, add that origin and
tighten `ALLOWED_ORIGINS` on the backend. Do not expose the demo API to the
internet without authentication.

---

## 4. Optional integrations

### Google Fact Check Tools (broader claim coverage)

1. In Google Cloud Console, create/select a project.
2. Enable **Fact Check Tools API**.
3. Create an API key; restrict it to the Fact Check Tools API.
4. Put it in `backend/.env`:

```dotenv
GOOGLE_FACTCHECK_API_KEY=...
```

5. Restart the backend.

TAYO first checks the local curated corpus, then queries this API for
published fact-checks. Results are aggregated by publisher rating, always with
links. The API does not answer arbitrary questions; it returns existing
fact-checks.

### Ollama (optional local summary)

The fact-check summary is normally a deterministic template built from the
matched evidence. Ollama can rewrite it in 2–3 sentences **using only the
retrieved evidence**; it never supplies new sources.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:4b
```

`backend/.env`:

```dotenv
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b
```

Restart the backend. If Ollama is unavailable, the app silently falls back to
the template summary. On a 6 GB GPU, 3–4B quantized models are the practical
limit; see [`TRAINING_AND_MODELS.md`](TRAINING_AND_MODELS.md).

### Threat-intel keys (all optional)

```dotenv
GOOGLE_SAFE_BROWSING_API_KEY=...
VIRUSTOTAL_API_KEY=...
URLHAUS_AUTH_KEY=...
```

- **Safe Browsing:** sends the URL for a threat match. No key → check skipped.
- **VirusTotal:** looks up an existing report by URL ID. The demo does not
  auto-submit new URLs. No key → skipped.
- **URLhaus:** queries abuse.ch for known malware URLs. No key → skipped.

Without keys, the scanner still performs offline heuristics, DNS, TLS, and
RDAP checks.

---

## 5. A 10-minute demo script

1. **Start server** (`./scripts/run_backend.sh`) and confirm the green status
   dot in the panel.
2. **Claim tab:** enter `Vaccines cause autism.` → show **False**, the short
   explanation, and the WHO link. Enter `Дэлхий хавтгай.` and `Delhii havtgai`;
   both should show the same **False** claim with a NASA link.
3. **Claim tab:** enter a claim not in the corpus → show **Not Sure** and the
   trusted search links. Explain that “not sure” means “no evidence in this
   build”, not “50/50”.
4. **Text tab:** paste a paragraph full of “delve”, “tapestry”, “pivotal” →
   show the high AI-like score and the style statistics. Then paste a personal
   paragraph and discuss false positives. A Mongolian Cyrillic or galig
   paragraph shows script and style statistics, without an AI score.
5. **Image tab:** upload
   `benchmark/data/ai/ai_03_sdxl_astronaut.png` → show score, SHA-256, EXIF
   flags. Upload an AI image with a missing EXIF block and explain why that is
   not proof.
6. **Right-click an image** on a page → show the context-menu workflow.
7. **Link tab:** scan `https://example.com` → low risk. Explain DNS/DNSSEC, TLS,
   domain age, and that the page body was never fetched. Try a synthetic
   lookalike domain to show red flags; never open it.
8. **Settings:** point out the backend URL and feature status.
9. **Benchmark:** open `benchmark/results/modern_image_eval.md` and explain why
   Smaug is primary, why Ateeqq is only a fallback, and why a 0.5 threshold is
   kept instead of tuning on the same 300 images.

---

## 6. Troubleshooting

### “Local server offline”

- Is `./scripts/run_backend.sh` running? Check the terminal for a traceback.
- Open <http://127.0.0.1:8000/api/health> in the same browser.
- Did you change the port? Update the panel settings and `host_permissions`.
- Another process may own port 8000:

```bash
ss -ltnp | grep 8000
PORT=8010 ./scripts/run_backend.sh
```

- Check the browser devtools console for a CORS or `ERR_CONNECTION_REFUSED`
  error. The extension page console is available from
  `chrome://extensions` → TAYO → **Inspect views: side panel**.

### First analysis seems stuck

The first text/image request downloads roughly 1.2 GB. Run
`scripts/warmup_models.py` before the demo and watch the server terminal. The
panel request timeout is 10 minutes, but the UI may not show progress from the
download. Subsequent runs are faster (Smaug ~2 s per image on CPU, text ~300 ms
for a short paragraph).

### `torch` / `torchvision` install or import errors

Smaug needs torchvision. Use **matching CPU wheels** from the PyTorch index:

```bash
source .venv/bin/activate
uv pip install --index-url https://download.pytorch.org/whl/cpu \
  torch==2.14.1 torchvision==0.29.1
uv pip install -r backend/requirements.txt
.venv/bin/python -c "import torch, torchvision; print(torch.__version__, torchvision.__version__)"
```

Do not install `torchvision` from PyPI on a CPU-only machine. The PyPI wheel is
CUDA-built and raises `RuntimeError: operator torchvision::nms does not exist`
or `libcudart.so` import errors when paired with CPU torch.

If you see `Could not import module 'AutoImageProcessor'`, torchvision failed to
import; fix torchvision before debugging transformers.

### Out-of-memory / system freeze

- Smaug is the largest component (~2 GB RAM in float32). Close other apps.
- Keep the secondary image model disabled (`ENABLE_SECONDARY_IMAGE_MODEL=0`).
- Fall back to the smaller model explicitly:
  `IMAGE_MODEL_PRIMARY=Ateeqq/ai-vs-human-image-detector` and
  `IMAGE_MODEL_FALLBACK=`.
- Disable the text model (`ENABLE_TEXT_MODEL=0`) to use the heuristic only.
- Reduce `VIDEO_FRAMES` to 4.
- Close other browsers/containers. WSL2 memory limits are configurable in
  `%UserProfile%\.wslconfig` (`memory=10GB`, then `wsl --shutdown`).
- Prefer 512–1024 px images; huge EXIF blocks and 50 MP images are unnecessary.

### Video analysis returns 0 frames

- The file may be audio-only, corrupt, or use an unsupported codec.
- Check the server log for ffmpeg errors.
- The endpoint samples evenly spaced frames using duration; videos with broken
  duration metadata may fail.
- Convert first: `ffmpeg -i input.mov -c:v libx264 -pix_fmt yuv420p out.mp4`.

### Image URL fails but upload works

Hotlink protection, signed URLs, cookies, and `403` responses are common. The
backend has no browser session. Download the image and upload it, or use a
direct public image URL. Private/loopback hosts are intentionally blocked.

### Fact-check always says “Not Sure”

The bundled corpus contains only four demo claims. Add `GOOGLE_FACTCHECK_API_KEY`
for published fact-checks, or extend `backend/app/data/factchecks.json` with
new entries and evidence. “Not Sure” is the honest response for out-of-corpus
claims.

### Extension panel is blank after an edit

- Check the side-panel devtools console.
- Click the reload icon on the extension card; close and reopen the panel.
- Verify `manifest.json` is valid JSON and all referenced icon files exist.
- If you changed `host_permissions`, reload the extension and restart the
  browser if needed.

### CORS errors

For localhost development, `ALLOWED_ORIGINS=*` should work. If you restricted
it, include the extension origin (`chrome-extension://<id>`) or keep `*` for
local use. Restart the backend after changing `.env`.

### Side panel does not open from the toolbar

- Chrome/Edge 116+ is required for `sidePanel.open()`. Update the browser.
- Enterprise policies can disable side panels; open it from the side-panel menu
  instead.
- Verify the `sidePanel` permission is present and the extension reloaded.

---

## 7. Running the benchmark

Modern 300-image Google-focused evaluation:

```bash
.venv/bin/python benchmark/build_modern_eval.py
.venv/bin/python benchmark/evaluate_modern.py
```

This downloads ~80 MB of evaluation images and, on first use, the Smaug and
Ateeqq weights (~1.2 GB). Results are written to `benchmark/results/`. On CPU,
Smaug inference is roughly 2 s/image; the full run takes about 15 minutes.

Recompression stress check (150 images):

```bash
.venv/bin/python benchmark/build_stress_eval.py
.venv/bin/python benchmark/evaluate_modern.py \
  --data-root benchmark/data/modern_stress --limit 50 \
  --output-prefix stress_image_eval \
  --dataset-note "Recompressed stress subset: first 50 per source, shorter side 512 px, JPEG quality 60."
```

Quick 10-image integration smoke test:

```bash
.venv/bin/python benchmark/download_samples.py
.venv/bin/python benchmark/benchmark.py
```

The first run downloads the two older image models (~718 MB) unless they are
already cached. Use a licensed, diverse dataset for any real conclusion. Ten
samples produce extremely wide confidence intervals.

## 8. Security checklist before sharing

- Keep the backend bound to `127.0.0.1`.
- Do not enable `--host 0.0.0.0` without authentication and rate limits.
- Restrict `ALLOWED_ORIGINS`; set `*` only for local development.
- Remove API keys from `backend/.env` before sharing the folder.
- Do not commit `backend/.env`, `.model_cache/`, or uploaded media.
- Treat all outputs as leads requiring human verification.
