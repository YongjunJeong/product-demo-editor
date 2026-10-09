# GitHub 게시 준비

공개 후보는 `scripts/package_portfolio.py`가 생성하는 ZIP의 파일 목록입니다.
게시할 저장소의 이름과 공개 범위가 정해지면 파일 목록을 확인한 뒤 커밋하고 푸시합니다.
현재 공개 저장소는 [YongjunJeong/product-demo-editor](https://github.com/YongjunJeong/product-demo-editor)입니다.

## 포함할 것

- Python 실행 도구, 정적 검토 UI, 테스트, 설정 예제, 에이전트 스킬과 CI
- README와 개발 사례, 검증 기록
- 직접 만든 합성 전사와 응답 JSON
- `docs/assets/`의 검토한 GIF와 UI 스크린샷
- 라이선스가 결정됐다면 LICENSE

## 제외할 것

개인 미디어, 전사, 에이전트 교환 결과, 작업 기록, 모델, `.venv`, `.env`, 자격 증명,
`runs/`, `work/`, 빌드 결과와 과거 작업의 Git 이력은 포함하지 않습니다.
이미 추적된 파일은 `.gitignore`만으로 보호되지 않습니다.

## 로컬 검증

```sh
video-agent doctor
python -m pip check
pytest -q
ruff check src tests scripts skills/product-demo-editor/scripts
ruff format --check src tests scripts skills/product-demo-editor/scripts
python scripts/demo_agent.py --output work/publish-demo
python scripts/package_portfolio.py --output work/portfolio-source.zip
```

출력 폴더와 ZIP은 새 경로를 사용하세요. 기존 결과를 덮어쓰지 않습니다.
자막 필터가 없는 FFmpeg는 렌더링을 할 수 없으므로 `doctor`부터 해결합니다.

## 저장소 경계와 파일 확인

프로젝트 폴더에서 다음을 실행합니다.

```sh
git rev-parse --show-toplevel
git status --short
git ls-files --cached --others --exclude-standard
```

Git 루트가 이 프로젝트 폴더인지 확인합니다. 상위 Downloads나 다른 프로젝트가 나오면
그 저장소에서 일괄 추가하지 마세요. 공개 ZIP을 별도 폴더에 풀어 독립 저장소로 만들 수도 있습니다.
푸시한 커밋의 GitHub Actions 결과도 확인하세요. 로컬 테스트와 실행 환경이 다를 수 있습니다.

## 저장소 소개 예시

> 한국어 제품 데모의 영어 자막과 편집안을 제안하고, 사용자 승인 후 로컬에서 실행하는 에이전트 편집 도구

주요 기술: Python, FFmpeg, Pydantic, faster-whisper, HTML/CSS/JavaScript.
포트폴리오 설명에서는 원본 보존, 검증 가능한 에이전트 응답, 발화 보호와 승인된 실행을 중심으로 설명합니다.
직접 설계·구현한 부분과 에이전트의 도움을 받은 부분은 실제 작업 내역에 맞춰 적으세요.
작업 시간과 모델 정확도는 별도 비교 평가가 필요합니다.
