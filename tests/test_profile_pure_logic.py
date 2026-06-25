"""Pure-logic unit tests for the static review profile framework (Issue #44).

Direct unit tests for the named pure-logic functions in
:mod:`metacrucible.profiles`:

  * :func:`compute_evaluation_harness_sha`
  * :func:`select_triggers`
  * :func:`select_supplemental`
  * :func:`evaluate_acceptance`
  * :func:`evaluate_runtime_neutrality`
  * :func:`evaluate_routing_surface_safety`
  * :func:`evaluate_secret_privacy_risk`
  * :func:`evaluate_darwin_skill_quality`
  * :func:`weakest_darwin_dimensions`
  * :func:`_content_hash`
  * :func:`_builtin_spec_index`
  * :func:`_sha256_hex`

Each test pins a piece of observable behavior of a single function.
No live LLM, network, sleep, or subprocess calls. No real secrets
appear in any fixture; any string that matches a high-confidence
secret pattern is an obviously fake placeholder (``AKIAIOSFODNN7EXAMPLE``,
zero-padded ``ghp_`` / ``sk_live_`` shapes) and is used only to exercise
the secret-privacy detector.
"""
from __future__ import annotations

import hashlib

import pytest

from metacrucible.profiles import (
    BUILTIN_PROFILES,
    BUILTIN_PROFILE_IDS,
    DARWIN_DIMENSIONS,
    DARWIN_SKILL_QUALITY_ID,
    DARWIN_SKILL_QUALITY_VERSION,
    ROUTING_SURFACE_CAP,
    ROUTING_SURFACE_SAFETY_ID,
    ROUTING_SURFACE_SAFETY_VERSION,
    RUNTIME_NEUTRALITY_ID,
    RUNTIME_NEUTRALITY_VERSION,
    RUNTIME_PORTABILITY_TARGETS,
    SECRET_PRIVACY_RISK_ID,
    SECRET_PRIVACY_RISK_VERSION,
    ProfileResult,
    ProfileSpec,
    _builtin_spec_index,
    _content_hash,
    _sha256_hex,
    compute_evaluation_harness_sha,
    evaluate_acceptance,
    evaluate_darwin_skill_quality,
    evaluate_routing_surface_safety,
    evaluate_runtime_neutrality,
    evaluate_secret_privacy_risk,
    select_supplemental,
    select_triggers,
    weakest_darwin_dimensions,
)


# --------------------------------------------------------------------------- #
# Constants / fake fixtures                                                    #
# --------------------------------------------------------------------------- #


#: Obviously fake placeholder matching ``AKIA[0-9A-Z]{16}`` (the AWS docs
#: canonical example access key). Used only to exercise the secret-privacy
#: detector. Not a real key.
FAKE_AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"

#: Obviously fake placeholder matching ``ghp_[A-Za-z0-9]{36}``. Not a real
#: GitHub personal access token; the body is zero-padded so no real PAT
#: ever matches this shape.
FAKE_GITHUB_PAT = "ghp_" + "0" * 36

#: Obviously fake placeholder matching ``sk_live_[A-Za-z0-9]{24,}``. Not a
#: real Stripe live secret key.
FAKE_STRIPE_LIVE_KEY = "sk_live_" + "0" * 30

#: Plain body with no secret-like patterns.
CLEAN_BODY = "this is a body that contains nothing secret-like at all"


# --------------------------------------------------------------------------- #
# _content_hash                                                                 #
# --------------------------------------------------------------------------- #


class TestContentHash:
    """Pin the SHA-256 hex digest helper for profile rule summaries."""

    def test_empty_string_returns_known_sha256(self) -> None:
        # SHA-256 of empty bytes is a published constant.
        assert _content_hash("") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_returns_64_char_lowercase_hex(self) -> None:
        digest = _content_hash("any rule summary")
        assert len(digest) == 64
        assert all(ch in "0123456789abcdef" for ch in digest)

    def test_matches_hashlib_sha256_of_utf8(self) -> None:
        text = "checks language claims against portability.target"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert _content_hash(text) == expected

    def test_distinct_inputs_yield_distinct_digests(self) -> None:
        assert _content_hash("a") != _content_hash("b")

    def test_unicode_input_is_hashed_as_utf8(self) -> None:
        text = "héllo · ✓"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert _content_hash(text) == expected


# --------------------------------------------------------------------------- #
# _sha256_hex                                                                   #
# --------------------------------------------------------------------------- #


