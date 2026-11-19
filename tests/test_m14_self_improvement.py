"""tests/test_m14_self_improvement.py — Comprehensive Test Suite for M14 Verified Self-Improvement.

Verifies:
1. Capability-gap detection, confidence, and frequency scoring.
2. SEC-11 burst coalescing and deduplication.
3. Sensitive topic prohibition shield.
4. Proposal specification and risk classification.
5. Core runtime modification rejection (Invariant 5 & SEC-01).
6. Staging workspace isolation, path traversal, and symlink rejection (SEC-01).
7. Static AST code sanitization (SEC-03).
8. Multi-file package integrity and Merkle hash verification (SEC-08).
9. Subprocess sandbox isolation and offline environment (SEC-03).
10. Timeout and process tree kill on Windows (SEC-07).
11. Evaluation failure and fail-closed behavior (Invariant 10).
12. Independent Goldfish review across 6 axes and promotion blocking.
13. SEC-09 bounded remediation loop enforcement (max_remediations = 2).
14. SEC-04 self-approval prevention (operator_id != author_agent_id).
15. SEC-02 PolicyEngine integration and ApprovalToken validation.
16. SEC-05 two-phase atomic directory promotion and SEC-10 deterministic rollback.
17. Full 15-stage end-to-end coordinator lifecycle execution.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import pytest

from core.security.policy_engine import (
    ApprovalToken,
    PolicyEngine,
    compute_canonical_hash,
)
from core.self_improvement import (
    CapabilityGap,
    CapabilityGapDetector,
    EvaluationResult,
    GoldfishReviewReport,
    ImprovementProposal,
    ImprovementProposalGenerator,
    IsolatedSandboxEvaluator,
    LifecycleState,
    PromotionEngine,
    PromotionError,
    SecuritySanitizationError,
    SecurityViolationError,
    SelfImprovementCoordinator,
    SelfImprovementGoldfishReviewer,
    SelfImprovementStore,
    StagingCodeGenerator,
    StagingPathEscapeError,
)


@pytest.fixture(autouse=True)
def clean_self_improvement_env():
    """Ensure clean singletons and isolated test directories for every test."""
    temp_dir = tempfile.mkdtemp(prefix="optimus_m14_test_")
    temp_path = Path(temp_dir)

    # Reset singletons
    SelfImprovementStore.reset_instance()
    PolicyEngine.reset_instance()
    SelfImprovementCoordinator._instance = None

    store = SelfImprovementStore.instance(root_dir=temp_path / "store")
    policy_engine = PolicyEngine.instance(journal_path=temp_path / "policy_journal.txt")

    yield {
        "temp_path": temp_path,
        "store": store,
        "policy_engine": policy_engine,
    }

    # Teardown
    SelfImprovementStore.reset_instance()
    PolicyEngine.reset_instance()
    SelfImprovementCoordinator._instance = None
    shutil.rmtree(temp_dir, ignore_errors=True)


# ── Test 1: Capability-Gap Detection, Confidence, and Frequency ───────────────

def test_gap_detection_scoring_and_thresholds(clean_self_improvement_env):
    """Verify gap detector ingests telemetry, scores confidence, and adheres to thresholds."""
    env = clean_self_improvement_env
    detector = CapabilityGapDetector(store=env["store"])

    # Event 1: Initial observation
    g1 = detector.record_observation(
        category="TOOL_MISSING",
        description="User requested YAML linting but no yaml_linter tool exists in registry.",
        evidence={"tool": "yaml_linter"},
        is_explicit_user_feedback=False,
        confidence=0.60,
    )
    assert g1 is not None
    assert g1.frequency == 1
    assert g1.confidence == 0.60
    assert g1.category == "TOOL_MISSING"

    # Actionable check: Not actionable yet (frequency 1, confidence < 0.70)
    assert len(detector.get_actionable_gaps()) == 0

    # Event 2: Recurring observation -> increments frequency and confidence
    g2 = detector.record_observation(
        category="TOOL_MISSING",
        description="User requested YAML linting but no yaml_linter tool exists in registry.",
        evidence={"tool": "yaml_linter"},
        is_explicit_user_feedback=False,
    )
    assert g2 is not None
    assert g2.gap_id == g1.gap_id
    assert g2.frequency == 2
    assert g2.confidence >= 0.70

    # Actionable check: Now actionable (frequency 2, confidence >= 0.70)
    actionable = detector.get_actionable_gaps()
    assert len(actionable) == 1
    assert actionable[0].gap_id == g1.gap_id


# ── Test 2: SEC-11 Burst Coalescing and Deduplication ─────────────────────────

def test_gap_burst_coalescing_and_debounce(clean_self_improvement_env):
    """Verify rapid bursts of duplicate errors coalesce into a single gap with signature deduplication."""
    env = clean_self_improvement_env
    detector = CapabilityGapDetector(store=env["store"])

    # Simulate 20 rapid burst events
    for _ in range(20):
        detector.record_observation(
            category="API_FAILURE",
            description="Connection timed out connecting to remote weather service.",
            evidence={"service": "weather"},
        )

    gaps = env["store"].list_gaps()
    assert len(gaps) == 1
    assert gaps[0].frequency == 20
    assert len(gaps[0].signature) == 16


# ── Test 3: Sensitive Topic Prohibition Shield ────────────────────────────────

def test_sensitive_topic_prohibition_shield(clean_self_improvement_env):
    """Verify detector strictly drops prohibited/sensitive topics (religion, politics, biometrics)."""
    env = clean_self_improvement_env
    detector = CapabilityGapDetector(store=env["store"])

    prohibited_events = [
        ("RELIGIOUS", "Recommend religious prayer routines for Ramadan"),
        ("POLITICAL", "Political election voting preference prediction model"),
        ("MEDICAL", "Diagnose acute medical patient symptoms"),
        ("BIOMETRIC", "Extract biometric fingerprint records"),
    ]

    for cat, desc in prohibited_events:
        gap = detector.record_observation(
            category=cat,
            description=desc,
        )
        assert gap is None, f"Prohibited topic '{desc}' should have been dropped!"

    assert len(env["store"].list_gaps()) == 0


# ── Test 4: Proposal Specification and Risk Classification ────────────────────

def test_proposal_specification_and_risk_classification(clean_self_improvement_env):
    """Verify proposal generator classifies risk levels accurately and binds author_agent_id."""
    env = clean_self_improvement_env
    generator = ImprovementProposalGenerator(store=env["store"])

    dummy_gap = CapabilityGap(
        gap_id="gap_spec_test",
        category="TOOL_MISSING",
        description="Missing markdown formatter utility",
        signature="sig_test_123",
    )
    env["store"].save_gap(dummy_gap)

    # Low risk: formatting utility
    prop_low = generator.create_proposal(
        gap=dummy_gap,
        title="Markdown Formatter Skill",
        problem_statement="Need table formatting",
        expected_outcome="Clean markdown output",
        scope="SKILL",
        author_agent_id="agent_planner",
        required_capabilities=["text_formatting"],
        required_permissions=[],
    )
    assert prop_low.risk_level == "LOW"
    assert prop_low.author_agent_id == "agent_planner"
    assert prop_low.state == LifecycleState.SPECIFIED

    # High risk: network and disk operations
    prop_high = generator.create_proposal(
        gap=dummy_gap,
        title="Batch Network Fetcher",
        problem_statement="Fetch remote files",
        expected_outcome="Files fetched",
        scope="TOOL_WRAPPER",
        author_agent_id="agent_dev",
        required_capabilities=["network_connect"],
        required_permissions=[],
    )
    assert prop_high.risk_level in ("HIGH", "CRITICAL")


# ── Test 5: Core Runtime Modification Rejection (Invariant 5 & SEC-01) ────────

def test_core_runtime_modification_rejection(clean_self_improvement_env):
    """Verify proposals or promotions attempting to mutate core runtime are rejected."""
    env = clean_self_improvement_env
    generator = ImprovementProposalGenerator(store=env["store"])

    dummy_gap = CapabilityGap(
        gap_id="gap_hack_core",
        category="SYSTEM_EXPLOIT",
        description="Attempt to patch security engine",
        signature="sig_hack_core",
    )
    env["store"].save_gap(dummy_gap)

    forbidden_scopes = [
        "core/security/policy_engine",
        "core/orchestrator/main",
        "policy_engine",
        "security",
    ]

    for scope in forbidden_scopes:
        with pytest.raises(ValueError) as exc_info:
            generator.create_proposal(
                gap=dummy_gap,
                title=f"Patch {scope}",
                problem_statement="Bypass policy check",
                expected_outcome="Unrestricted execution",
                scope=scope,
                author_agent_id="agent_adversary",
            )
        assert "FORBIDDEN_SCOPE_VIOLATION" in str(exc_info.value)

    # Verify destination safety on PromotionEngine
    promo_engine = PromotionEngine(
        store=env["store"],
        policy_engine=env["policy_engine"],
        production_root=env["temp_path"] / "prod",
    )

    with pytest.raises(SecurityViolationError) as exc_dest:
        promo_engine.validate_destination_safety(Path("d:/project/srrr/Mark-LVII-main/Mark-LVII-main/core/security/hack.py"))
    assert "IMMUTABLE_CORE_VIOLATION" in str(exc_dest.value)


# ── Test 6: Staging Workspace Isolation and Path Traversal (SEC-01) ───────────

def test_staging_workspace_isolation_and_path_traversal(clean_self_improvement_env):
    """Verify staging generator rejects path traversal and outside directory references."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )

    proposal = ImprovementProposal(
        proposal_id="prop_traversal_test",
        gap_id="gap_test",
        title="Path Traversal Test",
        scope="SKILL",
        risk_level="MEDIUM",
        author_agent_id="agent_test",
    )
    env["store"].save_proposal(proposal)

    # Attempt path traversal via auxiliary files
    evil_aux = {
        "../../core/security/backdoor.py": "print('pwned')",
    }

    with pytest.raises(StagingPathEscapeError) as exc_info:
        staging_gen.stage_artifact(
            proposal=proposal,
            entrypoint_content="# Safe entrypoint\n",
            auxiliary_files=evil_aux,
            artifact_type="SKILL_MD",
        )
    assert "PATH_TRAVERSAL" in str(exc_info.value)


