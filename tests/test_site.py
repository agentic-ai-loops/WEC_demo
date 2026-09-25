from intelliw import site


def test_build_renders_pages_and_assets(demo_ws):
    site.build(demo_ws)
    index = (demo_ws.site_dir / "index.html").read_text()
    assert "Maple Street Bakery" in index
    assert (demo_ws.site_dir / "assets" / "style.css").exists()
    assert not (demo_ws.site_dir / "_base.html").exists()
