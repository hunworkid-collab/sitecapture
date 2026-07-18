"""Google 검색 페이지 제어와 본문 영역 탐지."""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Callable

from .cdp import CdpConnection
from .errors import (
    CaptureAreaNotFoundError,
    CdpError,
    CdpTimeoutError,
    SearchBoxNotFoundError,
    UserActionRequiredError,
)
from .models import CaptureRect, PageState


_STATE_SCRIPT = r"""
(() => {
  const href = String(location.href || "");
  const host = String(location.hostname || "").toLowerCase();
  const path = String(location.pathname || "").toLowerCase();
  const documentUrl = String(document.URL || "");
  const bodyText = String(document.body?.innerText || "").slice(0, 30000);

  const captcha =
    path.startsWith("/sorry") ||
    Boolean(document.querySelector(
      'iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"], #captcha-form, form[action*="sorry"], [name="captcha"]'
    ));

  const consent =
    host === "consent.google.com" ||
    host.endsWith(".consent.google.com") ||
    Boolean(document.querySelector('form[action*="consent.google"], form[action*="save"]')) &&
      /동의|consent|accept all|모두 수락/i.test(bodyText);

  const networkError =
    documentUrl.startsWith("chrome-error://") ||
    /사이트에 연결할 수 없음|인터넷에 연결되어 있지 않음|this site can.?t be reached|no internet/i.test(bodyText);

  const resultContainer =
    document.querySelector("#search") ||
    document.querySelector("#rso") ||
    document.querySelector("main") ||
    document.querySelector('[role="main"]');

  const resultHeadingCount = document.querySelectorAll("a h3").length;
  const hasSearchResults = Boolean(resultContainer) || resultHeadingCount > 0;
  const noResults =
    /검색결과가 없습니다|일치하는 검색결과가 없습니다|did not match any documents|no results found/i.test(bodyText);

  let state = "loading";
  if (captcha) state = "captcha_required";
  else if (consent) state = "consent_required";
  else if (networkError) state = "network_error";
  else if (hasSearchResults && noResults) state = "no_results";
  else if (hasSearchResults && (resultHeadingCount > 0 || path === "/search")) state = "search_results";
  else if (document.readyState === "complete" || document.readyState === "interactive") state = "unknown_layout";

  return {
    state,
    href,
    title: document.title,
    readyState: document.readyState,
    resultHeadingCount,
    hasResultContainer: hasSearchResults,
    bodySnippet: bodyText.slice(0, 500)
  };
})()
"""


