"""Values that come from Apple reach the log only as booleans, numbers or words from an explicit list."""

from skyfold.safelog import value


def test_value_never_lets_free_text_through():
    assert value(True) == "True" and value(None) == "None" and value(3) == "3"
    assert value("sms", ("sms", "voice")) == "sms"
    assert value("private-auth-response") == "invalid"  # looks like a label, but isn't on any list
    assert value("private-auth-response", ("sms",)) == "invalid"
    assert value(["a", "b"]) == "list of 2" and value({"k": "v"}) == "dict of 1"
    assert value(b"abc") == "3 bytes" and value(object()) == "object"
