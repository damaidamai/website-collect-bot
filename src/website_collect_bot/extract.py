from __future__ import annotations

import re
from urllib.parse import urlparse


URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
DOMAIN_RE = re.compile(
    r"(?<!@)\b(?:[a-zA-Z0-9-]{1,63}\.)+(?:com|net|org|io|ai|co|cn|dev|app|xyz|info|biz|me|site|top|cc|tv)\b",
    re.IGNORECASE,
)


def normalize_domain(value: str) -> str:
    value = value.strip().rstrip(".,;:!?，。；：！？）)")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc or parsed.path
    host = host.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip("/")


def normalize_url(value: str) -> str | None:
    value = value.strip().rstrip(".,;:!?，。；：！？）)")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = normalize_domain(value)
    if not host:
        return None
    path = parsed.path if parsed.netloc else ""
    return f"{parsed.scheme or 'https'}://{host}{path}"


def extract_domains(text: str) -> list[str]:
    seen: set[str] = set()
    domains: list[str] = []

    for url in URL_RE.findall(text):
        domain = normalize_domain(url)
        if domain and domain not in seen:
            domains.append(domain)
            seen.add(domain)

    for raw_domain in DOMAIN_RE.findall(text):
        domain = normalize_domain(raw_domain)
        if domain and domain not in seen:
            domains.append(domain)
            seen.add(domain)

    return domains


def extract_first_url_for_domain(text: str, domain: str) -> str | None:
    for url in URL_RE.findall(text):
        if normalize_domain(url) == domain:
            return normalize_url(url)
    if domain in text:
        return normalize_url(domain)
    return None
