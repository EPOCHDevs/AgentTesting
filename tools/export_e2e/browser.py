"""Drives the Terminal in headless Chrome: the study page's Export menu, then the print route.

Sign-in: the browser gets the cached login's ID and access tokens in localStorage
under the keys aws-amplify v6 reads (`CognitoIdentityServiceProvider.<client>.…`),
through Playwright's `storage_state`, before the app loads. The refresh token is
never given to the browser, so it can neither rotate nor revoke the cached login;
each fixture starts a new context with tokens valid for longer than the Terminal's
15-minute refresh margin plus the fixture's run.

Everything the page shows is measured into one JSON-serializable observation;
`checks.evaluate` judges it, so a recorded observation can be re-checked offline.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import jwt
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from tools import local_auth
from tools.agent_testing import Stack

# The Terminal refreshes its ID token 15 minutes before expiry; without a refresh
# token that would sign the browser out, so every context starts with at least this.
MIN_TOKEN_SECONDS = 35 * 60
# The printable width of a portrait page with 15 mm margins, in CSS pixels.
PAGE_CONTENT_WIDTH = {"A4": 680, "Letter": 703}
# Starting an assistant run is a POST to a thread's runs (or a stateless run).
AI_RUN = re.compile(r"/runs(/stream|/wait|/batch|/crons)?/?$")

# The page function; its leading comment lines are dropped so Playwright sees a function.
DOM_SCRIPT = "\n".join(
    line
    for line in Path(__file__).with_name("dom.js").read_text().splitlines()
    if not line.startswith("//")
)


def storage_state(stack: Stack, ui_url: str | None = None) -> dict[str, object]:
    """localStorage for the Terminal's origin (or a local override), as Amplify stores a signed-in session."""
    if not stack.user_pool_client_id:
        raise ValueError(f"The {stack.environment} profile names no user_pool_client_id.")
    tokens = local_auth.cached_tokens(
        stack.login_path(),
        login_command=f"python -m tools.agent_testing --env {stack.environment} login",
        min_validity=MIN_TOKEN_SECONDS,
    )
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    if claims.get("client_id") != stack.user_pool_client_id:
        raise ValueError("The cached login was issued to another app client than the Terminal's.")
    user = claims["username"]
    prefix = f"CognitoIdentityServiceProvider.{stack.user_pool_client_id}"
    items = {
        f"{prefix}.LastAuthUser": user,
        f"{prefix}.{user}.accessToken": tokens["access_token"],
        f"{prefix}.{user}.idToken": tokens["id_token"],
        f"{prefix}.{user}.clockDrift": "0",
    }
    target = urlsplit(ui_url or stack.ui_url)
    origin = f"{target.scheme}://{target.netloc}"
    return {
        "cookies": [],
        "origins": [
            {"origin": origin, "localStorage": [{"name": k, "value": v} for k, v in items.items()]}
        ],
    }


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def strip_images(export: Mapping[str, object]) -> dict[str, object]:
    """The export JSON with each chart's PNG replaced by its hash and size."""
    data = json.loads(json.dumps(export))
    for section in data.get("sections", []):
        for block in section.get("blocks", []):
            image = block.get("image") if block.get("kind") == "chart" else None
            if isinstance(image, str):
                block["image"] = {"sha256": sha256(image), "length": len(image), "prefix": image[:22]}
    return data


