"""1.0.21: Settings opens tall enough for its longest page; hints sit under their own setting."""
from skyfold.config import Settings
from skyfold.ui import dialogs, theme
from tests.test_review27_codex_ui_probes import qapp  # noqa: F401


def test_settings_open_without_scrolling(qapp, monkeypatch):  # noqa: F811
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    monkeypatch.setattr(type(screen), "availableGeometry", lambda self: QRect(0, 0, 1920, 1040))
    dialog = dialogs.SettingsDialog(Settings(), theme.LIGHT)
    try:
        dialog.show()
        qapp.processEvents()
        for i in range(dialog.stack.count()):
            page = dialog.stack.widget(i)
            dialog.stack.setCurrentIndex(i)
            qapp.processEvents()
            assert page.verticalScrollBar().maximum() == 0, (i, page.verticalScrollBar().maximum(), dialog.size())
    finally:
        dialog.close()


def test_download_limit_hint_follows_the_download_limit(qapp):  # noqa: F811
    from PySide6.QtWidgets import QLabel

    dialog = dialogs.SettingsDialog(Settings(), theme.LIGHT)
    try:
        body = dialog.stack.widget(0).widget()
        texts = [w.text() for w in body.findChildren(QLabel)]
        limit = texts.index("Download speed limit")
        hint = next(i for i, x in enumerate(texts) if x.startswith("Keeps some of your connection free"))
        upload = texts.index("Upload send buffer")
        assert limit < hint < upload, texts[limit:upload + 1]
    finally:
        dialog.close()


def test_sign_in_fields_have_screen_reader_names(qapp):  # noqa: F811
    from skyfold.ui.dialogs import LoginDialog

    dlg = LoginDialog("someone@example.invalid")
    try:
        assert dlg.code.accessibleName() == "Verification code"
        assert dlg.email.accessibleName() and dlg.password.accessibleName()
    finally:
        dlg.close()
