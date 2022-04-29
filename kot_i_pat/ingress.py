import email.utils
import re
from base64 import b64decode
from datetime import datetime, timezone
from typing import cast

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from opentelemetry import trace
from pydantic.error_wrappers import ErrorWrapper

from .config import settings

tracer = trace.get_tracer(__name__)


def invalidate(reason: str) -> RequestValidationError:
    return RequestValidationError(
        [[ErrorWrapper(loc=("request",), exc=ValueError(reason))]]
    )


def http_host(
    host: str = Header(..., regex=r"^" + re.escape(settings.federation_host) + r"$")
):
    if host != settings.federation_host:
        raise invalidate("provided host doesn't match configured federation host")
    return host


def http_date(
    date: str = Header(
        ...,
        regex=r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT$",
    )
):
    return email.utils.parsedate_to_datetime(date)


def recent_http_date(date: datetime = Depends(http_date)):
    with tracer.start_as_current_span("recent_http_date") as span:
        time_of_check = datetime.now(timezone.utc)
        span.set_attribute("time_of_check", time_of_check.isoformat())
        span.set_attribute("provided_date", date.isoformat())
        if abs(time_of_check - date).total_seconds() > 30:
            raise invalidate("provided date is not current enough")
        return date


async def fetch_actor(url: str):
    # TODO: caching
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers={"Accept": "application/activity+json"})
        r.raise_for_status()
        return r.json()


async def http_signature(
    request: Request,
    host: str = Depends(http_host),
    date: datetime = Depends(recent_http_date),
    signature: str = Header(...),
):
    with tracer.start_as_current_span("http_signature") as span:
        # borrowing from https://blog.joinmastodon.org/2018/07/how-to-make-friends-and-verify-requests/
        # TODO: harden
        signature_header = {
            key: value.strip('"')
            for pair in signature.split(",")
            for key, value in [pair.split("=", 1)]
        }
        key_id = signature_header["keyId"]
        headers = signature_header["headers"]
        signature_bytes = b64decode(signature_header["signature"])

        signed_headers = headers.split(" ")
        span.set_attribute("signed_headers", signed_headers)
        required_headers = {"date", "host", "(request-target)"}
        if not set(signed_headers).issuperset(required_headers):
            raise invalidate(f"signed headers must include at least {required_headers}")
        comparison_string = "\n".join(
            signed_header_name
            + ": "
            + (
                f"{request.method.lower()} {request.url.path}" # TODO: query?
                if signed_header_name == "(request-target)"
                else request.headers[signed_header_name]
            )
            for signed_header_name in signed_headers
        )
        span.set_attribute("comparison_string", comparison_string)

        try:
            actor = await fetch_actor(key_id)
        except:
            raise invalidate("could not get actor")
        # TODO: make sure the key is there actually
        key = cast(
            RSAPublicKey, load_pem_public_key(actor["publicKey"]["publicKeyPem"].encode())
        )
        try:
            key.verify(
                signature_bytes, comparison_string.encode(), PKCS1v15(), SHA256()
            )
        except InvalidSignature:
            raise invalidate("invalid signature")
        return key_id
