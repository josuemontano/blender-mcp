import bpy

print("BLENDER_VERSION:", bpy.app.version, bpy.app.version_string)

scene = bpy.context.scene

# --- Camera DOF ---
cam_data = bpy.data.cameras.new("TestCam")
cam_obj = bpy.data.objects.new("TestCamObj", cam_data)
scene.collection.objects.link(cam_obj)
dof = cam_data.dof
print("\n--- CameraDOFSettings ---")
print("has use_dof:", hasattr(dof, "use_dof"))
print("has focus_object:", hasattr(dof, "focus_object"))
print("has focus_distance:", hasattr(dof, "focus_distance"))
print("has aperture_fstop:", hasattr(dof, "aperture_fstop"))
print("has aperture_blades:", hasattr(dof, "aperture_blades"))
print("has aperture_rotation:", hasattr(dof, "aperture_rotation"))
print("has aperture_ratio:", hasattr(dof, "aperture_ratio"))

print("\n--- Camera data ---")
for prop in ["type", "lens", "ortho_scale", "sensor_width", "sensor_height", "sensor_fit", "shift_x", "shift_y", "clip_start", "clip_end", "show_passepartout", "passepartout_alpha", "show_composition_thirds", "show_composition_center", "show_composition_center_diagonal", "show_composition_golden", "show_composition_golden_tria_a", "show_composition_golden_tria_b", "show_composition_harmony_tri_a", "show_composition_harmony_tri_b", "panorama_type"]:
    print(f"cam.{prop}:", hasattr(cam_data, prop))

# --- Lights ---
print("\n--- Light types & properties ---")
for lt in ["POINT", "SUN", "SPOT", "AREA"]:
    ld = bpy.data.lights.new(f"TestLight_{lt}", lt)
    props = ["energy", "color", "use_shadow", "diffuse_factor", "specular_factor", "transmission_factor", "volume_factor",
             "use_custom_distance", "cutoff_distance", "shadow_soft_size", "angle", "spread",
             "shape", "size", "size_y", "spot_size", "spot_blend", "show_cone", "use_soft_falloff",
             "use_temperature", "temperature", "normalize", "exposure"]
    have = [p for p in props if hasattr(ld, p)]
    missing = [p for p in props if not hasattr(ld, p)]
    print(f"{lt}: HAS {have}")
    print(f"{lt}: MISSING {missing}")

# --- light_linking ---
print("\n--- light_linking ---")
sun_data = bpy.data.lights.new("TestSun", "SUN")
sun_obj = bpy.data.objects.new("TestSunObj", sun_data)
scene.collection.objects.link(sun_obj)
print("Object has light_linking:", hasattr(sun_obj, "light_linking"))
ll = getattr(sun_obj, "light_linking", None)
if ll is not None:
    print("light_linking has receiver_collection:", hasattr(ll, "receiver_collection"))
    print("light_linking has blocker_collection:", hasattr(ll, "blocker_collection"))
    print("receiver_collection value:", ll.receiver_collection)
    print("blocker_collection value:", ll.blocker_collection)
    test_coll = bpy.data.collections.new("TestLinkColl")
    scene.collection.children.link(test_coll)
    try:
        ll.receiver_collection = test_coll
        print("SET receiver_collection OK, now:", ll.receiver_collection)
    except Exception as e:
        print("SET receiver_collection FAILED:", e)

# --- World background / node setup ---
print("\n--- World ---")
world = bpy.data.worlds.new("TestWorld")
scene.world = world
world.use_nodes = True
nt = world.node_tree
print("World node_tree exists:", nt is not None)
print("Node types available:", [n.bl_idname for n in nt.nodes])
bg = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBackground"), None)
print("Background node found:", bg is not None)
if bg:
    print("bg.inputs:", [i.name for i in bg.inputs])
env_node = nt.nodes.new("ShaderNodeTexEnvironment")
print("ShaderNodeTexEnvironment created OK:", env_node is not None)
mapping_node = nt.nodes.new("ShaderNodeMapping")
texcoord_node = nt.nodes.new("ShaderNodeTexCoord")
sky_node = nt.nodes.new("ShaderNodeTexSky")
print("ShaderNodeTexSky created OK:", sky_node is not None)
print("Sky node props:", [p.identifier for p in sky_node.bl_rna.properties if not p.is_readonly])
print("has sky_type:", hasattr(sky_node, "sky_type"))
print("sky_type items:", [item.identifier for item in sky_node.bl_rna.properties["sky_type"].enum_items] if hasattr(sky_node, "sky_type") else None)
for p in ["sun_disc", "sun_size", "sun_intensity", "sun_elevation", "sun_rotation", "altitude", "air_density", "dust_density", "ozone_density"]:
    print(f"sky.{p}:", hasattr(sky_node, p))

print("\n--- Color management ---")
view_settings = scene.view_settings
print("view_transform options:", [i.identifier for i in view_settings.bl_rna.properties["view_transform"].enum_items])
print("look options:", [i.identifier for i in view_settings.bl_rna.properties["look"].enum_items])
print("has exposure:", hasattr(view_settings, "exposure"))
print("has gamma:", hasattr(view_settings, "gamma"))

print("\n--- EEVEE engine name ---")
print("render.engine options:", [i.identifier for i in scene.render.bl_rna.properties["engine"].enum_items])
