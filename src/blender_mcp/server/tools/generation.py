"""
model_from_reference / model_generate_from_description orchestration.

These collapse the generate -> poll -> import workflow (three separate
tool calls above, per provider) into a single call, auto-selecting
whichever provider is enabled in Blender.
"""

import asyncio
import base64
import logging
import os
import time

from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..app import mcp
from ..connection import get_blender_connection
from ._envelope import ok
from .hyper3d import process_bbox

logger = logging.getLogger("BlenderMCPServer")

Provider = Literal["auto", "hyper3d", "hunyuan3d"]


def _rodin_extract_job_ids(result: dict[str, Any]) -> dict[str, str]:
    if not isinstance(result, dict):
        raise ValueError(f"Unexpected response from Hyper3D: {result}")
    if result.get("error"):
        raise ValueError(f"Hyper3D error: {result['error']}")
    if "uuid" in result and "jobs" in result:
        return {
            "task_uuid": result["uuid"],
            "subscription_key": result["jobs"]["subscription_key"],
        }
    if "request_id" in result:
        return {"request_id": result["request_id"]}
    raise ValueError(f"Could not determine Hyper3D job id from response: {result}")


async def _rodin_wait_until_done(blender, job_ids: dict[str, str], timeout_s: float) -> None:
    poll_kwargs = {k: v for k, v in job_ids.items() if k in {"subscription_key", "request_id"}}
    deadline = time.monotonic() + timeout_s
    while True:
        status = blender.send_command("poll_rodin_job_status", poll_kwargs)
        if not isinstance(status, dict):
            raise ValueError(f"Unexpected Hyper3D poll response: {status}")
        if "status_list" in status:
            statuses = status["status_list"]
            if any(s == "Failed" for s in statuses):
                raise ValueError(f"Hyper3D generation failed: {statuses}")
            if statuses and all(s == "Done" for s in statuses):
                return
        else:
            job_status = status.get("status")
            if job_status == "COMPLETED":
                return
            if job_status not in {None, "IN_PROGRESS", "IN_QUEUE"}:
                raise ValueError(f"Hyper3D generation failed: {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out after {timeout_s}s waiting for Hyper3D generation")
        await asyncio.sleep(3)


async def _generate_hyper3d_and_import(
    blender,
    *,
    name: str | None = None,
    text_prompt: str | None = None,
    images: list | None = None,
    bbox_condition: tuple[float, float, float] | None = None,
    timeout_s: float,
) -> dict[str, Any]:
    result = blender.send_command(
        "create_rodin_job",
        {
            "text_prompt": text_prompt,
            "images": images,
            "bbox_condition": process_bbox(bbox_condition),
        },
    )
    job_ids = _rodin_extract_job_ids(result)
    await _rodin_wait_until_done(blender, job_ids, timeout_s)
    import_kwargs = {"name": name or "GeneratedModel"}
    import_kwargs.update({k: v for k, v in job_ids.items() if k in {"task_uuid", "request_id"}})
    import_result = blender.send_command("import_generated_asset", import_kwargs)
    if isinstance(import_result, dict) and import_result.get("succeed") is False:
        raise ValueError(f"Hyper3D import failed: {import_result.get('error', import_result)}")
    return {
        "provider": "hyper3d",
        "import_result": import_result,
        "name": import_kwargs["name"],
    }


def _find_urls(value) -> list:
    """
    Recursively collect http(s) URL strings from an arbitrary JSON-like structure.

    Args:
        value: Value for value.

    Returns:
        list: Result produced by the operation.

    """
    urls = []
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            urls.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            urls.extend(_find_urls(v))
    elif isinstance(value, list):
        for v in value:
            urls.extend(_find_urls(v))
    return urls


