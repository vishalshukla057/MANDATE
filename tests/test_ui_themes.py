import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_ui_default_serves_light_mode():
    res = client.get("/")
    assert res.status_code == 200
    assert "PayPal Developer" in res.text or "PayPal Global Design System" in res.text or "ANIMATION_63" in res.text
    assert "Dark Mode" in res.text

def test_ui_dark_mode_route():
    res = client.get("/ui/dark")
    assert res.status_code == 200
    assert "MANDATE" in res.text or "Sovereign" in res.text or "ANIMATION_40" in res.text
    assert "Light Mode" in res.text
    assert "copilot_theme" in res.cookies
    # Verify text style is same as light mode (Inter & JetBrains Mono)
    assert "family=Inter" in res.text
    assert "family=JetBrains+Mono" in res.text
    assert "font-display-hero" in res.text

def test_ui_light_mode_route():
    res = client.get("/ui/light")
    assert res.status_code == 200
    assert "PayPal Developer" in res.text or "ANIMATION_63" in res.text
    assert "Dark Mode" in res.text
    assert "copilot_theme" in res.cookies

def test_ui_query_param_theme_switch():
    res_dark = client.get("/ui?theme=dark")
    assert res_dark.status_code == 200
    assert "ANIMATION_40" in res_dark.text or "Sovereign" in res_dark.text

    res_light = client.get("/ui?theme=light")
    assert res_light.status_code == 200
    assert "ANIMATION_63" in res_light.text or "PayPal Developer" in res_light.text