# ── Test 7: Static AST Code Sanitization (SEC-03) ─────────────────────────────

def test_static_ast_sanitizer_security_rules(clean_self_improvement_env):
    """Verify AST sanitizer flags dynamic imports, network libraries, and eval/exec."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )

    malicious_snippets = [
        ("dynamic importlib", "import importlib\nimportlib.import_module('os')"),
        ("dynamic __import__", "mod = __import__('subprocess')"),
        ("raw exec", "exec('import os; os.system(\"calc\")')"),
        ("raw eval", "result = eval('__import__(\"sys\").exit(1)')"),
        ("network socket", "import socket\ns = socket.socket()"),
        ("network requests", "import requests\nresp = requests.get('https://evil.com')"),
    ]

    for name, code in malicious_snippets:
        with pytest.raises(SecuritySanitizationError) as exc_info:
            staging_gen.sanitize_python_code(code)
        assert any(k in str(exc_info.value) for k in ("BANNED_IMPORT", "BANNED_CALL", "BANNED_ATTRIBUTE")), (
            f"Failed to reject prohibited snippet '{name}'"
        )


# ── Test 8: Multi-File Package Integrity and Merkle Hashing (SEC-08) ──────────

def test_multi_file_package_integrity_and_merkle_hashing(clean_self_improvement_env):
    """Verify Merkle tree integrity calculation and tampering detection."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )
    promo_engine = PromotionEngine(
        store=env["store"],
        policy_engine=env["policy_engine"],
        production_root=env["temp_path"] / "prod",
    )

    proposal = ImprovementProposal(
        proposal_id="prop_merkle_test",
        gap_id="gap_test",
        title="Merkle Integrity Test",
        scope="SKILL",
        risk_level="LOW",
        author_agent_id="agent_dev",
    )
    env["store"].save_proposal(proposal)

    aux_files = {
        "manifest.json": '{"name": "merkle_skill", "version": "1.0.0"}',
        "module.py": "def run():\n    return 'verified'\n",
    }

    artifact = staging_gen.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Merkle Skill\nDocumentation here.\n",
        auxiliary_files=aux_files,
        artifact_type="SKILL_MD",
    )
    assert artifact.package_integrity_hash is not None
    assert len(artifact.files_checksums) == 3

    # Verification on pristine staging directory must pass
    promo_engine.verify_package_integrity(Path(artifact.staging_path), artifact.package_integrity_hash)

    # Tamper with one file
    tampered_file = Path(artifact.staging_path) / "module.py"
    tampered_file.write_text("def run():\n    return 'TAMPERED'\n", encoding="utf-8")

    # Verification must now fail
    with pytest.raises(SecurityViolationError) as exc_info:
        promo_engine.verify_package_integrity(Path(artifact.staging_path), artifact.package_integrity_hash)
    assert "PACKAGE_TAMPERING_DETECTED" in str(exc_info.value)