_REGION_SCRIPT = r"""
(() => {
  const isVisible = (element) => {
    if (!element) {
      return false;
    }

    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Number(style.opacity || "1") > 0 &&
      rect.width >= 20 &&
      rect.height >= 20
    );
  };

  const pageWidth = () => Math.max(
    window.innerWidth || 0,
    document.documentElement?.clientWidth || 0,
    document.documentElement?.scrollWidth || 0,
    document.body?.scrollWidth || 0
  );

  const makeTopCapture = (bottom) => ({
    ok: true,
    selector: "search-results-with-search-box",
    x: 0,
    y: 0,
    width: pageWidth(),
    height: bottom
  });

  const makeSearchResultsCapture = (element) => {
    const rect = element.getBoundingClientRect();
    const searchBox = document.querySelector(
      'textarea[name="q"], input[name="q"], textarea[aria-label], input[aria-label]'
    );
    const searchBoxRect = searchBox?.getBoundingClientRect();
    const resultBottom = rect.bottom + window.scrollY;
    const searchBoxBottom = searchBoxRect
      ? searchBoxRect.bottom + window.scrollY
      : 0;
    return makeTopCapture(Math.max(resultBottom, searchBoxBottom));
  };

  const mainSelectors = [
    "#search",
    "#rso",
    "main",
    "[role='main']"
  ];

  for (const selector of mainSelectors) {
    const element = document.querySelector(selector);
    if (!isVisible(element)) {
      continue;
    }

    const rect = element.getBoundingClientRect();
    if (rect.width >= 300 && rect.height >= 60) {
      return makeSearchResultsCapture(element);
    }
  }

  const allHeadings = Array.from(
    document.querySelectorAll("a h3")
  ).filter(isVisible);

  if (allHeadings.length === 0) {
    return {
      ok: false,
      reason: "RESULT_HEADING_NOT_FOUND"
    };
  }

  const headingPositions = allHeadings.map((heading) => {
    const rect = heading.getBoundingClientRect();
    return {heading, left: rect.left};
  });

  const minimumLeft = Math.min(
    ...headingPositions.map((item) => item.left)
  );

  const headings = headingPositions
    .filter((item) => Math.abs(item.left - minimumLeft) <= 280)
    .map((item) => item.heading);

  const cards = headings.map((heading) => {
    let node = heading;
    let selected = heading;

    for (let depth = 0; node && depth < 7; depth += 1) {
      if (!isVisible(node)) {
        node = node.parentElement;
        continue;
      }

      const rect = node.getBoundingClientRect();
      const text = String(node.innerText || "").trim();
      if (
        rect.width >= 320 &&
        rect.width <= 1200 &&
        rect.height >= 40 &&
        rect.height <= 700 &&
        text.length >= 5
      ) {
        selected = node;
      }
      node = node.parentElement;
    }

    return selected;
  });

  const uniqueCards = Array.from(new Set(cards));
  const rects = uniqueCards
    .filter(isVisible)
    .map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left + window.scrollX,
        top: rect.top + window.scrollY,
        right: rect.right + window.scrollX,
        bottom: rect.bottom + window.scrollY
      };
    });

  if (rects.length === 0) {
    return {
      ok: false,
      reason: "RESULT_CARD_NOT_FOUND"
    };
  }

  let left = Math.min(...rects.map((rect) => rect.left));
  let top = Math.min(...rects.map((rect) => rect.top));
  let right = Math.max(...rects.map((rect) => rect.right));
  let bottom = Math.max(...rects.map((rect) => rect.bottom));
  const padding = 12;

  const documentWidth = Math.max(
    document.documentElement.scrollWidth,
    document.body ? document.body.scrollWidth : 0
  );
  const documentHeight = Math.max(
    document.documentElement.scrollHeight,
    document.body ? document.body.scrollHeight : 0
  );

  left = Math.max(0, left - padding);
  top = Math.max(0, top - padding);
  right = Math.min(documentWidth, right + padding);
  bottom = Math.min(documentHeight, bottom + padding);

  const width = right - left;
  const height = bottom - top;
  if (width < 300 || height < 60) {
    return {
      ok: false,
      reason: "FALLBACK_REGION_TOO_SMALL",
      width,
      height
    };
  }

  return makeTopCapture(bottom);
})()
"""


_SEARCH_BOX_PREPARE_SCRIPT = r"""
(() => {
  const selectors = [
    'textarea[name="q"]',
    'input[name="q"]',
    'textarea[aria-label]',
    'input[aria-label]'
  ];

  const isVisible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" &&
      style.visibility !== "hidden" &&
      rect.width > 100 &&
      rect.height > 10;
  };

  for (const selector of selectors) {
    const element = document.querySelector(selector);
    if (!isVisible(element)) continue;

    const prototype = element.tagName === "TEXTAREA"
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (setter) setter.call(element, "");
    else element.value = "";

    element.dispatchEvent(new Event("input", {bubbles: true}));
    element.dispatchEvent(new Event("change", {bubbles: true}));
    element.focus();
    element.click();

    return {ok: true, selector, tagName: element.tagName};
  }
  return {ok: false};
})()
"""


