import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.api.v1.health_deep import health_ready as deep_health_ready
from app.dependencies import get_db
from app.services.document_parser_adapter import get_document_parser_capabilities
from app.services.document_parser_identity import get_document_parser_identity
from app.services.release_readiness import (
    build_production_dry_run_report,
    build_production_preflight_report,
    build_production_secrets_tls_checklist,
    build_release_readiness_report,
    load_local_dependency_report,
    load_public_api_leak_scan_report,
    sanitize_public_operational_report,
)

router = APIRouter()


def _public_json(payload: dict, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(sanitize_public_operational_report(payload), status_code=status_code)


@router.get("")
async def health():
    return _public_json(
        {
            "status": "ok",
            "capabilities": {
                "document_parser": get_document_parser_capabilities(settings.DOCUMENT_PARSER_ADAPTER),
            },
            "identities": {
                "document_parser": get_document_parser_identity(),
            },
        }
    )


@router.get("/ready")
async def ready():
    ready_response = await deep_health_ready()
    payload = json.loads(ready_response.body.decode("utf-8"))
    return _public_json(payload, status_code=ready_response.status_code)


@router.get("/release-readiness")
async def release_readiness(db: Session = Depends(get_db)):
    ready_response = await deep_health_ready()
    runtime_readiness = json.loads(ready_response.body.decode("utf-8"))
    return _public_json(build_release_readiness_report(runtime_readiness=runtime_readiness, db=db))


@router.get("/production-preflight")
async def production_preflight(db: Session = Depends(get_db)):
    ready_response = await deep_health_ready()
    runtime_readiness = json.loads(ready_response.body.decode("utf-8"))
    release_report = build_release_readiness_report(runtime_readiness=runtime_readiness, db=db)
    return _public_json(
        build_production_preflight_report(
            release_readiness=release_report,
            runtime_readiness=runtime_readiness,
            local_dependency_report=load_local_dependency_report(),
            public_api_leak_scan=load_public_api_leak_scan_report(),
        )
    )


@router.get("/production-dry-run")
async def production_dry_run():
    ready_response = await deep_health_ready()
    runtime_readiness = json.loads(ready_response.body.decode("utf-8"))
    return _public_json(build_production_dry_run_report(runtime_readiness=runtime_readiness))


@router.get("/production-secrets-tls")
async def production_secrets_tls():
    return _public_json(build_production_secrets_tls_checklist(settings))
