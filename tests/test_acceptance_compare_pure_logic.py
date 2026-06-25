"""Pure-logic unit tests for the OPT-6 acceptance comparator (Issue #44).

These tests exercise the four named pure-logic helpers in
:mod:`metacrucible.optimizer` directly without going through the
broader contract tests in :mod:`tests.test_optimize_command` and
without running the full optimizer pipeline. Each helper is pinned
in isolation so a future change to the acceptance comparator cannot
hide behind the public end-to-end contract:

  - :func:`metacrucible.optimizer._eval_split_fail_or_blocked_count`
    — counts ``FAIL`` and ``BLOCKED`` cases in a single eval split.
  - :func:`metacrucible.optimizer._held_out_pass_to_fail_case_ids`
    — returns held-out ``case_id``s with a binary
    ``PASS`` -> ``FAIL`` regression (ACG-2r / Issue #35 guard).
  - :func:`metacrucible.optimizer._eval_split_transitions` —
    returns ``(fail_to_pass, pass_to_fail)`` sorted case-id lists
    for the eval split (ACG-1r / Issue #35 comparator).
  - :func:`metacrucible.optimizer.compare_eval_held_out` — the
    top-level acceptance comparator that returns the boolean
    verdict, the machine-readable reason, the eval-split
    FAIL+BLOCKED counts (backward compat), and the transition
    lists.

Fixtures are inline hand-built case lists with obviously fake
``case_id`` placeholders (e.g. ``"case-A"``, ``"held-H1"``) — no
real secrets, no LLM, network, sleep, or subprocess calls — so
the suite runs deterministically under ``pytest -q``.
"""
from __future__ import annotations

from typing import Any

import pytest

from metacrucible.optimizer import (
    _eval_split_fail_or_blocked_count,
    _eval_split_transitions,
    _held_out_pass_to_fail_case_ids,
    compare_eval_held_out,
)


# --------------------------------------------------------------------------- #
# _eval_split_fail_or_blocked_count                                            #
# --------------------------------------------------------------------------- #


class TestEvalSplitFailOrBlockedCount:
    """Pin the FAIL+BLOCKED count helper for a single eval split."""

    def test_empty_list_returns_zero(self) -> None:
        """An empty case list is the zero-count baseline."""
        assert _eval_split_fail_or_blocked_count([]) == 0

    def test_all_pass_returns_zero(self) -> None:
        """A list of only ``PASS`` rows yields zero FAIL+BLOCKED."""
        rows = [
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-B", "status": "PASS"},
            {"case_id": "case-C", "status": "PASS"},
        ]
        assert _eval_split_fail_or_blocked_count(rows) == 0

    def test_counts_fail_rows(self) -> None:
        """Each ``FAIL`` row increments the counter by one."""
        rows = [
            {"case_id": "case-A", "status": "FAIL"},
            {"case_id": "case-B", "status": "FAIL"},
        ]
        assert _eval_split_fail_or_blocked_count(rows) == 2

    def test_counts_blocked_rows(self) -> None:
        """Each ``BLOCKED`` row increments the counter by one."""
        rows = [
            {"case_id": "case-A", "status": "BLOCKED"},
            {"case_id": "case-B", "status": "BLOCKED"},
            {"case_id": "case-C", "status": "BLOCKED"},
        ]
        assert _eval_split_fail_or_blocked_count(rows) == 3

    def test_counts_fail_and_blocked_together(self) -> None:
        """``FAIL`` and ``BLOCKED`` rows both contribute to the total."""
        rows = [
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-B", "status": "FAIL"},
            {"case_id": "case-C", "status": "BLOCKED"},
            {"case_id": "case-D", "status": "FAIL"},
        ]
        assert _eval_split_fail_or_blocked_count(rows) == 3

    def test_ignores_non_mapping_entries(self) -> None:
        """Non-Mapping entries in the list are skipped, not counted."""
        rows: list[Any] = [
            {"case_id": "case-A", "status": "FAIL"},
            "not a mapping",
            None,
            42,
            {"case_id": "case-B", "status": "BLOCKED"},
        ]
        assert _eval_split_fail_or_blocked_count(rows) == 2

    def test_ignores_rows_with_non_fail_or_blocked_status(self) -> None:
        """``PASS`` and unknown statuses do not contribute."""
        rows = [
            {"case_id": "case-A", "status": "FAIL"},
            {"case_id": "case-B", "status": "PASS"},
            {"case_id": "case-C", "status": "UNKNOWN"},
            {"case_id": "case-D", "status": "BLOCKED"},
        ]
        assert _eval_split_fail_or_blocked_count(rows) == 2


