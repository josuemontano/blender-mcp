import bpy
from bpy.props import BoolProperty

from . import ADDON_ID
from .constants import RODIN_FREE_TRIAL_KEY
from .edit_capture import sync_edit_capture_handlers
from .helpers import get_blendermcp_addon_preferences
from .server_core import BlenderMCPServer


class BLENDERMCP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    def _on_telemetry_consent_changed(self, context):
        try:
            sync_edit_capture_handlers()
        except Exception as e:
            print(f"BlenderMCP: could not sync manual edit handlers: {e}")

    telemetry_consent: BoolProperty(
        name="Allow Telemetry",
        description="Allow collection of prompts, code snippets, screenshots, and trajectory data to help improve Blender MCP",
        default=True,
        update=_on_telemetry_consent_changed,
    )
    hyper3d_api_key: bpy.props.StringProperty(
        name="Hyper3D API Key",
        subtype="PASSWORD",
        description="Persistent Hyper3D API Key",
        default="",
    )
    sketchfab_api_key: bpy.props.StringProperty(
        name="Sketchfab API Key",
        subtype="PASSWORD",
        description="Persistent Sketchfab API Key",
        default="",
    )
    hunyuan3d_secret_id: bpy.props.StringProperty(
        name="Hunyuan3D SecretId",
        description="Persistent Hunyuan3D SecretId",
        default="",
    )
    hunyuan3d_secret_key: bpy.props.StringProperty(
        name="Hunyuan3D SecretKey",
        subtype="PASSWORD",
        description="Persistent Hunyuan3D SecretKey",
        default="",
    )
    hunyuan3d_api_url: bpy.props.StringProperty(
        name="Hunyuan3D API URL", description="Persistent Hunyuan3D API URL", default=""
    )

    def draw(self, context):
        layout = self.layout

        # Telemetry section
        layout.label(text="Telemetry & Privacy:", icon="PREFERENCES")

        box = layout.box()
        row = box.row()
        row.prop(self, "telemetry_consent", text="Allow Telemetry")

        # Info text
        box.separator()
        if self.telemetry_consent:
            box.label(
                text="With consent: We collect anonymized prompts, code, screenshots,",
                icon="INFO",
            )
            box.label(
                text="and trajectory data (actions, scene state, feedback).",
                icon="BLANK1",
            )
        else:
            box.label(
                text="Without consent: We only collect minimal anonymous usage data",
                icon="INFO",
            )
            box.label(
                text="(tool names, success/failure, duration - no prompts or code).",
                icon="BLANK1",
            )
        box.separator()
        box.label(
            text="Data is not linked to your name or account. Change this anytime.",
            icon="CHECKMARK",
        )

        # Terms and Conditions link
        box.separator()
        row = box.row()
        row.operator(
            "blendermcp.open_terms", text="View Terms and Conditions", icon="TEXT"
        )

        layout.separator()
        layout.label(text="Persistent API Credentials:", icon="LOCKED")
        cred_box = layout.box()
        cred_box.prop(self, "sketchfab_api_key", text="Sketchfab API Key")
        cred_box.prop(self, "hyper3d_api_key", text="Hyper3D API Key")
        cred_box.prop(self, "hunyuan3d_secret_id", text="Hunyuan3D SecretId")
        cred_box.prop(self, "hunyuan3d_secret_key", text="Hunyuan3D SecretKey")
        cred_box.prop(self, "hunyuan3d_api_url", text="Hunyuan3D API URL")