class TestSha256Hex:
    """Pin the SHA-256 hex digest helper used for fake-secret binding."""

    def test_empty_string_returns_known_sha256(self) -> None:
        assert _sha256_hex("") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_matches_hashlib_sha256_of_utf8(self) -> None:
        text = "fixture text"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert _sha256_hex(text) == expected

    def test_returns_64_char_lowercase_hex(self) -> None:
        digest = _sha256_hex("anything")
        assert len(digest) == 64
        assert all(ch in "0123456789abcdef" for ch in digest)

    def test_distinct_inputs_yield_distinct_digests(self) -> None:
        assert _sha256_hex("alpha") != _sha256_hex("beta")

    def test_two_helpers_agree_on_same_input(self) -> None:
        # Both helpers are SHA-256 of UTF-8 bytes; the binding helper and
        # the content-hash helper must agree on the same input.
        text = "shared input"
        assert _sha256_hex(text) == _content_hash(text)


# --------------------------------------------------------------------------- #
# _builtin_spec_index                                                           #
# --------------------------------------------------------------------------- #


class TestBuiltinSpecIndex:
    """Pin the spec-index helper used to resolve profile ids to specs."""

    def test_returns_dict_mapping_id_to_spec(self) -> None:
        index = _builtin_spec_index()
        assert isinstance(index, dict)
        for spec in BUILTIN_PROFILES:
            assert index[spec.id] is spec

    def test_keys_match_builtin_profile_ids(self) -> None:
        index = _builtin_spec_index()
        assert set(index.keys()) == set(BUILTIN_PROFILE_IDS)

    def test_returns_a_fresh_dict_each_call(self) -> None:
        # Defensive: callers may mutate the dict; the helper should not
        # hand out a shared reference.
        first = _builtin_spec_index()
        first["__sentinel__"] = object()
        second = _builtin_spec_index()
        assert "__sentinel__" not in second

    def test_resolves_every_pinned_builtin_id(self) -> None:
        index = _builtin_spec_index()
        for pinned_id in BUILTIN_PROFILE_IDS:
            assert pinned_id in index
            assert isinstance(index[pinned_id], ProfileSpec)


# --------------------------------------------------------------------------- #
# compute_evaluation_harness_sha                                                #
# --------------------------------------------------------------------------- #


class TestComputeEvaluationHarnessSha:
    """Pin the harness identity digest contract (ADR 0033)."""

    def test_returns_64_char_lowercase_hex(self) -> None:
        digest = compute_evaluation_harness_sha(BUILTIN_PROFILES)
        assert len(digest) == 64
        assert all(ch in "0123456789abcdef" for ch in digest)

    def test_digest_is_stable_for_identical_inputs(self) -> None:
        a = compute_evaluation_harness_sha(BUILTIN_PROFILES)
        b = compute_evaluation_harness_sha(BUILTIN_PROFILES)
        assert a == b

    def test_digest_is_order_independent(self) -> None:
        # Reorder the input specs; the digest must be identical.
        reversed_specs = tuple(reversed(BUILTIN_PROFILES))
        a = compute_evaluation_harness_sha(BUILTIN_PROFILES)
        b = compute_evaluation_harness_sha(reversed_specs)
        assert a == b

    def test_digest_changes_when_config_hash_changes(self) -> None:
        a = compute_evaluation_harness_sha(BUILTIN_PROFILES, config_hash="")
        b = compute_evaluation_harness_sha(
            BUILTIN_PROFILES, config_hash="threshold-v2"
        )
        assert a != b

    def test_digest_changes_when_profile_version_changes(self) -> None:
        # Bump one profile's version. Same id, same content_hash, new
        # version -- the digest must shift.
        tweaked = tuple(
            ProfileSpec(
                id=spec.id,
                version="v2",
                blocking=spec.blocking,
                built_in=spec.built_in,
                content_hash=spec.content_hash,
            )
            if spec.id == RUNTIME_NEUTRALITY_ID
            else spec
            for spec in BUILTIN_PROFILES
        )
        a = compute_evaluation_harness_sha(BUILTIN_PROFILES)
        b = compute_evaluation_harness_sha(tweaked)
        assert a != b

    def test_digest_changes_when_content_hash_changes(self) -> None:
        tweaked = tuple(
            ProfileSpec(
                id=spec.id,
                version=spec.version,
                blocking=spec.blocking,
                built_in=spec.built_in,
                content_hash="0" * 64,
            )
            if spec.id == DARWIN_SKILL_QUALITY_ID
            else spec
            for spec in BUILTIN_PROFILES
        )
        a = compute_evaluation_harness_sha(BUILTIN_PROFILES)
        b = compute_evaluation_harness_sha(tweaked)
        assert a != b

    def test_digest_changes_when_profile_disabled(self) -> None:
        a = compute_evaluation_harness_sha(BUILTIN_PROFILES)
        b = compute_evaluation_harness_sha(
            BUILTIN_PROFILES, disabled_profiles=[DARWIN_SKILL_QUALITY_ID]
        )
        assert a != b

    def test_disabling_secret_privacy_risk_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            compute_evaluation_harness_sha(
                BUILTIN_PROFILES, disabled_profiles=[SECRET_PRIVACY_RISK_ID]
            )

    def test_disabling_routing_surface_safety_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            compute_evaluation_harness_sha(
                BUILTIN_PROFILES,
                disabled_profiles=[ROUTING_SURFACE_SAFETY_ID],
            )

    def test_disabling_non_safety_profile_is_allowed(self) -> None:
        # Darwin / runtime-neutrality are not hard-coded; disabling them
        # must not raise.
        digest = compute_evaluation_harness_sha(
            BUILTIN_PROFILES,
            disabled_profiles=[
                DARWIN_SKILL_QUALITY_ID,
                RUNTIME_NEUTRALITY_ID,
            ],
        )
        assert len(digest) == 64

    def test_disabled_profiles_set_is_deduplicated(self) -> None:
        # Listing the same id twice must produce the same digest as once.
        a = compute_evaluation_harness_sha(
            BUILTIN_PROFILES, disabled_profiles=[DARWIN_SKILL_QUALITY_ID]
        )
        b = compute_evaluation_harness_sha(
            BUILTIN_PROFILES,
            disabled_profiles=[DARWIN_SKILL_QUALITY_ID, DARWIN_SKILL_QUALITY_ID],
        )
        assert a == b

    def test_disabled_profiles_are_sorted_in_digest(self) -> None:
        # The digest must be independent of the disabled-set iteration order.
        a = compute_evaluation_harness_sha(
            BUILTIN_PROFILES,
            disabled_profiles=[
                RUNTIME_NEUTRALITY_ID,
                DARWIN_SKILL_QUALITY_ID,
            ],
        )
        b = compute_evaluation_harness_sha(
            BUILTIN_PROFILES,
            disabled_profiles=[
                DARWIN_SKILL_QUALITY_ID,
                RUNTIME_NEUTRALITY_ID,
            ],
        )
        assert a == b