async def _hunyuan_wait_for_model_url(blender, job_id: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while True:
        status = blender.send_command("poll_hunyuan_job_status", {"job_id": job_id})
        if not isinstance(status, dict):
            raise ValueError(f"Unexpected Hunyuan3D poll response: {status}")
        if status.get("error"):
            raise ValueError(f"Hunyuan3D error: {status['error']}")
        response = status.get("Response", {})
        job_status = response.get("Status")
        if job_status == "DONE":
            urls = _find_urls(response.get("ResultFile3Ds", response))
            glb = next(
                (u for u in urls if u.split("?", 1)[0].split("#", 1)[0].lower().endswith(".glb")),
                None,
            )
            model_url = glb or (urls[0] if urls else None)
            if not model_url:
                raise ValueError(f"Hunyuan3D job completed but no result file URL was found: {status}")
            return model_url
        if job_status not in {
            None,
            "WAIT",
            "RUN",
            "SUBMITTED",
            "PENDING",
            "IN_PROGRESS",
        }:
            raise ValueError(f"Hunyuan3D generation failed: {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out after {timeout_s}s waiting for Hunyuan3D generation")
        await asyncio.sleep(3)


async def _generate_hunyuan_and_import(
    blender,
    *,
    name: str | None = None,
    text_prompt: str | None = None,
    image: str | None = None,
    timeout_s: float,
) -> dict[str, Any]:
    result = blender.send_command(
        "create_hunyuan_job",
        {
            "text_prompt": text_prompt,
            "image": image,
        },
    )
    if not isinstance(result, dict):
        raise ValueError(f"Unexpected response from Hunyuan3D: {result}")
    if result.get("error"):
        raise ValueError(f"Hunyuan3D error: {result['error']}")
    response = result.get("Response", {})
    if "JobId" in response:
        job_id = f"job_{response['JobId']}"
        model_url = await _hunyuan_wait_for_model_url(blender, job_id, timeout_s)
        import_result = blender.send_command(
            "import_generated_asset_hunyuan",
            {
                "name": name or "GeneratedModel",
                "zip_file_url": model_url,
            },
        )
        if isinstance(import_result, dict) and import_result.get("succeed") is False:
            raise ValueError(f"Hunyuan3D import failed: {import_result.get('error', import_result)}")
        return {
            "provider": "hunyuan3d",
            "import_result": import_result,
            "name": name or "GeneratedModel",
        }
    if result.get("status") == "DONE":
        # LOCAL_API mode generates and imports synchronously within create_hunyuan_job.
        return {
            "provider": "hunyuan3d",
            "import_result": result,
            "name": name or "GeneratedModel",
        }
    raise ValueError(f"Unexpected response from Hunyuan3D: {result}")


async def _select_3d_provider(blender, provider: str) -> str:
    provider = (provider or "auto").lower()
    if provider not in {"auto", "hyper3d", "hunyuan3d"}:
        raise ValueError(f"Unknown provider: {provider}. Must be one of auto, hyper3d, hunyuan3d")
    hyper3d_enabled = False
    hunyuan3d_enabled = False
    if provider in {"auto", "hyper3d"}:
        status = blender.send_command("get_hyper3d_status")
        hyper3d_enabled = bool(status.get("enabled", False))
    if provider in {"auto", "hunyuan3d"}:
        status = blender.send_command("get_hunyuan3d_status")
        hunyuan3d_enabled = bool(status.get("enabled", False))
    if provider == "hyper3d":
        if not hyper3d_enabled:
            raise ValueError("Hyper3D Rodin is not enabled in Blender.")
        return "hyper3d"
    if provider == "hunyuan3d":
        if not hunyuan3d_enabled:
            raise ValueError("Hunyuan3D is not enabled in Blender.")
        return "hunyuan3d"
    if hyper3d_enabled:
        return "hyper3d"
    if hunyuan3d_enabled:
        return "hunyuan3d"
    raise ValueError(
        "No 3D generation provider is enabled in Blender. Enable Hyper3D Rodin or Hunyuan3D in the addon preferences."
    )


@mcp.tool()
async def model_from_reference(
    ctx: Context,
    image_path_or_url: str,
    name: str | None = None,
    provider: Provider = "auto",
    timeout_s: float = 180,
) -> dict:
    """
    Generate a 3D model from a reference image and import it into the scene.

    Auto-selects an enabled AI provider (Hyper3D Rodin or Hunyuan3D), collapsing the
    generate -> poll -> import workflow into a single call.

    Args:
        ctx: MCP request context.
        image_path_or_url: Absolute local file path or http(s) URL of the reference image.
        name: Optional name for the imported object. Defaults to a generic generated name.
        provider: One of auto (default, prefers Hyper3D if enabled), hyper3d, hunyuan3d.
        timeout_s: Maximum seconds to wait for generation to finish before giving up.

    Returns:
        the import result.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        chosen = await _select_3d_provider(blender, provider)
        if chosen == "hyper3d":
            if os.path.exists(image_path_or_url):
                with open(image_path_or_url, "rb") as f:
                    images = [
                        (
                            Path(image_path_or_url).suffix,
                            base64.b64encode(f.read()).decode("ascii"),
                        )
                    ]
            else:
                images = [image_path_or_url]
            result = await _generate_hyper3d_and_import(
                blender,
                name=name,
                images=images,
                timeout_s=timeout_s,
            )
        else:
            result = await _generate_hunyuan_and_import(
                blender,
                name=name,
                image=image_path_or_url,
                timeout_s=timeout_s,
            )
        return ok(result, changed_objects=[result["name"]])
    except Exception as e:
        logger.error(f"Error generating model from reference: {e}")
        raise ToolError(f"Error generating model from reference: {e}") from e


@mcp.tool()
async def model_generate_from_description(
    ctx: Context,
    text_prompt: str,
    bbox_condition: tuple[float, float, float] | None = None,
    name: str | None = None,
    provider: Provider = "auto",
    timeout_s: float = 180,
) -> dict:
    """
    Generate a 3D model from a text description and import it into the scene.

    Auto-selects an enabled AI provider (Hyper3D Rodin or Hunyuan3D), collapsing the
    generate -> poll -> import workflow into a single call.

    Args:
        ctx: MCP request context.
        text_prompt: A short description of the desired model in English.
        bbox_condition: Optional [Length, Width, Height] ratio for the model (Hyper3D only).
        name: Optional name for the imported object. Defaults to a generic generated name.
        provider: One of auto (default, prefers Hyper3D if enabled), hyper3d, hunyuan3d.
        timeout_s: Maximum seconds to wait for generation to finish before giving up.

    Returns:
        the import result.

    Raises:
        ToolError: If the operation cannot be completed.

    """
    try:
        blender = get_blender_connection()
        chosen = await _select_3d_provider(blender, provider)
        if chosen == "hyper3d":
            result = await _generate_hyper3d_and_import(
                blender,
                name=name,
                text_prompt=text_prompt,
                bbox_condition=bbox_condition,
                timeout_s=timeout_s,
            )
        else:
            result = await _generate_hunyuan_and_import(
                blender,
                name=name,
                text_prompt=text_prompt,
                timeout_s=timeout_s,
            )
        return ok(result, changed_objects=[result["name"]])
    except Exception as e:
        logger.error(f"Error generating model from description: {e}")
        raise ToolError(f"Error generating model from description: {e}") from e
