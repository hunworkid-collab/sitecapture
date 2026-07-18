"""Google site 검색식 생성."""

from __future__ import annotations

from collections.abc import Iterable

def normalize_domains(values: Iterable[str]) -> tuple[str, ...]:
    domains: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        domain = raw_value.strip().lower()
        if not domain:
            continue
        if (
            any(character.isspace() for character in domain)
            or any(character in domain for character in "/:#?")
            or domain.startswith(".")
            or domain.endswith(".")
            or ".." in domain
        ):
            raise ValueError(
                f"도메인은 실제 도메인 형식으로 입력해야 합니다: {raw_value}"
            )
        if domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
    return tuple(domains)


def build_query(
    domain: str,
    keyword: str,
    *,
    exact_phrase: bool = False,
) -> str:
    domain = domain.strip().lower()
    keyword = " ".join(keyword.strip().split())
    if not domain:
        raise ValueError("도메인이 비어 있습니다.")
    if not keyword:
        raise ValueError("키워드가 비어 있습니다.")

    # exact 모드에서는 사용자가 입력한 큰따옴표가 검색식을 깨지 않게 제거한다.
    search_term = keyword.replace('"', " ")
    search_term = " ".join(search_term.split())
    if exact_phrase:
        search_term = f'"{search_term}"'

    return f"site:{domain} {search_term}"
