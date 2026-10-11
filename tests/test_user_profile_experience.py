"""Experience preferences remain private, optional and owned by the reader."""

import pytest
from devfeed_core.models import UserAccount
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("level", ["aspiring", "entry_level", "mid_level", "senior"])
def test_experience_is_private_optional_and_account_owned(user_data, database, level):
    client, current, first, second, _ = user_data
    path = "/v1/user/settings/profile"
    saved = client.put(path, json={"username": "experience-reader", "experience_level": level})
    assert saved.status_code == 200
    assert saved.json()["experience_level"] == level
    assert client.get(path).json()["experience_level"] == level
    assert client.put(path, json={"bio": "Builds things"}).json()["experience_level"] == level
    public = client.get("/v1/user/profiles/experience-reader")
    assert public.status_code == 200
    assert "experience_level" not in public.json()
    assert "experience_level" not in public.json()["dev_card"]
    with database() as session:
        assert session.get(UserAccount, first).profile["experience_level"] == level
        assert "experience_level" not in session.get(UserAccount, second).profile
    current.user_id, current.subject = str(second), "user-b"
    assert client.get(path).json()["experience_level"] is None
    current.user_id, current.subject = str(first), "user-a"
    cleared = client.put(path, json={"experience_level": None})
    assert cleared.status_code == 200
    assert cleared.json()["experience_level"] is None
    assert client.get(path).json()["experience_level"] is None
    with database() as session:
        assert session.get(UserAccount, first).profile["experience_level"] is None


@pytest.mark.parametrize("level", ["lead", "", "Senior", 3, ["senior"]])
def test_invalid_experience_cannot_overwrite_saved_preference(user_data, level):
    client = user_data[0]
    path = "/v1/user/settings/profile"
    assert client.put(path, json={"experience_level": "senior"}).status_code == 200
    assert client.put(path, json={"experience_level": level}).status_code == 422
    assert client.get(path).json()["experience_level"] == "senior"