# --------------------------------------------------------------------------- #
# select_triggers / select_supplemental                                         #
# --------------------------------------------------------------------------- #


class TestSelectTriggers:
    """Pin the trigger surface contract (ADR 0033)."""

    def test_secret_privacy_risk_always_runs(self) -> None:
        ids = {spec.id for spec in select_triggers(routing_touched=False)}
        assert SECRET_PRIVACY_RISK_ID in ids

    def test_routing_surface_safety_omitted_when_not_touched(self) -> None:
        ids = {spec.id for spec in select_triggers(routing_touched=False)}
        assert ROUTING_SURFACE_SAFETY_ID not in ids

    def test_routing_surface_safety_runs_when_touched(self) -> None:
        ids = {spec.id for spec in select_triggers(routing_touched=True)}
        assert ROUTING_SURFACE_SAFETY_ID in ids

    def test_returned_tuple_is_in_canonical_order(self) -> None:
        triggered = select_triggers(routing_touched=True)
        expected = tuple(
            spec for spec in BUILTIN_PROFILES if spec.id in {s.id for s in triggered}
        )
        assert triggered == expected

    def test_routing_touched_false_returns_only_secret_privacy_risk(self) -> None:
        assert select_triggers(routing_touched=False) == (
            _builtin_spec_index()[SECRET_PRIVACY_RISK_ID],
        )


class TestSelectSupplemental:
    """Pin the supplemental-layer contract (ADR 0033)."""

    def test_returns_darwin_and_runtime_neutrality(self) -> None:
        ids = {spec.id for spec in select_supplemental()}
        assert ids == {DARWIN_SKILL_QUALITY_ID, RUNTIME_NEUTRALITY_ID}

    def test_returns_exactly_two_profiles(self) -> None:
        assert len(select_supplemental()) == 2

    def test_returns_darwin_first_then_runtime_neutrality(self) -> None:
        # The tuple order is part of the canonical surface.
        assert [s.id for s in select_supplemental()] == [
            DARWIN_SKILL_QUALITY_ID,
            RUNTIME_NEUTRALITY_ID,
        ]

    def test_supplemental_profiles_are_non_blocking(self) -> None:
        for spec in select_supplemental():
            assert spec.blocking is False


