"""AntiDetect dataclass + sticky-assignment behaviour."""

import json
from pathlib import Path

from steadfast.anti_detect import COMMON_VIEWPORTS, DEFAULT_USER_AGENTS, AntiDetect, ProxyInfo

# ------------------------------------------------------- ProxyInfo


def test_proxy_info_url_with_auth():
    p = ProxyInfo("http", "host.example", 8080, "user", "pass")
    assert p.url == "http://user:pass@host.example:8080"


def test_proxy_info_url_without_auth():
    p = ProxyInfo("http", "host.example", 8080)
    assert p.url == "http://host.example:8080"


def test_proxy_info_to_playwright_dict_no_auth():
    p = ProxyInfo("http", "host.example", 8080)
    pw = p.to_playwright_proxy()
    assert pw == {"server": "http://host.example:8080"}


def test_proxy_info_to_playwright_dict_with_auth():
    p = ProxyInfo("socks5", "host.example", 1080, "user", "secret")
    pw = p.to_playwright_proxy()
    assert pw["server"] == "socks5://host.example:1080"
    assert pw["username"] == "user"
    assert pw["password"] == "secret"


def test_proxy_info_parse_various_forms():
    assert ProxyInfo.parse("host:8080") == ProxyInfo("http", "host", 8080)
    assert ProxyInfo.parse("http://host:8080") == ProxyInfo("http", "host", 8080)
    assert ProxyInfo.parse("socks5://u:p@host:1080") == ProxyInfo(
        "socks5", "host", 1080, "u", "p"
    )
    assert ProxyInfo.parse("https://user:secret@h.example:443") == ProxyInfo(
        "https", "h.example", 443, "user", "secret"
    )


# ------------------------------------------------------- AntiDetect base behaviour


def test_default_construction_uses_default_uas():
    ad = AntiDetect()
    assert ad.proxies == []
    assert ad.user_agents == DEFAULT_USER_AGENTS


def test_viewport_is_from_known_pool():
    ad = AntiDetect()
    v = ad.get_viewport()
    assert v in COMMON_VIEWPORTS
    # Confirm we got a *copy* — mutating must not affect the pool.
    v["width"] = 0
    assert {"width": 0} not in COMMON_VIEWPORTS


# ------------------------------------------------------- Stickiness


def test_user_agent_is_sticky_per_account_key():
    ad = AntiDetect()
    ua1 = ad.get_user_agent("twitter_main")
    ua2 = ad.get_user_agent("twitter_main")
    assert ua1 == ua2
    # Different key may give different UA (not guaranteed but very likely
    # with enough distinct keys; we don't assert inequality — only equality).


def test_user_agent_uses_known_pool():
    ad = AntiDetect()
    ua = ad.get_user_agent("foo")
    assert ua in DEFAULT_USER_AGENTS


def test_proxy_assignment_is_sticky_per_key():
    proxies = [
        ProxyInfo("http", "a.example", 1),
        ProxyInfo("http", "b.example", 2),
        ProxyInfo("http", "c.example", 3),
    ]
    ad = AntiDetect(proxies=proxies)
    p1 = ad.get_proxy_for_key("twitter_main")
    p2 = ad.get_proxy_for_key("twitter_main")
    assert p1 is p2  # cached reference, same proxy instance


def test_proxy_assignment_returns_none_when_empty():
    ad = AntiDetect()
    assert ad.get_proxy_for_key("twitter_main") is None


def test_is_proxy_in_pool():
    pool = [ProxyInfo("http", "a.example", 1), ProxyInfo("http", "b.example", 2)]
    ad = AntiDetect(proxies=pool)
    assert ad.is_proxy_in_pool(ProxyInfo("http", "a.example", 1)) is True
    assert ad.is_proxy_in_pool(ProxyInfo("http", "z.example", 99)) is False


def test_get_proxy_by_dict_roundtrip():
    ad = AntiDetect()
    raw = {
        "protocol": "socks5",
        "host": "host.example",
        "port": 1080,
        "username": "u",
        "password": "p",
    }
    p = ad.get_proxy_by_dict(raw)
    assert p == ProxyInfo("socks5", "host.example", 1080, "u", "p")


def test_get_proxy_by_dict_handles_none():
    ad = AntiDetect()
    assert ad.get_proxy_by_dict(None) is None
    assert ad.get_proxy_by_dict({}) is None


# ------------------------------------------------------- File loader


def test_from_proxy_file_parses_and_skips_comments(tmp_path: Path):
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text(
        "\n".join(
            [
                "# comment line",
                "",
                "host1:8080",
                "http://host2:8080",
                "  # indented comment",
                "socks5://user:pass@host3:1080",
            ]
        )
    )
    ad = AntiDetect.from_proxy_file(proxy_file)
    assert len(ad.proxies) == 3
    assert ad.proxies[0].host == "host1"
    assert ad.proxies[1].host == "host2"
    assert ad.proxies[2] == ProxyInfo("socks5", "host3", 1080, "user", "pass")


def test_from_proxy_file_missing_returns_empty():
    ad = AntiDetect.from_proxy_file("/nonexistent/path/that/does/not/exist.txt")
    assert ad.proxies == []


# ------------------------------------------------------- replace_dead_proxy


def test_replace_dead_proxy_updates_in_memory_and_disk(tmp_path: Path):
    proxies = [
        ProxyInfo("http", "good.example", 1),
        ProxyInfo("http", "bad.example", 2),
    ]
    ad = AntiDetect(proxies=proxies)
    # Pre-assign the bad one to a key
    ad._proxy_assignments["acct"] = proxies[1]

    fp_path = tmp_path / "fingerprint.json"
    fp_path.write_text(json.dumps({"viewport": {"width": 1920, "height": 1080}}))

    new = ad.replace_dead_proxy("acct", proxies[1], fp_path)
    assert new is not None
    assert new.host == "good.example"
    assert ad._proxy_assignments["acct"] is new

    saved = json.loads(fp_path.read_text())
    assert saved["proxy"]["host"] == "good.example"


def test_replace_dead_proxy_returns_none_when_no_alternative(tmp_path: Path):
    only_one = [ProxyInfo("http", "lonely.example", 1)]
    ad = AntiDetect(proxies=only_one)
    fp_path = tmp_path / "fingerprint.json"
    new = ad.replace_dead_proxy("acct", only_one[0], fp_path)
    assert new is None
