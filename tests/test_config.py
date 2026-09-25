from intelliw.config import Settings


def test_urls_use_loopback_for_wildcard_host():
    s = Settings(graphql_host="0.0.0.0", graphql_port=9001, mcp_host="example.com", mcp_port=9002)
    assert s.graphql_url == "http://127.0.0.1:9001/graphql"
    assert s.mcp_url == "http://example.com:9002/mcp"