# --------------------------------------------------------------------------- #
# evaluate_acceptance                                                           #
# --------------------------------------------------------------------------- #


def _result(
    profile_id: str,
    status: str = "PASS",
    *,
    version: str = "v1",
    blockers: tuple[dict, ...] = (),
    findings: tuple[dict, ...] = (),
) -> ProfileResult:
    """Build a real :class:`ProfileResult` with the given payload."""
    return ProfileResult(
        profile_id=profile_id,
        version=version,
        status=status,
        blockers=blockers,
        findings=findings,
    )


class TestEvaluateAcceptance:
    """Pin the acceptance-verdict aggregation contract."""

    def test_empty_results_yield_accepted_with_empty_lists(self) -> None:
        verdict = evaluate_acceptance([], profile_specs={})
        assert verdict == {
            "accepted": True,
            "blockers": [],
            "supplemental_findings": [],
        }

    def test_blocking_profile_pass_yields_accepted(self) -> None:
        spec = _builtin_spec_index()[SECRET_PRIVACY_RISK_ID]
        result = _result(SECRET_PRIVACY_RISK_ID, "PASS")
        verdict = evaluate_acceptance(
            [result], profile_specs={SECRET_PRIVACY_RISK_ID: spec}
        )
        assert verdict["accepted"] is True
        assert verdict["blockers"] == []

    def test_blocking_profile_blocked_yields_rejected(self) -> None:
        spec = _builtin_spec_index()[SECRET_PRIVACY_RISK_ID]
        result = _result(
            SECRET_PRIVACY_RISK_ID,
            "BLOCKED",
            blockers=({"id": "x", "message": "y"},),
        )
        verdict = evaluate_acceptance(
            [result], profile_specs={SECRET_PRIVACY_RISK_ID: spec}
        )
        assert verdict["accepted"] is False
        assert verdict["blockers"] == [{"id": "x", "message": "y"}]

    def test_blocking_profile_failed_yields_rejected(self) -> None:
        spec = _builtin_spec_index()[SECRET_PRIVACY_RISK_ID]
        result = _result(
            SECRET_PRIVACY_RISK_ID,
            "FAIL",
            blockers=({"id": "f", "message": "boom"},),
        )
        verdict = evaluate_acceptance(
            [result], profile_specs={SECRET_PRIVACY_RISK_ID: spec}
        )
        assert verdict["accepted"] is False
        assert verdict["blockers"] == [{"id": "f", "message": "boom"}]

    def test_supplemental_fail_does_not_block_acceptance(self) -> None:
        spec = _builtin_spec_index()[DARWIN_SKILL_QUALITY_ID]
        result = _result(
            DARWIN_SKILL_QUALITY_ID,
            "FAIL",
            blockers=({"id": "d", "message": "weak"},),
            findings=({"id": "f", "message": "obs"},),
        )
        verdict = evaluate_acceptance(
            [result], profile_specs={DARWIN_SKILL_QUALITY_ID: spec}
        )
        assert verdict["accepted"] is True
        # The supplemental blocker is NOT a verdict blocker; only the
        # finding surfaces.
        assert verdict["blockers"] == []
        assert verdict["supplemental_findings"] == [{"id": "f", "message": "obs"}]

    def test_supplemental_findings_are_always_collected(self) -> None:
        spec = _builtin_spec_index()[DARWIN_SKILL_QUALITY_ID]
        result = _result(
            DARWIN_SKILL_QUALITY_ID,
            "PASS",
            findings=({"id": "d.find", "message": "ok"},),
        )
        verdict = evaluate_acceptance(
            [result], profile_specs={DARWIN_SKILL_QUALITY_ID: spec}
        )
        assert verdict["accepted"] is True
        assert verdict["supplemental_findings"] == [
            {"id": "d.find", "message": "ok"}
        ]

    def test_missing_spec_is_treated_as_non_blocking(self) -> None:
        # A result whose id is not in profile_specs must not poison
        # acceptance; the verdict stays accepted.
        result = _result(
            "rogue-profile",
            "BLOCKED",
            blockers=({"id": "r", "message": "r-block"},),
        )
        verdict = evaluate_acceptance([result], profile_specs={})
        assert verdict["accepted"] is True
        # Findings / blockers from a non-blocking result are still
        # surfaced (defensive).
        assert verdict["supplemental_findings"] == []

    def test_multiple_blocking_blockers_aggregated(self) -> None:
        spec_a = _builtin_spec_index()[SECRET_PRIVACY_RISK_ID]
        spec_b = _builtin_spec_index()[ROUTING_SURFACE_SAFETY_ID]
        results = [
            _result(
                SECRET_PRIVACY_RISK_ID,
                "BLOCKED",
                blockers=({"id": "a", "message": "A"},),
            ),
            _result(
                ROUTING_SURFACE_SAFETY_ID,
                "BLOCKED",
                blockers=({"id": "b", "message": "B"},),
            ),
        ]
        verdict = evaluate_acceptance(
            results,
            profile_specs={
                SECRET_PRIVACY_RISK_ID: spec_a,
                ROUTING_SURFACE_SAFETY_ID: spec_b,
            },
        )
        assert verdict["accepted"] is False
        assert verdict["blockers"] == [
            {"id": "a", "message": "A"},
            {"id": "b", "message": "B"},
        ]



