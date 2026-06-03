from website_collect_bot.extract import extract_domains, normalize_domain, normalize_url


def test_extract_domains_from_urls_and_plain_domains() -> None:
    text = "看看 https://www.example.com/a?x=1，还有 demo.org 也要处理。"
    assert extract_domains(text) == ["example.com", "demo.org"]


def test_normalize_domain_strips_www_and_port() -> None:
    assert normalize_domain("https://www.example.com:443/path") == "example.com"


def test_normalize_url_defaults_https() -> None:
    assert normalize_url("example.com/path") == "https://example.com/path"