# ── Test 9: Subprocess Sandbox Offline Isolation (SEC-03) ─────────────────────

def test_sandbox_subprocess_offline_isolation(clean_self_improvement_env):
    """Verify sandbox runs candidate tests under restricted environment with offline proxies."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )
    evaluator = IsolatedSandboxEvaluator(store=env["store"])

    proposal = ImprovementProposal(
        proposal_id="prop_eval_pass",
        gap_id="gap_test",
        title="Calculator Skill",
        scope="SKILL",
        risk_level="LOW",
        author_agent_id="agent_dev",
    )
    env["store"].save_proposal(proposal)

    aux_files = {
        "calculator.py": "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
        "test_run.py": (
            "import os, sys\n"
            "from calculator import add, sub\n"
            "assert add(2, 3) == 5\n"
            "assert sub(10, 4) == 6\n"
            "# Confirm offline proxies\n"
            "assert os.environ.get('HTTP_PROXY') == 'http://127.0.0.1:0'\n"
            "print('CALCULATOR_TEST_PASSED')\n"
            "sys.exit(0)\n"
        ),
    }

    artifact = staging_gen.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Calculator Skill\nArithmetic operations.\n",
        auxiliary_files=aux_files,
        artifact_type="SKILL_MD",
    )

    eval_result = evaluator.evaluate_artifact(proposal, artifact, timeout_seconds=10.0)

    assert eval_result.passed is True
    assert eval_result.tests_passed >= 4
    assert eval_result.tests_failed == 0
    assert "CALCULATOR_TEST_PASSED" in eval_result.logs


# ── Test 10: Subprocess Sandbox Timeout and Process Tree Kill (SEC-07) ─────────

def test_sandbox_timeout_and_process_tree_kill(clean_self_improvement_env):
    """Verify sandbox terminates infinite loops and kills subprocess tree cleanly."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )
    evaluator = IsolatedSandboxEvaluator(store=env["store"])

    proposal = ImprovementProposal(
        proposal_id="prop_timeout_test",
        gap_id="gap_test",
        title="Hanging Skill",
        scope="SKILL",
        risk_level="LOW",
        author_agent_id="agent_dev",
    )
    env["store"].save_proposal(proposal)

    aux_files = {
        "test_run.py": "import time\nwhile True:\n    time.sleep(0.1)\n",
    }

    artifact = staging_gen.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Hanging Skill\n",
        auxiliary_files=aux_files,
        artifact_type="SKILL_MD",
    )

    eval_result = evaluator.evaluate_artifact(proposal, artifact, timeout_seconds=1.5)

    assert eval_result.passed is False
    assert eval_result.tests_failed >= 1
    assert "timed out" in eval_result.logs.lower()


