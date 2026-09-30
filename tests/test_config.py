from intelliw.config import Settings


def test_urls_use_loopback_for_wildcard_host():
    s = Settings(host="0.0.0.0", port=8000)
    assert s.graphql_url == "http://127.0.0.1:8000/graphql"
    assert s.mcp_url == "http://127.0.0.1:8000/mcp"
    assert s.health_url == "http://127.0.0.1:8000/health"
    assert Settings(host="example.com", port=9000).mcp_url == "http://example.com:9000/mcp"


def test_defaults_match_svr_start():
    s = Settings()
    assert (s.host, s.port, str(s.run_dir)) == ("0.0.0.0", 8000, "run")
