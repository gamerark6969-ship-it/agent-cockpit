from app.config import Settings


def test_frontend_origin_is_allowed_even_when_cors_setting_is_stale():
    settings = Settings(
        CORS_ORIGINS="http://localhost:5173",
        AUTOMATION_FRONTEND_URL="https://agent-cockpit-pwa.onrender.com/studio",
    )

    assert settings.cors_origins == [
        "http://localhost:5173",
        "https://agent-cockpit-pwa.onrender.com",
    ]


def test_duplicate_frontend_origin_is_not_added_twice():
    settings = Settings(
        CORS_ORIGINS="https://agent-cockpit-pwa.onrender.com/",
        AUTOMATION_FRONTEND_URL="https://agent-cockpit-pwa.onrender.com",
    )

    assert settings.cors_origins == ["https://agent-cockpit-pwa.onrender.com"]