# Blender UI Panel
class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlenderMCP"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        prefs = get_blendermcp_addon_preferences(context)

        layout.prop(scene, "blendermcp_port")
        layout.prop(
            scene, "blendermcp_use_polyhaven", text="Use assets from Poly Haven"
        )

        layout.prop(
            scene,
            "blendermcp_use_hyper3d",
            text="Use Hyper3D Rodin 3D model generation",
        )
        if scene.blendermcp_use_hyper3d:
            layout.prop(scene, "blendermcp_hyper3d_mode", text="Rodin Mode")
            if prefs:
                layout.prop(prefs, "hyper3d_api_key", text="API Key")
            else:
                layout.prop(scene, "blendermcp_hyper3d_api_key", text="API Key")
            layout.operator(
                "blendermcp.set_hyper3d_free_trial_api_key",
                text="Set Free Trial API Key",
            )

        layout.prop(scene, "blendermcp_use_sketchfab", text="Use assets from Sketchfab")
        if scene.blendermcp_use_sketchfab:
            if prefs:
                layout.prop(prefs, "sketchfab_api_key", text="API Key")
            else:
                layout.prop(scene, "blendermcp_sketchfab_api_key", text="API Key")

        layout.prop(
            scene,
            "blendermcp_use_hunyuan3d",
            text="Use Tencent Hunyuan 3D model generation",
        )
        if scene.blendermcp_use_hunyuan3d:
            layout.prop(scene, "blendermcp_hunyuan3d_mode", text="Hunyuan3D Mode")
            if scene.blendermcp_hunyuan3d_mode == "OFFICIAL_API":
                if prefs:
                    layout.prop(prefs, "hunyuan3d_secret_id", text="SecretId")
                    layout.prop(prefs, "hunyuan3d_secret_key", text="SecretKey")
                else:
                    layout.prop(
                        scene, "blendermcp_hunyuan3d_secret_id", text="SecretId"
                    )
                    layout.prop(
                        scene, "blendermcp_hunyuan3d_secret_key", text="SecretKey"
                    )
            if scene.blendermcp_hunyuan3d_mode == "LOCAL_API":
                if prefs:
                    layout.prop(prefs, "hunyuan3d_api_url", text="API URL")
                else:
                    layout.prop(scene, "blendermcp_hunyuan3d_api_url", text="API URL")
                layout.prop(
                    scene,
                    "blendermcp_hunyuan3d_octree_resolution",
                    text="Octree Resolution",
                )
                layout.prop(
                    scene,
                    "blendermcp_hunyuan3d_num_inference_steps",
                    text="Number of Inference Steps",
                )
                layout.prop(
                    scene, "blendermcp_hunyuan3d_guidance_scale", text="Guidance Scale"
                )
                layout.prop(
                    scene, "blendermcp_hunyuan3d_texture", text="Generate Texture"
                )

        layout.prop(
            scene,
            "blendermcp_use_nd",
            text="Use ND (non-destructive hard-surface tools)",
        )

        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Connect to MCP server")
        else:
            layout.operator("blendermcp.stop_server", text="Disconnect from MCP server")
            layout.label(text=f"Running on port {scene.blendermcp_port}")

        # Feedback section
        layout.separator()
        feedback_box = layout.box()

        col = feedback_box.column(align=True)
        col.label(text="Feedback", icon="URL")
        col.label(text="bit.ly/blender-mcp-form")
        col.separator()
        col.label(text="Schedule a call", icon="URL")
        col.label(text="bit.ly/blender-mcp-call")
        col.label(text="(we'll credit you in the repo!)")


# Operator to set Hyper3D API Key
class BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey(bpy.types.Operator):
    bl_idname = "blendermcp.set_hyper3d_free_trial_api_key"
    bl_label = "Set Free Trial API Key"

    def execute(self, context):
        prefs = get_blendermcp_addon_preferences(context)
        if prefs:
            if (
                not prefs.hyper3d_api_key
                or prefs.hyper3d_api_key == RODIN_FREE_TRIAL_KEY
            ):
                prefs.hyper3d_api_key = RODIN_FREE_TRIAL_KEY
            else:
                self.report(
                    {"INFO"},
                    "Using free trial for this session only; saved private key was kept.",
                )
        context.scene.blendermcp_hyper3d_api_key = RODIN_FREE_TRIAL_KEY
        context.scene.blendermcp_hyper3d_mode = "MAIN_SITE"
        self.report({"INFO"}, "API Key set successfully!")
        return {"FINISHED"}


# Operator to start the server
class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the BlenderMCP server to connect with Claude"

    def execute(self, context):
        scene = context.scene

        # Create a new server instance
        if (
            not hasattr(bpy.types, "blendermcp_server")
            or not bpy.types.blendermcp_server
        ):
            bpy.types.blendermcp_server = BlenderMCPServer(port=scene.blendermcp_port)

        # Start the server
        bpy.types.blendermcp_server.start()
        scene.blendermcp_server_running = bpy.types.blendermcp_server.running

        return {"FINISHED"}


# Operator to stop the server
class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server

        scene.blendermcp_server_running = False

        return {"FINISHED"}


# Operator to open Terms and Conditions
class BLENDERMCP_OT_OpenTerms(bpy.types.Operator):
    bl_idname = "blendermcp.open_terms"
    bl_label = "View Terms and Conditions"
    bl_description = "Open the Terms and Conditions document"

    def execute(self, context):
        # Open the Terms and Conditions on GitHub
        terms_url = (
            "https://github.com/ahujasid/blender-mcp/blob/main/TERMS_AND_CONDITIONS.md"
        )
        try:
            import webbrowser

            webbrowser.open(terms_url)
            self.report({"INFO"}, "Terms and Conditions opened in browser")
        except Exception as e:
            self.report({"ERROR"}, f"Could not open Terms and Conditions: {str(e)}")

        return {"FINISHED"}
