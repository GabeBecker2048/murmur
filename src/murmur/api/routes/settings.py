from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from murmur.api.auth import require_token
from murmur.api.deps import get_config, get_provider
from murmur.api.schemas import SettingsPatch
from murmur.config import Config, ConfigError, effective_settings, settings_schema, write_settings

router = APIRouter(tags=["settings"])
write = APIRouter(tags=["settings"], dependencies=[Depends(require_token)])


@router.get("/settings")
def get_settings(config: Config = Depends(get_config)):
    return {
        "effective": effective_settings(config),
        "provenance": config.provenance,
        "ops_file": str(config.source_path) if config.source_path else None,
        "settings_file": str(config.settings_path),
    }


@router.get("/settings/schema")
def get_schema(config: Config = Depends(get_config)):
    return {"keys": settings_schema(config)}


@write.put("/settings")
def put_settings(body: SettingsPatch, request: Request, config: Config = Depends(get_config)):
    patch = body.model_dump()
    if not patch:
        raise HTTPException(400, "empty patch")
    try:
        new = write_settings(config, patch, env=get_provider(request).env)
    except ConfigError as e:
        raise HTTPException(400, str(e)) from None
    get_provider(request).invalidate()
    return {
        "effective": effective_settings(new),
        "provenance": new.provenance,
        "settings_file": str(new.settings_path),
    }


router.include_router(write)
