"""MCP prompts."""

from .app import mcp


@mcp.prompt()
def asset_creation_strategy() -> str:
    """
    Defines the preferred strategy for creating assets in Blender.

    Returns:
        str: Result produced by the operation.

    """
    return """When creating 3D content in Blender, always start by checking if integrations are available:

    0. Before anything, always check the scene from get_scene_info()

    **IMPORTANT: Visual Verification**
    - Use get_viewport_screenshot() BEFORE making changes to see the current state
    - Use get_viewport_screenshot() AFTER executing code or importing assets to verify the result
    - This helps confirm your changes worked as expected and catch any visual issues

    1. First use the following tools to verify if the following integrations are enabled:
        1. PolyHaven
            Use get_integration_status(provider="polyhaven") to verify its status
            If PolyHaven is enabled:
            - For objects/models: Use download_polyhaven_asset() with asset_type="models"
            - For materials/textures: Use download_polyhaven_asset() with asset_type="textures"
            - For environment lighting: Use download_polyhaven_asset() with asset_type="hdris"
        2. Sketchfab
            Sketchfab is good at Realistic models, and has a wider variety of models than PolyHaven.
            Use get_integration_status(provider="sketchfab") to verify its status
            If Sketchfab is enabled:
            - For objects/models: First search using search_sketchfab_models() with your query
            - Then download specific models using download_sketchfab_model() with the UID
            - Note that only downloadable models can be accessed, and API key must be properly configured
            - Sketchfab has a wider variety of models than PolyHaven, especially for specific subjects
    2. For primitives and direct mesh/model editing, use the dedicated tools instead of execute_blender_code:
        - mesh_create_primitive() for cubes, spheres, cylinders, cones, tori, planes, and curves
        - mesh_extrude(), mesh_inset(), mesh_bevel(), mesh_bridge(), mesh_boolean(), mesh_subdivide(), mesh_remesh(), mesh_solidify(), mesh_symmetrize() for direct mesh edits
        - model_match_reference(), model_refine(), add_procedural_displacement(), model_mirror(), model_array(), model_radial_array() for higher-level modeling operations (use mesh_create_primitive() with purpose="blockout" for placeholder proxies)

    2.5. For non-destructive hard-surface workflows (utility objects, ID materials, LOD naming, viewport overlays), use the ND (HugeMenace) tools instead of execute_blender_code:
        - Use get_integration_status(provider="nd") to verify its status
        - nd_boolean(), nd_mark_as_util(), nd_clean_utils() for the utility-object boolean workflow
        - nd_create_id_material(), nd_bulk_create_id_materials(), nd_clear_materials(), nd_set_lod_suffix(), nd_name_sync() for export/packaging prep
        - nd_single_vertex(), nd_clear_edge_marks(), nd_clear_vertex_groups(), nd_apply_modifiers() for sketch/data cleanup
        - nd_viewport_toggle(), nd_capture_utils() for viewport helpers

    3. Always check the world_bounding_box for each item so that:
        - Ensure that all objects that should not be clipping are not clipping.
        - Items have right spatial relationship.

    4. Recommended asset source priority:
        - For specific existing objects: First try Sketchfab, then PolyHaven
        - For generic objects/furniture: First try PolyHaven, then Sketchfab
        - For environment lighting: Use PolyHaven HDRIs
        - For materials/textures: Use PolyHaven textures

    Only fall back to execute_blender_code scripting when:
    - PolyHaven and Sketchfab are both disabled and no suitable asset exists in any of the libraries
    - The task specifically requires a basic material/color
    - The needed operation has no dedicated mesh_*/model_* tool (e.g. a primitive is explicitly requested - use mesh_create_primitive() instead, or a mesh edit covered by mesh_extrude/mesh_inset/mesh_bevel/mesh_bridge/mesh_boolean/mesh_subdivide/mesh_remesh/mesh_solidify/mesh_symmetrize/model_match_reference/model_refine/add_procedural_displacement/model_mirror/model_array/model_radial_array)

    **Best Practices:**
    - Always take a screenshot after completing a task to verify the visual result
    - Always call get_scene_info() after completing a task to verify the changes worked
    - When executing multiple operations, take intermediate screenshots to confirm each step
    - If something looks wrong in the screenshot or scene info, investigate and fix before proceeding
    """
