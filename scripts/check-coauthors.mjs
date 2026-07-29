#!/usr/bin/env node
// 커밋 훅은 --file로 작성 중인 메시지를, CI는 --range로 커밋 범위를 허용 목록과 대조한다.

import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// 사람 기여자는 저장소 소유자뿐이므로 도구가 붙이는 트레일러만 예외로 둔다.
const ALLOWED_EMAILS = new Set(["noreply@anthropic.com", "omx@oh-my-codex.dev"]);
const COAUTHOR_PATTERN = /^Co-authored-by:\s*(?<name>.*?)\s*<(?<email>[^>]+)>\s*$/gim;

/** 메시지에서 허용되지 않은 공동 작성자를 찾는다. */
export function findDisallowedCoauthors(message) {
  return [...message.matchAll(COAUTHOR_PATTERN)]
    .map((match) => ({ name: match.groups.name, email: match.groups.email.toLowerCase() }))
    .filter((coauthor) => !ALLOWED_EMAILS.has(coauthor.email));
}

function messagesOf(range) {
  const shas = execFileSync("git", ["log", range, "--format=%H"], { encoding: "utf8" })
    .split("\n")
    .filter(Boolean);
  return shas.map((sha) => ({
    sha,
    message: execFileSync("git", ["log", "-1", "--format=%B", sha], { encoding: "utf8" }),
  }));
}

function main() {
  const [flag, value] = process.argv.slice(2);
  if (flag !== "--file" && flag !== "--range") {
    console.error("사용: node scripts/check-coauthors.mjs --file <메시지-파일> | --range <커밋-범위>");
    process.exit(2);
  }

  const commits = flag === "--file"
    ? [{ sha: null, message: fs.readFileSync(value, "utf8") }]
    : messagesOf(value);

  const violations = commits.flatMap((commit) =>
    findDisallowedCoauthors(commit.message).map((coauthor) => ({ ...coauthor, sha: commit.sha })),
  );

  if (violations.length > 0) {
    console.error("허용되지 않은 공동 작성자 트레일러가 있다.\n");
    for (const violation of violations) {
      const where = violation.sha ? `${violation.sha.slice(0, 8)} ` : "";
      console.error(`  ✗ ${where}${violation.name} <${violation.email}>`);
    }
    console.error(`\n허용: ${[...ALLOWED_EMAILS].join(", ")}`);
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
