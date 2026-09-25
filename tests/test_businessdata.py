from intelliw import businessdata


def test_roundtrip(demo_ws):
    data = businessdata.load(demo_ws)
    assert data.business.name == "Maple Street Bakery"
    data.business.tagline = "Changed"
    businessdata.save(demo_ws, data)
    assert businessdata.load(demo_ws).business.tagline == "Changed"
