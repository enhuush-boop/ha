#!/usr/bin/env python3
"""End-to-end smoke test against a running TAYO backend.

Start the server first:
    ./scripts/run_backend.sh

Then:
    .venv/bin/python scripts/smoke_test.py
    .venv/bin/python scripts/smoke_test.py --skip-models   # fast checks only
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = "http://127.0.0.1:8000"


def ensure_sample_video() -> Path | None:
    """Create a tiny MP4 from a benchmark image so the video path is covered."""
    target = ROOT / "benchmark" / "data" / "sample.mp4"
    if target.exists() and target.stat().st_size > 0:
        return target
    images = sorted((ROOT / "benchmark" / "data" / "real").glob("*.jpg")) or sorted(
        (ROOT / "benchmark" / "data" / "ai").glob("*")
    )
    if not images:
        return None
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-i",
        str(images[0]),
        "-t",
        "3",
        "-r",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-y",
        str(target),
    ]
    try:
        subprocess.run(command, check=True, timeout=60, capture_output=True)
    except Exception:
        return None
    return target if target.exists() else None



class Checker:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.passed = 0
        self.failed = 0
        self.client = httpx.Client(timeout=600, follow_redirects=False)

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}  {detail}")

    def get(self, path: str, **kwargs):
        return self.client.get(f"{self.base_url}{path}", **kwargs)

    def post(self, path: str, **kwargs):
        return self.client.post(f"{self.base_url}{path}", **kwargs)

    def run(self, skip_models: bool) -> int:
        print(f"TAYO smoke test → {self.base_url}")

        print("\n[health]")
        try:
            response = self.get("/api/health", timeout=10)
            health = response.json()
            self.check("health returns 200", response.status_code == 200)
            self.check("health status ok", health.get("status") == "ok", str(health))
        except Exception as exc:
            print(f"  FATAL  backend unreachable: {exc}")
            print("\nStart it with ./scripts/run_backend.sh and try again.")
            return 1

        print("\n[fact-checking]")
        response = self.post("/api/factcheck", json={"claim": "Vaccines cause autism."})
        data = response.json()
        self.check("known claim returns 200", response.status_code == 200)
        self.check("known claim verdict is False", data.get("verdict") == "False", str(data)[:200])
        self.check("evidence links present", bool(data.get("evidence")), str(data)[:200])

        response = self.post(
            "/api/factcheck", json={"claim": "The moon is made of a very specific type of cheese."}
        )
        data = response.json()
        self.check("unknown claim returns 200", response.status_code == 200)
        self.check("unknown claim is Not Sure", data.get("verdict") == "Not Sure", str(data)[:200])

        for claim, language in (
            ("Дэлхий хавтгай.", "mn_cyrl"),
            ("Delhii havtgai", "mn_latn"),
        ):
            response = self.post("/api/factcheck", json={"claim": claim})
            data = response.json()
            self.check(
                f"Mongolian {language} claim matches sourced corpus",
                response.status_code == 200
                and data.get("language") == language
                and data.get("verdict") == "False"
                and bool(data.get("evidence")),
                str(data)[:200],
            )

        response = self.post(
            "/api/factcheck", json={"claim": "Вакцин аутизм үүсгэдэггүй."}
        )
        self.check(
            "negated Mongolian claim does not inherit False",
            response.status_code == 200 and response.json().get("verdict") == "Not Sure",
        )

        response = self.post(
            "/api/analyze/text",
            json={
                "text": "Ene bol mongol heleer bichsen uguulber yum. "
                "Delhii havtgai gesen medee shalgana."
            },
        )
        data = response.json()
        self.check(
            "Mongolian galig text abstains from AI score",
            response.status_code == 200
            and data.get("language") == "mn_latn"
            and data.get("ai_probability") is None
            and data.get("text_stats", {}).get("words", 0) > 5,
            str(data)[:200],
        )

        print("\n[URL scanning]")
        response = self.post("/api/scan/url", json={"url": "https://example.com"})
        data = response.json()
        self.check("example.com scan returns 200", response.status_code == 200)
        self.check("risk level present", bool(data.get("risk_level")), str(data)[:200])
        self.check("no page body fetched", "checks" in data)

        response = self.post(
            "/api/scan/url",
            json={"url": "https://tayo-nonexistent-domain.invalid/login"},
        )
        data = response.json()
        self.check("nonexistent domain returns a report", response.status_code == 200, str(data)[:200])
        self.check(
            "nonexistent domain is not low risk",
            data.get("risk_level") in {"Medium risk", "High risk"},
            str(data)[:200],
        )

        response = self.post("/api/scan/url", json={"url": "http://127.0.0.1:8000/"})
        self.check(
            "loopback target blocked (SSRF guard)",
            response.status_code == 400,
            f"got {response.status_code}",
        )

        response = self.post(
            "/api/analyze/image-url", json={"url": "http://169.254.169.254/latest/meta-data/"}
        )
        self.check(
            "cloud metadata target blocked",
            response.status_code == 400,
            f"got {response.status_code}",
        )

        if skip_models:
            print("\n[models skipped with --skip-models]")
        else:
            print("\n[text model — first run may download ~300 MB]")
            text = (
                "In today's fast-paced digital landscape, it is important to note that "
                "artificial intelligence plays a pivotal role in shaping communication. "
                "Furthermore, organizations must leverage these multifaceted tools to remain "
                "competitive. Moreover, this seamless integration offers a wide range of "
                "benefits. In conclusion, navigating this evolving tapestry requires innovation."
            )
            response = self.post("/api/analyze/text", json={"text": text})
            data = response.json()
            self.check("text analysis returns 200", response.status_code == 200, str(data)[:200])
            self.check("text probability is a number", isinstance(data.get("ai_probability"), (int, float)))
            self.check("text label present", bool(data.get("label")))

            print("\n[image model — first run may download ~870 MB (Smaug)]")
            sample = ROOT / "benchmark" / "data" / "ai" / "ai_01_flux_statue.jpg"
            if sample.exists():
                with sample.open("rb") as handle:
                    response = self.post(
                        "/api/analyze/image",
                        files={"file": (sample.name, handle, "image/jpeg")},
                    )
            else:
                response = self.post(
                    "/api/analyze/image-url",
                    json={"url": "https://picsum.photos/id/10/512/512"},
                )
            data = response.json()
            self.check("image analysis returns 200", response.status_code == 200, str(data)[:300])
            self.check("image probability is a number", isinstance(data.get("ai_probability"), (int, float)))
            self.check("metadata returned", isinstance(data.get("metadata"), dict))

            print("\n[synthetic video]")
            sample_video = ensure_sample_video()
            if sample_video:
                with sample_video.open("rb") as handle:
                    response = self.post(
                        "/api/analyze/video",
                        files={"file": (sample_video.name, handle, "video/mp4")},
                    )
                data = response.json()
                self.check("video analysis returns 200", response.status_code == 200, str(data)[:300])
                self.check("frames analyzed", (data.get("frames_analyzed") or 0) > 0)
            else:
                print("  SKIP  could not create benchmark/data/sample.mp4")

        print(f"\n{self.passed} passed, {self.failed} failed")
        self.client.close()
        return 1 if self.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument(
        "--skip-models",
        action="store_true",
        help="Test only fast endpoints (no model downloads or inference).",
    )
    args = parser.parse_args()
    return Checker(args.base_url).run(args.skip_models)


if __name__ == "__main__":
    sys.exit(main())