# --------------------------------------------------------------------------- #
# _held_out_pass_to_fail_case_ids                                              #
# --------------------------------------------------------------------------- #


class TestHeldOutPassToFailCaseIds:
    """Pin the held-out binary ``PASS`` -> ``FAIL`` regression guard."""

    def test_empty_inputs_return_empty_list(self) -> None:
        """Empty baseline + empty candidate yields no regressions."""
        assert _held_out_pass_to_fail_case_ids([], []) == []

    def test_no_regressions_returns_empty_list(self) -> None:
        """``PASS`` -> ``PASS`` is not a regression."""
        baseline = [{"case_id": "held-H1", "status": "PASS"}]
        candidate = [{"case_id": "held-H1", "status": "PASS"}]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == []

    def test_single_pass_to_fail_is_returned(self) -> None:
        """A single ``PASS`` -> ``FAIL`` flip surfaces its ``case_id``."""
        baseline = [{"case_id": "held-H1", "status": "PASS"}]
        candidate = [{"case_id": "held-H1", "status": "FAIL"}]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == [
            "held-H1"
        ]

    def test_multiple_regressions_returned_sorted(self) -> None:
        """Multiple ``PASS`` -> ``FAIL`` flips are returned sorted."""
        baseline = [
            {"case_id": "held-Z", "status": "PASS"},
            {"case_id": "held-A", "status": "PASS"},
            {"case_id": "held-M", "status": "PASS"},
        ]
        candidate = [
            {"case_id": "held-Z", "status": "FAIL"},
            {"case_id": "held-A", "status": "FAIL"},
            {"case_id": "held-M", "status": "PASS"},
        ]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == [
            "held-A",
            "held-Z",
        ]

    def test_blocked_to_fail_is_not_a_regression(self) -> None:
        """``BLOCKED`` -> ``FAIL`` is NOT a held-out regression."""
        baseline = [{"case_id": "held-H1", "status": "BLOCKED"}]
        candidate = [{"case_id": "held-H1", "status": "FAIL"}]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == []

    def test_pass_to_blocked_is_not_a_regression(self) -> None:
        """``PASS`` -> ``BLOCKED`` is NOT a held-out regression."""
        baseline = [{"case_id": "held-H1", "status": "PASS"}]
        candidate = [{"case_id": "held-H1", "status": "BLOCKED"}]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == []

    def test_fail_to_pass_is_not_a_regression(self) -> None:
        """``FAIL`` -> ``PASS`` is the opposite of a regression."""
        baseline = [{"case_id": "held-H1", "status": "FAIL"}]
        candidate = [{"case_id": "held-H1", "status": "PASS"}]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == []

    def test_case_in_candidate_only_is_not_a_regression(self) -> None:
        """A case present only in the candidate side is ignored."""
        baseline = [{"case_id": "held-H1", "status": "PASS"}]
        candidate = [
            {"case_id": "held-H1", "status": "PASS"},
            {"case_id": "held-H2", "status": "FAIL"},
        ]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == []

    def test_case_in_baseline_only_is_not_a_regression(self) -> None:
        """A case present only in the baseline side is ignored."""
        baseline = [
            {"case_id": "held-H1", "status": "PASS"},
            {"case_id": "held-H2", "status": "PASS"},
        ]
        candidate = [{"case_id": "held-H1", "status": "PASS"}]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == []

    def test_ignores_rows_missing_case_id(self) -> None:
        """Rows without a stable ``case_id`` are ignored."""
        baseline = [
            {"case_id": "held-H1", "status": "PASS"},
            {"status": "PASS"},  # no case_id
        ]
        candidate = [
            {"case_id": "held-H1", "status": "FAIL"},
            {"status": "FAIL"},  # no case_id
        ]
        # Only ``held-H1`` is keyed; the unkeyed row must not
        # create a regression because it cannot be matched.
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == [
            "held-H1"
        ]

    def test_ignores_rows_with_non_string_case_id(self) -> None:
        """Non-string ``case_id`` values are ignored."""
        baseline = [
            {"case_id": "held-H1", "status": "PASS"},
            {"case_id": 42, "status": "PASS"},
        ]
        candidate = [
            {"case_id": "held-H1", "status": "FAIL"},
            {"case_id": 42, "status": "FAIL"},
        ]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == [
            "held-H1"
        ]

    def test_ignores_non_mapping_entries(self) -> None:
        """Non-Mapping entries on either side are skipped."""
        baseline: list[Any] = [
            {"case_id": "held-H1", "status": "PASS"},
            "not a mapping",
        ]
        candidate: list[Any] = [
            {"case_id": "held-H1", "status": "FAIL"},
            None,
        ]
        assert _held_out_pass_to_fail_case_ids(baseline, candidate) == [
            "held-H1"
        ]


