"""Counts read naturally in both languages: Bosnian's 1 / 2-4 / 5+ forms (11-14 take the last) and
English's 1 / other, chosen by t() from the number it is given (also "1,284" as the app writes it)."""
import pytest

from skyfold import i18n


@pytest.fixture
def bs():
    i18n.set_language("bs")
    yield
    i18n.set_language("en")


@pytest.mark.parametrize("n, want", [
    (1, "1 slika je obrisana u iCloud-u"), (3, "3 slike su obrisane u iCloud-u"),
    (5, "5 slika je obrisano u iCloud-u"), (11, "11 slika je obrisano u iCloud-u"),
    (12, "12 slika je obrisano u iCloud-u"), (21, "21 slika je obrisana u iCloud-u"),
    (24, "24 slike su obrisane u iCloud-u"), (112, "112 slika je obrisano u iCloud-u"),
    ("1,284", "1,284 slike su obrisane u iCloud-u"),
])
def test_bosnian_deletion_counts(bs, n, want):
    assert i18n.t("{n} photos were deleted in iCloud", n=n) == want


def test_bosnian_rows_and_choices(bs):
    assert i18n.t("{photos} photos · {videos} videos · {size}", photos="921", videos=2, size="7 GB") \
        == "921 slika · 2 videa · 7 GB"
    assert i18n.t("{files} files · {size}", files=3, size="1 GB") == "3 fajla · 1 GB"
    assert [i18n.t("{n} days", n=n) for n in (1, 3, 30)] == ["1 dan", "3 dana", "30 dana"]
    assert [i18n.t("{n} months", n=n) for n in (1, 3, 6)] == ["1 mjesec", "3 mjeseca", "6 mjeseci"]
    assert [i18n.t("{n} notes", n=n) for n in (1, 2, 126)] == ["1 bilješka", "2 bilješke", "126 bilješki"]
    assert i18n.t("Finished with {n} errors. Will retry.", n=2) == "Završeno uz 2 greške. Pokušaću ponovo."


def test_english_singulars():
    assert i18n.t("{n} photos were deleted on this PC", n=1) == "1 photo was deleted on this PC"
    assert i18n.t("{n} photos were deleted on this PC", n=2) == "2 photos were deleted on this PC"
    assert i18n.t("{photos} photos · {videos} videos · {size}", photos=1, videos=1, size="1 MB") == "1 photo · 1 video · 1 MB"
    assert i18n.t("{n} items", n="1") == "1 item" and i18n.t("{n} items", n="1,001") == "1,001 items"


def test_every_form_fills_the_same_values():
    """Each form uses exactly the placeholders of its text, so no call can fail or lose a value."""
    import string

    def names(text):
        return {f for _, f, _, _ in string.Formatter().parse(text) if f}

    for table in (i18n._FORMS_BS, i18n._FORMS_EN):
        for text, forms in table.items():
            if callable(forms):
                continue
            for form in forms[1:]:
                if form:
                    assert names(form) == names(text), (text, form)
