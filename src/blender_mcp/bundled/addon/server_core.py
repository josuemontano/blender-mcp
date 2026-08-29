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
from .handlers.mesh import MeshHandlersMixin
from .handlers.model import ModelHandlersMixin
from .handlers.nd import NDHandlersMixin
from .handlers.polyhaven import PolyhavenHandlersMixin
from .handlers.sketchfab import SketchfabHandlersMixin
from .handlers.viewport import ViewportHandlersMixin
from .helpers import get_mesh_object, paginate, get_blendermcp_addon_preferences


class BlenderMCPServer(
    ViewportHandlersMixin,
    MeshHandlersMixin,
    ModelHandlersMixin,
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
                response_json = json.dumps(response)
            except Exception as e:
                print(f"Error executing command: {e!s}")
                traceback.print_exc()
                response_json = json.dumps({"status": "error", "message": str(e)})

            try:
                client.sendall(response_json.encode("utf-8"))
            except Exception:
                print("Failed to send response - client disconnected")

        return 0.05

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
                    try:
                        # Try to parse command
                        command = json.loads(buffer.decode("utf-8"))
                        buffer = b""

                        # Hand off to the main thread. Never call
                        # bpy.app.timers.register() from here - it is not
                        # thread-safe and the callback can be silently lost.
                        print(f"Queued command: {command.get('type')}")
                        self.command_queue.put((command, client))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        # Incomplete data, wait for more. A multi-byte UTF-8
                        # character can land split across a recv() chunk
                        # boundary, which fails decode() before json.loads()
                        # ever runs - that's incomplete data too, not garbage.
                        pass
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
            "get_scene_info": self.get_scene_info,
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
            "model_match_reference": self.model_match_reference,
            "model_refine": self.model_refine,
            "add_procedural_displacement": self.add_procedural_displacement,
            "model_mirror": self.model_mirror,
            "model_array": self.model_array,
            "model_radial_array": self.model_radial_array,
        }

        # Add Polyhaven handlers only if enabled
        if bpy.context.scene.blendermcp_use_polyhaven:
            polyhaven_handlers = {
                "get_polyhaven_categories": self.get_polyhaven_categories,
                "search_polyhaven_assets": self.search_polyhaven_assets,
                "download_polyhaven_asset": self.download_polyhaven_asset,
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
                "nd_clear_materials": self.nd_clear_materials,
                "nd_set_lod_suffix": self.nd_set_lod_suffix,
                "nd_name_sync": self.nd_name_sync,
                "nd_single_vertex": self.nd_single_vertex,
                "nd_clear_edge_marks": self.nd_clear_edge_marks,
                "nd_clear_vertex_groups": self.nd_clear_vertex_groups,
                "nd_apply_modifiers": self.nd_apply_modifiers,
                "nd_viewport_toggle": self.nd_viewport_toggle,
                "nd_capture_utils": self.nd_capture_utils,
            }
            handlers.update(nd_handlers)

        return handlers

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
                result = handler(**params)
                print("Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {e!s}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

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

    def get_scene_info(self, limit=25, offset=0):
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
            print(f"Error in get_scene_info: {e!s}")
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

        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [
                obj.rotation_euler.x,
                obj.rotation_euler.y,
                obj.rotation_euler.z,
            ],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
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
