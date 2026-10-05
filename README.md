# aside-blog — 교재 학습카드 → 네이버 블로그 딸깍 게시기

교재 PDF 한 권을 **주제별 학습카드**(textbook-study-cards 스킬과 같은 HTML)로 만들고,
카드 1장을 **네이버 블로그 글 1개**(제목·도입·소제목·글머리·표·OX·태그·대표이미지)로 바꿔
**실제 Chrome 에서 눈앞에 글을 써 넣고 발행·예약**한다.

- 글·그림은 **Codex CLI OAuth**(각자 회사 ChatGPT 구독, API 키 없음) — aside-threads 의 Codex 층을 그대로 쓴다.
- 대시보드·사이드패널·실제 Chrome(CDP) 게시·예약은 aside-shorts 구조를 그대로 쓴다.
- 게시 흐름은 레퍼런스 영상 「딸깍 SNS」(5:11~7:35)와 같다: 글쓰기 창에 제목·본문·그림 넣기 → 발행 창에서 카테고리·전체공개·태그·현재/예약 → 발행.

## 흐름

| 단계 | 하는 일 | 할당량 |
|---|---|---|
| ① 교재·카드 | 교재 PDF → 목차대로 조각 → **학습카드**(Codex) · 이미 만든 카드 폴더는 그대로 불러오기 | 카드 만들 때만 |
| 1 글 쓰기 | 학습카드 → 블로그 글(제목·도입·소제목 문단·글머리·태그·그림 장면). 표·용어·시험 포인트·OX·한 줄 정리는 **카드에서 그대로** 붙인다 | Codex |
| 2 그림 설명 | 장면에 「아이보리 바탕·글자 금지·플랫 벡터」 규칙을 붙인다 | 0 |
| 3 그림 그리기 | 대표이미지 + 소제목 삽화(Codex image_generation). **계정이 그림을 못 그리면 건너뛴다** | Codex |
| 4 카드 그림·표지 | 카드의 표·OX 정답(펼친 상태)을 그림으로 찍고, **디자인 표지**를 만든다(대표이미지 대체) | 0 |
| ④ 올리기·예약 | 왼쪽 네이버 Chrome 에 글을 넣고 발행 / 네이버 **예약 발행**(PC 꺼도 됨) / 패턴 추천으로 여러 편 미리 예약 | 0 |

네이버 스마트에디터에는 접기 칸(`<details>`)이 없어서 **OX 정답은 펼친 그림**으로, 표는 카드 모양 그대로 그림으로 넣는다
(설정 `post.table_mode`·`quiz_mode` 로 글로 바꿀 수 있다). 핵심 용어·글머리는 검색에 걸리도록 **글자**로 넣는다.

## 설치·실행 (팀원 PC)

1. `setup.bat` — venv, 패키지, Chromium, 글꼴, 점검
2. `run.bat` — 대시보드가 뜬다
3. 대시보드 ④ → **Codex 로그인** (회사 ChatGPT 계정)
4. ④ → 네이버 계정 추가(이름 `dekman`, 블로그 아이디 `dekman`) → **로그인 창 열기** → 왼쪽 Chrome 에서 네이버 로그인(로그인 상태 유지)
5. ① 에서 교재 PDF 또는 학습카드 폴더로 작업 만들기 → ② 「딸깍」 → ③ 확인·고치기 → ④ 「게시 패널 열기」

처음 한 번은 꼭 **「글쓰기 화면 구조 확인」(probe) → 「미리 채워 보기」(발행 직전에서 멈춤)** 순서로 확인한다.
네이버 화면이 바뀌어 버튼을 못 찾으면 `aside_blog/naver.py` 의 `SEL`·`TEXT` 표만 고친다(probe 결과는 `logs/probe/`, 단계별 스크린샷은 `logs/post/`).

## 작업 폴더

```
jobs/<job>/
  job.json              책 이름·연재 문구·출처·카테고리·교재 경로
  source/               all.txt · chunks/<장-절-소단원>.txt · manifest.json (교재 목차)
  cards/json/<id>.json  학습카드 내용
  cards/html/           학습카드 HTML + 00_목차.html (스킬 산출물과 글자 하나까지 같은 꼴)
  posts/<id>.json       블로그 글 · overrides/<id>.json 손편집(언제나 이김)
  images/               Codex 그림(<id>-cover.png, <id>-sec1.png …) + 이미지프롬프트.json
  assets/<id>/          카드에서 찍은 table-N.png · quiz.png · terms.png · cover.png(디자인 표지)
  state.json            발행·예약 기록
```

## 명령 (run.bat <명령> = python -m aside_blog <명령>)

```
new vb --pdf 교재.pdf --cards 카드폴더      둘 다: 카드를 교재의 결과물로 강제로 넣기(할당량 0)
new vb2 --pdf 교재.pdf                       교재에서 새로 → extract 결과(목차) 확인 → s0-cards
make --job vb [--only 1-1-01,3-2-03] [--offline] [--limit 3]
post --job vb --only 1-1-01 --dry-run        왼쪽 창에 채우고 발행 직전에서 멈춤
post --job vb --only 1-1-01 --at "2026-10-06 19:00"   네이버 예약 발행 (10분 단위)
plan --job vb --pattern 2-lunch [--apply]    남은 글을 하루 2편 빈 자리에 예약
tabs / fill --job vb --only 1-1-01 --tab 0   내가 연 글쓰기 창에 넣기(영상식)
probe · login · doctor · codex-login · fonts · ui
```

## 이 PC 의 원본 (저장소에는 올리지 않음)

`_` 로 시작하는 폴더(`_context` 교재·카드 원본, `_skill`, `_video`)와 `jobs/` 의 실제 작업·입력 샘플은 **깃허브에 올리지 않는다**(`.gitignore`).
이 PC 에서는 `_context/varisu_recent.pdf`(교재)와 `_context/변액보험_학습카드/`(그 교재로 만든 카드 133장)를 묶어
작업 `vb` 로 넣어 두었다:

```
run.bat new vb --pdf "_context\varisu_recent.pdf" --cards "_context\변액보험_학습카드" --title "변액보험 학습카드" --category "변액보험판매관리사 133제"
```

교재 조각 81개 중 78개가 카드와 이어지고, 카드 폴더에 없는 짧은 절 머리말 3개(1-5-00, 2-3-00, 4-3-00)는 카드 번호가 밀리지 않도록 건너뛴다.

## 규칙

- `.bat` 파일에는 ASCII 영문과 CRLF 줄바꿈만 쓴다(한국어 Windows cmd가 UTF-8 한글·LF를 잘못 읽음). `.gitattributes`가 CRLF를 고정한다. 한글 메시지는 파이썬 쪽에 둔다.
- 화면·로그 문구는 쉬운 해요체. 기술 내용은 `logs/detail.log` 로만.
- 발행 버튼을 누른 뒤에는 다시 시도하지 않는다(두 번 올라갈 수 있다).
