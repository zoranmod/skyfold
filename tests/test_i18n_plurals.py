"""Counts read naturally: one form for 1, another for everything else, chosen by t() from the
number it is given (also "1,284" as the app writes it)."""
from skyfold import i18n


def test_english_singulars():
    assert i18n.t("{n} photos were deleted on this PC", n=1) == "1 photo was deleted on this PC"
    assert i18n.t("{n} photos were deleted on this PC", n=2) == "2 photos were deleted on this PC"
    assert i18n.t("{photos} photos · {videos} videos · {size}", photos=1, videos=1, size="1 MB") == "1 photo · 1 video · 1 MB"
    assert i18n.t("{n} items", n="1") == "1 item" and i18n.t("{n} items", n="1,001") == "1,001 items"


def test_only_one_itself_is_singular():
    """21, 101 and 1,001 are plural in English (they were singular in the Bosnian the app once had)."""
    assert [i18n.t("{n} days", n=n) for n in (1, 2, 11, 21, 101)] == ["1 day", "2 days", "11 days", "21 days", "101 days"]
    assert [i18n.t("{n} notes", n=n) for n in (1, 21, "1,001")] == ["1 note", "21 notes", "1,001 notes"]


def test_every_form_fills_the_same_values():
    """Each form uses exactly the placeholders of its text, so no call can fail or lose a value."""
    import string

    def names(text):
        return {f for _, f, _, _ in string.Formatter().parse(text) if f}

    for text, forms in i18n._FORMS_EN.items():
        if callable(forms):
            continue
        for form in forms[1:]:
            if form:
                assert names(form) == names(text), (text, form)
