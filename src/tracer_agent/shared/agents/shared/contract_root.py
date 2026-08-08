"""계약 저장소가 놓인 자리 하나를 소유해 계약 파일을 읽는 모든 모듈이 같은 뿌리를 쓰게 한다."""

from __future__ import annotations

from pathlib import Path

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
CONTRACT_ROOT = Path(__file__).resolve().parents[5] / "contract"