# --------------------------------------------------------------------------- #
# evaluate_runtime_neutrality                                                   #
# --------------------------------------------------------------------------- #


class TestEvaluateRuntimeNeutrality:
    """Pin the runtime-neutrality portability-trigger contract (Issue #23)."""

    @pytest.mark.parametrize("target", list(RUNTIME_PORTABILITY_TARGETS))
    def test_each_allowed_target_passes(self, target: str) -> None:
        result = evaluate_runtime_neutrality(
            {"portability": {"target": target}}
        )
        assert result.profile_id == RUNTIME_NEUTRALITY_ID
        assert result.version == RUNTIME_NEUTRALITY_VERSION
        assert result.status == "PASS"
        assert result.blockers == ()
        assert len(result.findings) == 1

    @pytest.mark.parametrize("target", list(RUNTIME_PORTABILITY_TARGETS))
    def test_pass_finding_records_observed_target(self, target: str) -> None:
        finding = evaluate_runtime_neutrality(
            {"portability": {"target": target}}
        ).findings[0]
        assert finding["id"] == f"runtime-neutrality.target.{target}"
        assert finding["target"] == target

    def test_unknown_target_blocks(self) -> None:
        result = evaluate_runtime_neutrality(
            {"portability": {"target": "unknown-runtime"}}
        )
        assert result.status == "BLOCKED"
        assert result.blockers[0]["id"] == "runtime-neutrality.target"
        assert result.blockers[0]["target"] == "unknown-runtime"

    def test_missing_portability_block_blocks(self) -> None:
        result = evaluate_runtime_neutrality({})
        assert result.status == "BLOCKED"
        assert result.blockers[0]["id"] == "runtime-neutrality.target"
        assert result.blockers[0]["target"] is None

    def test_missing_target_block_blocks(self) -> None:
        result = evaluate_runtime_neutrality({"portability": {}})
        assert result.status == "BLOCKED"
        assert result.blockers[0]["id"] == "runtime-neutrality.target"
        assert result.blockers[0]["target"] is None

    def test_non_mapping_portability_blocks(self) -> None:
        result = evaluate_runtime_neutrality({"portability": "nope"})
        assert result.status == "BLOCKED"
        # A non-mapping portability block fails the ``isinstance`` gate
        # for target extraction, so target falls through to None and the
        # blocker surfaces the unresolved trigger.
        assert result.blockers[0]["id"] == "runtime-neutrality.target"
        assert result.blockers[0]["target"] is None

    def test_non_mapping_artifact_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            evaluate_runtime_neutrality("not a mapping")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# evaluate_routing_surface_safety                                               #
# --------------------------------------------------------------------------- #


