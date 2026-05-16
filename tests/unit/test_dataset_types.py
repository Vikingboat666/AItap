"""Unit tests for :mod:`aitap.dataset.types`."""

from __future__ import annotations

from aitap.dataset.types import Case, InputShape, case_id


def test_case_id_is_stable_across_key_order() -> None:
    a = case_id("site-1", {"a": 1, "b": 2})
    b = case_id("site-1", {"b": 2, "a": 1})
    assert a == b


def test_case_id_changes_with_site() -> None:
    assert case_id("site-1", {"a": 1}) != case_id("site-2", {"a": 1})


def test_case_id_changes_with_inputs() -> None:
    assert case_id("site-1", {"a": 1}) != case_id("site-1", {"a": 2})


def test_case_id_handles_non_json_safe_values() -> None:
    """Sets aren't JSON-serialisable; ``default=str`` keeps id computation
    from raising on them. We don't assert the id value — just that it
    returns *something* deterministic."""

    class Custom:
        def __str__(self) -> str:
            return "custom"

    first = case_id("site-1", {"obj": Custom()})
    second = case_id("site-1", {"obj": Custom()})
    assert first == second
    assert len(first) == 12


def test_input_shape_is_empty_when_blank() -> None:
    assert InputShape().is_empty() is True


def test_input_shape_is_not_empty_when_any_field_set() -> None:
    assert InputShape(fields={"x": "str"}).is_empty() is False
    assert InputShape(function_name="f").is_empty() is False
    assert InputShape(docstring="hi").is_empty() is False


def test_case_round_trips_through_model_dump() -> None:
    c = Case(
        id="abc",
        prompt_site_id="site-1",
        inputs={"body": "hi"},
        tags=["seed"],
        source="seed",
    )
    dumped = c.model_dump(mode="json")
    back = Case.model_validate(dumped)
    assert back == c
