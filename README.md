# Site Capture

## 프로그램 목적

이 프로그램은 상위 기관의 요청에 따라 반복적으로 수행해야 하는 Google 도메인 검색 및 결과 화면 캡처 업무를 자동화하기 위해 제작되었습니다. 특정 도메인과 키워드로 Google 검색을 반복 실행하고, 검색창이 포함된 검색 결과 화면을 증빙 이미지로 저장하여 수작업에 드는 시간과 누락을 줄이는 것을 목적으로 합니다.

Chrome CDP로 Google `site:` 검색 결과의 본문 영역을 PNG로 저장하는 Windows 로컬 프로그램입니다. Selenium과 ChromeDriver는 사용하지 않습니다. CLI와 PySide6 GUI는 같은 캡처 실행 경로를 사용합니다.

## 기능

- Chrome 전용 프로필과 로컬 CDP 포트로 안전하게 실행
- `site:` 검색, 본문 영역 탐지·안정화, PNG 검증·SHA-256·원자적 저장
- 동의/CAPTCHA 화면은 자동 우회하지 않고 사용자가 Chrome에서 직접 처리
- 작업별 진행표, 일시정지·재개·중단
- SQLite에 실행·작업·오류·캡처 메타데이터 저장
- 비정상 종료 뒤 남은 작업을 GUI에서 이어서 실행

## 요구 환경과 설치

- Python 3.11 이상
- Google Chrome 또는 Chromium
- Windows 권장 (macOS/Linux 경로도 일부 지원)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Windows에서는 `install.bat`도 사용할 수 있습니다.

## 처음 사용하는 분: GUI로 실행하기

명령어가 익숙하지 않다면 GUI를 권장합니다.

1. Google Chrome을 모두 종료합니다.
2. 프로젝트 폴더에서 `install.bat`을 한 번 실행해 필요한 패키지를 설치합니다.
3. 설치가 끝나면 `run_gui.bat`을 더블클릭합니다.
4. 창의 키워드 입력란에 검색할 단어를 한 줄에 하나씩 입력합니다.

   ```text
   검색어1
   검색어2
   검색어3
   ```

5. 검색할 도메인을 한 줄에 하나씩 입력하고, 필요하면 출력 경로를 바꿉니다. 기본 출력 경로를 그대로 써도 됩니다.
6. `전체 실행`을 누릅니다. 진행표에서 각 키워드·도메인별 상태와 저장 경로를 확인할 수 있습니다.

처음에는 키워드 하나로 `테스트 실행`을 해 Chrome이 정상적으로 열리고 PNG가 저장되는지 확인하는 편이 좋습니다.

## GUI 동작 방법

`run_gui.bat`을 실행하거나 다음 명령을 사용합니다.

```powershell
python gui_main.py
```

키워드와 실제 검색 도메인을 각각 줄마다 입력하고 출력 경로·옵션을 설정한 뒤 `전체 실행`을 누릅니다. 작업 중에는 `일시정지`, `재개`, `중단`을 사용할 수 있습니다. 동의 또는 CAPTCHA가 표시되면 Chrome에서 직접 처리한 뒤 GUI의 `Chrome 처리 완료`를 누릅니다.

### 동의 또는 CAPTCHA가 보일 때

이 프로그램은 Google의 동의·CAPTCHA를 자동으로 넘지 않습니다.

1. 프로그램이 열어 둔 Chrome 창에서 동의 또는 CAPTCHA를 직접 처리합니다.
2. 검색 결과 화면으로 돌아온 것을 확인합니다.
3. 프로그램 창의 `Chrome 처리 완료`를 누릅니다.

Chrome 창을 닫지 말고 처리해야 합니다. `--headless` 모드에서는 사람이 화면을 처리할 수 없으므로 이 기능을 사용할 수 없습니다.

### 중단한 작업 다시 시작하기

이전에 끝나지 않은 작업이 있으면 프로그램을 열 때 재개 여부를 묻습니다.