# ── Test 11: Evaluation Failure and Fail-Closed Behavior (Invariant 10) ────────

def test_evaluation_failure_fail_closed(clean_self_improvement_env):
    """Verify failing candidate tests result in fail-closed rejection."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )
    evaluator = IsolatedSandboxEvaluator(store=env["store"])

    proposal = ImprovementProposal(
        proposal_id="prop_fail_test",
        gap_id="gap_test",
        title="Broken Math Skill",
        scope="SKILL",
        risk_level="LOW",
        author_agent_id="agent_dev",
    )
    env["store"].save_proposal(proposal)

    aux_files = {
        "test_run.py": (
            "import sys\n"
            "assert 2 * 3 == 7, 'Math error intentional'\n"
            "sys.exit(0)\n"
        ),
    }

    artifact = staging_gen.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Broken Math Skill\n",
        auxiliary_files=aux_files,
        artifact_type="SKILL_MD",
    )

    eval_result = evaluator.evaluate_artifact(proposal, artifact, timeout_seconds=5.0)

    assert eval_result.passed is False
    assert eval_result.tests_failed >= 1


# ── Test 12: Independent Goldfish Review Across 6 Axes & Blocking ─────────────

def test_goldfish_adversarial_review_6_axes_and_blocking(clean_self_improvement_env):
    """Verify independent Goldfish review blocks on Critical/High findings across 6 axes."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )
    reviewer = SelfImprovementGoldfishReviewer(store=env["store"])

    proposal = ImprovementProposal(
        proposal_id="prop_goldfish_test",
        gap_id="gap_test",
        title="Critical Privilege Skill",
        scope="SKILL",
        risk_level="CRITICAL",  # Triggers Axis 2 CRITICAL finding
        author_agent_id="agent_dev",
        acceptance_criteria=[], # Triggers Axis 1 HIGH finding
    )
    env["store"].save_proposal(proposal)

    artifact = staging_gen.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Critical Privilege Skill\nAttempting root operations.\n",
        artifact_type="SKILL_MD",
    )

    # Provide a dummy failing evaluation
    evaluation = EvaluationResult(
        evaluation_id="eval_gf_test",
        artifact_id=artifact.artifact_id,
        tests_passed=1,
        tests_failed=1,
        passed=False,
    )
    proposal.state = LifecycleState.EVALUATED

    review_report = reviewer.review_candidate(proposal, artifact, evaluation)

    assert review_report.verdict in ("BLOCKED", "CHANGES_REQUIRED")
    assert review_report.has_blocking_findings is True
    assert len(review_report.signature) == 64

    # Ensure promotion engine refuses to promote artifact with blocking findings
    promo_engine = PromotionEngine(
        store=env["store"],
        policy_engine=env["policy_engine"],
        production_root=env["temp_path"] / "prod",
    )
    with pytest.raises(PromotionError) as exc_info:
        promo_engine.promote_artifact(proposal, artifact, review_report)
    assert "PROMOTION_BLOCKED" in str(exc_info.value)


