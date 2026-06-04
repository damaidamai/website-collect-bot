from website_collect_bot.extract import canonical_site_key, extract_domains, normalize_domain, normalize_url


def test_extract_domains_from_urls_and_plain_domains() -> None:
    text = "看看 https://www.example.com/a?x=1，还有 demo.org 也要处理。"
    assert extract_domains(text) == ["example.com", "demo.org"]


def test_normalize_domain_strips_www_and_port() -> None:
    assert normalize_domain("https://www.example.com:443/path") == "example.com"


def test_normalize_url_defaults_https() -> None:
    assert normalize_url("example.com/path") == "https://example.com/path"


def test_canonical_site_key_groups_subdomains() -> None:
    assert canonical_site_key("admin.example.com") == "example.com"
    assert canonical_site_key("agent.admin.example.com") == "example.com"
    assert canonical_site_key("www.example.com") == "example.com"
    assert canonical_site_key("admin.example.com.cn") == "example.com.cn"
