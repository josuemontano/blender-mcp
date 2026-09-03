import itertools
import json
import os
import queue
import socket
import threading
import time
import traceback

from contextlib import suppress

import bpy
import mathutils

from . import ADDON_PROTOCOL_VERSION, bl_info
from .handlers.camera import CameraHandlersMixin
from .handlers.character_rigging import CharacterRiggingHandlersMixin
from .handlers.cloth import ClothHandlersMixin
from .handlers.lighting import LightingHandlers
from .handlers.liquid import LiquidHandlersMixin
from .handlers.mesh import MeshHandlersMixin
from .handlers.model import ModelHandlersMixin
from .handlers.nd import NDHandlersMixin
from .handlers.polyhaven import PolyhavenHandlersMixin
from .handlers.retopology import RetopologyHandlersMixin
from .handlers.rigid_body import RigidBodyHandlersMixin
from .handlers.sketchfab import SketchfabHandlersMixin
from .handlers.viewport import ViewportHandlersMixin
from .helpers import get_blendermcp_addon_preferences, get_mesh_object, paginate, sync_from_editmode
from .transaction import mutation_transaction


class HandlerReportedError(Exception):
    """
    A handler reported failure by *returning* a failure shape instead of
    raising (e.g. {"error": ...} from a provider import).

    Raised inside the mutation transaction so a partially-applied request rolls
    back like any other failure, instead of committing the partial state and
    pushing an undo checkpoint. Its message is what the client receives.
    """


def _handler_failure_message(result):
    """
    Detect a handler that returned a failure shape rather than raising.

    Deliberately mirrors connection.ad_hoc_failure_message on the server side;
    the two live in separate runtimes (the addon must not import server code,
    and vice versa), so the shared shape contract is duplicated on purpose -
    keep the two in sync. A {"cancelled": True} outcome (ND operators the user
    cancelled) is NOT a failure and must fall through.

    Args:
        result: The value a handler returned.

    Returns:
        str | None: The failure message if `result` is a known failure shape,
        else None.

    """
    if isinstance(result, dict):
        if result.get("succeed") is False:
            return str(result.get("error") or result)
        error = result.get("error")
        if error:
            return str(error)
    return None


