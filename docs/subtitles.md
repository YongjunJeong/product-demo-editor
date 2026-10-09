# M2: 전사 교정 → 영어 번역 → SRT

기존 분석의 `transcript.json`만 있으면 됩니다. 영상 디코딩이나 ASR을 다시 실행하지
않고 처리하며, 모델/API 키도 필요 없습니다. 모든 출력 파일·폴더는 새 경로를 사용합니다.

## 1. 한국어 전사 교정

```bash
video-agent transcript-edit runs/<run-id>/transcript.json --output work/corrections.json
```

`corrections.json`의 `segments[].text`만 수정합니다. 구간 ID를 삭제·추가하거나
`source_sha256`을 바꾸지 않습니다. 교정이 필요 없다면 이 단계를 생략할 수 있습니다.
원본 전사는 수정하지 않으며, 교정한 문장의 기존 단어 타임스탬프는 부정확해지므로
교정본에서 비웁니다. 구간 시작·종료 시간은 보존합니다.

## 2. 번역 요청 내보내기

```bash
video-agent translation-export runs/<run-id>/transcript.json \
  --corrections work/corrections.json \
  --glossary config/glossary.example.yaml \
  --settings config/subtitles.yaml \
  --output work/translation-v1
```

`--corrections`, `--glossary`, `--settings`는 선택 항목입니다.
용어집에는 실제 제품·메뉴 이름을 추가합니다. 설정 기본값은 두 줄, 한 줄 42자,
노출 1–7초, 초당 20자입니다. 노출 시간과 읽기 속도는 경고 기준이며 자동 승인 기준이
아닙니다. 글자 수는 화면 픽셀 너비가 아닙니다.

생성물:

- `corrected-transcript.json`: 원본과 분리된 한국어 교정본
- `request.json`: 구간 ID·문맥·시간·용어집·자막 설정
- `response.template.json`: 번역문을 입력할 응답 틀
- `prompt.md`: 수동 번역 지침
- `export_report.json`: 교정한 구간과 단어 정렬 초기화 기록

요청에서는 단어별 시간·확률과 로컬 모델 경로를 제외해 불필요한 문맥을 줄입니다.
앱은 파일을 외부로 전송하지 않습니다. 외부 번역 도구를 직접 이용한다면 전사문과
용어집이 해당 서비스에 전달됩니다.

## 3. 영어 결과 입력

`response.template.json`을 `response.json`으로 복사하고 `text`를 영어로 채웁니다.
수동 번역 도구를 사용한다면 `prompt.md`, `request.json`, 응답 틀을 함께 전달합니다.

```json
{
  "schema_version": "1.0",
  "request_id": "템플릿의 값을 그대로 유지",
  "request_sha256": "템플릿의 값을 그대로 유지",
  "segments": [
    {"id": 1, "text": "Create a new customer segment."},
    {"id": 2, "text": "Add conditions, then click Save."}
  ]
}
```

모든 구간을 정확히 한 번씩 포함합니다. 구간 순서는 달라도 ID로 원본 순서를 복원합니다.
다른 구간으로 의미를 옮기거나 시간을 직접 추가하지 않습니다. 번역 JSON을 가져온 뒤에도
부정 표현·조건·수치·제품 기능의 정확성은 사람이 검토해야 합니다.

## 4. 자막 생성

```bash
video-agent translation-import work/translation-v1/request.json \
  work/translation-v1/response.json --output work/subtitles-v1
```

결과는 `subtitles.en.srt`, `subtitles.en.json`, `review_report.json` 및 입력 사본입니다.
SRT는 UTF-8이며 원본 영상 시간 기준입니다. 모두 검토 전 초안입니다.

긴 구간은 최대 두 줄씩 나눈 뒤 원래 발화 구간 내부에서 글자 수에 따라 시간을
배분합니다. 읽기 속도가 빠르면 내용을 줄이거나 다시 번역하도록 경고하며, 임의로
말을 삭제하거나 다음 장면까지 시간을 늘리지 않습니다. 줄 제한보다 긴 식별자는
임의로 끊지 않고 오류를 안내합니다.

검토 보고서는 읽기 속도, 짧거나 긴 노출, 용어집 누락, 영어 자막에 남은 한글을
표시합니다. 용어 검사는 단순 문자열 검사이며 오탐·누락이 가능합니다. 경고가 없어도
번역 의미나 타이밍의 정확성을 보장하지 않습니다. 알 수 없는 수동 번역 비용은
0으로 기록하지 않고 `null`로 둡니다.

번역만 수정했으면 같은 요청에 응답만 수정하고 **새 출력 폴더**로 가져옵니다.
한국어·용어집·설정이 바뀌면 새 요청을 내보내야 합니다. 요청 해시는 서식 변경도
감지하므로 요청 파일을 직접 편집하지 않습니다. 기존 결과는 덮어쓰지 않습니다.

## 개발용 샘플

`work/m2-demo/`에는 기존 한국어 합성 영상 전사에 예시 영어 번역을 수동으로 넣어
처리한 결과가 있습니다. 별도 외부 번역 호출은 하지 않았습니다. 실제 고객 영상의
번역 품질을 평가한 자료는 아닙니다.
