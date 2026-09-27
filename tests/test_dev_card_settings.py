import pytest
from devfeed_core.user_settings import DevCardSettings, UserProfileUpdate
from pydantic import ValidationError


def test_card_design_defaults_and_validated_choices():
    assert DevCardSettings().motion == "static"
    assert DevCardSettings(motion="animated").motion == "animated"
    assert DevCardSettings().theme == "classic"
    assert DevCardSettings().accent == "default"
    for theme in ("classic", "terminal", "aurora", "minimal"):
        value = UserProfileUpdate.model_validate(
            {"dev_card": {"theme": theme, "accent": "rose", "stats": [], "technologies": []}}
        )
        assert value.model_dump(mode="json")["dev_card"] == {
            "motion": "static",
            "theme": theme,
            "accent": "rose",
            "stats": [],
            "technologies": [],
        }
    for settings in (
        {"theme": "unknown"},
        {"motion": "unknown"},
        {"accent": "url(https://example.com)"},
    ):
        with pytest.raises(ValidationError):
            DevCardSettings.model_validate(settings)
