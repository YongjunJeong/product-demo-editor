# M3–M4: 자막 검토, 승인, 미리보기와 컷 편집

기존 분석 run과 M2 영어 자막 JSON으로 시작합니다. 모든 명령은 로컬에서 실행하며
ASR이나 번역을 다시 호출하지 않습니다. 프로젝트 가상환경을 먼저 활성화합니다.

## 계획 만들기와 검토

```bash
video-agent plan runs/<run-id> \
  --subtitles work/subtitles-v1/subtitles.en.json --output work/edit-plan.json
video-agent review work/edit-plan.json
```

`--subtitles`는 선택입니다. 원본·분석 파일의 해시를 확인하고, 다른 전사에서 만든
자막은 거부합니다. `edit-plan.json`의 `cues`에서 자막 문구·줄바꿈·시작·종료 시간을
수정할 수 있습니다. `review`를 다시 실행하면 수정된 자막과 가독성 경고를 보여줍니다.
구간을 겹치게 만들거나 원본 범위를 벗어나게 하면 검증에서 차단합니다.

무음 후보는 양끝 0.2초를 남기고 안쪽으로 프레임 경계를 맞춘 뒤, 남은 길이가
0.7초 이상인 것만 제안합니다. 알려진 발화의 앞뒤 0.15초 또는 자막과 겹치는 후보는
제안하지 않습니다. ASR을 생략했다면 `speech_protection_available: false`가 표시됩니다.
무음에도 중요한 UI 조작이 있을 수 있으므로 자동 선택하지 않습니다.

## 승인 전 미리보기

```bash
video-agent preview work/edit-plan.json --output work/preview-v1
```

기본값은 삭제 없음입니다. 예를 들어 후보 1번만 제거한 결과를 미리 보려면:

```bash
video-agent review work/edit-plan.json --cuts 1
video-agent preview work/edit-plan.json --cuts 1 --output work/preview-cut-v1
```

`review`는 후보별 원본 시간, 선택 여부, 예상 결과 길이, 자막을 보여줍니다.
`preview`는 승인 기록을 만들지 않습니다. 계획 파일을 편집한 뒤에는 새 출력 폴더로
미리보기를 만듭니다. 폴더에 있는 `preview.mp4`를 재생해 확인합니다.

## 승인과 최종 실행

```bash
video-agent approve work/edit-plan.json --cuts 1 --output work/approved-plan.json
video-agent render work/approved-plan.json --output work/final-v1
```

`--cuts`로 지정한 후보만 삭제 승인하고, 나머지는 거절 처리합니다. 삭제 없이 자막만
승인하려면 `--cuts`를 생략합니다. 승인 명령은 자막과 컷 결정을 포함한 정확한 계획 및
컴파일된 타임라인의 해시를 기록합니다. 승인 파일의 내용을 바꾸면 렌더링이 거부됩니다.
수정이 필요하면 초안 계획을 고친 후 새 승인 파일을 만듭니다. 해시는 변경 감지이며
사용자 신원을 증명하는 전자서명은 아닙니다.

발화·자막과 겹치는 삭제는 승인 단계에서도 차단합니다. 발화 삭제·실수 제거는 M4의
지원 범위가 아닙니다. 자막 없는 영상은 자막 없이 동일한 흐름을 사용할 수 있습니다.

## 출력

| 파일 | 내용 |
|---|---|
| `edited.mp4` | 컷이 적용된 자막 없는 H.264/AAC 영상, 원래 목소리 유지 |
| `preview.mp4` | 영어 자막을 입힌 미리보기, 최대 가로 1280px |
| `subtitles.en.srt` | 편집된 출력 시간으로 재계산한 자막 |
| `execution.json` | 원본→출력 프레임 매핑과 출력 자막 |
| `plan.json` | 적용한 자막·컷 결정의 사본 |
| `render.ffgraph` | 실제 FFmpeg 필터 그래프 |
| `render_metrics.json` | 길이·처리 시간·캐시·승인 여부·오류 기록 |
| `render_manifest.json` | 실행 지문과 완성된 영상 해시 |

음성 트랙이 없는 영상도 처리합니다. 오디오는 재인코딩하므로 원본 바이트와 동일하지
않지만 목소리·선택한 구간의 속도는 유지합니다. 원본은 변경하지 않습니다.

## 실패 후 재개

```bash
video-agent render work/approved-plan.json --output work/final-v1 --resume
```

동일한 계획·도구·구현에 한해 검증된 영상 단계를 재사용합니다. 깨진 출력은 재생성하고,
자막 미리보기가 실패했어도 완료된 `edited.mp4`는 재사용합니다. 미리보기 명령도
`--resume`을 지원합니다. 승인 전/후 모드가 바뀌거나 계획을 수정했으면 새 폴더를
사용합니다. 동일 출력 폴더에 대한 동시 실행은 지원하지 않습니다.

## 시간 기준과 제한

- 분석·원본 자막은 원본 시간, 컷과 실행 타임라인은 고정 프레임레이트 프레임 기준입니다.
- 기본 출력은 30fps입니다. 계획 생성 시 `--fps 25` 또는 `--fps 60`도 가능합니다.
- VFR 원본은 선택한 CFR로 정규화하므로 프레임 복제/생략이 발생할 수 있습니다.
- 마지막 불완전한 프레임보다 짧은 꼬리는 제외하며 `discarded_tail_ms`에 기록합니다.
- 컷은 프레임 단위, 자막 파일은 밀리초 단위입니다. 자막 경계 반올림 오차는 약 1ms입니다.
- 자막은 하단 중앙 고정입니다. 중요한 UI를 피하는 자동 배치와 스타일 UI는 아직 없습니다.
- 색 공간/HDR 보존, 다중 오디오 선택, 긴 4K 영상, 수백 개 컷의 성능은 미검증입니다.
- 배속은 [M5 워크플로](speed.md)에서 지원합니다. 자동 줌·CapCut 프로젝트 생성은 아직 없습니다.

자막 렌더링에는 libass가 포함된 FFmpeg가 필요합니다. macOS에서는 Homebrew의
`ffmpeg-full` 설치 경로를 자동으로 찾습니다. 다른 환경에서는 `subtitles` 필터를
지원하는 FFmpeg를 PATH에 준비하세요.

## 검증용 영상

- `work/m34-demo/korean-final/preview.mp4`: 합성 한국어 음성 + 영어 자막, 컷 없음
- `work/m34-demo/cut-final/preview.mp4`: 알려진 두 구간 삭제, 10초 → 6.8초

승인 명령의 자동 실행 검증은 이 합성 테스트 자료에만 적용했습니다. 실제 고객 영상의
편집 판단이나 번역 품질을 검증한 자료는 아닙니다.
