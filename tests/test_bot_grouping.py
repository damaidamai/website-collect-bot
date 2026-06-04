from website_collect_bot.bot import group_domains_by_site_key


def test_group_domains_by_site_key() -> None:
    grouped = group_domains_by_site_key(
        [
            "admin.example.com",
            "agent.example.com",
            "other.test.com",
        ]
    )

    assert grouped == {
        "example.com": ["admin.example.com", "agent.example.com"],
        "test.com": ["other.test.com"],
    }
