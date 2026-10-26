# Run TAYO in Firefox

The source extension manifest targets Chromium's side panel. Build a Firefox
sidebar copy from the same source files:

```bash
.venv/bin/python scripts/build_firefox_extension.py \
  --backend-url http://127.0.0.1:8000
```

To use another local backend port, pass that URL instead. The build is written
to `build/firefox/` and includes the Firefox sidebar manifest plus the correct
local backend permission.

Load it temporarily for development:

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…**.
3. Select `build/firefox/manifest.json`.

Firefox lists the TAYO sidebar after loading. The temporary add-on is removed
when Firefox closes. For a persistent installation, package and sign the
Firefox build through Mozilla Add-ons.
