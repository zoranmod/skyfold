"""Where Skyfold's own requests may go: Apple's iCloud services and GitHub's update servers.

Every HTTP request Skyfold makes goes through Requests (pyicloud's session, the transfers and the
updater). `install()` checks the address of each one, including each step of a redirect, before
anything is sent; any other address is refused with `NetworkPolicyBlocked`.

What is checked is the address the request is for. The connection itself may still go through a
proxy the user set up, to an IP address that DNS gives for that name, as in any program. Links
the user opens (iCloud.com, GitHub, Google Maps for Find My, links in notes) open in the browser
or default app, not here.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Apple's iCloud: sign-in (idmsa.apple.com), setup, the services Apple names at sign-in
# (pXX-ckdatabasews.icloud.com, ...) and their content servers. Apple's own domains only; a name
# matches only whole labels (idmsa.apple.com.example.org is not Apple's).
APPLE_DOMAINS = ("apple.com", "icloud.com", "icloud.com.cn", "icloud-content.com", "icloud-content.com.cn",
                 "apple-cloudkit.com", "cdn-apple.com", "apzones.com")
# Skyfold's own updates: exactly these (the updater also checks each one, and its repository).
GITHUB_HOSTS = frozenset({"api.github.com", "github.com", "objects.githubusercontent.com",
                          "release-assets.githubusercontent.com", "raw.githubusercontent.com",
                          "tmaproduction.blob.core.windows.net"})  # GitHub's build attestations

_blocked = 0
_lock = threading.Lock()


class NetworkPolicyBlocked(Exception):
    """A request to an address that isn't Apple's iCloud or GitHub's update servers: never sent.
    (Not a Requests error, so nothing treats it as "offline, try again".)"""

    def __init__(self) -> None:
        from .i18n import t

        super().__init__(t("Skyfold blocked a connection to an address it doesn't know. "
                           "Check for a Skyfold update, then try again."))


def _host_ok(host: str, apple: bool, github: bool) -> bool:
    if apple and any(host == d or host.endswith("." + d) for d in APPLE_DOMAINS):
        return True
    return github and host in GITHUB_HOSTS


def destination(url: str) -> str:
    """'apple', 'github' or '' (not allowed) for an address. Only plain HTTPS to a host name on the
    usual port: no user name or password in it, no IP address, no other scheme."""
    try:
        p = urlsplit(url)
        host = p.hostname or ""
        port = p.port
    except (ValueError, TypeError, AttributeError):
        return ""
    if p.scheme.lower() != "https" or port not in (None, 443) or "@" in p.netloc or not host:
        return ""
    host = host.rstrip(".").lower()
    if not host.isascii() or any(not label for label in host.split(".")):
        return ""
    try:
        ipaddress.ip_address(host)
        return ""  # a bare IP address: nobody's name
    except ValueError:
        pass
    if _host_ok(host, True, False):
        return "apple"
    if _host_ok(host, False, True):
        return "github"
    return ""


def allowed(url: str, *, apple: bool = True, github: bool = True) -> bool:
    kind = destination(url)
    return bool(kind) and ((kind == "apple" and apple) or (kind == "github" and github))


def check(url: str, *, apple: bool = True, github: bool = True) -> None:
    """Raise NetworkPolicyBlocked unless the address is allowed. The address itself is never logged
    (it can hold a file name, an id or an access token)."""
    global _blocked
    if allowed(url, apple=apple, github=github):
        return
    with _lock:
        _blocked += 1
        n = _blocked
    if n in (1, 10, 100) or n % 1000 == 0:
        log.warning("Blocked %d request(s) to an address that isn't Apple's iCloud or GitHub's updates", n)
    from . import netdiag

    netdiag.blocked()
    raise NetworkPolicyBlocked()


_INSTALLED = False


def install() -> None:
    """Check every request any Requests session in Skyfold sends (redirects included: Requests sends
    each step through Session.send). An iCloud session (pyicloud's) never goes to GitHub."""
    global _INSTALLED
    if _INSTALLED:
        return
    import requests

    original = requests.Session.send

    def send(self, request, **kwargs):
        from . import netdiag

        icloud = type(self).__name__ == "PyiCloudSession" or getattr(self, "_skyfold_icloud", False)
        check(request.url, apple=True, github=not icloud)
        if kwargs.get("verify") is False:
            # the server's certificate is always checked (a pyicloud session made without saying
            # so defaults to not checking it; Skyfold's own pass verify=True, and this keeps it so)
            kwargs["verify"] = True
        return netdiag.timed(original, self, request, kwargs)

    send._skyfold_policy = True  # type: ignore[attr-defined]
    requests.Session.send = send  # type: ignore[method-assign]
    _INSTALLED = True
