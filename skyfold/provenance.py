"""Proof that an update was built by Skyfold's own release workflow (GitHub artifact attestations).

The release workflow asks GitHub to attest the zip it built: a short-lived certificate from Sigstore's
public Fulcio CA names the workflow (repository, file and tag) that ran, and signs a statement with the
zip's SHA-256. Before an update is unpacked, Skyfold fetches the attestations GitHub keeps for that
SHA-256 and accepts the zip only when one of them

- is signed by a certificate that Sigstore's CA issued (the CA's certificates are built in here),
- names exactly Skyfold's release workflow for exactly this release's tag, run by GitHub Actions,
- and states this very SHA-256.

So a zip and checksum put into a release by hand - or by anyone who isn't that workflow - are refused.
(Certificate lifetimes and the transparency log aren't checked here; the CA signature, the workflow
identity and the digest are.)
"""
from __future__ import annotations

import base64
import json
from typing import Callable

from .i18n import t

REPO = "zoranmod/skyfold"
WORKFLOW = ".github/workflows/build.yml"
ATTESTATIONS = f"https://api.github.com/repos/{REPO}/attestations/sha256:"
BUNDLE_HOSTS = {"tmaproduction.blob.core.windows.net"}  # where GitHub serves attestation bundles

# Sigstore's public-good Fulcio CA (https://fulcio.sigstore.dev/api/v1/rootCert), valid until 2031.
FULCIO_CHAIN = b"""-----BEGIN CERTIFICATE-----
MIICGjCCAaGgAwIBAgIUALnViVfnU0brJasmRkHrn/UnfaQwCgYIKoZIzj0EAwMw
KjEVMBMGA1UEChMMc2lnc3RvcmUuZGV2MREwDwYDVQQDEwhzaWdzdG9yZTAeFw0y
MjA0MTMyMDA2MTVaFw0zMTEwMDUxMzU2NThaMDcxFTATBgNVBAoTDHNpZ3N0b3Jl
LmRldjEeMBwGA1UEAxMVc2lnc3RvcmUtaW50ZXJtZWRpYXRlMHYwEAYHKoZIzj0C
AQYFK4EEACIDYgAE8RVS/ysH+NOvuDZyPIZtilgUF9NlarYpAd9HP1vBBH1U5CV7
7LSS7s0ZiH4nE7Hv7ptS6LvvR/STk798LVgMzLlJ4HeIfF3tHSaexLcYpSASr1kS
0N/RgBJz/9jWCiXno3sweTAOBgNVHQ8BAf8EBAMCAQYwEwYDVR0lBAwwCgYIKwYB
BQUHAwMwEgYDVR0TAQH/BAgwBgEB/wIBADAdBgNVHQ4EFgQU39Ppz1YkEZb5qNjp
KFWixi4YZD8wHwYDVR0jBBgwFoAUWMAeX5FFpWapesyQoZMi0CrFxfowCgYIKoZI
zj0EAwMDZwAwZAIwPCsQK4DYiZYDPIaDi5HFKnfxXx6ASSVmERfsynYBiX2X6SJR
nZU84/9DZdnFvvxmAjBOt6QpBlc4J/0DxvkTCqpclvziL6BCCPnjdlIB3Pu3BxsP
mygUY7Ii2zbdCdliiow=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIB9zCCAXygAwIBAgIUALZNAPFdxHPwjeDloDwyYChAO/4wCgYIKoZIzj0EAwMw
KjEVMBMGA1UEChMMc2lnc3RvcmUuZGV2MREwDwYDVQQDEwhzaWdzdG9yZTAeFw0y
MTEwMDcxMzU2NTlaFw0zMTEwMDUxMzU2NThaMCoxFTATBgNVBAoTDHNpZ3N0b3Jl
LmRldjERMA8GA1UEAxMIc2lnc3RvcmUwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAAT7
XeFT4rb3PQGwS4IajtLk3/OlnpgangaBclYpsYBr5i+4ynB07ceb3LP0OIOZdxex
X69c5iVuyJRQ+Hz05yi+UF3uBWAlHpiS5sh0+H2GHE7SXrk1EC5m1Tr19L9gg92j
YzBhMA4GA1UdDwEB/wQEAwIBBjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQWBBRY
wB5fkUWlZql6zJChkyLQKsXF+jAfBgNVHSMEGDAWgBRYwB5fkUWlZql6zJChkyLQ
KsXF+jAKBggqhkjOPQQDAwNpADBmAjEAj1nHeXZp+13NWBNa+EDsDP8G1WWg1tCM
WP/WHPqpaVo0jhsweNFZgSs0eE7wYI4qAjEA2WB9ot98sIkoF3vZYdd3/VtWB5b9
TNMea7Ix/stJ5TfcLLeABLE4BNJOsQ4vnBHJ
-----END CERTIFICATE-----
"""

OID_ISSUER_V2 = "1.3.6.1.4.1.57264.1.8"  # OIDC issuer (DER UTF8String)
OID_SOURCE_REPO = "1.3.6.1.4.1.57264.1.12"  # source repository URI
OID_SOURCE_REF = "1.3.6.1.4.1.57264.1.14"  # source repository ref (refs/tags/vX.Y.Z)
OID_RUNNER = "1.3.6.1.4.1.57264.1.11"  # runner environment (github-hosted)
GITHUB_ISSUER = "https://token.actions.githubusercontent.com"


class ProvenanceError(Exception):
    pass