class TestEvaluateRoutingSurfaceSafety:
    """Pin the routing-surface-safety contract (Issue #24, ADR 0027 / 0032)."""

    def test_no_routing_changes_passes(self) -> None:
        result = evaluate_routing_surface_safety({"routing_changes": []})
        assert result.profile_id == ROUTING_SURFACE_SAFETY_ID
        assert result.version == ROUTING_SURFACE_SAFETY_VERSION
        assert result.status == "PASS"
        assert result.findings == ()
        assert result.blockers == ()

    def test_missing_routing_changes_passes(self) -> None:
        # Missing key is treated as no changes.
        result = evaluate_routing_surface_safety({})
        assert result.status == "PASS"
        assert result.blockers == ()

    def test_one_change_with_human_confirmed_passes_with_finding(self) -> None:
        result = evaluate_routing_surface_safety(
            {
                "routing_changes": [{"field": "name"}],
                "human_confirmed": True,
            }
        )
        assert result.status == "PASS"
        assert len(result.findings) == 1
        assert result.findings[0]["id"] == (
            "routing-surface-safety.change.recorded"
        )
        assert result.findings[0]["field"] == "name"
        assert result.findings[0]["human_confirmed"] is True

    def test_one_change_without_human_confirmed_blocks(self) -> None:
        result = evaluate_routing_surface_safety(
            {
                "routing_changes": [{"field": "name"}],
                "human_confirmed": False,
            }
        )
        assert result.status == "BLOCKED"
        ids = [b["id"] for b in result.blockers]
        assert "routing-surface-safety.hitl-required" in ids
        assert "routing-surface-safety.cap-exceeded" not in ids

    def test_cap_exceeded_blocks_with_cap_blocker(self) -> None:
        result = evaluate_routing_surface_safety(
            {
                "routing_changes": [
                    {"field": "name"},
                    {"field": "description"},
                ],
                "human_confirmed": True,
            }
        )
        assert result.status == "BLOCKED"
        cap = [b for b in result.blockers if b["id"] ==
               "routing-surface-safety.cap-exceeded"]
        assert cap
        assert cap[0]["change_count"] == 2
        assert cap[0]["cap"] == ROUTING_SURFACE_CAP

    def test_cap_and_hitl_can_co_occur(self) -> None:
        # Three changes + no confirmation: both blockers surface.
        result = evaluate_routing_surface_safety(
            {
                "routing_changes": [
                    {"field": "a"},
                    {"field": "b"},
                    {"field": "c"},
                ],
                "human_confirmed": False,
            }
        )
        assert result.status == "BLOCKED"
        ids = {b["id"] for b in result.blockers}
        assert "routing-surface-safety.cap-exceeded" in ids
        assert "routing-surface-safety.hitl-required" in ids

    def test_non_sequence_routing_changes_treated_as_empty(self) -> None:
        result = evaluate_routing_surface_safety(
            {"routing_changes": "not-a-sequence", "human_confirmed": False}
        )
        assert result.status == "PASS"

    def test_non_mapping_proposal_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            evaluate_routing_surface_safety("not a mapping")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# evaluate_secret_privacy_risk                                                  #
# --------------------------------------------------------------------------- #


