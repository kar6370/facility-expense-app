# GitHub · Streamlit 업로드 안내 — V45.11

## 이번 오류의 원인

`ModuleNotFoundError: No module named 'owner_access'`는 `app.py`가 사용하는 `owner_access.py`를 GitHub에서 찾지 못해 발생합니다. 이 앱은 여러 프로그램 파일이 함께 실행됩니다. 로컬 폴더에는 파일들이 있어서 정상 실행되지만 `app.py`만 업로드하면 실행할 수 없습니다.

## 그대로 따라 하는 순서

1. ZIP을 압축 해제하고 **GITHUB_UPLOAD 폴더 안으로 들어갑니다.**
2. GitHub의 기존 `facility-expense-app` 저장소에서 **main 브랜치**, 기존 `app.py`가 보이는 최상위 화면을 엽니다.
3. **Add file → Upload files**를 선택합니다.
4. **GITHUB_UPLOAD 안의 내용물 전체**를 끌어다 놓습니다. `app.py`와 나머지 18개 `.py`, `requirements.txt`, `.streamlit` 폴더 등이 같은 구조로 올라가야 합니다. ZIP 자체나 GITHUB_UPLOAD 바깥 폴더를 올리지 않습니다.
5. 업로드 목록에서 `app.py`, `owner_access.py`, `budget_workspace.py`, `management_view.py`, `adjustment_guard.py`, `requirements.txt`, `.streamlit/config.toml`이 보이는지 확인하고 **Commit changes**로 저장합니다. 새 브랜치로 올렸다면 main으로 병합되어야 현재 앱에 적용됩니다.
6. Streamlit 실행 경로는 기존처럼 **app.py**, 브랜치는 **main**으로 유지합니다. 자동 재시작 후에도 이전 오류 화면이 남으면 **Manage app → ⋮ → Reboot app → Reboot**를 누릅니다.

GitHub에서 파일 위치는 `app.py`, `owner_access.py`, `budget_workspace.py`가 모두 같은 최상위 폴더입니다. `GITHUB_UPLOAD/app.py`처럼 한 단계 안쪽으로 넣으면 기존 실행 경로와 달라집니다.

## 함께 교체할 파일

이 폴더에는 실행에 필요한 Python 파일 **19개**, 라이브러리 목록 `requirements.txt`, 화면 설정 `.streamlit/config.toml`, 이 안내문, `.gitignore`가 있습니다. `owner_access.py` 하나만 추가하면 그다음 누락 파일에서 다시 멈출 수 있으므로 전부 올리세요.

`requirements.txt`도 반드시 함께 올립니다. 이번 파일은 Streamlit 1.63.0, pandas 2.2.3, Altair 6.2.2, openpyxl 3.1.5와 Firebase Admin 7.7.0을 지정합니다. 앱 내부 파일인 `owner_access.py`는 설치 라이브러리가 아니므로 requirements에 이름을 추가하는 방법으로 해결되지 않습니다.

## 기존 자료·설정

기존 `facility_data.json`, `packages.txt`, `.devcontainer`는 이번 업로드에서 삭제하거나 교체하지 않습니다. 기존 Streamlit **Secrets의 Firebase 설정과 app_id도 그대로 유지**합니다. 이 폴더에는 운영자료나 비밀번호가 없습니다. 로컬 앱 폴더 전체나 `.streamlit/secrets.toml`, 관리자 설정 파일은 GitHub에 올리지 마세요.

편성예산·부기관리 메뉴의 관리자 인증은 유지됩니다. 로그인 기능을 제거하거나 누락된 인증 파일을 무시하도록 바꾸지 않았습니다. Firebase 연결과 SQLite 편성자료 저장은 기존 방식 그대로이며, PC에서 등록한 자료가 이 프로그램 업로드만으로 클라우드에 복사되지는 않습니다.

## 검증 범위

프로그램 기능은 직전 V45.11과 동일합니다. 기존 기능의 전체 자동검사 181개 통과 기록은 로컬용 폴더의 VALIDATION.md에 있습니다. 이번에는 새 Python 3.12 환경에서 이 폴더의 requirements를 설치하고 의존성 충돌 없음, 필수 파일 19개의 연결, 일반 메뉴 6개·간편보기 6개·관리자 화면의 실행을 확인했습니다(14.304초). 실제 GitHub 저장소나 운영 중인 Streamlit 앱에는 직접 반영하지 않았습니다. 사용자 Firebase 데이터 연결과 서버 Python 3.13의 실기동은 별도 확인 대상입니다.

## 공식 안내

- [Streamlit 파일 배치](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization)
- [Streamlit 라이브러리 목록](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
- [GitHub 파일 업로드](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository)
- [Streamlit 재시작](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/reboot-your-app)