def snappy_decompress(data: bytes) -> bytes:
    """Raw Snappy block format (how GitHub stores attestation bundles)."""
    pos, length, shift = 0, 0, 0
    while True:
        b = data[pos]
        pos += 1
        length |= (b & 0x7F) << shift
        if b < 0x80:
            break
        shift += 7
    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:  # literal
            n = tag >> 2
            if n >= 60:
                extra = n - 59
                n = int.from_bytes(data[pos:pos + extra], "little")
                pos += extra
            n += 1
            out += data[pos:pos + n]
            pos += n
            continue
        if kind == 1:
            n = ((tag >> 2) & 7) + 4
            off = ((tag >> 5) << 8) | data[pos]
            pos += 1
        elif kind == 2:
            n = (tag >> 2) + 1
            off = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
        else:
            n = (tag >> 2) + 1
            off = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        if off == 0 or off > len(out):
            raise ProvenanceError("bad snappy data")
        for _ in range(n):
            out.append(out[-off])
    if len(out) != length:
        raise ProvenanceError("bad snappy data")
    return bytes(out)


def _der_text(raw: bytes) -> str:
    """The value of a Fulcio extension: newer ones are DER UTF8String, older ones raw text."""
    if len(raw) >= 2 and raw[0] == 0x0C:
        n, start = raw[1], 2
        if n & 0x80:
            k = n & 0x7F
            n, start = int.from_bytes(raw[2:2 + k], "big"), 2 + k
        return raw[start:start + n].decode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


def _pae(payload_type: str, payload: bytes) -> bytes:
    pt = payload_type.encode()
    return b"DSSEv1 %d %s %d %s" % (len(pt), pt, len(payload), payload)


def check_bundle(bundle: dict, digest: str, workflow_uri: str, repo_uri: str, ref: str) -> None:
    """Raise ProvenanceError unless this Sigstore bundle proves `digest` was built by `workflow_uri`."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        vm = bundle["verificationMaterial"]
        raw = (vm.get("certificate") or {}).get("rawBytes") or \
            vm["x509CertificateChain"]["certificates"][0]["rawBytes"]
        cert = x509.load_der_x509_certificate(base64.b64decode(raw))
        envelope = bundle["dsseEnvelope"]
        payload = base64.b64decode(envelope["payload"])
        sig = base64.b64decode(envelope["signatures"][0]["sig"])
        payload_type = envelope["payloadType"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProvenanceError(f"unreadable attestation ({type(exc).__name__})") from None
    # 1) issued by Sigstore's CA
    intermediate, root = x509.load_pem_x509_certificates(FULCIO_CHAIN)
    try:
        cert.verify_directly_issued_by(intermediate)
        intermediate.verify_directly_issued_by(root)
    except Exception:  # noqa: BLE001
        raise ProvenanceError("not issued by Sigstore") from None
    # 2) for exactly this workflow, repository and tag, from GitHub Actions
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        uris = sans.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound:
        uris = []
    ext = {e.oid.dotted_string: e.value.value for e in cert.extensions
           if isinstance(e.value, x509.UnrecognizedExtension)}
    if workflow_uri not in uris:
        raise ProvenanceError("built by another workflow")
    if _der_text(ext.get(OID_ISSUER_V2, b"")) != GITHUB_ISSUER:
        raise ProvenanceError("not from GitHub Actions")
    if _der_text(ext.get(OID_SOURCE_REPO, b"")) != repo_uri or _der_text(ext.get(OID_SOURCE_REF, b"")) != ref:
        raise ProvenanceError("built from another repository or tag")
    # 3) the signed statement is about exactly this file
    try:
        cert.public_key().verify(sig, _pae(payload_type, payload), ec.ECDSA(hashes.SHA256()))
    except Exception:  # noqa: BLE001
        raise ProvenanceError("signature doesn't match") from None
    try:
        statement = json.loads(payload)
        digests = [s.get("digest", {}).get("sha256") for s in statement.get("subject", [])]
    except (ValueError, AttributeError):
        raise ProvenanceError("unreadable statement") from None
    if digest.lower() not in [d.lower() for d in digests if isinstance(d, str)]:
        raise ProvenanceError("about another file")


def verify(digest: str, version: str, get: Callable) -> None:
    """The zip with this SHA-256 was built by Skyfold's release workflow for tag v<version>. Raises
    UpdateError (with a short reason) otherwise. get(url) as in updater._get (bundle host allowed)."""
    from .updater import UpdateError

    tag = f"refs/tags/v{version}"
    workflow_uri = f"https://github.com/{REPO}/{WORKFLOW}@{tag}"
    repo_uri = f"https://github.com/{REPO}"
    reasons = []
    from .updater import RateLimited

    try:
        r = get(ATTESTATIONS + digest.lower())
        items = (r.json() or {}).get("attestations") or []
    except RateLimited:
        raise  # GitHub's limit: "try again in N min", not "can't be confirmed"
    except Exception as exc:  # noqa: BLE001 - no attestation = not proven
        raise UpdateError(t("the update's build origin can't be confirmed")) from exc
    for item in items[:10]:
        try:
            bundle = item.get("bundle") if isinstance(item, dict) else None
            if not bundle and isinstance(item, dict) and isinstance(item.get("bundle_url"), str):
                raw = get(item["bundle_url"]).content
                bundle = json.loads(snappy_decompress(raw) if not raw.lstrip().startswith(b"{") else raw)
            check_bundle(bundle, digest, workflow_uri, repo_uri, tag)
            return
        except Exception as exc:  # noqa: BLE001 - try the next one
            reasons.append(str(exc)[:80])
    raise UpdateError(t("the update's build origin can't be confirmed"))