class TestEvaluateSecretPrivacyRisk:
    """Pin the secret-privacy-risk contract (Issue #25)."""

    def test_clean_body_passes(self) -> None:
        result = evaluate_secret_privacy_risk({"body": CLEAN_BODY})
        assert result.profile_id == SECRET_PRIVACY_RISK_ID
        assert result.version == SECRET_PRIVACY_RISK_VERSION
        assert result.status == "PASS"
        assert result.blockers == ()

    def test_empty_body_passes(self) -> None:
        result = evaluate_secret_privacy_risk({"body": ""})
        assert result.status == "PASS"

    def test_missing_body_passes(self) -> None:
        result = evaluate_secret_privacy_risk({})
        assert result.status == "PASS"

    def test_aws_key_with_no_review_blocks_unreviewed(self) -> None:
        result = evaluate_secret_privacy_risk({"body": FAKE_AWS_ACCESS_KEY_ID})
        assert result.status == "BLOCKED"
        ids = [b["id"] for b in result.blockers]
        assert "secret-privacy-risk.unreviewed-secret" in ids
        assert result.blockers[0]["pattern_id"] == "aws-access-key-id"
        assert result.blockers[0]["match"] == FAKE_AWS_ACCESS_KEY_ID

    def test_github_pat_with_no_review_blocks_unreviewed(self) -> None:
        result = evaluate_secret_privacy_risk({"body": FAKE_GITHUB_PAT})
        assert result.status == "BLOCKED"
        assert any(
            b["pattern_id"] == "github-personal-access-token"
            for b in result.blockers
        )

    def test_stripe_live_key_with_no_review_blocks_unreviewed(self) -> None:
        result = evaluate_secret_privacy_risk({"body": FAKE_STRIPE_LIVE_KEY})
        assert result.status == "BLOCKED"
        assert any(
            b["pattern_id"] == "stripe-live-secret-key" for b in result.blockers
        )

    def test_reviewed_match_with_valid_hash_passes(self) -> None:
        body = f"prefix {FAKE_AWS_ACCESS_KEY_ID} suffix"
        binding = _sha256_hex(FAKE_AWS_ACCESS_KEY_ID)
        result = evaluate_secret_privacy_risk(
            {
                "body": body,
                "reviewed_fake_secrets": [
                    {
                        "match": FAKE_AWS_ACCESS_KEY_ID,
                        "fixture_sha256": binding,
                        "fixture_id": "fixture-aws-1",
                    }
                ],
            }
        )
        assert result.status == "PASS"
        assert result.blockers == ()

    def test_reviewed_match_with_mismatched_hash_blocks(self) -> None:
        body = f"prefix {FAKE_AWS_ACCESS_KEY_ID} suffix"
        result = evaluate_secret_privacy_risk(
            {
                "body": body,
                "reviewed_fake_secrets": [
                    {
                        "match": FAKE_AWS_ACCESS_KEY_ID,
                        "fixture_sha256": "f" * 64,  # wrong binding
                        "fixture_id": "fixture-aws-stale",
                    }
                ],
            }
        )
        assert result.status == "BLOCKED"
        ids = [b["id"] for b in result.blockers]
        assert "secret-privacy-risk.reviewed-hash-mismatch" in ids
        # The mismatch blocker carries the listed fixture_id so a report
        # can link the failure back to the reviewer.
        mismatch = [
            b for b in result.blockers
            if b["id"] == "secret-privacy-risk.reviewed-hash-mismatch"
        ]
        assert mismatch[0]["fixture_id"] == "fixture-aws-stale"

    def test_non_string_fixture_sha_is_invalid(self) -> None:
        # A non-string binding is degenerate and treated as no review.
        result = evaluate_secret_privacy_risk(
            {
                "body": FAKE_AWS_ACCESS_KEY_ID,
                "reviewed_fake_secrets": [
                    {
                        "match": FAKE_AWS_ACCESS_KEY_ID,
                        "fixture_sha256": 12345,  # type: ignore[dict-item]
                    }
                ],
            }
        )
        assert result.status == "BLOCKED"
        ids = [b["id"] for b in result.blockers]
        assert "secret-privacy-risk.unreviewed-secret" in ids

    def test_empty_reviewed_list_does_not_clear(self) -> None:
        result = evaluate_secret_privacy_risk(
            {
                "body": FAKE_AWS_ACCESS_KEY_ID,
                "reviewed_fake_secrets": [],
            }
        )
        assert result.status == "BLOCKED"

    def test_partial_review_leaves_unreviewed_blocked(self) -> None:
        # Two distinct matches; only one is reviewed and hash-bound.
        body = f"{FAKE_AWS_ACCESS_KEY_ID} and {FAKE_GITHUB_PAT}"
        result = evaluate_secret_privacy_risk(
            {
                "body": body,
                "reviewed_fake_secrets": [
                    {
                        "match": FAKE_AWS_ACCESS_KEY_ID,
                        "fixture_sha256": _sha256_hex(FAKE_AWS_ACCESS_KEY_ID),
                        "fixture_id": "fixture-aws",
                    }
                ],
            }
        )
        assert result.status == "BLOCKED"
        # The unreviewed GitHub PAT must be reported; the AWS one is
        # cleared by the matching binding.
        pattern_ids = {b["pattern_id"] for b in result.blockers}
        assert "aws-access-key-id" not in pattern_ids
        assert "github-personal-access-token" in pattern_ids

    def test_non_mapping_artifact_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            evaluate_secret_privacy_risk("not a mapping")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# evaluate_darwin_skill_quality                                                 #
# --------------------------------------------------------------------------- #