def pdf_pages(data: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", data))


def pdf_media_box(data: bytes) -> list[float] | None:
    found = re.search(rb"/MediaBox\s*\[\s*([\d.\s-]+)\]", data)
    return [float(x) for x in found.group(1).split()] if found else None


class Recorder:
    """Console errors, failed requests and assistant run starts for one page."""

    def __init__(self, page: Page, stack: Stack, ai_runs: list[dict[str, str]]):
        self.console: list[str] = []
        self.failed: list[dict[str, object]] = []
        ai_host = urlsplit(stack.ai_url).netloc
        page.on(
            "console",
            lambda message: self.console.append(message.text[:500])
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: self.console.append(f"pageerror: {str(error)[:500]}"))

        def failed(request) -> None:  # noqa: ANN001
            self.failed.append(
                {"method": request.method, "url": request.url.split("?")[0], "error": request.failure}
            )

        def response(resp) -> None:  # noqa: ANN001
            if resp.status >= 400:
                self.failed.append(
                    {"method": resp.request.method, "url": resp.url.split("?")[0], "status": resp.status}
                )

        def request(req) -> None:  # noqa: ANN001
            parts = urlsplit(req.url)
            if parts.netloc == ai_host and req.method == "POST" and AI_RUN.search(parts.path):
                ai_runs.append({"method": req.method, "url": req.url.split("?")[0]})

        page.on("requestfailed", failed)
        page.on("response", response)
        page.on("request", request)


class Terminal:
    def __init__(
        self, stack: Stack, headed: bool = False, ready_timeout: float = 180, ui_url: str | None = None
    ):
        self.stack = stack
        # The Terminal to drive; Backend reads and the sign-in stay the stack's.
        self.ui_url = (ui_url or stack.ui_url).rstrip("/")
        self.ready_timeout = ready_timeout
        self._playwright = sync_playwright().start()
        # The installed Google Chrome; the suite never downloads a browser.
        self.browser: Browser = self._playwright.chromium.launch(channel="chrome", headless=not headed)

    def close(self) -> None:
        self.browser.close()
        self._playwright.stop()

    def version(self) -> str:
        return self.browser.version

    def context(self) -> BrowserContext:
        return self.browser.new_context(
            storage_state=storage_state(self.stack, self.ui_url),  # type: ignore[arg-type]
            viewport={"width": 1440, "height": 900},
            timezone_id="UTC",
            locale="en-US",
        )

    def observe(
        self, run_id: str, study_id: str, out: Path, formats: list[Mapping[str, object]]
    ) -> dict[str, object]:
        """Opens the study and its print route, measures both, and writes the evidence files."""
        out.mkdir(parents=True, exist_ok=True)
        observation: dict[str, object] = {"run_id": run_id, "study_id": study_id, "ai_runs": []}
        ai_runs: list[dict[str, str]] = observation["ai_runs"]  # type: ignore[assignment]
        context = self.context()
        try:
            study = context.new_page()
            study_log = Recorder(study, self.stack, ai_runs)
            observation["menu"] = self._menu(study, run_id, study_id, out)
            observation["study_page"] = {"console": study_log.console, "failed_requests": study_log.failed}
            href = observation["menu"].get("print_href") or f"/print/studies/{study_id}?run={run_id}"  # type: ignore[union-attr]
            printed = context.new_page()
            print_log = Recorder(printed, self.stack, ai_runs)
            observation["print"] = self._print(printed, str(href), out)
            if observation["print"].get("ready") == "true":  # type: ignore[union-attr]
                observation.update(self._measure(printed, out))
            observation["print_page"] = {"console": print_log.console, "failed_requests": print_log.failed}
            study.close()
            printed.close()
        finally:
            context.close()
        observation["formats"] = {
            str(f["id"]): {"offered": any(
                item.get("label") == f["menu_label"] for item in observation["menu"].get("items", [])  # type: ignore[union-attr]
            )}
            for f in formats
        }
        return observation

    def _menu(self, page: Page, run_id: str, study_id: str, out: Path) -> dict[str, object]:
        url = f"{self.ui_url}/studies/{study_id}?run={run_id}"
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        try:  # Let the dashboard draw, so the menu screenshot shows the study.
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            pass
        if urlsplit(page.url).path.startswith("/login"):
            page.screenshot(path=str(out / "study.png"))
            return {
                "found": False,
                "error": f"{self.ui_url} showed its sign-in page: it did not accept the seeded "
                f"{self.stack.environment} login (is it built for user pool client {self.stack.user_pool_client_id}?).",
            }
        # The working menu is a Radix trigger; the placeholder for an unfinished run isn't.
        trigger = page.locator('button[aria-haspopup="menu"]', has_text="Export")
        try:
            trigger.wait_for(state="visible", timeout=120_000)
        except PlaywrightTimeout:
            page.screenshot(path=str(out / "study.png"))
            return {"found": False, "error": "The study page showed no Export menu within 120s."}
        trigger.click()
        menu = page.get_by_role("menu")
        menu.wait_for(state="visible", timeout=15_000)
        details = menu.evaluate(
            """menu => ({
              label: [...menu.children].find(e => !e.matches('[role=menuitem], [role=separator], p'))?.textContent ?? null,
              items: [...menu.querySelectorAll('[role=menuitem]')].map(item => {
                // Visible lines, without the screen-reader-only "(opens in a new tab)".
                const hidden = [...item.querySelectorAll('.sr-only')].map(s => s.textContent.trim());
                const texts = item.innerText.split('\\n').map(t => t.trim()).filter(t => t && !hidden.includes(t));
                return {label: texts[0] ?? null, description: texts[1] ?? null,
                        href: item.getAttribute('href'), target: item.getAttribute('target'),
                        rel: item.getAttribute('rel'), screen_reader: item.querySelector('.sr-only')?.textContent ?? null};
              }),
              note: [...menu.querySelectorAll('p')].map(p => p.textContent).join(' ') || null,
            })"""
        )
        page.screenshot(path=str(out / "menu.png"))
        page.keyboard.press("Escape")
        pdf = next((i for i in details["items"] if i["label"] == "PDF"), None)
        return {"found": True, "url": url, **details, "print_href": pdf["href"] if pdf else None}

    def _print(self, page: Page, href: str, out: Path) -> dict[str, object]:
        url = self.ui_url + href if href.startswith("/") else href
        started = time.monotonic()
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        result: dict[str, object] = {"url": url}
        try:
            page.wait_for_function(
                "document.body && document.body.dataset.exportReady",
                timeout=self.ready_timeout * 1000,
                polling=250,
            )
            result["ready"] = page.evaluate("document.body.dataset.exportReady")
        except PlaywrightTimeout:
            result["ready"] = None
        result["elapsed_s"] = round(time.monotonic() - started, 1)
        if result["ready"] != "true":
            result["message"] = page.evaluate("document.body.innerText.slice(0, 1000)")
            page.screenshot(path=str(out / "print-page.png"))
        return result

    def _measure(self, page: Page, out: Path) -> dict[str, object]:
        raw = page.locator("#epoch-export-data").text_content() or ""
        export = json.loads(raw)
        found: dict[str, object] = {
            "document_title": page.title(),
            "export": strip_images(export),
        }
        found["dom"] = page.evaluate(DOM_SCRIPT, "describe")
        found["images"] = page.evaluate(DOM_SCRIPT, "imageStats")
        page.screenshot(path=str(out / "print-screen.png"))
        overflow = {}
        page.emulate_media(media="print")
        for paper, width in PAGE_CONTENT_WIDTH.items():
            page.set_viewport_size({"width": width, "height": 1000})
            page.wait_for_timeout(300)
            overflow[paper] = page.evaluate(DOM_SCRIPT, "tableOverflow")
        page.emulate_media(media="screen")
        page.set_viewport_size({"width": 1440, "height": 900})
        found["overflow"] = overflow
        pdfs = {}
        for paper, css_size in (("A4", True), ("Letter", False)):
            path = out / f"export-{paper.lower()}.pdf"
            page.pdf(path=str(path), format=paper, prefer_css_page_size=css_size)
            data = path.read_bytes()
            pdfs[paper] = {
                "path": path.name,
                "bytes": len(data),
                "pages": pdf_pages(data),
                "media_box": pdf_media_box(data),
                "prefer_css_page_size": css_size,
            }
        found["pdf"] = pdfs
        found["page1"] = render_page1(out / "export-a4.pdf", out / "page-1.png", page)
        (out / "export-data.json").write_text(json.dumps(found["export"], indent=1))
        return found


def render_page1(pdf: Path, png: Path, page: Page) -> dict[str, object]:
    """Page 1 of the A4 PDF as an image (poppler), else the print layout's top."""
    if shutil.which("pdftoppm"):
        subprocess.run(
            ["pdftoppm", "-png", "-r", "80", "-f", "1", "-l", "1", "-singlefile", str(pdf), str(png.with_suffix(""))],
            check=True,
            capture_output=True,
            timeout=120,
        )
        return {"path": png.name, "method": "pdftoppm page 1 of the A4 PDF"}
    page.emulate_media(media="print")
    page.set_viewport_size({"width": 794, "height": 1123})
    page.screenshot(path=str(png))
    page.emulate_media(media="screen")
    return {"path": png.name, "method": "print-media screenshot (pdftoppm unavailable)"}