class GoogleSearchPage:
    def __init__(
        self,
        cdp: CdpConnection,
        *,
        viewport_width: int,
        viewport_height: int,
        timeout_seconds: float,
        stabilization_interval_seconds: float,
        stabilization_required_count: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        self.cdp = cdp
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.timeout_seconds = timeout_seconds
        self.stabilization_interval_seconds = stabilization_interval_seconds
        self.stabilization_required_count = stabilization_required_count
        self._checkpoint_callback = checkpoint

    def _check_control(self) -> None:
        if self._checkpoint_callback is not None:
            self._checkpoint_callback()

    def initialize(self) -> None:
        for method in ("Page.enable", "Runtime.enable", "DOM.enable", "Network.enable"):
            self._check_control()
            self.cdp.call(method)
        self._check_control()
        self.cdp.call("Page.setLifecycleEventsEnabled", {"enabled": True})
        self._check_control()
        self.cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": self.viewport_width,
                "height": self.viewport_height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )

    def navigate(self, url: str) -> None:
        self._check_control()
        result = self.cdp.call("Page.navigate", {"url": url}, timeout=self.timeout_seconds)
        if result.get("errorText"):
            raise CdpError(
                f"페이지 이동 실패: {result['errorText']}", method="Page.navigate"
            )
        self.wait_document_ready()

    def open_google_home(self) -> None:
        self.navigate("https://www.google.com/?hl=ko")

    def wait_document_ready(self) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            self._check_control()
            try:
                ready = self.cdp.evaluate("document.readyState")
                if ready in {"interactive", "complete"}:
                    return
            except CdpError as exc:
                # 탐색 중 execution context가 교체되는 순간은 정상적으로 발생할 수 있다.
                last_error = exc
            time.sleep(0.15)
        raise CdpTimeoutError(
            f"문서 준비 상태 대기 시간 초과: {last_error}", method="Runtime.evaluate"
        )

    def inspect_state(self) -> dict[str, Any]:
        raw = self.cdp.evaluate(_STATE_SCRIPT)
        if not isinstance(raw, dict):
            return {"state": PageState.UNKNOWN_LAYOUT.value, "href": ""}
        return raw

    def page_state(self) -> PageState:
        raw = str(self.inspect_state().get("state", PageState.UNKNOWN_LAYOUT.value))
        try:
            return PageState(raw)
        except ValueError:
            return PageState.UNKNOWN_LAYOUT

    def submit_search_box(self, query: str) -> None:
        self._check_control()
        prepared = self.cdp.evaluate(_SEARCH_BOX_PREPARE_SCRIPT)
        if not isinstance(prepared, dict) or not prepared.get("ok"):
            raise SearchBoxNotFoundError(
                "Google 검색창을 찾지 못했습니다. 동의 화면인지 먼저 확인하세요."
            )

        initial_url = str(self.cdp.evaluate("location.href") or "")
        self.cdp.call("Input.insertText", {"text": query})
        self.cdp.call(
            "Input.dispatchKeyEvent",
            {
                "type": "rawKeyDown",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            },
        )
        self.cdp.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            },
        )

        if not self._wait_for_url_change(initial_url, timeout=min(5.0, self.timeout_seconds)):
            # 일부 레이아웃에서 Enter 이벤트가 무시되면 현재 검색창의 폼을 표준 방식으로 제출한다.
            submitted = self.cdp.evaluate(
                r"""
                (() => {
                  const active = document.activeElement;
                  const form = active?.form || active?.closest?.("form");
                  if (!form) return false;
                  if (typeof form.requestSubmit === "function") form.requestSubmit();
                  else form.submit();
                  return true;
                })()
                """
            )
            if not submitted:
                raise SearchBoxNotFoundError("검색창의 검색 폼을 제출하지 못했습니다.")

    def search_direct_url(self, query: str) -> None:
        params = urllib.parse.urlencode({"q": query, "hl": "ko"})
        self.navigate(f"https://www.google.com/search?{params}")

    def _wait_for_url_change(self, initial_url: str, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check_control()
            try:
                current = str(self.cdp.evaluate("location.href") or "")
                if current and current != initial_url:
                    return True
                # 같은 URL에서 동적으로 결과를 그리는 레이아웃도 성공으로 본다.
                state = self.page_state()
                if state in {
                    PageState.SEARCH_RESULTS,
                    PageState.NO_RESULTS,
                    PageState.CONSENT_REQUIRED,
                    PageState.CAPTCHA_REQUIRED,
                }:
                    return True
            except CdpError as exc:
                message = str(exc).casefold()
                if (
                    "execution context was destroyed" in message
                    or "cannot find context" in message
                ):
                    return True
                raise
            time.sleep(0.1)
        return False

    def wait_for_terminal_state(self) -> PageState:
        deadline = time.monotonic() + self.timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            self._check_control()
            try:
                last = self.inspect_state()
                state = PageState(str(last.get("state")))
            except (CdpError, ValueError):
                time.sleep(0.2)
                continue

            if state in {
                PageState.SEARCH_RESULTS,
                PageState.NO_RESULTS,
                PageState.CONSENT_REQUIRED,
                PageState.CAPTCHA_REQUIRED,
                PageState.NETWORK_ERROR,
            }:
                return state
            time.sleep(0.25)

        raise CdpTimeoutError(
            "검색결과 상태 대기 시간 초과. "
            f"마지막 URL={last.get('href', '')}, 상태={last.get('state', '')}",
            method="Runtime.evaluate",
        )

    def current_url(self) -> str:
        return str(self.cdp.evaluate("location.href") or "")

    def stable_main_rect(self) -> CaptureRect:
        self._check_control()
        state = self.page_state()
        if state in {PageState.CONSENT_REQUIRED, PageState.CAPTCHA_REQUIRED}:
            raise UserActionRequiredError(state.value)
        if state not in {PageState.SEARCH_RESULTS, PageState.NO_RESULTS}:
            raise CaptureAreaNotFoundError(
                f"캡처 가능한 Google 검색결과 상태가 아닙니다: {state.value}"
            )

        self._check_control()
        self.cdp.evaluate(
            """
            (async () => {
              const step = Math.max(window.innerHeight || 600, 600);
              const bottom = Math.max(
                document.documentElement?.scrollHeight || 0,
                document.body?.scrollHeight || 0
              );
              for (let y = 0; y < bottom; y += step) {
                window.scrollTo(0, y);
                await new Promise((resolve) => setTimeout(resolve, 40));
              }
              window.scrollTo(0, 0);
              return true;
            })()
            """,
            timeout=min(self.timeout_seconds, 5.0),
            await_promise=True,
        )
        self._check_control()

        deadline = time.monotonic() + self.timeout_seconds
        previous: CaptureRect | None = None
        stable_count = 0
        last_reason = ""

        while time.monotonic() < deadline:
            self._check_control()
            raw = self.cdp.evaluate(_REGION_SCRIPT)
            if not isinstance(raw, dict) or not raw.get("ok"):
                last_reason = str(raw.get("reason", "UNKNOWN")) if isinstance(raw, dict) else "INVALID_RESULT"
                stable_count = 0
                previous = None
                time.sleep(self.stabilization_interval_seconds)
                continue

            rect = CaptureRect(
                x=float(raw["x"]),
                y=float(raw["y"]),
                width=float(raw["width"]),
                height=float(raw["height"]),
                selector=str(raw["selector"]),
            )

            if rect.width < 300 or rect.height < 60:
                last_reason = f"영역이 너무 작음: {rect.width}x{rect.height}"
                stable_count = 0
                previous = None
            elif previous is not None and self._rect_close(previous, rect):
                stable_count += 1
            else:
                stable_count = 1
                previous = rect

            if stable_count >= self.stabilization_required_count:
                return rect
            time.sleep(self.stabilization_interval_seconds)

        raise CaptureAreaNotFoundError(
            f"검색결과 본문 영역이 안정화되지 않았습니다: {last_reason}"
        )

    @staticmethod
    def _rect_close(left: CaptureRect, right: CaptureRect, tolerance: float = 2.0) -> bool:
        return (
            left.selector == right.selector
            and abs(left.x - right.x) <= tolerance
            and abs(left.y - right.y) <= tolerance
            and abs(left.width - right.width) <= tolerance
            and abs(left.height - right.height) <= tolerance
        )
