import pytest

from blender_mcp.server.tools.hyper3d import process_bbox


@pytest.mark.parametrize("bbox", ([0, 1, 1], [-1, 1, 1]))
def test_process_bbox_rejects_nonpositive_integers(bbox) -> None:
    with pytest.raises(ValueError, match="bbox must be bigger than zero"):
        process_bbox(bbox)