class TestEvaluateDarwinSkillQuality:
    """Pin the Darwin 9-dimension rubric contract (Issue #22)."""

    def test_returns_pass_status(self) -> None:
        result = evaluate_darwin_skill_quality({"body": CLEAN_BODY})
        assert result.profile_id == DARWIN_SKILL_QUALITY_ID
        assert result.version == DARWIN_SKILL_QUALITY_VERSION
        assert result.status == "PASS"

    def test_emits_one_score_per_pinned_dimension(self) -> None:
        result = evaluate_darwin_skill_quality({"body": CLEAN_BODY})
        assert len(result.dimension_scores) == len(DARWIN_DIMENSIONS)
        assert [d["id"] for d in result.dimension_scores] == list(DARWIN_DIMENSIONS)

    def test_empty_body_scores_zero_on_content_dimensions(self) -> None:
        result = evaluate_darwin_skill_quality({"body": ""})
        content_dims = [d for d in result.dimension_scores
                        if d["id"] != "runtime_neutrality"]
        assert all(d["score"] == 0.0 for d in content_dims)

    def test_runtime_neutrality_scores_one_for_allowed_target(self) -> None:
        result = evaluate_darwin_skill_quality(
            {"body": "", "portability": {"target": "claude_code"}}
        )
        runtime = next(
            d for d in result.dimension_scores if d["id"] == "runtime_neutrality"
        )
        assert runtime["score"] == 1.0

    def test_runtime_neutrality_scores_zero_for_missing_target(self) -> None:
        result = evaluate_darwin_skill_quality({"body": ""})
        runtime = next(
            d for d in result.dimension_scores if d["id"] == "runtime_neutrality"
        )
        assert runtime["score"] == 0.0

    def test_runtime_neutrality_scores_zero_for_unknown_target(self) -> None:
        result = evaluate_darwin_skill_quality(
            {"body": "", "portability": {"target": "unknown-runtime"}}
        )
        runtime = next(
            d for d in result.dimension_scores if d["id"] == "runtime_neutrality"
        )
        assert runtime["score"] == 0.0

    def test_content_dimensions_respond_to_artifact(self) -> None:
        # A body that hits every signal on a single dimension should
        # score strictly higher than the same dimension on an empty body.
        rich_body = (
            "## input\n"
            "input: a parameter is required.\n"
            "- bullet one\n"
            "- bullet two\n"
            "```\ncode\n```\n" + "x" * 1000
        )
        result = evaluate_darwin_skill_quality({"body": rich_body})
        input_dim = next(
            d for d in result.dimension_scores if d["id"] == "input_contract"
        )
        assert input_dim["score"] > 0.0

    def test_dimension_scores_are_rounded_to_three_decimals(self) -> None:
        result = evaluate_darwin_skill_quality({"body": CLEAN_BODY})
        for d in result.dimension_scores:
            # Each score is a numeric type; ensure the rounding contract
            # is at-most three decimals.
            score = d["score"]
            assert isinstance(score, (int, float))
            # The string form of the rounded score must match at three
            # decimal places.
            assert round(float(score), 3) == float(score)

    def test_non_mapping_artifact_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            evaluate_darwin_skill_quality("not a mapping")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# weakest_darwin_dimensions                                                     #
# --------------------------------------------------------------------------- #


def _darwin_result(scores: tuple[tuple[str, float], ...]) -> ProfileResult:
    """Build a synthetic Darwin :class:`ProfileResult` with the given scores."""
    return ProfileResult(
        profile_id=DARWIN_SKILL_QUALITY_ID,
        version=DARWIN_SKILL_QUALITY_VERSION,
        status="PASS",
        dimension_scores=tuple({"id": sid, "score": s} for sid, s in scores),
    )


class TestWeakestDarwinDimensions:
    """Pin the deterministic weakest-dimensions ranking contract (Issue #22)."""

    def test_default_n_returns_three_weakest(self) -> None:
        scores = (
            ("a", 0.1),
            ("b", 0.9),
            ("c", 0.4),
            ("d", 0.7),
            ("e", 0.2),
        )
        result = _darwin_result(scores)
        weakest = weakest_darwin_dimensions(result)
        assert [d["id"] for d in weakest] == ["a", "e", "c"]

    def test_sort_is_score_ascending_then_id_ascending(self) -> None:
        # Two dimensions tie at 0.5; the tie-break is ascending id.
        scores = (
            ("zzz", 0.5),
            ("aaa", 0.5),
            ("mmm", 0.1),
        )
        result = _darwin_result(scores)
        weakest = weakest_darwin_dimensions(result, n=3)
        assert [d["id"] for d in weakest] == ["mmm", "aaa", "zzz"]

    def test_n_zero_returns_empty_tuple(self) -> None:
        result = _darwin_result((("a", 0.1), ("b", 0.2)))
        assert weakest_darwin_dimensions(result, n=0) == ()

    def test_n_one_returns_single_weakest(self) -> None:
        result = _darwin_result((("a", 0.3), ("b", 0.1), ("c", 0.2)))
        weakest = weakest_darwin_dimensions(result, n=1)
        assert [d["id"] for d in weakest] == ["b"]

    def test_n_larger_than_dimensions_returns_all(self) -> None:
        result = _darwin_result((("a", 0.1), ("b", 0.2)))
        weakest = weakest_darwin_dimensions(result, n=10)
        assert [d["id"] for d in weakest] == ["a", "b"]

    def test_rejects_non_darwin_result(self) -> None:
        # The helper is the Darwin-specific ranking; any other profile id
        # is a programming error.
        bad = ProfileResult(
            profile_id="other-profile",
            version="v1",
            status="PASS",
            dimension_scores=({"id": "x", "score": 0.1},),
        )
        with pytest.raises(ValueError):
            weakest_darwin_dimensions(bad)

    def test_rejects_negative_n(self) -> None:
        result = _darwin_result((("a", 0.1),))
        with pytest.raises(ValueError):
            weakest_darwin_dimensions(result, n=-1)
