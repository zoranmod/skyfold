"""Build provenance: a real public attestation (GitHub's gh CLI 2.105.0, Sigstore public good) is
accepted for its own workflow and digest, and every forged or mismatched variant is refused."""
import base64
import copy
import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from skyfold import provenance as pv, updater as up

BUNDLE = json.loads((Path(__file__).parent / "data" / "attestation_gh_cli_2.105.0.json").read_text())
DIGEST = "8813a72c899900afaa10e3fec8fbc64b908c89dbfe837e95ed2fa500fc140627"
WF = "https://github.com/cli/cli/.github/workflows/deployment.yml@refs/heads/trunk"
REPO, REF = "https://github.com/cli/cli", "refs/heads/trunk"


def test_a_genuine_attestation_is_accepted():
    pv.check_bundle(BUNDLE, DIGEST, WF, REPO, REF)


@pytest.mark.parametrize("digest,wf,repo,ref", [
    ("0" * 64, WF, REPO, REF),
    (DIGEST, WF.replace("deployment", "other"), REPO, REF),
    (DIGEST, WF, "https://github.com/someone/else", REF),
    (DIGEST, WF, REPO, "refs/tags/v1.0.0"),
])
def test_anything_else_is_refused(digest, wf, repo, ref):
    with pytest.raises(pv.ProvenanceError):
        pv.check_bundle(BUNDLE, digest, wf, repo, ref)


def test_a_changed_statement_is_refused():
    b = copy.deepcopy(BUNDLE)
    p = json.loads(base64.b64decode(b["dsseEnvelope"]["payload"]))
    p["subject"][0]["digest"]["sha256"] = "0" * 64
    b["dsseEnvelope"]["payload"] = base64.b64encode(json.dumps(p).encode()).decode()
    with pytest.raises(pv.ProvenanceError):
        pv.check_bundle(b, "0" * 64, WF, REPO, REF)


def test_a_self_made_certificate_with_the_right_name_is_refused():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sigstore-intermediate")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(datetime.datetime(2025, 1, 1))
            .not_valid_after(datetime.datetime(2030, 1, 1))
            .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(WF)]), False)
            .sign(key, hashes.SHA256()))
    b = copy.deepcopy(BUNDLE)
    vm = b["verificationMaterial"]
    der = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()
    (vm["certificate"] if "certificate" in vm else vm["x509CertificateChain"]["certificates"][0])["rawBytes"] = der
    payload = base64.b64decode(b["dsseEnvelope"]["payload"])
    b["dsseEnvelope"]["signatures"][0]["sig"] = base64.b64encode(
        key.sign(pv._pae(b["dsseEnvelope"]["payloadType"], payload), ec.ECDSA(hashes.SHA256()))).decode()
    with pytest.raises(pv.ProvenanceError):
        pv.check_bundle(b, DIGEST, WF, REPO, REF)


def test_snappy_literal_stream():
    data = b'{"a": 1}'
    raw = bytes([len(data), (len(data) - 1) << 2]) + data
    assert pv.snappy_decompress(raw) == data


def test_without_an_attestation_the_update_is_refused(tmp_path):
    def get(url, **kw):
        if url.startswith(pv.ATTESTATIONS):
            return SimpleNamespace(json=lambda: {"attestations": []})
        return SimpleNamespace(text="a" * 64 + "  x")

    rel = up.Release("9.9.9", "", up.PAGE, up.DOWNLOADS + "v9.9.9/" + up.ZIP_NAME,
                     up.DOWNLOADS + "v9.9.9/" + up.ZIP_NAME + ".sha256")
    with pytest.raises(up.UpdateError):
        up.download(rel, tmp_path, get=get, verify=pv.verify)
    assert not list(tmp_path.iterdir())  # nothing downloaded or staged
