"""Shared strict request records for transactional node-graph patches."""

import math

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeGraphRequest(BaseModel):
    """Reject unknown fields and non-finite values nested in open JSON data."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def reject_nested_nonfinite_values(self) -> "NodeGraphRequest":
        """Reject NaN and infinities inside property and socket-value payloads."""

        def validate(value: Any) -> None:
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("numeric values must be finite")
            if isinstance(value, dict):
                for nested in value.values():
                    validate(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    validate(nested)

        validate(self.model_dump())
        return self


class NodeGraphEdit(NodeGraphRequest):
    """One ordered, stable-name node-graph mutation."""

    operation: Literal[
        "ADD_NODE",
        "UPDATE_NODE",
        "SET_INPUT",
        "ADD_LINK",
        "REMOVE_LINK",
        "MOVE_TO_FRAME",
        "REMOVE_NODE",
        "SET_ACTIVE_OUTPUT",
    ]
    node_name: str | None = None
    bl_idname: str | None = None
    new_name: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    socket_identifier: str | None = None
    socket_index: int | None = Field(default=None, ge=0)
    value: Any = None
    from_node: str | None = None
    from_socket_identifier: str | None = None
    from_socket_index: int | None = Field(default=None, ge=0)
    to_node: str | None = None
    to_socket_identifier: str | None = None
    to_socket_index: int | None = Field(default=None, ge=0)
    frame_name: str | None = None
    managed_role: str | None = Field(default=None, min_length=1, max_length=128)
