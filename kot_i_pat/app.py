import json
import typing
from http import HTTPStatus

from fastapi import Body, Depends, FastAPI, Header, Query, Response
from fastapi.exceptions import HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from . import db
from .config import settings
from .ingress import http_signature

tracer_provider = TracerProvider()
processor = BatchSpanProcessor(OTLPSpanExporter())
tracer_provider.add_span_processor(processor)
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer(__name__)

app = FastAPI()


@app.post("/inbox", status_code=HTTPStatus.ACCEPTED)
async def inbox(
    body: typing.Any = Body(...),
    key_id: str = Depends(http_signature),
):
    print(key_id)
    print(body)
    # TODO: is key_id permitted to publish body["id"]
    db.insert_object(body["id"], key_id, body)


def http_accept(accept: str = Header("application/activity+json")):
    options = [sub.split(";", 1)[0] for sub in accept.split(",") if sub]
    # TODO: make sure this is valid logic
    for option in options:
        if option in {"application/json", "application/activity+json"}:
            return "application/activity+json"
        if option in {"text/html", "application/xhtml+xml"}:
            return "text/html"
    return "application/activity+json"


@app.get("/db/{path:path}")
async def get_object(path: str, accept: str = Depends(http_accept)) -> typing.Any:
    try:
        obj = db.get_public_object(f"https://{settings.federation_host}/db/{path}")
    except FileNotFoundError:
        raise HTTPException(HTTPStatus.NOT_FOUND)
    # TODO: if accept == "application/activity+json":
    return Response(
        json.dumps(obj), headers={"Content-Type": "application/activity+json"}
    )


@app.get("/.well-known/nodeinfo")
async def nodeinfo():
    return Response(
        json.dumps(
            {
                "links": [
                    {
                        "rel": "http://nodeinfo.diaspora.software/ns/schema/2.1",
                        "href": f"https://{settings.federation_host}/.well-known/nodeinfo/2.1",
                    }
                ]
            }
        ),
        headers={"Content-Type": "application/json"},
    )


@app.get("/.well-known/nodeinfo/2.1")
async def nodeinfo_2_1():
    return Response(
        json.dumps(
            {
                "version": "2.1",
                "software": {
                    "name": "kot-i-pat",
                    "version": "0.1.0",  # TODO: version
                },
                "protocols": [
                    "activitypub",
                ],
                "services": {"inbound": [], "outbound": []},
                "openRegistrations": False,
                "usage": {
                    "users": {
                        "total": 1,  # TODO: the rest
                    }
                },
                "metadata": {},
            }
        ),
        headers={
            "Content-Type": "application/json; profile=http://nodeinfo.diaspora.software/ns/schema/2.1#; charset=utf-8"
        },
    )


@app.get("/.well-known/webfinger")
async def webfinger(resource: str = Query(...)):
    expected_prefix = "acct:"
    expected_suffix = "@" + settings.federation_host
    if resource.startswith(expected_prefix) and resource.endswith(expected_suffix):
        username = resource[len(expected_prefix) :][: -len(expected_suffix)]
        print(username)
        try:
            user_url = f"https://{settings.federation_host}/db/{username}"
            db.get_public_object(user_url)
            # TODO: be more precise here
            return Response(
                json.dumps(
                    {
                        "subject": resource,
                        "aliases": [user_url],
                        "links": [
                            {
                                "rel": "http://webfinger.net/rel/profile-page",
                                "type": "text/html",
                                "href": user_url,
                            },
                            {
                                "rel": "self",
                                "type": "application/activity+json",
                                "href": user_url,
                            },
                        ],
                    }
                ),
                headers={"Content-Type": "application/jrd+json; charset=utf-8"},
            )
        except FileNotFoundError:
            raise HTTPException(HTTPStatus.NOT_FOUND)


FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()
