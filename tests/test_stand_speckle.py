"""Post-LETO speckle cleanup: small parent stands absorbed into the neighbour they share most edge with."""

import numpy as np

from pipeline.stand_speckle import absorb_small_stands


def test_a_small_stand_joins_the_neighbour_it_shares_the_longest_edge_with():
    # Stand 3 (1 px) touches 1 on one edge and 2 on three.
    seg = np.array([[1, 2, 2],
                    [1, 3, 2],
                    [1, 2, 2]])
    out, report = absorb_small_stands(seg, min_pixels=2)
    assert out.tolist() == [[1, 2, 2], [1, 2, 2], [1, 2, 2]]
    assert (report.absorbed_stands, report.absorbed_pixels) == (1, 1)


def test_a_tie_on_edge_goes_to_the_larger_neighbour():
    seg = np.array([[1, 1, 3, 2, 2, 2]])
    out, _ = absorb_small_stands(seg, min_pixels=2)
    assert out.tolist() == [[1, 1, 2, 2, 2, 2]]


def test_an_isolated_small_stand_stays():
    seg = np.array([[1, 1, 0, 3],
                    [1, 1, 0, 0]])
    out, report = absorb_small_stands(seg, min_pixels=2)
    assert out.tolist() == [[1, 1, 0, 2], [1, 1, 0, 0]]   # kept, renumbered
    assert report.isolated_small_stands == 1


def test_a_cluster_of_small_stands_ends_in_one_stand_at_least_the_minimum():
    # 2, 3 and 4 are each 1 px: they join each other and then 1.
    seg = np.array([[1, 1, 1, 1, 1, 2, 3, 4]])
    out, report = absorb_small_stands(seg, min_pixels=3)
    assert len(np.unique(out)) == 1
    assert report.absorbed_stands == 3


def test_stands_at_or_above_the_minimum_are_untouched_and_labels_are_consecutive():
    seg = np.array([[5, 5, 9, 9]])
    out, report = absorb_small_stands(seg, min_pixels=2)
    assert out.tolist() == [[1, 1, 2, 2]]
    assert report.absorbed_stands == 0


def test_diagonal_contact_is_not_an_edge():
    # 4-neighbour edges only, as LETO's CA (use_eight_neighbors False).
    seg = np.array([[1, 0],
                    [0, 2]])
    out, report = absorb_small_stands(seg, min_pixels=2)
    assert report.absorbed_stands == 0
