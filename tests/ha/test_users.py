"""Names for keypad users (users.py)."""

from __future__ import annotations

from custom_components.texecom.users import format_user_names, parse_user_names, user_name, with_user_name


def test_reading_names():
    assert parse_user_names("1 = Alex\n\n  User 3: Sam \n10-Pat") == {"1": "Alex", "3": "Sam", "10": "Pat"}
    assert parse_user_names("") == {}
    assert parse_user_names("Sam") is None  # no number
    assert parse_user_names("3 =") is None  # no name


def test_names_shown_for_editing():
    names = {"10": "Pat", "3": "Sam", "1": "Alex"}
    assert format_user_names(names) == "1 = Alex\n3 = Sam\n10 = Pat"
    assert parse_user_names(format_user_names(names)) == names


def test_names_in_changed_by():
    options = {"user_names": {"3": "Sam"}}
    assert with_user_name(options, "User 3") == "Sam"
    assert with_user_name(options, "User 4") == "User 4"
    assert with_user_name(options, "Kitchen") == "Kitchen"  # a zone
    assert with_user_name(options, "Home Assistant") == "Home Assistant"
    assert with_user_name({}, None) is None
    assert user_name(options, 3) == "Sam" and user_name({}, 7) == "User 7"
