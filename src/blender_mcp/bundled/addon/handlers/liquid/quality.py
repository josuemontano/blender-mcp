"""Apply named liquid quality profiles by delegating to the existing solver and mesh handlers."""

from __future__ import annotations

from .inspection_and_setup import _get_domain

_PROFILES = ("PREVIEW", "BALANCED", "FINAL")


class LiquidQualityHandlers:
    """Apply a caller-resolved quality profile through configure_liquid_solver/configure_liquid_mesh."""

    def apply_liquid_quality_profile(
        self,
        domain_object_name,
        modifier_name,
        profile,
        solver_patch=None,
        mesh_patch=None,
    ):
        if profile not in _PROFILES:
            raise ValueError(f"Unknown quality profile: {profile}; choose one of {list(_PROFILES)}")
        if not solver_patch and not mesh_patch:
            raise ValueError("A quality profile must apply at least a solver or a mesh patch")
        # Resolve first so an unknown domain fails before either sub-handler mutates anything; the
        # dispatcher's mutation_transaction rolls back both patches together if the second one fails.
        obj, modifier, _settings = _get_domain(domain_object_name, modifier_name)
        solver = self.configure_liquid_solver(domain_object_name, modifier_name, solver_patch) if solver_patch else None
        mesh = self.configure_liquid_mesh(domain_object_name, modifier_name, mesh_patch) if mesh_patch else None
        applied = [name for name, result in (("solver", solver), ("mesh", mesh)) if result is not None]
        changes = {}
        for result in (solver, mesh):
            if result is not None:
                changes.update(result["changes"])
        return {
            "changed_objects": [obj.name],
            "object": obj.name,
            "modifier": modifier.name,
            "profile": profile,
            "applied_sections": applied,
            "changes": changes,
            "solver": solver,
            "mesh": mesh,
            "invalidated_cache_stages": ["DATA", "MESH", "PARTICLES", "GUIDES"] if solver else ["MESH"],
            "next_required_bake_stage": "DATA" if solver or (mesh and mesh["data_rebake_required"]) else "MESH",
            "retained_live_modifier": True,
        }