# --------------------------------------------------------------------------- #
# _eval_split_transitions                                                      #
# --------------------------------------------------------------------------- #


class TestEvalSplitTransitions:
    """Pin the eval-split per-case ``FAIL`` -> ``PASS`` and
    ``PASS`` -> ``FAIL`` transition helper."""

    def test_empty_inputs_return_empty_lists(self) -> None:
        """Empty baseline + empty candidate yields two empty lists."""
        fail_to_pass, pass_to_fail = _eval_split_transitions([], [])
        assert fail_to_pass == []
        assert pass_to_fail == []

    def test_no_transitions_returns_empty_lists(self) -> None:
        """Identical statuses produce no transitions."""
        baseline = [
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-B", "status": "FAIL"},
        ]
        candidate = [
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-B", "status": "FAIL"},
        ]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == []
        assert pass_to_fail == []

    def test_fail_to_pass_only(self) -> None:
        """``FAIL`` -> ``PASS`` flips are returned in the first list."""
        baseline = [
            {"case_id": "case-A", "status": "FAIL"},
            {"case_id": "case-B", "status": "PASS"},
        ]
        candidate = [
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-B", "status": "PASS"},
        ]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == ["case-A"]
        assert pass_to_fail == []

    def test_pass_to_fail_only(self) -> None:
        """``PASS`` -> ``FAIL`` flips are returned in the second list."""
        baseline = [
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-B", "status": "PASS"},
        ]
        candidate = [
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-B", "status": "FAIL"},
        ]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == []
        assert pass_to_fail == ["case-B"]

    def test_both_transitions_returned_sorted(self) -> None:
        """Both transition lists are returned sorted by ``case_id``."""
        baseline = [
            {"case_id": "case-C", "status": "FAIL"},
            {"case_id": "case-A", "status": "FAIL"},
            {"case_id": "case-Z", "status": "PASS"},
            {"case_id": "case-M", "status": "PASS"},
        ]
        candidate = [
            {"case_id": "case-C", "status": "PASS"},
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-Z", "status": "FAIL"},
            {"case_id": "case-M", "status": "FAIL"},
        ]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == ["case-A", "case-C"]
        assert pass_to_fail == ["case-M", "case-Z"]

    def test_blocked_to_pass_is_not_fail_to_pass(self) -> None:
        """``BLOCKED`` -> ``PASS`` is NOT a ``FAIL`` -> ``PASS`` flip."""
        baseline = [{"case_id": "case-A", "status": "BLOCKED"}]
        candidate = [{"case_id": "case-A", "status": "PASS"}]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == []
        assert pass_to_fail == []

    def test_blocked_to_fail_is_not_pass_to_fail(self) -> None:
        """``BLOCKED`` -> ``FAIL`` is NOT a ``PASS`` -> ``FAIL`` flip."""
        baseline = [{"case_id": "case-A", "status": "BLOCKED"}]
        candidate = [{"case_id": "case-A", "status": "FAIL"}]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == []
        assert pass_to_fail == []

    def test_fail_to_blocked_is_not_fail_to_pass(self) -> None:
        """``FAIL`` -> ``BLOCKED`` is NOT a ``FAIL`` -> ``PASS`` flip."""
        baseline = [{"case_id": "case-A", "status": "FAIL"}]
        candidate = [{"case_id": "case-A", "status": "BLOCKED"}]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == []
        assert pass_to_fail == []

    def test_case_in_baseline_only_is_ignored(self) -> None:
        """A case present only in baseline produces no transition."""
        baseline = [
            {"case_id": "case-A", "status": "FAIL"},
            {"case_id": "case-B", "status": "FAIL"},
        ]
        candidate = [{"case_id": "case-A", "status": "PASS"}]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == ["case-A"]
        assert pass_to_fail == []

    def test_case_in_candidate_only_is_ignored(self) -> None:
        """A case present only in candidate produces no transition."""
        baseline = [{"case_id": "case-A", "status": "FAIL"}]
        candidate = [
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-B", "status": "PASS"},
        ]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == ["case-A"]
        assert pass_to_fail == []

    def test_ignores_rows_missing_case_id(self) -> None:
        """Rows without a stable ``case_id`` are ignored."""
        baseline = [
            {"case_id": "case-A", "status": "FAIL"},
            {"status": "FAIL"},
        ]
        candidate = [
            {"case_id": "case-A", "status": "PASS"},
            {"status": "PASS"},
        ]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == ["case-A"]
        assert pass_to_fail == []

    def test_ignores_rows_with_non_string_status(self) -> None:
        """Non-string status values are ignored."""
        baseline = [
            {"case_id": "case-A", "status": "FAIL"},
            {"case_id": "case-B", "status": 1},
        ]
        candidate = [
            {"case_id": "case-A", "status": "PASS"},
            {"case_id": "case-B", "status": "PASS"},
        ]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == ["case-A"]
        assert pass_to_fail == []

    def test_ignores_non_mapping_entries(self) -> None:
        """Non-Mapping entries on either side are skipped."""
        baseline: list[Any] = [
            {"case_id": "case-A", "status": "FAIL"},
            "garbage",
        ]
        candidate: list[Any] = [
            {"case_id": "case-A", "status": "PASS"},
            None,
        ]
        fail_to_pass, pass_to_fail = _eval_split_transitions(
            baseline, candidate
        )
        assert fail_to_pass == ["case-A"]
        assert pass_to_fail == []


