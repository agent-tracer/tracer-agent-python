#!/usr/bin/env node
// 커밋 훅과 CI가 함께 쓰는 커밋 메시지 검사기다.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TYPES = ["feat", "fix", "refactor", "test", "docs", "chore", "ci"];
const SCOPES = ["api", "worker", "agents", "persistence", "tracer-client", "ci", "docs", "repo", "scripts"];
const HEADER_PATTERN = /^(\w+)(?:\(([\w-]+)\))?: (.+)$/;
const KOREAN_PATTERN = /[가-힣]/;
// 명사구 제목을 막고 행위 문장을 강제한다.
const ACTION_SENTENCE_PATTERN = /다$/;
const COAUTHOR_TRAILER_PATTERN = /^Co-authored-by:/i;
// 도구가 근거와 검증을 Key: Value 트레일러로 붙이는 관습을 막는다.
const STRUCTURED_BODY_PATTERN = /^(?<prefix>[A-Za-z][A-Za-z-]*):/;
const MAX_TITLE_LENGTH = 60;
const MAX_BODY_LINES = 4;

// 저장소가 어떻게 만들어졌는지가 아니라 무엇이 되었는지를 쓰게 한다.
const TRACE_WORDS = Object.freeze({
  "기존": "지금의 이름과 구조로 쓴다",
  "레거시": "지금의 이름과 구조로 쓴다",
  "이전 버전": "지금의 이름과 구조로 쓴다",
  "마이그레이션": "무엇이 되었는지를 쓴다",
  "이식": "무엇이 되었는지를 쓴다",
  "옮긴다": "무엇이 되었는지를 쓴다",
  "옮겼다": "무엇이 되었는지를 쓴다",
  "가져온다": "무엇이 되었는지를 쓴다",
  "임시": "한시적인 것은 커밋에 적지 않는다",
  "당분간": "한시적인 것은 커밋에 적지 않는다",
  "TODO": "남길 것은 커밋에 적지 않는다",
  "FIXME": "남길 것은 커밋에 적지 않는다",
});

// 한국어는 어간에 어미가 붙어 형태가 바뀌므로 은유와 구어를 실제로 나타나는 표면형으로 적는다.
export const REGISTER_WORDS = [
  [/걷어/, "제거한다"], [/가른|가르는|가르고/, "구분한다"],
  [/캔다|캐는|캐고|캐지|캐라|캐낸|캘/, "수집한다"], [/잠근|잠그는/, "고정한다"],
  [/죽인|죽이|죽어 있/, "중단한다"], [/이긴/, "우선한다"],
  [/(?<![가-힣])(?:돈다|도는|도므로)|(?<!되)돌[린리]|(?<!되)돌려(?![주준줄줘줍받보])|돌았/, "실행한다"],
  [/집는|(?<!뒤)집히|집어/, "가져간다"], [/흘린|흘리|흘려/, "전송한다"],
  [/붙는/, "연결된다"], [/바닥나|바닥난/, "소진된다"], [/혼자/, "단독으로"],
  [/태우|태운|태울|태워/, "실행한다"], [/쥐고/, "가지고"],
  [/무너지|무너진|무너져|무너졌/, "실패한다"], [/착지/, "종료한다"], [/강등/, "낮춘다"],
  [/열어보|열어본|열어볼|열어봤/, "조회한다"], [/훑는|훑은|훑고|훑어/, "조회한다"],
  [/깨우고|깨우는|깨운|깨웠|깨움/, "알린다"], [/못박|못 박/, "고정한다"],
  [/완주/, "끝까지 실행한다"], [/견준|견주고|견주는|견주게|견줄|견줘/, "비교한다"],
  [/굶기|굶주/, "막는다"], [/새긴|새기|새겨|새겼/, "기록한다"],
  [/(?<![가-힣])심[는은어었]/, "기록한다"], [/먹도록|먹었|먹는다/, "쓴다"],
  [/빚는|빚은|빚어/, "만든다"], [/팔지|팔고|파낸/, "조사한다"],
  [/편다|펴 보|펴지|펼친/, "정리한다"], [/(?<!맞)물린다|물려(?![받준])/, "넘긴다"],
  [/노릇/, "역할을 한다"], [/멋대로/, "근거 없이"], [/되레/, "오히려"], [/영영/, "끝내"],
  [/그러듯/, "실제 동작과 같이"], [/감당/, "처리한다"], [/새면/, "나간다"],
];

const HANGUL_BASE = 0xac00;
const FINAL_COUNT = 28;
// 어간의 받침에 맞지 않는 어미와 조사는 문장을 성립시키지 못한다.
const VERB_ENDING = /([가-힣])(는다|은다)(?![가-힣])/g;
const OBJECT_PARTICLE = /([가-힣])를(?![가-힣])/g;

