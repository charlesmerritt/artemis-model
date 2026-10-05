"""The add-back method registry: which hole pixels each method proposes, and how they combine."""

import numpy as np
import pytest

from pipeline.s1_initial_state.add_back_methods import (
    METHOD_PRIORITY,
    AddBackMethod,
    ConsensusRule,
    MethodMasks,
    method_bits,
    priority_attribution,
)

T, F = True, False


def masks(bookends=None, obata=None, hansen=None) -> MethodMasks:
    return MethodMasks({
        AddBackMethod.BOOKENDS: None if bookends is None else np.array(bookends, dtype=bool),
        AddBackMethod.OBATA_DISTURBANCE: None if obata is None else np.array(obata, dtype=bool),
        AddBackMethod.HANSEN_LOSS: None if hansen is None else np.array(hansen, dtype=bool),
    })


def test_priority_runs_from_least_to_most_permissive():
    assert METHOD_PRIORITY == (AddBackMethod.BOOKENDS, AddBackMethod.OBATA_DISTURBANCE,
                               AddBackMethod.HANSEN_LOSS)


def test_a_method_that_has_not_run_is_pending_not_empty():
    m = masks(bookends=[T, F])
    assert m.run == (AddBackMethod.BOOKENDS,)
    assert m.pending == (AddBackMethod.OBATA_DISTURBANCE, AddBackMethod.HANSEN_LOSS)


def test_union_is_every_pixel_any_run_method_proposes():
    m = masks(bookends=[T, F, F, F], hansen=[F, T, F, F])
    assert m.combine(ConsensusRule.UNION).tolist() == [T, T, F, F]


def test_union_with_bookends_alone_is_bookends():
    m = masks(bookends=[T, F, T])
    assert m.combine(ConsensusRule.UNION).tolist() == [T, F, T]


def test_at_least_two_needs_two_methods_to_agree():
    m = masks(bookends=[T, T, F, F], obata=[T, F, T, F], hansen=[F, F, T, T])
    assert m.combine(ConsensusRule.AT_LEAST_TWO).tolist() == [T, F, T, F]


def test_at_least_two_refuses_to_run_on_fewer_than_two_methods():
    # With one method run, "any two agree" would silently add nothing back.
    with pytest.raises(ValueError, match="at least two"):
        masks(bookends=[T, F]).combine(ConsensusRule.AT_LEAST_TWO)


def test_no_method_run_is_an_error_for_every_rule():
    with pytest.raises(ValueError, match="no add-back method"):
        masks().combine(ConsensusRule.UNION)


def test_masks_must_share_a_shape():
    with pytest.raises(ValueError, match="shape"):
        masks(bookends=[T, F], obata=[T, F, F])


def test_method_bits_record_every_method_that_found_a_pixel():
    m = masks(bookends=[T, T, F, F], obata=[F, T, F, F], hansen=[F, T, T, F])
    assert method_bits(m).tolist() == [1, 1 | 2 | 4, 4, 0]
    assert method_bits(m).dtype == np.uint8


def test_priority_attribution_counts_each_pixel_once_in_priority_order():
    # Pixel 1 is found by all three but is credited to bookends only; pixel 2 by
    # Obata and Hansen, credited to Obata.
    m = masks(bookends=[T, T, F, F], obata=[F, T, T, F], hansen=[F, T, T, T])
    credited = priority_attribution(m, ConsensusRule.UNION)
    assert credited == {AddBackMethod.BOOKENDS: 2, AddBackMethod.OBATA_DISTURBANCE: 1,
                        AddBackMethod.HANSEN_LOSS: 1}
    assert sum(credited.values()) == int(m.combine(ConsensusRule.UNION).sum())


def test_priority_attribution_only_credits_pixels_the_rule_accepts():
    m = masks(bookends=[T, F, F], obata=[F, T, F], hansen=[F, T, T])
    credited = priority_attribution(m, ConsensusRule.AT_LEAST_TWO)
    assert credited == {AddBackMethod.BOOKENDS: 0, AddBackMethod.OBATA_DISTURBANCE: 1,
                        AddBackMethod.HANSEN_LOSS: 0}


def test_priority_attribution_reports_pending_methods_as_none():
    credited = priority_attribution(masks(bookends=[T, T, F]), ConsensusRule.UNION)
    assert credited == {AddBackMethod.BOOKENDS: 2, AddBackMethod.OBATA_DISTURBANCE: None,
                        AddBackMethod.HANSEN_LOSS: None}