- `예`: 이전 설정과 작업표를 복원하고 완료되지 않은 작업만 이어서 실행합니다.
- `아니오`: 남아 있던 작업을 취소 처리합니다. 다음 시작 때 같은 작업을 다시 묻지 않습니다.

SQLite DB와 오류 로그는 Windows에서 다음 위치에 저장됩니다.

```text
%LOCALAPPDATA%\SiteCapture\data\jobs.db
%LOCALAPPDATA%\SiteCapture\logs\
```

## CLI 실행

GUI 대신 명령 프롬프트 또는 PowerShell에서 실행하려면 다음처럼 입력합니다.

```powershell
$keyword = Read-Host "검색 키워드"
$domain = Read-Host "검색 도메인"
python main.py `
  --keyword $keyword `
  --domain $domain `
  --delay 5
```

`--domain`은 한 번 이상 입력해야 하며, 원하는 도메인을 반복 지정할 수 있습니다. 같은 도메인을 반복해도 한 번만 실행합니다.

여러 키워드는 UTF-8-SIG, UTF-8, CP949를 지원하는 TXT 파일로 전달할 수 있습니다.

```powershell
python main.py --keyword-file .\keywords.txt --domain $domain --delay 8
```

## 주요 옵션

```text
--keyword TEXT                  키워드. 여러 번 사용 가능
--keyword-file PATH             한 줄당 하나인 TXT
--domain DOMAIN                 검색할 실제 도메인. 여러 번 사용 가능
--search-mode search-box        Google 검색창 입력 (기본값)
--search-mode direct-url        검색 URL 직접 이동
--exact                         정확 문구 검색
--output-dir PATH               날짜 폴더를 생성할 상위 출력 경로
--profile-dir PATH              전용 Chrome 프로필
--timeout 30                    제한시간(초)
--delay 5                       작업 사이 대기시간(초)
--overwrite                     동일 파일 덮어쓰기
--no-metadata                   JSON 메타데이터를 생성하지 않음
--keep-chrome-open              완료 후 Chrome 유지
--headless                      화면 없이 실행. 수동 검증 처리 불가
```

## 출력 구조

```text
Downloads/
└─ YYYY-MM-DD/
   └─ 기관도메인/
      ├─ YYYY-MM-DD_검색어.png
      └─ YYYY-MM-DD_검색어.json
```

JSON에는 검색식, 실제 Google URL, 캡처 선택자·좌표·시각, PNG 크기, SHA-256을 기록합니다.

## 문제가 생기면

- `python` 명령을 찾지 못하면 Python 3.11 이상을 설치한 뒤 설치를 다시 실행합니다.
- Chrome이 열리지 않으면 실행 중인 Chrome을 모두 닫고 다시 시도합니다.
- `Chrome 처리 완료`를 눌러도 진행되지 않으면 Chrome에서 검색 결과가 실제로 표시됐는지 확인합니다.
- 오류가 나면 `%LOCALAPPDATA%\SiteCapture\logs\`의 최신 로그 파일을 확인합니다.
- Google 화면 구조가 바뀌면 캡처 영역을 찾지 못할 수 있습니다. 이 경우 오류 로그와 함께 개발자에게 알려 주세요.

## 테스트

```powershell
python -m unittest discover -s tests -v
python tests/integration_local_cdp.py
```

## 제한

- Google DOM이 바뀌면 `site_capture/google.py`의 후보 선택자를 조정해야 할 수 있습니다.
- 캡처 전 문서 전체를 순회해 지연 로딩을 유도하지만, 사용자 동작이 필요한 리소스까지 보장하지는 않습니다.
- Google `site:` 결과는 해당 사이트의 전체 색인 목록을 보장하지 않습니다.

## 라이선스

이 프로그램은 MIT 라이선스로 배포됩니다. 자세한 라이선스 조건은 프로젝트 루트의 `LICENSE` 파일을 확인하세요.

## 사용 글꼴

GUI 기본 글꼴은 **에이투지체(A2Z Regular)** 입니다. 글꼴 원본과 라이선스 정보는 프로젝트 루트의 `THIRD_PARTY_NOTICES.md`에서 확인할 수 있습니다.