const hasFinal = (syllable) => (syllable.codePointAt(0) - HANGUL_BASE) % FINAL_COUNT !== 0;

/** 금지 어휘 표에서 메시지에 나타난 것을 찾는다. */
export function findBannedWords(message, table) {
  return Object.entries(table)
    .filter(([word]) => message.includes(word))
    .map(([word, hint]) => ({ word, hint }));
}

/** 은유와 구어가 실제로 나타난 표면형을 대신 쓸 동사와 함께 찾는다. */
export function findRegisterWords(message) {
  return REGISTER_WORDS.map(([pattern, hint]) => [message.match(pattern), hint])
    .filter(([found]) => found !== null)
    .map(([found, hint]) => ({ word: found[0], hint }));
}

/** 어간의 받침이 뒤에 온 어미나 조사와 맞지 않는 자리를 모두 찾는다. */
export function findMalformedKorean(message) {
  const errors = [];
  for (const [surface, stem, ending] of message.matchAll(VERB_ENDING)) {
    if (hasFinal(stem) === (ending === "은다")) {
      errors.push(`어간의 받침에 맞지 않는 어미다: ${surface}`);
    }
  }
  for (const [surface, stem] of message.matchAll(OBJECT_PARTICLE)) {
    if (hasFinal(stem)) errors.push(`받침 있는 말 뒤의 목적격 조사는 을이다: ${surface}`);
  }
  return errors;
}

/** 커밋 메시지 한 건을 검사해 위반 목록을 낸다. */
export function checkCommitMessage(message) {
  const lines = message.split("\n");
  const subject = lines[0]?.trimEnd() ?? "";

  if (subject.startsWith("Merge ") || subject.startsWith("Revert ")) return [];

  const errors = [];
  const match = subject.match(HEADER_PATTERN);
  if (!match) {
    return [`형식이 "타입(범위): 제목"이 아니다: "${subject}"`];
  }

  const [, type, scope, title] = match;
  if (!TYPES.includes(type)) {
    errors.push(`타입 "${type}"은 허용 목록에 없다: ${TYPES.join(", ")}`);
  }
  if (scope !== undefined && !SCOPES.includes(scope)) {
    errors.push(`범위 "${scope}"는 허용 목록에 없다: ${SCOPES.join(", ")}`);
  }
  if (!KOREAN_PATTERN.test(title)) {
    errors.push(`제목은 한글로 쓴다: "${title}"`);
  }
  if (title.endsWith(".")) {
    errors.push(`제목 끝에 마침표를 쓰지 않는다: "${title}"`);
  }
  if (title.length > MAX_TITLE_LENGTH) {
    errors.push(`제목이 ${MAX_TITLE_LENGTH}자를 넘는다(${title.length}자): "${title}"`);
  }
  if (!ACTION_SENTENCE_PATTERN.test(title)) {
    errors.push(`제목은 행위 문장으로 쓴다("…한다"): "${title}"`);
  }

  const body = lines.slice(1).join("\n");
  for (const { word, hint } of findBannedWords(`${title}\n${body}`, TRACE_WORDS)) {
    errors.push(`"${word}"를 쓰지 않는다: ${hint}`);
  }
  for (const { word, hint } of findRegisterWords(`${title}\n${body}`)) {
    errors.push(`"${word}" 대신 "${hint}"를 쓴다`);
  }
  errors.push(...findMalformedKorean(`${title}\n${body}`));

  errors.push(...checkBody(lines.slice(1)));
  return errors;
}

function checkBody(bodyLines) {
  const errors = [];
  const content = bodyLines
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith("#") && !COAUTHOR_TRAILER_PATTERN.test(line));

  const banned = content.map((line) => line.match(STRUCTURED_BODY_PATTERN)?.groups?.prefix).find(Boolean);
  if (banned) {
    errors.push(`본문에 정형 블록 "${banned}:"을 쓰지 않는다`);
  }
  if (content.length > MAX_BODY_LINES) {
    errors.push(`본문이 ${MAX_BODY_LINES}줄을 넘는다(${content.length}줄)`);
  }

  return errors;
}

function main() {
  const filePath = process.argv[2];
  if (!filePath) {
    console.error("사용: node scripts/check-commit-msg.mjs <커밋-메시지-파일>");
    process.exit(2);
  }
  const errors = checkCommitMessage(fs.readFileSync(filePath, "utf8"));
  if (errors.length > 0) {
    console.error("커밋 메시지가 규칙을 위반한다.\n");
    for (const error of errors) console.error(`  ✗ ${error}`);
    console.error('\n형식: 타입(범위): 한글 제목    예) fix(tracer-api): 태스크 상태 전이 판정을 고친다');
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
