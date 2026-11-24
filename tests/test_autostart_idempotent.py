"""The autostart value is written only when it changes, never on every start."""

import sys

import pytest

from skyfold import config


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_autostart_is_written_only_when_it_changes(monkeypatch):
    import winreg

    store, writes = {}, []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *a: Key())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda k, n: (store[n], 1) if n in store
                        else (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(winreg, "SetValueEx", lambda k, n, r, t, v: (writes.append(v), store.update({n: v})))
    monkeypatch.setattr(winreg, "DeleteValue", lambda k, n: store.pop(n))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    config.set_start_with_windows(True)
    config.set_start_with_windows(True)
    assert len(writes) == 1 and writes[0].endswith('" --tray')
    config.set_start_with_windows(False)
    assert config.APP_ID not in store


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_an_entry_for_another_copy_shows_as_off_and_is_never_rewritten_at_start(monkeypatch):
    import inspect
    import winreg

    from skyfold.app import Controller

    old = r'"C:\old place\Skyfold.exe" --tray'
    store = {config.APP_ID: old}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *a: Key())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda k, n: (store[n], 1) if n in store
                        else (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert config.starts_with_windows(True) is False  # Settings shows it off: switching it on fixes it
    store[config.APP_ID] = f'"{sys.executable}" --tray'
    assert config.starts_with_windows(True) is True
    assert config.starts_with_windows(False) is False
    # Re-adding itself to Run at every start looks like malware to antivirus programs.
    assert "set_start_with_windows" not in inspect.getsource(Controller.start)
