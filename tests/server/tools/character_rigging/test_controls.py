"""Regression coverage for character control, deformation, and pose workflows."""

import asyncio

import pytest

from pydantic import ValidationError
from test_mutation_transaction import _load_addon

from blender_mcp.server.tools import character_rigging


def _run(function, **kwargs):
    return asyncio.run(function(ctx=None, **kwargs))


def test_all_control_and_deformation_commands_are_registered() -> None:
    names = {
        "transfer_skin_weights",
        "create_ik_chain",
        "create_ik_fk_limb",
        "create_spline_ik_rig",
        "configure_bendy_bones",
        "create_rig_property_driver",
        "assign_bone_custom_shapes",
        "set_character_pose",
        "keyframe_character_pose",
        "create_shape_key_controls",
    }

    assert all(callable(getattr(character_rigging, name)) for name in names)
    assert set(character_rigging.mcp._tool_manager._tools) >= names


def test_deformation_and_pose_models_reject_ambiguous_inputs() -> None:
    with pytest.raises(ValidationError, match="non-AUTO"):
        character_rigging.BendyBonePatch(
            bone_name="spine",
            custom_handle_start="MCH-spine",
        )
    with pytest.raises(ValidationError, match="mutually exclusive"):
        character_rigging.BonePose(
            bone_name="hand.L",
            matrix=((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
            location=(1, 2, 3),
        )
    with pytest.raises(ValidationError, match="identity field"):
        character_rigging.DrivenChannel(
            owner="CONSTRAINT",
            object_name="Rig",
            bone_name="shin.L",
            property_name="influence",
        )


def test_irreversible_weight_commit_requires_confirmation() -> None:
    with pytest.raises(ValueError, match="confirm_commit"):
        _run(
            character_rigging.transfer_skin_weights,
            source_mesh_name="Body.LOD0",
            target_mesh_name="Body.LOD1",
            commit=True,
        )


def test_spline_ik_requires_exactly_one_curve_source() -> None:
    with pytest.raises(ValueError, match="either curve_object_name"):
        _run(
            character_rigging.create_spline_ik_rig,
            armature_object_name="Rig",
            chain_bone_names=["spine.001", "spine.002"],
        )
    with pytest.raises(ValueError, match="either curve_object_name"):
        _run(
            character_rigging.create_spline_ik_rig,
            armature_object_name="Rig",
            chain_bone_names=["spine.001", "spine.002"],
            curve_object_name="SpineCurve",
            new_curve_name="OtherCurve",
        )


def test_ik_chain_serializes_explicit_controls(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        character_rigging,
        "_call",
        lambda command, params, changed_objects=None: calls.append((command, params, changed_objects)) or {"ok": True},
    )

    _run(
        character_rigging.create_ik_chain,
        armature_object_name="Rig",
        chain_bone_names=["thigh.L", "shin.L"],
        target_control=character_rigging.ControlBoneDefinition(
            name="foot_ik.L",
            head=(0, 0, 0),
            tail=(0, 0, 0.25),
        ),
        pole_control=character_rigging.PoleControlDefinition(
            name="knee_pole.L",
            head=(0, -1, 1),
            tail=(0, -1, 1.25),
            pole_angle=1.5708,
        ),
    )

    assert calls == [
        (
            "create_ik_chain",
            {
                "armature_object_name": "Rig",
                "chain_bone_names": ["thigh.L", "shin.L"],
                "target_control": {
                    "name": "foot_ik.L",
                    "head": (0.0, 0.0, 0.0),
                    "tail": (0.0, 0.0, 0.25),
                    "collection": "CTRL",
                },
                "pole_control": {
                    "name": "knee_pole.L",
                    "head": (0.0, -1.0, 1.0),
                    "tail": (0.0, -1.0, 1.25),
                    "collection": "CTRL",
                    "pole_angle": 1.5708,
                },
                "constraint_name": "IK",
                "iterations": 500,
                "use_stretch": False,
            },
            ["Rig"],
        )
    ]


def test_pose_keyframe_serializes_typed_channels(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        character_rigging,
        "_call",
        lambda command, params, changed_objects=None: calls.append((command, params, changed_objects)) or {"ok": True},
    )

    _run(
        character_rigging.keyframe_character_pose,
        armature_object_name="Rig",
        action_name="Walk",
        frame=12.5,
        poses=[
            character_rigging.BonePose(
                bone_name="root",
                location=(0, 1, 0),
                rotation_quaternion=(1, 0, 0, 0),
            )
        ],
        space="WORLD",
        action_policy="REUSE",
    )

    assert calls[0][0] == "keyframe_character_pose"
    assert calls[0][1]["poses"][0]["rotation_quaternion"] == (1.0, 0.0, 0.0, 0.0)
    assert calls[0][1]["space"] == "WORLD"
    assert calls[0][1]["frame"] == 12.5


def test_shape_key_control_modes_are_typed_and_serialized(monkeypatch) -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        character_rigging.CorrectiveShapeKeyControl(
            shape_key_name="ElbowCorrective",
            inputs=[
                character_rigging.CorrectivePropertyInput(property_name="bend"),
                character_rigging.CorrectivePropertyInput(property_name="bend"),
            ],
        )

    calls = []
    monkeypatch.setattr(
        character_rigging,
        "_call",
        lambda command, params, changed_objects=None: calls.append((command, params, changed_objects)) or {"ok": True},
    )
    _run(
        character_rigging.create_shape_key_controls,
        mesh_object_name="Body",
        armature_object_name="Rig",
        property_owner="POSE_BONE",
        property_bone_name="settings",
        controls=[
            character_rigging.DirectShapeKeyControl(
                shape_key_name="Smile",
                property_name="smile",
            ),
            character_rigging.SignedShapeKeyControl(
                positive_shape_key_name="SmileWide",
                negative_shape_key_name="Frown",
                property_name="expression",
            ),
            character_rigging.CorrectiveShapeKeyControl(
                shape_key_name="ElbowCorrective",
                inputs=[
                    character_rigging.CorrectivePropertyInput(property_name="bend"),
                    character_rigging.CorrectivePropertyInput(property_name="twist"),
                ],
            ),
        ],
    )

    assert [item["mode"] for item in calls[0][1]["controls"]] == ["DIRECT", "SIGNED", "CORRECTIVE"]


def test_dispatch_exposes_complete_character_surface(monkeypatch) -> None:
    addon, _bpy = _load_addon(monkeypatch, data={})
    server = addon.BlenderMCPServer()
    handlers = server._build_command_handlers()
    new_commands = {
        "transfer_skin_weights",
        "create_ik_chain",
        "create_ik_fk_limb",
        "create_spline_ik_rig",
        "configure_bendy_bones",
        "create_rig_property_driver",
        "assign_bone_custom_shapes",
        "set_character_pose",
        "keyframe_character_pose",
        "create_shape_key_controls",
    }

    assert set(handlers) >= new_commands
    assert not new_commands & server._READ_ONLY_COMMANDS
