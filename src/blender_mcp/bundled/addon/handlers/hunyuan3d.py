import base64
import hashlib
import hmac
import json
import os
import os.path as osp
import re
import shutil
import tempfile
import time
import zipfile
from contextlib import suppress
from datetime import datetime

import bpy
import requests


class Hunyuan3DHandlersMixin:
    # region Hunyuan3D
    def get_hunyuan3d_status(self):
        """Get the current status of Hunyuan3D integration"""
        enabled = bpy.context.scene.blendermcp_use_hunyuan3d
        hunyuan3d_mode = bpy.context.scene.blendermcp_hunyuan3d_mode
        secret_id = self._get_hunyuan3d_secret_id()
        secret_key = self._get_hunyuan3d_secret_key()
        api_url = self._get_hunyuan3d_api_url()
        if enabled:
            match hunyuan3d_mode:
                case "OFFICIAL_API":
                    if not secret_id or not secret_key:
                        return {
                            "enabled": False,
                            "mode": hunyuan3d_mode,
                            "message": """Hunyuan3D integration is currently enabled, but SecretId or SecretKey is not given. To enable it:
                                1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Tencent Hunyuan 3D model generation' checkbox checked
                                3. Choose the right platform and fill in the SecretId and SecretKey
                                4. Restart the connection to Claude""",
                        }
                case "LOCAL_API":
                    if not api_url:
                        return {
                            "enabled": False,
                            "mode": hunyuan3d_mode,
                            "message": """Hunyuan3D integration is currently enabled, but API URL  is not given. To enable it:
                                1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Tencent Hunyuan 3D model generation' checkbox checked
                                3. Choose the right platform and fill in the API URL
                                4. Restart the connection to Claude""",
                        }
                case _:
                    return {
                        "enabled": False,
                        "message": "Hunyuan3D integration is enabled and mode is not supported.",
                    }
            return {
                "enabled": True,
                "mode": hunyuan3d_mode,
                "message": "Hunyuan3D integration is enabled and ready to use.",
            }
        return {
            "enabled": False,
            "message": """Hunyuan3D integration is currently disabled. To enable it:
                        1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                        2. Check the 'Use Tencent Hunyuan 3D model generation' checkbox
                        3. Restart the connection to Claude""",
        }

    @staticmethod
    def get_tencent_cloud_sign_headers(
        method: str,
        path: str,
        headParams: dict,
        data: dict,
        service: str,
        region: str,
        secret_id: str,
        secret_key: str,
        host: str = None,
    ):
        """Generate the signature header required for Tencent Cloud API requests headers"""
        # Generate timestamp
        timestamp = int(time.time())
        date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")

        # If host is not provided, it is generated based on service and region.
        if not host:
            host = f"{service}.tencentcloudapi.com"

        endpoint = f"https://{host}"

        # Constructing the request body
        payload_str = json.dumps(data)

        # ************* Step 1: Concatenate the canonical request string *************
        canonical_uri = path
        canonical_querystring = ""
        ct = "application/json; charset=utf-8"
        canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{headParams.get('Action', '').lower()}\n"
        signed_headers = "content-type;host;x-tc-action"
        hashed_request_payload = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

        canonical_request = (
            method
            + "\n"
            + canonical_uri
            + "\n"
            + canonical_querystring
            + "\n"
            + canonical_headers
            + "\n"
            + signed_headers
            + "\n"
            + hashed_request_payload
        )

        # ************* Step 2: Construct the reception signature string *************
        credential_scope = f"{date}/{service}/tc3_request"
        hashed_canonical_request = hashlib.sha256(
            canonical_request.encode("utf-8")
        ).hexdigest()
        string_to_sign = (
            "TC3-HMAC-SHA256"
            + "\n"
            + str(timestamp)
            + "\n"
            + credential_scope
            + "\n"
            + hashed_canonical_request
        )

        # ************* Step 3: Calculate the signature *************
        def sign(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        secret_date = sign(("TC3" + secret_key).encode("utf-8"), date)
        secret_service = sign(secret_date, service)
        secret_signing = sign(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        # ************* Step 4: Connect Authorization *************
        authorization = (
            "TC3-HMAC-SHA256"
            + " "
            + "Credential="
            + secret_id
            + "/"
            + credential_scope
            + ", "
            + "SignedHeaders="
            + signed_headers
            + ", "
            + "Signature="
            + signature
        )

        # Constructing request headers
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-TC-Action": headParams.get("Action", ""),
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": headParams.get("Version", ""),
            "X-TC-Region": region,
        }

        return headers, endpoint

    def create_hunyuan_job(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hunyuan3d_mode:
            case "OFFICIAL_API":
                return self.create_hunyuan_job_main_site(*args, **kwargs)
            case "LOCAL_API":
                return self.create_hunyuan_job_local_site(*args, **kwargs)
            case _:
                return "Error: Unknown Hunyuan3D mode!"

    def create_hunyuan_job_main_site(self, text_prompt: str = None, image: str = None):
        try:
            secret_id = self._get_hunyuan3d_secret_id()
            secret_key = self._get_hunyuan3d_secret_key()

            if not secret_id or not secret_key:
                return {"error": "SecretId or SecretKey is not given"}

            # Parameter verification
            if not text_prompt and not image:
                return {"error": "Prompt or Image is required"}
            if text_prompt and image:
                return {"error": "Prompt and Image cannot be provided simultaneously"}
            # Updated to Tencent Cloud AI3D API 3.0 (2025-05-13)
            service = "ai3d"
            action = "SubmitHunyuanTo3DProJob"
            version = "2025-05-13"
            region = "ap-guangzhou"

            headParams = {
                "Action": action,
                "Version": version,
                "Region": region,
            }

            # Constructing request parameters
            data = {}

            # Handling text prompts
            if text_prompt:
                if len(text_prompt) > 1024:
                    return {"error": "Prompt exceeds 1024 characters limit"}
                data["Prompt"] = text_prompt

            # Handling image
            if image:
                if re.match(r"^https?://", image, re.IGNORECASE) is not None:
                    data["ImageUrl"] = image
                else:
                    try:
                        # Convert to Base64 format
                        with open(image, "rb") as f:
                            image_base64 = base64.b64encode(f.read()).decode("ascii")
                        data["ImageBase64"] = image_base64
                    except Exception as e:
                        return {"error": f"Image encoding failed: {str(e)}"}

            # Get signed headers
            headers, endpoint = self.get_tencent_cloud_sign_headers(
                "POST", "/", headParams, data, service, region, secret_id, secret_key
            )

            response = requests.post(endpoint, headers=headers, data=json.dumps(data))

            if response.status_code == 200:
                return response.json()
            return {
                "error": f"API request failed with status {response.status_code}: {response}"
            }
        except Exception as e:
            return {"error": str(e)}

    def create_hunyuan_job_local_site(self, text_prompt: str = None, image: str = None):
        try:
            base_url = self._get_hunyuan3d_api_url().rstrip("/")
            octree_resolution = bpy.context.scene.blendermcp_hunyuan3d_octree_resolution
            num_inference_steps = (
                bpy.context.scene.blendermcp_hunyuan3d_num_inference_steps
            )
            guidance_scale = bpy.context.scene.blendermcp_hunyuan3d_guidance_scale
            texture = bpy.context.scene.blendermcp_hunyuan3d_texture

            if not base_url:
                return {"error": "API URL is not given"}
            # Parameter verification
            if not text_prompt and not image:
                return {"error": "Prompt or Image is required"}

            # Constructing request parameters
            data = {
                "octree_resolution": octree_resolution,
                "num_inference_steps": num_inference_steps,
                "guidance_scale": guidance_scale,
                "texture": texture,
            }

            # Handling text prompts
            if text_prompt:
                data["text"] = text_prompt

            # Handling image
            if image:
                if re.match(r"^https?://", image, re.IGNORECASE) is not None:
                    try:
                        resImg = requests.get(image)
                        resImg.raise_for_status()
                        image_base64 = base64.b64encode(resImg.content).decode("ascii")
                        data["image"] = image_base64
                    except Exception as e:
                        return {
                            "error": f"Failed to download or encode image: {str(e)}"
                        }
                else:
                    try:
                        # Convert to Base64 format
                        with open(image, "rb") as f:
                            image_base64 = base64.b64encode(f.read()).decode("ascii")
                        data["image"] = image_base64
                    except Exception as e:
                        return {"error": f"Image encoding failed: {str(e)}"}

            response = requests.post(
                f"{base_url}/generate",
                json=data,
            )

            if response.status_code != 200:
                return {"error": f"Generation failed: {response.text}"}

            # Decode base64 and save to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".glb") as temp_file:
                temp_file.write(response.content)
                temp_file_name = temp_file.name

            # Import the GLB file in the main thread
            def import_handler():
                bpy.ops.import_scene.gltf(filepath=temp_file_name)
                os.unlink(temp_file.name)
                return None

            bpy.app.timers.register(import_handler)

            return {"status": "DONE", "message": "Generation and Import glb succeeded"}
        except Exception as e:
            print(f"An error occurred: {e}")
            return {"error": str(e)}

    def poll_hunyuan_job_status(self, *args, **kwargs):
        return self.poll_hunyuan_job_status_ai(*args, **kwargs)

    def poll_hunyuan_job_status_ai(self, job_id: str):
        """Call the job status API to get the job status"""
        print(job_id)
        try:
            secret_id = self._get_hunyuan3d_secret_id()
            secret_key = self._get_hunyuan3d_secret_key()

            if not secret_id or not secret_key:
                return {"error": "SecretId or SecretKey is not given"}
            if not job_id:
                return {"error": "JobId is required"}

            # Updated to Tencent Cloud AI3D API 3.0 (2025-05-13)
            service = "ai3d"
            action = "QueryHunyuanTo3DProJob"
            version = "2025-05-13"
            region = "ap-guangzhou"

            headParams = {
                "Action": action,
                "Version": version,
                "Region": region,
            }

            clean_job_id = job_id.removeprefix("job_")
            data = {"JobId": clean_job_id}

            headers, endpoint = self.get_tencent_cloud_sign_headers(
                "POST", "/", headParams, data, service, region, secret_id, secret_key
            )

            response = requests.post(endpoint, headers=headers, data=json.dumps(data))

            if response.status_code == 200:
                return response.json()
            return {
                "error": f"API request failed with status {response.status_code}: {response}"
            }
        except Exception as e:
            return {"error": str(e)}

    def import_generated_asset_hunyuan(self, *args, **kwargs):
        return self.import_generated_asset_hunyuan_ai(*args, **kwargs)

    def import_generated_asset_hunyuan_ai(self, name: str, zip_file_url: str):
        if not zip_file_url:
            return {"error": "No file URL provided"}

        # Validate URL
        if not re.match(r"^https?://", zip_file_url, re.IGNORECASE):
            return {"error": "Invalid URL format. Must start with http:// or https://"}

        # Prefer GLB (self-contained with materials) over OBJ/ZIP (API 3.0 returns .glb URLs)
        url_path = zip_file_url.split("?", 1)[0].split("#", 1)[0].lower()
        if url_path.endswith(".glb"):
            temp_dir = tempfile.mkdtemp(prefix="hunyuan_glb_")
            glb_path = osp.join(temp_dir, "model.glb")
            try:
                glb_response = requests.get(zip_file_url, stream=True)
                glb_response.raise_for_status()
                with open(glb_path, "wb") as f:
                    for chunk in glb_response.iter_content(chunk_size=8192):
                        f.write(chunk)
                bpy.ops.import_scene.gltf(filepath=glb_path)
                imported_objs = [
                    obj for obj in bpy.context.selected_objects if obj.type == "MESH"
                ]
                if not imported_objs:
                    return {
                        "succeed": False,
                        "error": "No mesh objects imported from GLB",
                    }
                obj = imported_objs[0]
                if name:
                    obj.name = name
                result = {
                    "name": obj.name,
                    "type": obj.type,
                    "location": [obj.location.x, obj.location.y, obj.location.z],
                    "rotation": [
                        obj.rotation_euler.x,
                        obj.rotation_euler.y,
                        obj.rotation_euler.z,
                    ],
                    "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
                }
                if obj.type == "MESH":
                    result["world_bounding_box"] = self._get_aabb(obj)
                return {"succeed": True, **result}
            except Exception as e:
                return {"succeed": False, "error": str(e)}
            finally:
                with suppress(Exception):
                    shutil.rmtree(temp_dir)

        # Fallback: ZIP/OBJ import (legacy)
        temp_dir = tempfile.mkdtemp(prefix="tencent_obj_")
        zip_file_path = osp.join(temp_dir, "model.zip")
        obj_file_path = osp.join(temp_dir, "model.obj")
        try:
            zip_response = requests.get(zip_file_url, stream=True)
            zip_response.raise_for_status()
            with open(zip_file_path, "wb") as f:
                for chunk in zip_response.iter_content(chunk_size=8192):
                    f.write(chunk)
            with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
                # Mirror the Sketchfab zip-slip checks before extractall.
                abs_temp_dir = os.path.abspath(temp_dir)
                for file_info in zip_ref.infolist():
                    file_path = file_info.filename
                    target_path = os.path.join(temp_dir, os.path.normpath(file_path))
                    abs_target_path = os.path.abspath(target_path)
                    if (
                        not abs_target_path.startswith(abs_temp_dir + os.sep)
                        and abs_target_path != abs_temp_dir
                    ):
                        return {
                            "succeed": False,
                            "error": "Security issue: Zip contains files with path traversal attempt",
                        }
                    if ".." in file_path:
                        return {
                            "succeed": False,
                            "error": "Security issue: Zip contains files with directory traversal sequence",
                        }
                zip_ref.extractall(temp_dir)
            for file in os.listdir(temp_dir):
                if file.endswith(".obj"):
                    obj_file_path = osp.join(temp_dir, file)
            if not osp.exists(obj_file_path):
                return {
                    "succeed": False,
                    "error": "OBJ file not found after extraction",
                }
            if bpy.app.version >= (4, 0, 0):
                bpy.ops.wm.obj_import(filepath=obj_file_path)
            else:
                bpy.ops.import_scene.obj(filepath=obj_file_path)
            imported_objs = [
                obj for obj in bpy.context.selected_objects if obj.type == "MESH"
            ]
            if not imported_objs:
                return {"succeed": False, "error": "No mesh objects imported"}
            obj = imported_objs[0]
            if name:
                obj.name = name
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [
                    obj.rotation_euler.x,
                    obj.rotation_euler.y,
                    obj.rotation_euler.z,
                ],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }
            if obj.type == "MESH":
                result["world_bounding_box"] = self._get_aabb(obj)
            return {"succeed": True, **result}
        except Exception as e:
            return {"succeed": False, "error": str(e)}
        finally:
            with suppress(Exception):
                shutil.rmtree(temp_dir)

    # endregion
