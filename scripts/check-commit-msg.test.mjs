import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { checkCommitMessage } from "./check-commit-msg.mjs";

describe("커밋 메시지 검사기", () => {
  it("타입과 범위와 행위 문장을 갖춘 제목을 통과시킨다", () => {
    assert.deepEqual(checkCommitMessage("feat(api): 조회 창구를 세운다"), []);
  });

  it("명사구 제목을 거부한다", () => {
    const errors = checkCommitMessage("feat(api): 조회 창구");
    assert.ok(errors.some((error) => error.includes("행위 문장")));
  });

  it("허용 목록에 없는 범위를 거부한다", () => {
    const errors = checkCommitMessage("feat(nowhere): 창구를 세운다");
    assert.ok(errors.some((error) => error.includes("범위")));
  });

  it("저장소가 만들어진 경위를 가리키는 어휘를 거부한다", () => {
    const errors = checkCommitMessage("feat(api): 기존 창구를 세운다");
    assert.ok(errors.some((error) => error.includes("기존")));
  });

  it("은유와 구어를 거부하고 대신 쓸 동사를 알린다", () => {
    const errors = checkCommitMessage("refactor(api): 중계 경로를 걷어낸다");
    assert.ok(errors.some((error) => error.includes("제거한다")));
  });

  it("본문의 정형 블록을 거부한다", () => {
    const errors = checkCommitMessage("feat(api): 조회 창구를 세운다\n\nRelated: 무언가");
    assert.ok(errors.some((error) => error.includes("정형 블록")));
  });
});