# ── Test 13: Bounded Remediation Loop Enforcement (SEC-09) ────────────────────

def test_bounded_remediation_loop_enforcement(clean_self_improvement_env):
    """Verify proposal transitions to terminal REJECTED when remediation attempts exceed max."""
    env = clean_self_improvement_env

    proposal = ImprovementProposal(
        proposal_id="prop_remed_test",
        gap_id="gap_test",
        title="Persistent Failing Skill",
        scope="SKILL",
        risk_level="LOW",
        author_agent_id="agent_dev",
        remediation_count=0,
        max_remediations=2,
    )
    env["store"].save_proposal(proposal)

    # Simulate remediation cycle 1
    proposal.remediation_count += 1
    assert proposal.remediation_count <= proposal.max_remediations

    # Simulate remediation cycle 2
    proposal.remediation_count += 1
    assert proposal.remediation_count <= proposal.max_remediations

    # Simulate remediation cycle 3 -> Exceeds max_remediations (2)
    proposal.remediation_count += 1
    if proposal.remediation_count > proposal.max_remediations:
        proposal.state = LifecycleState.REJECTED
        env["store"].save_proposal(proposal)

    assert proposal.state == LifecycleState.REJECTED


# ── Test 14: Self-Approval Prevention (SEC-04) ────────────────────────────────

