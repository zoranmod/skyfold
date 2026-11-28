"""The app's wording. Skyfold is English only; every text still goes through t(), which fills
in the values and picks the singular where a count is 1.

Usage: t("Downloading {i} of {n}", i=3, n=10).
"""

from __future__ import annotations

LANGUAGES = {"en": "English"}


from .safelog import Text  # noqa: E402


def set_language(code: str) -> None:
    """Kept for old settings files and callers: whatever is asked for ("bs" from the time the
    app also spoke Bosnian), the app stays English."""


def language() -> str:
    return "en"


def t(text: str, **kw) -> str:
    s = text
    forms = _FORMS_EN.get(text)
    if forms and kw:
        s = forms(kw, s) if callable(forms) else _form(kw.get(forms[0]), forms[1], forms[2], s)
    return Text(s.format(**kw) if kw else s)  # marked as Skyfold's own wording (see safelog)


def _count(value) -> int | None:
    """The number a count was given as: 7, or "1,284" / "1.284" / "1 284" as the app writes them."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits and len(digits) < 16 else None


def _form(value, one: str | None, few: str | None, many: str) -> str:
    """The wording for this number: one form for 1, the other for everything else. (`few` is
    unused in English; the shape of the tables allows a language with a third form.)"""
    n = _count(value)
    if n is None:
        return many
    return one if (n == 1 and one) else many


def _word(n, one: str, few: str, many: str) -> str:
    return _form(n, one, few, many)


# Wordings that change with the number in them: text -> (the count's name, the form for 1, None),
# or a function of the values for texts with two counts. The text itself is the form for every
# other number.
def _photos_videos_en(kw, s):
    return (_word(kw.get("photos"), "{photos} photo", None, "{photos} photos") + " · "
            + _word(kw.get("videos"), "{videos} video", None, "{videos} videos") + " · {size}")


_FORMS_EN = {
    "{n} items are waiting for your OK to be deleted.": ("n", "{n} item is waiting for your OK to be deleted.", None),
    "Finished with {n} errors. Will retry.": ("n", "Finished with {n} error. Will retry.", None),
    "iCloud Drive: finished with {n} errors. Will retry.": ("n", "iCloud Drive: finished with {n} error. Will retry.", None),
    "Notes: finished with {n} errors. Will retry.": ("n", "Notes: finished with {n} error. Will retry.", None),
    "Followed {n} files moved inside the folder": ("n", "Followed {n} file moved inside the folder", None),
    "Photos: {n} missing files are kept until {folder} can be read. Fix its access rights, or move that folder "
    "out of the photos folder.": (
        "n", "Photos: {n} missing file is kept until {folder} can be read. Fix its access rights, or move that "
             "folder out of the photos folder.", None),
    "Photos: {n} missing files are kept for now, part of the photos folder couldn't be read": (
        "n", "Photos: {n} missing file is kept for now, part of the photos folder couldn't be read", None),
    "The first download is done: {n} files. From now on only changes are synced.": (
        "n", "The first download is done: {n} file. From now on only changes are synced.", None),
    "The first sync is done: {n} files downloaded. From now on only changes are synced.": (
        "n", "The first sync is done: {n} file downloaded. From now on only changes are synced.", None),
    "Drive: {name} is no longer synced; {n} files that aren't in iCloud were kept on this PC": (
        "n", "Drive: {name} is no longer synced; {n} file that isn't in iCloud was kept on this PC", None),
    "{prefix}{i} of {n} files": ("n", "{prefix}{i} of {n} file", None),
    "{n} photos were deleted in iCloud": ("n", "{n} photo was deleted in iCloud", None),
    "{n} photos were deleted on this PC": ("n", "{n} photo was deleted on this PC", None),
    "Drive: uploading, {i} of {n} files done": ("n", "Drive: uploading, {i} of {n} file done", None),
    "{files} files · {size}": ("files", "{files} file · {size}", None),
    "{photos} photos · {videos} videos · {size}": _photos_videos_en,
    "Notes: moved {n} notes to other folders": ("n", "Notes: moved {n} note to another folder", None),
    "{n} days": ("n", "{n} day", None),
    "{n} items": ("n", "{n} item", None),
    "{n} months": ("n", "{n} month", None),
    "{n} notes": ("n", "{n} note", None),
    "Deleting {n} items…": ("n", "Deleting {n} item…", None),
    "{n} items couldn't be deleted: {error}": ("n", "{n} item couldn't be deleted: {error}", None),
}
