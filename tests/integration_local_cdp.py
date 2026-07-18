"""인터넷 없이 Chrome 실행→CDP→검색창 입력→본문 캡처를 점검하는 통합 테스트.

실행:
    python tests/integration_local_cdp.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from site_capture.capture import capture_png, validate_png  # noqa: E402
from site_capture.cdp import CdpConnection  # noqa: E402
from site_capture.chrome import ChromeSession, locate_chrome  # noqa: E402
from site_capture.google import GoogleSearchPage  # noqa: E402
from site_capture.models import PageState  # noqa: E402


def main() -> int:
    temporary = Path(tempfile.mkdtemp(prefix="site-capture-integration-"))
    output = temporary / "local_cdp_capture.png"
    session = ChromeSession(
        executable=locate_chrome(),
        profile_dir=temporary / "profile",
        width=1440,
        height=1000,
        headless=True,
    )
    cdp: CdpConnection | None = None

    try:
        session.start()
        target = session.get_page_target()
        cdp = CdpConnection(target["webSocketDebuggerUrl"], default_timeout=20)
        cdp.connect()
        page = GoogleSearchPage(
            cdp,
            viewport_width=1440,
            viewport_height=1000,
            timeout_seconds=20,
            stabilization_interval_seconds=0.2,
            stabilization_required_count=2,
        )
        page.initialize()

        html = r'''<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>로컬 검색 테스트</title></head>
<body>
  <form><textarea name="q" aria-label="검색"></textarea></form>
  <script>
    document.querySelector("textarea").addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      document.body.innerHTML = `
        <form><textarea name="q" aria-label="search"></textarea></form>
        <header style="height:120px">캡처에서 제외할 상단</header>
        <div id="search" style="margin-left:180px;width:760px;padding:20px;border:1px solid #ddd">
          <a href="#"><h3>테스트 검색결과 1</h3></a><p>본문 내용입니다.</p>
          <a href="#"><h3>테스트 검색결과 2</h3></a><p>추가 본문 내용입니다.</p>
          <div style="height:900px">긴 본문 영역</div>
        </div>
        <footer style="height:200px">캡처에서 제외할 푸터</footer>`;
    });
  </script>
</body>
</html>'''
        frame_tree = cdp.call("Page.getFrameTree")
        frame_id = frame_tree["frameTree"]["frame"]["id"]
        cdp.call("Page.setDocumentContent", {"frameId": frame_id, "html": html})
        page.wait_document_ready()

        page.submit_search_box("site:example.com 테스트키워드")
        state = page.wait_for_terminal_state()
        if state != PageState.SEARCH_RESULTS:
            raise AssertionError(f"검색결과 상태가 아닙니다: {state}")

        rect = page.stable_main_rect()
        if rect.y != 0:
            raise AssertionError(f"capture does not start at page top: {rect}")
        if rect.selector != "search-results-with-search-box":
            raise AssertionError(f"unexpected capture selector: {rect.selector}")
        png = capture_png(cdp, rect)
        width, height = validate_png(png)
        output.write_bytes(png)

        fallback_html = r'''<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>로컬 fallback 테스트</title></head>
<body>
  <header style="height:120px">캡처에서 제외할 상단</header>
  <section style="margin-left:180px;width:760px">
    <div style="width:760px;padding:20px;border:1px solid #ddd">
      <a href="#"><h3>fallback 검색결과 1</h3></a><p>fallback 본문 내용입니다.</p>
    </div>
    <div style="width:760px;padding:20px;border:1px solid #ddd">
      <a href="#"><h3>fallback 검색결과 2</h3></a><p>추가 fallback 본문 내용입니다.</p>
    </div>
  </section>
  <footer style="height:200px">캡처에서 제외할 푸터</footer>
</body>
</html>'''
        frame_tree = cdp.call("Page.getFrameTree")
        frame_id = frame_tree["frameTree"]["frame"]["id"]
        cdp.call("Page.setDocumentContent", {"frameId": frame_id, "html": fallback_html})
        page.wait_document_ready()
        fallback_state = page.wait_for_terminal_state()
        if fallback_state != PageState.SEARCH_RESULTS:
            raise AssertionError(f"fallback 검색결과 상태가 아닙니다: {fallback_state}")
        fallback_rect = page.stable_main_rect()
        if fallback_rect.y != 0:
            raise AssertionError(f"fallback capture does not start at page top: {fallback_rect}")
        if fallback_rect.selector != "search-results-with-search-box":
            raise AssertionError(f"fallback 선택자가 아닙니다: {fallback_rect.selector}")
        fallback_png = capture_png(cdp, fallback_rect)
        fallback_width, fallback_height = validate_png(fallback_png)

        print(f"통합 테스트 성공: state={state.value}")
        print(f"캡처 영역: {rect}")
        print(f"PNG: {width}x{height}, {output}")
        print(f"fallback 캡처 영역: {fallback_rect}")
        print(f"fallback PNG: {fallback_width}x{fallback_height}")
        return 0
    finally:
        if cdp is not None:
            try:
                cdp.call("Browser.close", timeout=5)
            except Exception:
                pass
            cdp.close()
        session.stop()
        # 성공 이미지 경로를 출력한 뒤 테스트 임시 폴더는 지우지 않는다.
        # 실패하여 이미지가 없을 때만 정리한다.
        if not output.exists():
            shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