# --------------------------------------------------------------------------- #
# compare_eval_held_out                                                        #
# --------------------------------------------------------------------------- #


class TestCompareEvalHeldOut:
    """Pin the top-level OPT-6 acceptance comparator."""

    def test_accepts_clean_fail_to_pass(self) -> None:
        """``FAIL`` -> ``PASS`` in eval with clean held-out yields
        ``accepted=True`` and ``reason='accepted'``."""
        decision = compare_eval_held_out(
            baseline_eval=[
                {"case_id": "case-A", "status": "FAIL"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            candidate_eval=[
                {"case_id": "case-A", "status": "PASS"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            baseline_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
            ],
            candidate_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
            ],
        )
        assert decision["accepted"] is True
        assert decision["reason"] == "accepted"
        assert decision["eval_fail_to_pass_case_ids"] == ["case-A"]
        assert decision["eval_pass_to_fail_case_ids"] == []
        assert decision["new_held_out_fail_blocked_case_ids"] == []
        assert decision["held_out_pass_to_fail_case_ids"] == []
        # Backward-compat: count fields are populated.
        assert decision["baseline_eval_fail_blocked_count"] == 1
        assert decision["candidate_eval_fail_blocked_count"] == 0

    def test_rejects_eval_pass_to_fail(self) -> None:
        """Any ``PASS`` -> ``FAIL`` in eval yields
        ``accepted=False`` and ``reason='eval_regression'`` even
        when an aggregate FAIL+BLOCKED count improves."""
        decision = compare_eval_held_out(
            baseline_eval=[
                {"case_id": "case-A", "status": "FAIL"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            candidate_eval=[
                # A flips FAIL -> PASS (aggregate count improves)
                # but B regresses PASS -> FAIL.
                {"case_id": "case-A", "status": "PASS"},
                {"case_id": "case-B", "status": "FAIL"},
            ],
            baseline_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
            ],
            candidate_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
            ],
        )
        assert decision["accepted"] is False
        assert decision["reason"] == "eval_regression"
        assert decision["eval_pass_to_fail_case_ids"] == ["case-B"]
        assert "case-B" in decision["eval_pass_to_fail_case_ids"]

    def test_rejects_when_no_fail_to_pass(self) -> None:
        """Zero per-case ``FAIL`` -> ``PASS`` in eval yields
        ``reason='eval_no_improvement'`` even when the aggregate
        FAIL+BLOCKED count improves (e.g. only ``BLOCKED`` ->
        ``PASS``)."""
        decision = compare_eval_held_out(
            baseline_eval=[
                {"case_id": "case-A", "status": "BLOCKED"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            candidate_eval=[
                # BLOCKED -> PASS is NOT a FAIL -> PASS.
                {"case_id": "case-A", "status": "PASS"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            baseline_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
            ],
            candidate_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
            ],
        )
        assert decision["accepted"] is False
        assert decision["reason"] == "eval_no_improvement"
        assert decision["eval_fail_to_pass_case_ids"] == []

    def test_rejects_held_out_pass_to_fail(self) -> None:
        """A held-out ``PASS`` -> ``FAIL`` regression yields
        ``reason='held_out_regression'`` and surfaces the
        regressing ``case_id``."""
        decision = compare_eval_held_out(
            baseline_eval=[
                {"case_id": "case-A", "status": "FAIL"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            candidate_eval=[
                {"case_id": "case-A", "status": "PASS"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            baseline_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
            ],
            candidate_held_out=[
                {"case_id": "held-H1", "status": "FAIL"},
            ],
        )
        assert decision["accepted"] is False
        assert decision["reason"] == "held_out_regression"
        assert decision["new_held_out_fail_blocked_case_ids"] == [
            "held-H1"
        ]
        # The two held-out fields carry the same list (the
        # ``held_out_pass_to_fail_case_ids`` field is the
        # machine-readable alias for the held-out guard).
        assert decision["held_out_pass_to_fail_case_ids"] == [
            "held-H1"
        ]

    def test_reason_precedence_eval_regression_wins(self) -> None:
        """``eval_regression`` is the most specific reason: a
        regressing eval case blocks the candidate even when the
        held-out side also regresses."""
        decision = compare_eval_held_out(
            baseline_eval=[
                {"case_id": "case-A", "status": "FAIL"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            candidate_eval=[
                {"case_id": "case-A", "status": "PASS"},
                {"case_id": "case-B", "status": "FAIL"},
            ],
            baseline_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
            ],
            candidate_held_out=[
                {"case_id": "held-H1", "status": "FAIL"},
            ],
        )
        assert decision["accepted"] is False
        assert decision["reason"] == "eval_regression"
        # Both regressions surface in the dict, but the
        # ``reason`` field is the most specific signal.
        assert decision["eval_pass_to_fail_case_ids"] == ["case-B"]
        assert decision["held_out_pass_to_fail_case_ids"] == [
            "held-H1"
        ]

    def test_reason_no_improvement_overrides_held_out_regression(self) -> None:
        """When the eval split has no per-case ``FAIL`` -> ``PASS``
        AND the held-out split regresses, the ``eval_no_improvement``
        reason takes precedence over ``held_out_regression`` because
        the comparator's reason cascade checks ``eval_no_improvement``
        before ``held_out_regression``. The held-out regression is
        still recorded in the machine-readable held-out fields."""
        decision = compare_eval_held_out(
            baseline_eval=[
                {"case_id": "case-A", "status": "PASS"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            candidate_eval=[
                # No FAIL -> PASS at all.
                {"case_id": "case-A", "status": "PASS"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            baseline_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
            ],
            candidate_held_out=[
                {"case_id": "held-H1", "status": "FAIL"},
            ],
        )
        assert decision["accepted"] is False
        # Precedence: eval_no_improvement is checked before
        # held_out_regression, so the reason is
        # ``eval_no_improvement`` even though held-out also
        # regresses. The held-out regression still surfaces
        # in the machine-readable field.
        assert decision["reason"] == "eval_no_improvement"
        assert decision["held_out_pass_to_fail_case_ids"] == [
            "held-H1"
        ]

    def test_returned_dict_has_expected_keys(self) -> None:
        """The comparator's return shape is stable and complete."""
        decision = compare_eval_held_out(
            baseline_eval=[{"case_id": "case-A", "status": "FAIL"}],
            candidate_eval=[{"case_id": "case-A", "status": "PASS"}],
            baseline_held_out=[{"case_id": "held-H1", "status": "PASS"}],
            candidate_held_out=[{"case_id": "held-H1", "status": "PASS"}],
        )
        expected_keys = {
            "accepted",
            "reason",
            "baseline_eval_fail_blocked_count",
            "candidate_eval_fail_blocked_count",
            "new_held_out_fail_blocked_case_ids",
            "held_out_pass_to_fail_case_ids",
            "eval_fail_to_pass_case_ids",
            "eval_pass_to_fail_case_ids",
        }
        assert set(decision.keys()) == expected_keys

    def test_held_out_alias_fields_carry_same_list(self) -> None:
        """``new_held_out_fail_blocked_case_ids`` and
        ``held_out_pass_to_fail_case_ids`` carry the same list
        (machine-readable alias)."""
        decision = compare_eval_held_out(
            baseline_eval=[{"case_id": "case-A", "status": "FAIL"}],
            candidate_eval=[{"case_id": "case-A", "status": "PASS"}],
            baseline_held_out=[{"case_id": "held-H1", "status": "PASS"}],
            candidate_held_out=[{"case_id": "held-H1", "status": "FAIL"}],
        )
        assert decision["new_held_out_fail_blocked_case_ids"] == [
            "held-H1"
        ]
        assert (
            decision["new_held_out_fail_blocked_case_ids"]
            == decision["held_out_pass_to_fail_case_ids"]
        )

    def test_empty_splits_are_clean(self) -> None:
        """Empty eval + empty held-out splits: no transitions,
        no held-out regressions, no FAIL+BLOCKED counts."""
        decision = compare_eval_held_out(
            baseline_eval=[],
            candidate_eval=[],
            baseline_held_out=[],
            candidate_held_out=[],
        )
        assert decision["accepted"] is False
        # No FAIL -> PASS in the (empty) eval split.
        assert decision["reason"] == "eval_no_improvement"
        assert decision["baseline_eval_fail_blocked_count"] == 0
        assert decision["candidate_eval_fail_blocked_count"] == 0
        assert decision["eval_fail_to_pass_case_ids"] == []
        assert decision["eval_pass_to_fail_case_ids"] == []
        assert decision["new_held_out_fail_blocked_case_ids"] == []
        assert decision["held_out_pass_to_fail_case_ids"] == []

    def test_ignores_held_out_rows_without_case_id(self) -> None:
        """Held-out rows missing a stable ``case_id`` do NOT
        create a false positive regression."""
        decision = compare_eval_held_out(
            baseline_eval=[
                {"case_id": "case-A", "status": "FAIL"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            candidate_eval=[
                {"case_id": "case-A", "status": "PASS"},
                {"case_id": "case-B", "status": "PASS"},
            ],
            baseline_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
                # No case_id -> ignored (no false positive).
                {"status": "PASS"},
                # Non-string case_id -> ignored.
                {"case_id": 42, "status": "PASS"},
            ],
            candidate_held_out=[
                {"case_id": "held-H1", "status": "PASS"},
                # Matching unkeyed row flips to FAIL but has
                # no case_id, so it must NOT trip the guard.
                {"status": "FAIL"},
                # Matching int-keyed row also flips to FAIL
                # but its case_id is not a string, so it must
                # NOT trip the guard.
                {"case_id": 99, "status": "FAIL"},
            ],
        )
        assert decision["accepted"] is True
        assert decision["reason"] == "accepted"
        assert decision["new_held_out_fail_blocked_case_ids"] == []
        assert decision["held_out_pass_to_fail_case_ids"] == []


# --------------------------------------------------------------------------- #
# Smoke test: parametrized coverage of all four helpers                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "helper_name",
    [
        "_eval_split_fail_or_blocked_count",
        "_held_out_pass_to_fail_case_ids",
        "_eval_split_transitions",
        "compare_eval_held_out",
    ],
)
def test_helper_is_callable(helper_name: str) -> None:
    """Each named helper is importable and callable from the
    :mod:`metacrucible.optimizer` public surface."""
    import metacrucible.optimizer as optimizer_module

    helper = getattr(optimizer_module, helper_name)
    assert callable(helper), (
        f"{helper_name!r} must be callable; "
        f"got non-callable attribute of type {type(helper).__name__}"
    )
