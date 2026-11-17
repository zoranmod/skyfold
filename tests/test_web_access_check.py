"""Apple's web-access (PCS) check failing with a server error means: sign in again."""
from types import SimpleNamespace

from pyicloud.exceptions import PyiCloudAPIResponseException

from skyfold.remote import AuthRequired, _translate


def _err(code, url=""):
    return PyiCloudAPIResponseException("Internal Server Error", code, SimpleNamespace(url=url, text=""))


def test_a_failing_web_access_check_asks_for_a_new_sign_in():
    exc = _err(500, "https://setup.icloud.com/setup/ws/1/requestWebAccessState?clientId=x")
    assert isinstance(_translate(exc), AuthRequired)


def test_the_check_is_also_recognised_by_where_it_failed():
    def _check_pcs_consent():
        raise PyiCloudAPIResponseException("Internal Server Error", 500)

    try:
        _check_pcs_consent()
    except PyiCloudAPIResponseException as exc:
        assert isinstance(_translate(exc), AuthRequired)


def test_other_server_errors_stay_what_they_are():
    exc = _err(500, "https://p00-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/records/query")
    assert _translate(exc) is exc
    assert _translate(_err(404, "https://setup.icloud.com/setup/ws/1/requestWebAccessState")) is not None
    assert not isinstance(_translate(_err(404, "https://setup.icloud.com/setup/ws/1/requestWebAccessState")),
                          AuthRequired)