class BlenderMCPServer(
    ViewportHandlersMixin,
    CameraHandlersMixin,
    LightingHandlers,
    CharacterRiggingHandlersMixin,
    RigidBodyHandlersMixin,
    RetopologyHandlersMixin,
    MeshHandlersMixin,
    ModelHandlersMixin,
    ClothHandlersMixin,
    LiquidHandlersMixin,
    NDHandlersMixin,
    PolyhavenHandlersMixin,
    SketchfabHandlersMixin,
):
    """Serve MCP commands from clients through the Blender addon."""

    def __init__(self, host="localhost", port=9876) -> None:
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None
        # Commands are pushed here by client threads and drained by a single
        # timer running on Blender's main thread. bpy.app.timers is not
        # thread-safe, so registering a timer per command (the previous
        # approach) could silently drop the callback - on Windows especially -
        # leaving the client blocked in recv() until its socket timeout.
        self.command_queue = queue.Queue()
        # Live client sockets, so stop() can unblock threads parked in recv().
        self._clients = set()
        self._clients_lock = threading.Lock()

    def _get_config_value(self, scene_attr, pref_attr=None, env_var=None):
        """
        Read config in order: addon preferences -> scene -> env var.

        Args:
            scene_attr: Value for scene attr.
            pref_attr: Value for pref attr.
            env_var: Value for env var.

        Returns:
            Result produced by the operation.

        """
        prefs = get_blendermcp_addon_preferences()
        if prefs and pref_attr:
            pref_value = getattr(prefs, pref_attr, "")
            if pref_value:
                return pref_value

        scene_value = getattr(bpy.context.scene, scene_attr, "")
        if scene_value:
            return scene_value

        if env_var:
            env_value = os.getenv(env_var, "")
            if env_value:
                return env_value
        return ""

    def get_sketchfab_api_key(self):
        return self._get_config_value(
            "blendermcp_sketchfab_api_key",
            "sketchfab_api_key",
            "BLENDERMCP_SKETCHFAB_API_KEY",
        )

    def start(self) -> None:
        if bpy.app.background:
            print(
                "BlenderMCP: cannot start server in background mode (blender -b) - commands would never execute\n"
                "BlenderMCP: run Blender with a GUI, or use a virtual display: xvfb-run -a blender"
            )
            return

        if self.running:
            print("Server is already running")
            return

        self.running = True

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            # Backlog of 1 meant a reconnecting client could complete the TCP
            # handshake and then never be accept()ed - a connection that looks
            # established but is never serviced.
            self.socket.listen(5)

            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            # start() is called from the operator, i.e. the main thread, so
            # this is the only safe place to touch bpy.app.timers.
            if not bpy.app.timers.is_registered(self.drain_command_queue):
                bpy.app.timers.register(self.drain_command_queue, persistent=True)

            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {e!s}")
            self.stop()

    def stop(self) -> None:
        self.running = False

        try:
            if bpy.app.timers.is_registered(self.drain_command_queue):
                bpy.app.timers.unregister(self.drain_command_queue)
        except Exception:
            pass

        # Close socket
        if self.socket:
            with suppress(Exception):
                self.socket.close()
            self.socket = None

        # Shut down live client sockets. Without this, handler threads stay
        # parked in a blocking recv() forever; being daemon threads they then
        # outlive the restart and close connections the new server owns
        # (the WinError 10054 seen after toggling the addon).
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            with suppress(Exception):
                client.shutdown(socket.SHUT_RDWR)
            with suppress(Exception):
                client.close()

        # Drop any commands that will never be serviced now.
        while True:
            try:
                self.command_queue.get_nowait()
            except queue.Empty:
                break

        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except Exception:
                pass
            self.server_thread = None

        print("BlenderMCP server stopped")

    def _server_loop(self) -> None:
        """Main server loop in a separate thread."""
        print("Server thread started")
        self.socket.settimeout(1.0)  # Timeout to allow for stopping

        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")

                    # Handle client in a separate thread
                    client_thread = threading.Thread(target=self.handle_client, args=(client,))
                    client_thread.daemon = True
                    client_thread.start()
                except TimeoutError:
                    # Just check running condition
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {e!s}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {e!s}")
                if not self.running:
                    break
                time.sleep(0.5)

        print("Server thread stopped")

    def drain_command_queue(self) -> float | None:
        """
        Run queued commands on Blender's main thread.

        Registered once by start(); returns the poll interval so Blender keeps
        calling it. All bpy access happens here, on the main thread.

        Returns:
            Result produced by the operation.

        """
        if not self.running:
            return None

        while True:
            try:
                command, client = self.command_queue.get_nowait()
            except queue.Empty:
                break

            try:
                response = self.execute_command(command)
            except Exception as e:
                print(f"Error executing command: {e!s}")
                traceback.print_exc()
                response = {"status": "error", "message": str(e)}

            # Echo the request id (if any) back so the client can match this
            # response to the command it sent instead of relying purely on
            # stream ordering.
            response["id"] = command.get("id")

            try:
                # Newline-terminated - see handle_client for why this
                # protocol needs explicit framing.
                client.sendall(json.dumps(response).encode("utf-8") + b"\n")
            except Exception:
                print("Failed to send response - client disconnected")

        return 0.05

    # Messages are newline-delimited JSON. Bound how large a single message
    # can grow before we give up on it - without this, malformed input (or
    # a client that never sends a terminator) would make `buffer` grow
    # forever. The largest legitimate payloads are paginated mesh/element
    # dumps (capped well under 1000 elements); screenshots are written to
    # disk and never cross the socket. 64 MiB is generous headroom above that.
    _MAX_MESSAGE_BYTES = 64 * 1024 * 1024

    def _decode_and_queue_frame(self, line: bytes, client) -> bool:
        r"""
        Decode one framed line, parse it as JSON, and queue it as a command.

        Args:
            line: The bytes of one `\n`-delimited frame (never containing
                the terminator itself).
            client: The socket the frame arrived on, passed through to the
                queued command so its response goes back to the right peer.

        Returns:
            False if `line` exceeds `_MAX_MESSAGE_BYTES` - the caller must
            treat this as a protocol violation and drop the connection.
            True otherwise, including when the line is malformed (discarded,
            not fatal).

        """
        if len(line) > self._MAX_MESSAGE_BYTES:
            print(f"Client sent an oversized frame ({len(line)} bytes) - disconnecting")
            return False
        try:
            command = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"Discarding malformed message: {e!s}")
            return True

        # Hand off to the main thread. Never call
        # bpy.app.timers.register() from here - it is not thread-safe and
        # the callback can be silently lost.
        print(f"Queued command: {command.get('type')}")
        self.command_queue.put((command, client))
        return True

    def handle_client(self, client) -> None:
        """
        Handle connected client.

        Args:
            client: Value for client.

        """
        print("Client handler started")
        # A finite timeout keeps this loop responsive to self.running instead
        # of parking in recv() forever.
        client.settimeout(1.0)
        with self._clients_lock:
            self._clients.add(client)
        buffer = b""

        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break

                    buffer += data

                    # A single json.loads() over the whole buffer can't tell
                    # "incomplete message" apart from "complete message plus
                    # the start of the next one" - both raise
                    # json.JSONDecodeError, and treating the latter as
                    # "incomplete, wait for more" means the buffer can never
                    # parse again (the trailing bytes are never valid on
                    # their own). Splitting on the newline terminator each
                    # side appends after every message removes that
                    # ambiguity: each complete line is exactly one message.
                    oversized_frame = False
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line:
                            continue
                        if not self._decode_and_queue_frame(line, client):
                            oversized_frame = True
                            break

                    if oversized_frame:
                        break

                    if len(buffer) > self._MAX_MESSAGE_BYTES:
                        print(
                            f"Client sent an oversized message without a terminator "
                            f"({len(buffer)} bytes) - disconnecting"
                        )
                        break
                except TimeoutError:
                    # Expected; loop round and re-check self.running.
                    continue
                except Exception as e:
                    print(f"Error receiving data: {e!s}")
                    break
        except Exception as e:
            print(f"Error in client handler: {e!s}")
        finally:
            with self._clients_lock:
                self._clients.discard(client)
            with suppress(Exception):
                client.close()
            print("Client handler stopped")

    def execute_command(self, command):
        """
        Execute a command in the main Blender thread.

        Args:
            command: Command requested by the client.

        Returns:
            Result produced by the operation.

        """
        try:
            return self.execute_command_internal(command)
        except Exception as e:
            print(f"Error executing command: {e!s}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _build_command_handlers(self):
        """
        Build the cmd_type -> handler map, including conditionally-enabled providers.

        Shared by execute_command_internal (dispatch) and get_addon_info
        (advertised capabilities), so the two can never drift apart.

        Returns:
            Result produced by the operation.

        """
        # Base handlers that are always available
        handlers = {
            "list_scene_objects": self.list_scene_objects,
            "get_addon_info": self.get_addon_info,
            "get_object_info": self.get_object_info,
            "get_mesh_data": self.get_mesh_data,
            "get_viewport_screenshot": self.get_viewport_screenshot,
            "execute_code": self.execute_code,
            "get_polyhaven_status": self.get_polyhaven_status,
            "get_sketchfab_status": self.get_sketchfab_status,
            "create_primitive": self.create_primitive,
            "mesh_extrude": self.mesh_extrude,
            "mesh_inset": self.mesh_inset,
            "mesh_bevel": self.mesh_bevel,
            "mesh_bridge": self.mesh_bridge,
            "mesh_boolean": self.mesh_boolean,
            "mesh_subdivide": self.mesh_subdivide,
            "mesh_remesh": self.mesh_remesh,
            "mesh_solidify": self.mesh_solidify,
            "mesh_symmetrize": self.mesh_symmetrize,
            "create_retopology_target": self.create_retopology_target,
            "inspect_retopology": self.inspect_retopology,
            "analyze_surface_conformity": self.analyze_surface_conformity,
            "manage_retopology_checkpoint": self.manage_retopology_checkpoint,
            "configure_surface_projection": self.configure_surface_projection,
            "project_mesh_elements": self.project_mesh_elements,
            "build_quad_patch": self.build_quad_patch,
            "extend_boundary": self.extend_boundary,
            "fill_boundary_quads": self.fill_boundary_quads,
            "reroute_topology": self.reroute_topology,
            "relax_topology": self.relax_topology,
            "redistribute_edge_loop": self.redistribute_edge_loop,
            "configure_retopology_symmetry": self.configure_retopology_symmetry,
            "validate_retopology": self.validate_retopology,
            "create_retopology_guides": self.create_retopology_guides,
            "create_surface_section": self.create_surface_section,
            "set_retopology_features": self.set_retopology_features,
            "add_support_loops": self.add_support_loops,
            "transfer_mesh_attributes": self.transfer_mesh_attributes,
            "unwrap_retopology_uvs": self.unwrap_retopology_uvs,
            "create_bake_cage": self.create_bake_cage,
            "bake_retopology_maps": self.bake_retopology_maps,
            "test_deformation": self.test_deformation,
            "generate_quadriflow_draft": self.generate_quadriflow_draft,
            "fit_surface_primitive": self.fit_surface_primitive,
            "bind_surface_deformation": self.bind_surface_deformation,
            "generate_retopology_lods": self.generate_retopology_lods,
            "copy_object_transform": self.copy_object_transform,
            "add_subdivision_surface_modifier": self.add_subdivision_surface_modifier,
            "add_displace_modifier": self.add_displace_modifier,
            "model_mirror": self.model_mirror,
            "model_array": self.model_array,
            "model_radial_array": self.model_radial_array,
            "viewport_overlay_toggle": self.viewport_overlay_toggle,
            "clear_materials": self.clear_materials,
            "clear_vertex_groups": self.clear_vertex_groups,
            "clear_edge_marks": self.clear_edge_marks,
            "sync_data_name": self.sync_data_name,
            "get_character_rig_info": self.get_character_rig_info,
            "get_skinning_info": self.get_skinning_info,
            "create_armature": self.create_armature,
            "patch_armature_bones": self.patch_armature_bones,
            "mirror_armature_bones": self.mirror_armature_bones,
            "manage_bone_collections": self.manage_bone_collections,
            "configure_armature_bones": self.configure_armature_bones,
            "bind_mesh_to_armature": self.bind_mesh_to_armature,
            "set_skin_weights": self.set_skin_weights,
            "clean_skin_weights": self.clean_skin_weights,
            "add_pose_bone_constraint": self.add_pose_bone_constraint,
            "validate_character_rig": self.validate_character_rig,
            "transfer_skin_weights": self.transfer_skin_weights,
            "create_ik_chain": self.create_ik_chain,
            "create_ik_fk_limb": self.create_ik_fk_limb,
            "create_spline_ik_rig": self.create_spline_ik_rig,
            "configure_bendy_bones": self.configure_bendy_bones,
            "create_rig_property_driver": self.create_rig_property_driver,
            "assign_bone_custom_shapes": self.assign_bone_custom_shapes,
            "set_character_pose": self.set_character_pose,
            "keyframe_character_pose": self.keyframe_character_pose,
            "create_shape_key_controls": self.create_shape_key_controls,
            "get_rigid_body_scene_info": self.get_rigid_body_scene_info,
            "get_rigid_body_object_info": self.get_rigid_body_object_info,
            "get_rigid_body_constraint_info": self.get_rigid_body_constraint_info,
            "configure_rigid_body_world": self.configure_rigid_body_world,
            "add_rigid_bodies": self.add_rigid_bodies,
            "configure_rigid_bodies": self.configure_rigid_bodies,
            "set_rigid_body_mass": self.set_rigid_body_mass,
            "set_rigid_body_collision_layers": self.set_rigid_body_collision_layers,
            "create_rigid_body_collision_proxy": self.create_rigid_body_collision_proxy,
            "create_rigid_body_constraint": self.create_rigid_body_constraint,
            "configure_rigid_body_constraint": self.configure_rigid_body_constraint,
            "validate_rigid_body_setup": self.validate_rigid_body_setup,
            "remove_rigid_body_components": self.remove_rigid_body_components,
            "animate_rigid_body_release": self.animate_rigid_body_release,
            "create_compound_rigid_body": self.create_compound_rigid_body,
            "create_rigid_body_constraint_network": self.create_rigid_body_constraint_network,
            "prepare_fracture_rigid_bodies": self.prepare_fracture_rigid_bodies,
            "create_rigid_body_chain": self.create_rigid_body_chain,
            "setup_animated_passive_collider": self.setup_animated_passive_collider,
            "configure_rigid_body_force_fields": self.configure_rigid_body_force_fields,
            "sample_rigid_body_simulation": self.sample_rigid_body_simulation,
            "manage_rigid_body_cache": self.manage_rigid_body_cache,
            "bake_rigid_bodies_to_keyframes": self.bake_rigid_bodies_to_keyframes,
            "create_rigid_body_debris_field": self.create_rigid_body_debris_field,
            "create_rigid_body_proxy_rig": self.create_rigid_body_proxy_rig,
            "create_ragdoll_rig": self.create_ragdoll_rig,
            "bake_ragdoll_to_armature": self.bake_ragdoll_to_armature,
            "export_rigid_body_animation": self.export_rigid_body_animation,
            "analyze_rigid_body_performance": self.analyze_rigid_body_performance,
            "get_cloth_simulation_info": self.get_cloth_simulation_info,
            "get_cloth_object_info": self.get_cloth_object_info,
            "get_liquid_simulation_info": self.get_liquid_simulation_info,
            "get_fluid_object_info": self.get_fluid_object_info,
            "get_camera_rig_info": self.get_camera_rig_info,
            "create_camera": self.create_camera,
            "configure_camera": self.configure_camera,
            "set_scene_camera": self.set_scene_camera,
            "aim_camera": self.aim_camera,
            "create_camera_target": self.create_camera_target,
            "frame_camera_on_objects": self.frame_camera_on_objects,
            "create_orbit_camera_rig": self.create_orbit_camera_rig,
            "create_dolly_camera_rig": self.create_dolly_camera_rig,
            "create_crane_camera_rig": self.create_crane_camera_rig,
            "create_camera_path_rig": self.create_camera_path_rig,
            "configure_camera_dof": self.configure_camera_dof,
            "keyframe_camera_rig": self.keyframe_camera_rig,
            "set_camera_interpolation": self.set_camera_interpolation,
            "create_focus_pull": self.create_focus_pull,
            "create_dolly_zoom": self.create_dolly_zoom,
            "add_camera_shake": self.add_camera_shake,
            "create_camera_markers": self.create_camera_markers,
            "match_camera_transform": self.match_camera_transform,
            "duplicate_camera_rig": self.duplicate_camera_rig,
            "add_camera_constraint": self.add_camera_constraint,
            "configure_camera_render_gate": self.configure_camera_render_gate,
            "validate_camera_rig": self.validate_camera_rig,
            "list_lights": self.list_lights,
            "inspect_light": self.inspect_light,
            "inspect_lighting_setup": self.inspect_lighting_setup,
            "validate_lighting_setup": self.validate_lighting_setup,
            "create_light": self.create_light,
            "configure_light": self.configure_light,
            "aim_light": self.aim_light,
            "configure_light_linking": self.configure_light_linking,
            "configure_world_background": self.configure_world_background,
            "configure_hdri_environment": self.configure_hdri_environment,
            "configure_procedural_sky": self.configure_procedural_sky,
            "configure_lighting_quality": self.configure_lighting_quality,
            "configure_color_management": self.configure_color_management,
            "render_lighting_preview": self.render_lighting_preview,
            "list_materials": self.list_materials,
            "inspect_material": self.inspect_material,
            "create_pbr_material": self.create_pbr_material,
            "configure_pbr_material": self.configure_pbr_material,
            "assign_material": self.assign_material,
            "configure_texture_mapping": self.configure_texture_mapping,
            "list_texture_images": self.list_texture_images,
            "load_texture_image": self.load_texture_image,
            "configure_texture_image": self.configure_texture_image,
            "apply_pbr_texture_set": self.apply_pbr_texture_set,
            "save_texture_image": self.save_texture_image,
            "render_pbr_material_preview": self.render_pbr_material_preview,
            "manage_uv_maps": self.manage_uv_maps,
            "set_uv_seams": self.set_uv_seams,
            "unwrap_uvs": self.unwrap_uvs,
            "optimize_uv_layout": self.optimize_uv_layout,
            "inspect_uv_layout": self.inspect_uv_layout,
            "bake_texture_map": self.bake_texture_map,
            "validate_pbr_asset": self.validate_pbr_asset,
            "add_cloth_simulation": self.add_cloth_simulation,
            "configure_cloth_material": self.configure_cloth_material,
            "configure_cloth_solver": self.configure_cloth_solver,
            "set_cloth_vertex_weights": self.set_cloth_vertex_weights,
            "configure_cloth_pinning": self.configure_cloth_pinning,
            "configure_cloth_collisions": self.configure_cloth_collisions,
            "add_cloth_collider": self.add_cloth_collider,
            "configure_cloth_collider": self.configure_cloth_collider,
            "estimate_cloth_resources": self.estimate_cloth_resources,
            "validate_cloth_setup": self.validate_cloth_setup,
            "configure_cloth_sewing": self.configure_cloth_sewing,
            "configure_cloth_pressure": self.configure_cloth_pressure,
            "configure_cloth_internal_springs": self.configure_cloth_internal_springs,
            "configure_cloth_rest_shape": self.configure_cloth_rest_shape,
            "configure_cloth_field_weights": self.configure_cloth_field_weights,
            "animate_cloth_parameters": self.animate_cloth_parameters,
            "create_cloth_attachment": self.create_cloth_attachment,
            "create_character_cloth_setup": self.create_character_cloth_setup,
            "sample_cloth_simulation": self.sample_cloth_simulation,
            "manage_cloth_cache": self.manage_cloth_cache,
            "remove_cloth_components": self.remove_cloth_components,
            "create_cloth_proxy_rig": self.create_cloth_proxy_rig,
            "duplicate_cloth_setup_variant": self.duplicate_cloth_setup_variant,
            "prepare_cloth_render_surface": self.prepare_cloth_render_surface,
            "export_cloth_simulation": self.export_cloth_simulation,
            "analyze_cloth_performance": self.analyze_cloth_performance,
            "create_liquid_domain": self.create_liquid_domain,
            "fit_liquid_domain": self.fit_liquid_domain,
            "configure_liquid_solver": self.configure_liquid_solver,
            "add_liquid_flow": self.add_liquid_flow,
            "configure_liquid_flow": self.configure_liquid_flow,
            "add_liquid_effector": self.add_liquid_effector,
            "configure_liquid_effector": self.configure_liquid_effector,
            "configure_liquid_scope_and_boundaries": self.configure_liquid_scope_and_boundaries,
            "estimate_liquid_resources": self.estimate_liquid_resources,
            "validate_liquid_setup": self.validate_liquid_setup,
            "configure_liquid_mesh": self.configure_liquid_mesh,
            "configure_liquid_secondary_particles": self.configure_liquid_secondary_particles,
            "configure_liquid_diffusion": self.configure_liquid_diffusion,
            "animate_liquid_flow": self.animate_liquid_flow,
            "create_liquid_guide": self.create_liquid_guide,
            "configure_liquid_force_fields": self.configure_liquid_force_fields,
            "create_liquid_material": self.create_liquid_material,
            "create_secondary_particle_render_setup": self.create_secondary_particle_render_setup,
            "sample_liquid_simulation": self.sample_liquid_simulation,
            "manage_liquid_cache": self.manage_liquid_cache,
            "remove_fluid_components": self.remove_fluid_components,
            "create_liquid_proxy_rig": self.create_liquid_proxy_rig,
            "duplicate_liquid_setup_variant": self.duplicate_liquid_setup_variant,
            "prepare_liquid_render_mesh": self.prepare_liquid_render_mesh,
            "export_liquid_simulation": self.export_liquid_simulation,
            "analyze_liquid_performance": self.analyze_liquid_performance,
        }

        # Add Polyhaven handlers only if enabled
        if bpy.context.scene.blendermcp_use_polyhaven:
            polyhaven_handlers = {
                "get_polyhaven_categories": self.get_polyhaven_categories,
                "search_polyhaven_assets": self.search_polyhaven_assets,
                "import_polyhaven_asset": self.import_polyhaven_asset,
                "apply_polyhaven_texture": self.apply_polyhaven_texture,
            }
            handlers.update(polyhaven_handlers)

        # Add Sketchfab handlers only if enabled
        if bpy.context.scene.blendermcp_use_sketchfab:
            sketchfab_handlers = {
                "search_sketchfab_models": self.search_sketchfab_models,
                "get_sketchfab_model_preview": self.get_sketchfab_model_preview,
                "download_sketchfab_model": self.download_sketchfab_model,
            }
            handlers.update(sketchfab_handlers)

        # Add ND (HugeMenace) handlers only if enabled
        if bpy.context.scene.blendermcp_use_nd:
            nd_handlers = {
                "nd_boolean": self.nd_boolean,
                "nd_mark_as_util": self.nd_mark_as_util,
                "nd_clean_utils": self.nd_clean_utils,
                "nd_create_id_material": self.nd_create_id_material,
                "nd_bulk_create_id_materials": self.nd_bulk_create_id_materials,
                "nd_set_lod_suffix": self.nd_set_lod_suffix,
                "nd_single_vertex": self.nd_single_vertex,
                "nd_apply_modifiers": self.nd_apply_modifiers,
                "nd_pulse_viewport_toggle": self.nd_pulse_viewport_toggle,
                "nd_capture_utils": self.nd_capture_utils,
            }
            handlers.update(nd_handlers)

        return handlers

    # Commands that never mutate bpy.data. Everything else gets wrapped in
    # mutation_transaction() - snapshotting/diffing/rolling back these would
    # just be pointless overhead and undo-stack noise.
    _READ_ONLY_COMMANDS = frozenset(
        {
            "list_scene_objects",
            "get_object_info",
            "get_mesh_data",
            "get_viewport_screenshot",
            "get_polyhaven_status",
            "get_sketchfab_status",
            "get_nd_status",
            "get_polyhaven_categories",
            "search_polyhaven_assets",
            "search_sketchfab_models",
            "get_sketchfab_model_preview",
            "get_cloth_simulation_info",
            "get_cloth_object_info",
            "get_camera_rig_info",
            "get_character_rig_info",
            "get_skinning_info",
            "validate_character_rig",
            "get_rigid_body_scene_info",
            "get_rigid_body_object_info",
            "get_rigid_body_constraint_info",
            "validate_rigid_body_setup",
            "validate_camera_rig",
            "list_lights",
            "inspect_light",
            "inspect_lighting_setup",
            "validate_lighting_setup",
            "list_materials",
            "inspect_material",
            "list_texture_images",
            "inspect_uv_layout",
            "validate_pbr_asset",
            "estimate_cloth_resources",
            "validate_cloth_setup",
            "get_liquid_simulation_info",
            "get_fluid_object_info",
            "estimate_liquid_resources",
            "validate_liquid_setup",
            "inspect_retopology",
            "validate_retopology",
            "test_deformation",
        }
    )

    def execute_command_internal(self, command):
        """
        Internal command execution with proper context.

        Args:
            command: Command requested by the client.

        Returns:
            Result produced by the operation.

        """
        cmd_type = command.get("type")
        params = command.get("params", {})

        # Trivial liveness check. Touches no bpy data, so a successful ping
        # alongside a failing command isolates data access from transport.
        if cmd_type == "ping":
            return {"status": "success", "result": {"pong": True}}

        # Add a handler for checking PolyHaven status
        if cmd_type == "get_polyhaven_status":
            return {"status": "success", "result": self.get_polyhaven_status()}

        # Add a handler for checking ND status
        if cmd_type == "get_nd_status":
            return {"status": "success", "result": self.get_nd_status()}

        handlers = self._build_command_handlers()

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = self._run_handler(cmd_type, handler, params)
                print("Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {e!s}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    # Params that name an *existing* object a mutating command touches, so the
    # transaction can capture that object's state and restore it on failure.
    # "name" is excluded: in create_primitive it names a new object, not one to
    # protect.
    _TARGET_NAME_PARAMS = (
        "object_name",
        "camera_name",
        "light_name",
        "curve_object_name",
        "cutter_object_name",
        "reference_object_name",
        "target_object_name",
        "cloth_object_name",
        "garment_object_name",
        "armature_object_name",
        "mesh_object_name",
        "source_mesh_name",
        "target_mesh_name",
        "constraint_object_name",
        "object1_name",
        "object2_name",
        "low_resolution_source_name",
        "render_object_name",
        "proxy_object_name",
        "source_object_name",
        "destination_name",
        "movement_object_name",
        "owner_name",
        "source_root_name",
        "root_object_name",
        "domain_object_name",
        "guide_object_name",
        "guide_parent_domain_object_name",
        "instance_object_name",
    )
    _TARGET_NAMES_PARAMS = (
        "object_names",
        "camera_names",
        "body_collider_object_names",
        "source_object_names",
        "collider_object_names",
        "mesh_object_names",
        "armature_object_names",
        "body_names",
        "child_object_names",
        "piece_object_names",
    )

    # Commands that edit an existing object's mesh geometry. Only these back up
    # the mesh datablock (a full copy) so a failed edit can be swapped back;
    # transform-only commands (e.g. copy_object_transform) skip that cost.
    _GEOMETRY_MUTATING_COMMANDS = frozenset(
        {
            "mesh_extrude",
            "mesh_inset",
            "mesh_bevel",
            "mesh_bridge",
            "mesh_boolean",
            "mesh_subdivide",
            "mesh_remesh",
            "mesh_solidify",
            "mesh_symmetrize",
            "analyze_surface_conformity",
            "manage_retopology_checkpoint",
            "configure_surface_projection",
            "project_mesh_elements",
            "build_quad_patch",
            "extend_boundary",
            "fill_boundary_quads",
            "reroute_topology",
            "relax_topology",
            "redistribute_edge_loop",
            "set_retopology_features",
            "add_support_loops",
            "transfer_mesh_attributes",
            "unwrap_retopology_uvs",
            "configure_cloth_sewing",
            "fit_liquid_domain",
        }
    )

    def _resolve_targets(self, params):
        """
        Resolve the existing objects a mutating request will touch, from its params.

        Missing objects are skipped (the handler will raise its own clear error);
        duplicates are collapsed while preserving order.

        Args:
            params: The command's params dict.

        Returns:
            list: Existing bpy objects named by the target params.

        """
        names = []
        for key in self._TARGET_NAME_PARAMS:
            value = params.get(key)
            if isinstance(value, str):
                names.append(value)
        for key in self._TARGET_NAMES_PARAMS:
            value = params.get(key)
            if isinstance(value, (list, tuple)):
                names.extend(name for name in value if isinstance(name, str))
        for record in params.get("targets", ()):
            if isinstance(record, dict) and isinstance(record.get("object_name"), str):
                names.append(record["object_name"])
        for record in params.get("fields", ()):
            if isinstance(record, dict) and isinstance(record.get("object_name"), str):
                names.append(record["object_name"])
        for record_key in ("sources", "mappings", "bodies"):
            for record in params.get(record_key, ()):
                if not isinstance(record, dict):
                    continue
                for name_key in (
                    "object_name",
                    "render_object_name",
                    "proxy_object_name",
                    "low_resolution_source_name",
                    "convex_source_object_name",
                ):
                    if isinstance(record.get(name_key), str):
                        names.append(record[name_key])

        objects = []
        seen = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            obj = bpy.data.objects.get(name)
            if obj is not None:
                objects.append(obj)
        return objects

    def _run_handler(self, cmd_type, handler, params):
        """
        Call a resolved handler, wrapping mutating commands in mutation_transaction.

        A handler that reports failure by returning a failure shape (rather than
        raising) is converted to a HandlerReportedError inside the transaction,
        so its partial mutation rolls back instead of being committed. On
        success, any undo-unavailability warning is merged into the result.

        Args:
            cmd_type: The MCP command type, used to pick read-only vs. mutating dispatch.
            handler: The bound handler method to call.
            params: Keyword arguments to call the handler with.

        Returns:
            Result produced by the handler.

        """
        dynamic_read_only = (
            cmd_type == "manage_retopology_checkpoint" and str(params.get("action", "")).upper() in {"LIST", "COMPARE"}
        ) or (cmd_type == "analyze_surface_conformity" and not params.get("create_heat_map", False))
        dynamic_read_only = dynamic_read_only or (cmd_type == "configure_cloth_sewing" and params.get("dry_run", True))
        dynamic_read_only = dynamic_read_only or (
            cmd_type == "manage_cloth_cache" and str(params.get("action", "INSPECT")).upper() == "INSPECT"
        )
        dynamic_read_only = dynamic_read_only or (
            cmd_type == "manage_liquid_cache" and str(params.get("action", "STATUS")).upper() == "STATUS"
        )
        dynamic_read_only = dynamic_read_only or (
            cmd_type == "analyze_liquid_performance" and not params.get("measure_replay_evaluation", False)
        )
        dynamic_read_only = dynamic_read_only or (
            cmd_type == "create_camera_markers" and str(params.get("action", "")).upper() == "LIST"
        )
        dynamic_read_only = dynamic_read_only or (
            cmd_type == "manage_rigid_body_cache" and str(params.get("action", "INSPECT")).upper() == "INSPECT"
        )
        dynamic_read_only = dynamic_read_only or (
            cmd_type == "analyze_rigid_body_performance" and not params.get("sample_frames")
        )
        if cmd_type in self._READ_ONLY_COMMANDS or dynamic_read_only:
            return handler(**params)

        targets = self._resolve_targets(params)
        capture_geometry = cmd_type in self._GEOMETRY_MUTATING_COMMANDS
        with mutation_transaction(cmd_type, targets, capture_geometry) as txn:
            result = handler(**params)
            failure = _handler_failure_message(result)
            if failure is not None:
                raise HandlerReportedError(failure)
            warning = txn.commit()
            if warning and isinstance(result, dict):
                result = {**result, "warnings": [*result.get("warnings", []), warning]}
            return result

    def get_addon_info(self):
        """
        Version/capability handshake for the MCP server (and install tooling).

        Returns:
            Result produced by the operation.

        """
        return {
            "name": bl_info.get("name", "Blender MCP"),
            "addon_version": list(bl_info.get("version", (0, 0))),
            "protocol_version": ADDON_PROTOCOL_VERSION,
            "capabilities": sorted({"ping", "get_polyhaven_status", "get_nd_status", *self._build_command_handlers()}),
            "blender_version": bpy.app.version_string,
        }

    _SCENE_INFO_MAX_LIMIT = 200

    def list_scene_objects(self, limit=25, offset=0):
        """
        Get information about the current Blender scene, paginated over its objects.

        Args:
            limit: Maximum number of items to return.
            offset: Zero-based starting position.

        Returns:
            Result produced by the operation.

        """
        try:
            print("Getting scene info...")
            scene_objects = list(bpy.context.scene.objects)
            total = len(scene_objects)
            start, end, truncated, next_offset = paginate(total, offset, limit, self._SCENE_INFO_MAX_LIMIT)

            objects = []
            for obj in scene_objects[start:end]:
                objects.append(
                    {
                        "name": obj.name,
                        "type": obj.type,
                        # Only include basic location data
                        "location": [
                            round(float(obj.location.x), 2),
                            round(float(obj.location.y), 2),
                            round(float(obj.location.z), 2),
                        ],
                    }
                )

            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": total,
                "objects": objects,
                "materials_count": len(bpy.data.materials),
                "offset": start,
                "limit": limit,
                "returned_count": len(objects),
                "truncated": truncated,
                "next_offset": next_offset,
            }

            print(f"Scene info collected: {len(objects)} of {total} objects")
            return scene_info
        except Exception as e:
            print(f"Error in list_scene_objects: {e!s}")
            traceback.print_exc()
            return {"error": str(e)}

    @staticmethod
    def get_aabb(obj):
        """
        Returns the world-space axis-aligned bounding box (AABB) of an object.

        Args:
            obj: Value for obj.

        Returns:
            the world-space axis-aligned bounding box (AABB) of an object.

        Raises:
            TypeError: If the operation cannot be completed.

        """
        if obj.type != "MESH":
            raise TypeError("Object must be a mesh")

        # Get the bounding box corners in local space
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

        # Convert to world coordinates
        world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]

        # Compute axis-aligned min/max coordinates
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners, strict=False)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners, strict=False)))

        return [[*min_corner], [*max_corner]]

    def get_object_info(self, name):
        """
        Get detailed information about a specific object.

        `location`/`rotation`/`scale` are the object's local (parent-relative)
        transform; `world_bounding_box` (mesh objects only) is the world-space
        AABB computed via `matrix_world` (see `get_aabb`) - the two live in
        different spaces and are not directly comparable for a parented or
        transformed object.

        `rotation_mode` names how to read `rotation`: one of the six Euler
        orders ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX") means
        `[x, y, z]` radians in that order; "QUATERNION" means `[w, x, y, z]`;
        "AXIS_ANGLE" means `[angle, x, y, z]`. Reading `rotation` without
        checking `rotation_mode` will misinterpret non-Euler objects.

        `mesh.vertices`/`edges`/`polygons` are base-mesh (pre-modifier)
        counts, same caveat as `get_mesh_data`.

        Args:
            name: Name to assign or look up.

        Returns:
            Result produced by the operation.

        Raises:
            ValueError: If the operation cannot be completed.

        """
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")
        sync_from_editmode(obj)

        if obj.rotation_mode == "QUATERNION":
            q = obj.rotation_quaternion
            rotation = [q.w, q.x, q.y, q.z]
        elif obj.rotation_mode == "AXIS_ANGLE":
            angle, x, y, z = obj.rotation_axis_angle
            rotation = [angle, x, y, z]
        else:
            rotation = [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z]

        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation_mode": obj.rotation_mode,
            "rotation": rotation,
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
            "modifiers": [
                {
                    "name": m.name,
                    "type": m.type,
                    "show_viewport": m.show_viewport,
                    "show_render": m.show_render,
                }
                for m in obj.modifiers
            ],
        }

        if obj.type == "MESH":
            bounding_box = self.get_aabb(obj)
            obj_info["world_bounding_box"] = bounding_box

        # Add material slots
        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        # Add mesh data if applicable
        if obj.type == "MESH" and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        return obj_info

    _MESH_DATA_ELEMENT_TYPES = ("vertices", "edges", "faces", "loops")
    _MESH_DATA_MAX_LIMIT = 1000

    @staticmethod
    def _mesh_data_vertex(v):
        return {
            "index": v.index,
            "co": [v.co.x, v.co.y, v.co.z],
            "normal": [v.normal.x, v.normal.y, v.normal.z],
            "select": bool(v.select),
        }

    @staticmethod
    def _mesh_data_edge(e):
        return {
            "index": e.index,
            "vertices": list(e.vertices),
            "select": bool(e.select),
        }

    @staticmethod
    def _mesh_data_face(f):
        return {
            "index": f.index,
            "vertices": list(f.vertices),
            "normal": [f.normal.x, f.normal.y, f.normal.z],
            "select": bool(f.select),
            "material_index": f.material_index,
        }

    def get_mesh_data(self, object_name, element_type="vertices", limit=100, offset=0, selected_only=False):
        """
        Paginated inspection of a mesh's vertices/edges/faces/loops (indices, coords, normals, selection).

        Prerequisite for index-based edits: mesh_extrude/mesh_inset/mesh_bevel/
        mesh_bridge/mesh_subdivide take raw indices with no way to discover them
        otherwise, since get_object_info only reports element counts.

        Coordinates and normals come from the object's base mesh (`obj.data`)
        in local (object-space) coordinates - modifiers are not evaluated. To
        get world-space positions, transform by the object's `matrix_world`
        (see `get_object_info`).

        Args:
            object_name: Name of the Blender object to operate on.
            element_type: Value for element type.
            limit: Maximum number of items to return.
            offset: Zero-based starting position.
            selected_only: Value for selected only.

        Returns:
            Result produced by the operation.

        Raises:
            ValueError: If the operation cannot be completed.

        """
        if element_type not in self._MESH_DATA_ELEMENT_TYPES:
            raise ValueError(f"Invalid element_type: {element_type}. Must be one of {self._MESH_DATA_ELEMENT_TYPES}")
        obj = get_mesh_object(object_name)
        sync_from_editmode(obj)
        mesh = obj.data

        if element_type == "vertices":
            all_elements = mesh.vertices
            to_dict = self._mesh_data_vertex
        elif element_type == "edges":
            all_elements = mesh.edges
            to_dict = self._mesh_data_edge
        elif element_type == "faces":
            all_elements = mesh.polygons
            to_dict = self._mesh_data_face
        else:
            if selected_only:
                raise ValueError(
                    "selected_only is not supported for element_type='loops': "
                    "MeshLoop has no selection state of its own (use 'vertices', "
                    "'edges', or 'faces' instead)"
                )
            all_elements = mesh.loops
            face_of_loop = {}
            for face in mesh.polygons:
                for loop_index in face.loop_indices:
                    face_of_loop[loop_index] = face.index
            if hasattr(mesh, "calc_normals_split"):
                mesh.calc_normals_split()

            def to_dict(loop):
                normal = loop.normal
                return {
                    "index": loop.index,
                    "vertex_index": loop.vertex_index,
                    "edge_index": loop.edge_index,
                    "face_index": face_of_loop.get(loop.index),
                    "normal": [normal.x, normal.y, normal.z],
                }

        total_unfiltered = len(all_elements)
        if selected_only:
            universe = [el for el in all_elements if el.select]
            total = len(universe)
            start, end, truncated, next_offset = paginate(total, offset, limit, self._MESH_DATA_MAX_LIMIT)
            page = universe[start:end]
        else:
            total = total_unfiltered
            start, end, truncated, next_offset = paginate(total, offset, limit, self._MESH_DATA_MAX_LIMIT)
            # islice avoids materializing the whole (possibly huge) collection
            # when the caller only asked for a small page of it.
            page = itertools.islice(all_elements, start, end)
        elements = [to_dict(el) for el in page]

        return {
            "name": obj.name,
            "element_type": element_type,
            "total": total,
            "total_unfiltered": total_unfiltered,
            "offset": start,
            "limit": limit,
            "returned_count": len(elements),
            "truncated": truncated,
            "next_offset": next_offset,
            "elements": elements,
        }