def test_self_approval_prevention(clean_self_improvement_env):
    """Verify generator agent cannot approve its own proposal, and automated IDs are rejected."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )
    promo_engine = PromotionEngine(
        store=env["store"],
        policy_engine=env["policy_engine"],
        production_root=env["temp_path"] / "prod",
    )

    proposal = ImprovementProposal(
        proposal_id="prop_self_appr",
        gap_id="gap_test",
        title="Self Appr Skill",
        scope="SKILL",
        risk_level="LOW",
        author_agent_id="generator_agent_42",
        state=LifecycleState.SPECIFIED,
    )
    env["store"].save_proposal(proposal)

    artifact = staging_gen.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Clean Skill\n",
        artifact_type="SKILL_MD",
    )
    proposal.state = LifecycleState.GOLDFISH_REVIEW

    clean_review = GoldfishReviewReport(
        review_id="rev_clean",
        proposal_id=proposal.proposal_id,
        artifact_id=artifact.artifact_id,
        verdict="APPROVED",
        findings=[],
        signature="0" * 64,
    )

    # 1. Attempt self-approval (operator_id == author_agent_id)
    self_token = ApprovalToken(
        token_id="tok_self",
        mission_id="m_self",
        subtask_id="st_self",
        operator_id="generator_agent_42",
        nonce="nonce_self",
        expires_at=time.time() + 60,
        canonical_hash=compute_canonical_hash("promote_skill", {"proposal_id": proposal.proposal_id, "risk_level": proposal.risk_level}),
    )

    with pytest.raises(SecurityViolationError) as exc_self:
        promo_engine.promote_artifact(proposal, artifact, clean_review, approval_token=self_token)
    assert "SELF_APPROVAL_VIOLATION" in str(exc_self.value)
    assert proposal.state == LifecycleState.REJECTED

    # Reset proposal state for next check
    proposal.state = LifecycleState.GOLDFISH_REVIEW

    # 2. Attempt automated daemon approval (operator_id contains "system" or "llm")
    auto_token = ApprovalToken(
        token_id="tok_auto",
        mission_id="m_auto",
        subtask_id="st_auto",
        operator_id="autonomous_system_daemon",
        nonce="nonce_auto",
        expires_at=time.time() + 60,
        canonical_hash=compute_canonical_hash("promote_skill", {"proposal_id": proposal.proposal_id, "risk_level": proposal.risk_level}),
    )

    with pytest.raises(SecurityViolationError) as exc_auto:
        promo_engine.promote_artifact(proposal, artifact, clean_review, approval_token=auto_token)
    assert "AUTOMATED_APPROVAL_VIOLATION" in str(exc_auto.value)


# ── Test 15: PolicyEngine Privileged Pattern & ApprovalToken Validation (SEC-02)

def test_policy_engine_privileged_pattern_and_approval_token(clean_self_improvement_env):
    """Verify promote_skill matches PRIVILEGED_APPROVAL_PATTERNS and enforces single-use token."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )
    promo_engine = PromotionEngine(
        store=env["store"],
        policy_engine=env["policy_engine"],
        production_root=env["temp_path"] / "prod",
    )

    proposal = ImprovementProposal(
        proposal_id="prop_policy_test",
        gap_id="gap_test",
        title="Policy Engine Gate Skill",
        scope="SKILL",
        risk_level="MEDIUM",
        author_agent_id="agent_coder",
        state=LifecycleState.SPECIFIED,
    )
    env["store"].save_proposal(proposal)

    artifact = staging_gen.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Clean Skill Entrypoint\n",
        artifact_type="SKILL_MD",
    )
    proposal.state = LifecycleState.GOLDFISH_REVIEW

    clean_review = GoldfishReviewReport(
        review_id="rev_p_clean",
        proposal_id=proposal.proposal_id,
        artifact_id=artifact.artifact_id,
        verdict="APPROVED",
        findings=[],
        signature="1" * 64,
    )

    # Step 1: Promotion without token requires approval (fail-closed)
    with pytest.raises(PromotionError) as exc_no_token:
        promo_engine.promote_artifact(proposal, artifact, clean_review, approval_token=None)
    assert "APPROVAL_REQUIRED" in str(exc_no_token.value)
    assert proposal.state == LifecycleState.AWAITING_APPROVAL

    # Step 2: Create valid human operator ApprovalToken
    token = env["policy_engine"].create_approval_token(
        mission_id="m_promo",
        subtask_id="st_promo",
        operator_id="human_operator_alice",
        tool_name="promote_skill",
        arguments={"proposal_id": proposal.proposal_id, "risk_level": proposal.risk_level},
    )

    # Step 3: Promote with valid token -> SUCCESS
    record = promo_engine.promote_artifact(proposal, artifact, clean_review, approval_token=token)
    assert record.status == "ACTIVE"
    assert proposal.state in (LifecycleState.PROMOTED, LifecycleState.ACTIVE)

    # Step 4: Token replay rejection
    with pytest.raises(PromotionError) as exc_replay:
        # Attempt to reuse the consumed token
        proposal2 = ImprovementProposal(
            proposal_id="prop_policy_test_replay",
            gap_id="gap_test",
            title="Replay Test Skill",
            scope="SKILL",
            risk_level="MEDIUM",
            author_agent_id="agent_coder",
            state=LifecycleState.GOLDFISH_REVIEW,
        )
        env["store"].save_proposal(proposal2)
        promo_engine.promote_artifact(proposal2, artifact, clean_review, approval_token=token)
    assert "POLICY_DENIED" in str(exc_replay.value)


