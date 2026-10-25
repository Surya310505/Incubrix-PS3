import pytest

from app.pipeline import _validate_scene_timing


@pytest.mark.unit
def test_scene_timing_requires_complete_contiguous_narration_coverage():
    _validate_scene_timing([(0.0, 1.25), (1.25, 2.8)], 2.8)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("scene_timing", "duration", "message"),
    [
        ([], 2.0, "must contain scenes"),
        ([(0.1, 2.0)], 2.0, "must begin"),
        ([(0.0, 0.0)], 2.0, "invalid scene interval"),
        ([(0.0, 1.0), (1.1, 2.0)], 2.0, "gap or overlap"),
        ([(0.0, 1.1), (1.0, 2.0)], 2.0, "gap or overlap"),
        ([(0.0, 1.0)], 2.0, "complete narration"),
        ([(0.0, 2.1)], 2.0, "invalid scene interval"),
        ([(0.0, 1.0)], 0.0, "positive narration duration"),
        ([(0.0, 1.0)], float("nan"), "positive narration duration"),
        ([(0.0, float("nan"))], 1.0, "invalid scene interval"),
    ],
)
def test_scene_timing_rejects_invalid_coverage(scene_timing, duration, message):
    with pytest.raises(ValueError, match=message):
        _validate_scene_timing(scene_timing, duration)
