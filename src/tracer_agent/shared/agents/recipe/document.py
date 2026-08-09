"""recipes 색인 문서의 유일한 정의와 배출 한 번이 읽는 행의 수를 소유한다."""

from __future__ import annotations

from ..shared.instant import iso
from ..shared.json_view import JsonObject, JsonValue
from .models import Recipe

# 배출 한 번이 읽는 아웃박스 행의 수이며 계약의 batchSize 와 같다.
SEARCH_OUTBOX_BATCH_SIZE = 100


def touched_file_paths(touched_files: list[JsonValue]) -> list[str]:
    """원장의 객체 배열에서 색인이 받는 경로 문자열만 낸다."""
    paths: list[str] = []
    for entry in touched_files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if isinstance(path, str):
            paths.append(path)
    return paths


def build_recipe_document(recipe: Recipe) -> JsonObject:
    """색인에 실리는 칸의 정본이며 계약의 wire/search.index.json 이 칸을 갖는다."""
    return {
        "userId": recipe.user_id,
        "title": recipe.title,
        "intent": recipe.intent,
        "description": recipe.description,
        "useWhen": recipe.use_when,
        "summaryMd": recipe.summary_md,
        "touchedFiles": list(touched_file_paths(recipe.touched_files)),
        "status": recipe.status,
        "userEdited": recipe.user_edited,
        "rev": recipe.rev,
        "updatedAt": iso(recipe.updated_at),
    }
