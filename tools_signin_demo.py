"""Try the real sign-in window by hand, against a pretend Apple (nothing is sent anywhere).

Any Apple ID and password are accepted (type anything, never your real password).
The pretend Apple waits 8 seconds before "sending" the code, so there is time to close the
window, press Esc, click the panel again, etc. The right code is 123456; any other is refused.

    python tools_signin_demo.py
"""
import sys
import time

from PySide6.QtWidgets import QApplication

from skyfold import auth
from skyfold.ui import dialogs, theme


def fake_start(apple_id, password):
    time.sleep(8)
    return auth.LoginStep(object(), needs_code=True, notice="DEMO: type 123456 (any other code is refused).")


def fake_code(api, code):
    time.sleep(3)
    if code != "123456":
        raise auth.LoginError("DEMO: wrong code, try 123456.")


auth.start_login = fake_start
auth.submit_code = fake_code

app = QApplication(sys.argv)
c = theme.palette("system")
app.setPalette(theme.qpalette(c))
app.setStyleSheet(theme.stylesheet(c))
d = dialogs.LoginDialog("demo@example.invalid")
d.loggedIn.connect(lambda api, who: print("DEMO RESULT: signed in", flush=True))
d.rejected.connect(lambda: print("DEMO RESULT: cancelled" if d._cancelled else "DEMO: window hidden", flush=True))
from PySide6.QtWidgets import QPushButton  # noqa: E402

again = QPushButton("DEMO: Sign in (like clicking the panel)")
again.setWindowTitle("Skyfold sign-in demo")
again.resize(360, 60)
again.clicked.connect(lambda: d._bring_forward() if not d._cancelled and not d._completed
                      else print("DEMO: that attempt is over; restart the demo for a new one", flush=True))
again.show()
d.show()
print("Demo running. Close this console window to end.", flush=True)
sys.exit(app.exec())