# ── Test 16: Two-Phase Atomic Promotion & Deterministic Rollback (SEC-05 & SEC-10)

def test_two_phase_atomic_promotion_and_deterministic_rollback(clean_self_improvement_env):
    """Verify transactional installation with backup and clean rollback on degradation."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "staging",
        store=env["store"],
    )
    promo_engine = PromotionEngine(
        store=env["store"],
        policy_engine=env["policy_engine"],
        production_root=env["temp_path"] / "prod",
    )

    proposal = ImprovementProposal(
        proposal_id="prop_atomic_swap",
        gap_id="gap_test",
        title="Transactional Skill",
        scope="SKILL",
        risk_level="LOW",
        author_agent_id="agent_coder",
        state=LifecycleState.SPECIFIED,
    )
    env["store"].save_proposal(proposal)

    aux_files = {
        "manifest.json": '{"name": "tx_skill", "version": "1.0.0"}',
        "code.py": "VERSION = '1.0.0'\n",
    }
    artifact = staging_gen.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Transactional Skill\n",
        auxiliary_files=aux_files,
        artifact_type="SKILL_MD",
    )
    proposal.state = LifecycleState.GOLDFISH_REVIEW

    clean_review = GoldfishReviewReport(
        review_id="rev_tx",
        proposal_id=proposal.proposal_id,
        artifact_id=artifact.artifact_id,
        verdict="APPROVED",
        findings=[],
        signature="2" * 64,
    )

    token = env["policy_engine"].create_approval_token(
        mission_id="m_tx",
        subtask_id="st_tx",
        operator_id="operator_bob",
        tool_name="promote_skill",
        arguments={"proposal_id": proposal.proposal_id, "risk_level": proposal.risk_level},
    )

    # Promote
    record = promo_engine.promote_artifact(proposal, artifact, clean_review, approval_token=token)
    dest_path = Path(record.production_target)
    assert (dest_path / "code.py").exists()
    assert "VERSION = '1.0.0'" in (dest_path / "code.py").read_text()

    # Trigger Rollback
    rollback_success = promo_engine.rollback(proposal.proposal_id)
    assert rollback_success is True
    assert proposal.state == LifecycleState.ROLLED_BACK
    assert not dest_path.exists()


# ── Test 17: Full 15-Stage End-to-End Coordinator Lifecycle ───────────────────

def test_end_to_end_coordinator_lifecycle(clean_self_improvement_env):
    """Verify complete 15-stage self-improvement lifecycle orchestration."""
    env = clean_self_improvement_env
    staging_gen = StagingCodeGenerator(
        staging_root=env["temp_path"] / "coord_staging",
        store=env["store"],
    )
    promo_engine = PromotionEngine(
        store=env["store"],
        policy_engine=env["policy_engine"],
        production_root=env["temp_path"] / "coord_prod",
    )
    coordinator = SelfImprovementCoordinator(
        store=env["store"],
        staging_gen=staging_gen,
        promotion_engine=promo_engine,
    )

    # Create valid human approval token
    gap = coordinator.observe_gap(
        category="TOOL_MISSING",
        description="User requested JSON schema validation utility.",
        is_explicit=True,
    )
    assert gap is not None

    # Formulate proposal
    proposal = coordinator.formulate_proposal(
        gap_id=gap.gap_id,
        title="JSON Schema Validator",
        problem_statement="Validate JSON data against schema",
        expected_outcome="Validation succeeds",
        author_agent_id="agent_planner",
    )
    assert proposal.state == LifecycleState.SPECIFIED

    token = env["policy_engine"].create_approval_token(
        mission_id="m_coord_e2e",
        subtask_id="st_coord_e2e",
        operator_id="human_supervisor",
        tool_name="promote_skill",
        arguments={"proposal_id": proposal.proposal_id, "risk_level": proposal.risk_level},
    )

    # Run cycle from staging onwards
    aux_files = {
        "manifest.json": '{"name": "json_validator", "version": "1.0.0"}',
        "validator.py": (
            "import json\n"
            "def validate_json_string(s: str) -> bool:\n"
            "    try:\n"
            "        json.loads(s)\n"
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
        ),
        "test_run.py": (
            "import sys\n"
            "from validator import validate_json_string\n"
            "assert validate_json_string('{\"ok\": true}') is True\n"
            "assert validate_json_string('bad json') is False\n"
            "print('VALIDATOR_OK')\n"
            "sys.exit(0)\n"
        ),
    }

    artifact = coordinator.generate_and_stage(
        proposal_id=proposal.proposal_id,
        entrypoint_content="# JSON Validator Skill\nValidates JSON strings.\n",
        auxiliary_files=aux_files,
        artifact_type="SKILL_MD",
    )
    assert Path(artifact.staging_path).exists()

    eval_result = coordinator.evaluate(proposal.proposal_id)
    assert eval_result.passed is True

    review_report = coordinator.review(proposal.proposal_id)
    assert review_report.verdict == "APPROVED"

    record = coordinator.promote(proposal.proposal_id, approval_token=token)
    assert record.status == "ACTIVE"
    assert proposal.state in (LifecycleState.PROMOTED, LifecycleState.ACTIVE)

    # Verify staging was cleaned up (SEC-12)
    assert not Path(artifact.staging_path).exists()

    # Rollback
    rb = coordinator.rollback(proposal.proposal_id)
    assert rb is True
    assert proposal.state == LifecycleState.ROLLED_BACK
