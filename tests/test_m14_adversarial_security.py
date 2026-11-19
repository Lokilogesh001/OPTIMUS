"""tests/test_m14_adversarial_security.py — Deep Adversarial Security & Stress Test Suite.

Mandatory M14 Security Auditing:
1. Workspace isolation & path traversal (relative, UNC, drive-relative, ADS, colon escapes).
2. Sandbox containment truth & credential scrubbing.
3. AST static code sanitization matrix (banned modules, functions, attributes).
4. Package Merkle hash integrity & post-copy tampering detection.
5. PolicyEngine authorization, self-approval prevention, automated approval rejection, token replay.
6. Destination confinement (core, config, .git, memory boundary assertions).
7. Invalid lifecycle state transitions across the 15-state graph.
8. Two-phase installation failure, post-copy corruption rollback, and emergency circuit breaker.
9. Concurrency, thread safety, and burst stress.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.security.policy_engine import (
    ApprovalToken,
    PolicyEngine,
    compute_canonical_hash,
)
from core.self_improvement import (
    CapabilityGap,
    CapabilityGapDetector,
    GoldfishReviewReport,
    ImprovementProposal,
    IsolatedSandboxEvaluator,
    LifecycleState,
    PromotionEngine,
    PromotionError,
    SecuritySanitizationError,
    SecurityViolationError,
    SelfImprovementCoordinator,
    SelfImprovementStore,
    StagingCodeGenerator,
    StagingPathEscapeError,
)


@pytest.fixture
def clean_env():
    """Setup isolated temporary workspace and fresh singletons."""
    temp_dir = tempfile.mkdtemp(prefix="optimus_m14_adv_")
    temp_path = Path(temp_dir)

    SelfImprovementStore.reset_instance()
    PolicyEngine.reset_instance()
    SelfImprovementCoordinator._instance = None

    store = SelfImprovementStore.instance(root_dir=temp_path / "store")
    policy_engine = PolicyEngine.instance(journal_path=temp_path / "policy_journal.txt")

    prod_root = temp_path / "skills" / "installed"
    prod_root.mkdir(parents=True, exist_ok=True)

    staging_root = temp_path / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)

    yield {
        "temp_path": temp_path,
        "store": store,
        "policy_engine": policy_engine,
        "prod_root": prod_root,
        "staging_root": staging_root,
    }

    SelfImprovementStore.reset_instance()
    PolicyEngine.reset_instance()
    SelfImprovementCoordinator._instance = None
    shutil.rmtree(temp_dir, ignore_errors=True)


# ── Test 1: Invalid Lifecycle State Transitions Across 15-State Graph ────────

def test_invalid_lifecycle_state_transitions(clean_env):
    """Enforce strict DAG transition invariants across all 15 states."""
    proposal = ImprovementProposal(
        proposal_id="prop_adv_001",
        gap_id="gap_adv_001",
        title="Test Proposal",
        problem_statement="Testing state transitions",
        risk_level="LOW",
        author_agent_id="test_runner",
    )
    assert proposal.state == LifecycleState.PROPOSED

    # Cannot jump directly from PROPOSED to PROMOTED or ACTIVE
    with pytest.raises(ValueError, match="ILLEGAL_LIFECYCLE_TRANSITION"):
        proposal.transition_to(LifecycleState.PROMOTED)

    with pytest.raises(ValueError, match="ILLEGAL_LIFECYCLE_TRANSITION"):
        proposal.transition_to(LifecycleState.ACTIVE)

    with pytest.raises(ValueError, match="ILLEGAL_LIFECYCLE_TRANSITION"):
        proposal.transition_to(LifecycleState.ROLLED_BACK)

    # Valid step to SPECIFIED
    proposal.transition_to(LifecycleState.SPECIFIED)
    assert proposal.state == LifecycleState.SPECIFIED

    # Cannot skip ISOLATED directly to EVALUATED
    with pytest.raises(ValueError, match="ILLEGAL_LIFECYCLE_TRANSITION"):
        proposal.transition_to(LifecycleState.EVALUATED)

    # Valid sequence: SPECIFIED -> ISOLATED -> TESTING
    proposal.transition_to(LifecycleState.ISOLATED)
    proposal.transition_to(LifecycleState.TESTING)

    # Cannot jump from TESTING to PROMOTED without EVALUATED, GOLDFISH_REVIEW, POLICY_VALIDATION, APPROVAL
    with pytest.raises(ValueError, match="ILLEGAL_LIFECYCLE_TRANSITION"):
        proposal.transition_to(LifecycleState.PROMOTED)


# ── Test 2: Workspace Isolation & Path Traversal Matrix ───────────────────────

def test_workspace_isolation_and_path_traversals(clean_env):
    """Verify that relative .., drive-relative, UNC, and colon paths are blocked."""
    env = clean_env
    generator = StagingCodeGenerator(store=env["store"], staging_root=env["staging_root"])
    proposal = ImprovementProposal(
        proposal_id="prop_adv_traversal",
        gap_id="gap_adv_002",
        title="Traversal Test",
        problem_statement="Test path traversal attempts",
        risk_level="LOW",
        author_agent_id="test_runner",
    )
    proposal.transition_to(LifecycleState.SPECIFIED)

    valid_content = "# Test Skill\nValid declarative content.\n"

    # 1. Standard relative path traversal '..'
    with pytest.raises(StagingPathEscapeError, match="PATH_TRAVERSAL"):
        generator.stage_artifact(
            proposal=proposal,
            entrypoint_content=valid_content,
            auxiliary_files={"../../evil.py": "print('evil')"},
        )

    # 2. Windows drive-relative path 'C:evil.py' or 'D:evil.py'
    with pytest.raises(StagingPathEscapeError, match="PATH_TRAVERSAL"):
        generator.stage_artifact(
            proposal=proposal,
            entrypoint_content=valid_content,
            auxiliary_files={"C:evil.py": "print('evil')"},
        )

    # 3. UNC network share path '\\attacker\share\evil.py'
    with pytest.raises(StagingPathEscapeError, match="PATH_TRAVERSAL"):
        generator.stage_artifact(
            proposal=proposal,
            entrypoint_content=valid_content,
            auxiliary_files={"\\\\attacker\\share\\evil.py": "print('evil')"},
        )

    # 4. NTFS Alternate Data Stream or colon escape 'file.py:stream'
    with pytest.raises(StagingPathEscapeError, match="PATH_TRAVERSAL"):
        generator.stage_artifact(
            proposal=proposal,
            entrypoint_content=valid_content,
            auxiliary_files={"file.py:stream": "print('evil')"},
        )

    # 5. Unix absolute path '/etc/shadow'
    with pytest.raises(StagingPathEscapeError, match="PATH_TRAVERSAL"):
        generator.stage_artifact(
            proposal=proposal,
            entrypoint_content=valid_content,
            auxiliary_files={"/etc/shadow": "root:x:0:0::/root:/bin/bash"},
        )


# ── Test 3: AST Static Code Sanitization Matrix ───────────────────────────────

@pytest.mark.parametrize(
    "malicious_code,expected_error",
    [
        ("import socket\ns = socket.socket()", "BANNED_IMPORT"),
        ("import subprocess\nsubprocess.run(['dir'])", "BANNED_IMPORT"),
        ("import ctypes\nctypes.windll.kernel32", "BANNED_IMPORT"),
        ("import winreg\nkey = winreg.OpenKey()", "BANNED_IMPORT"),
        ("from http import client\nclient.HTTPConnection('localhost')", "BANNED_IMPORT"),
        ("eval('1 + 1')", "BANNED_CALL"),
        ("exec('a = 1')", "BANNED_CALL"),
        ("__import__('os').system('dir')", "BANNED_CALL"),
        ("compile('print(1)', '', 'exec')", "BANNED_CALL"),
        ("class X: pass\nX.__subclasses__()", "BANNED_ATTRIBUTE"),
        ("def f(): pass\nf.__globals__", "BANNED_ATTRIBUTE"),
        ("def f(): pass\nf.__code__", "BANNED_ATTRIBUTE"),
    ],
)
def test_ast_sanitization_matrix(clean_env, malicious_code, expected_error):
    """Verify that prohibited imports, functions, and attributes are rejected."""
    generator = StagingCodeGenerator(store=clean_env["store"])
    with pytest.raises(SecuritySanitizationError, match=expected_error):
        generator.sanitize_python_code(malicious_code)


# ── Test 4: Sandbox Environment Isolation & Secret Scrubbing ─────────────────

def test_sandbox_environment_isolation_and_secret_scrubbing(clean_env):
    """Verify that credentials, tokens, secrets, and proxies are rigorously scrubbed."""
    evaluator = IsolatedSandboxEvaluator(store=clean_env["store"])
    staging_dir = clean_env["staging_root"] / "prop_dummy"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Inject dirty environment secrets
    os.environ["OPENAI_API_KEY"] = "sk-test-secret-12345"
    os.environ["ANTHROPIC_API_KEY"] = "ant-test-secret-67890"
    os.environ["OPTIMUS_VAULT_KEY"] = "super-secret-vault-key"
    os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost:5432/db"
    os.environ["GITHUB_TOKEN"] = "ghp_superSecretTokenValue"

    try:
        isolated_env = evaluator.build_isolated_environment(staging_dir)

        # 1. Check all sensitive variables are purged
        for key in isolated_env:
            upper_key = key.upper()
            assert "OPENAI_API_KEY" not in upper_key
            assert "ANTHROPIC_API_KEY" not in upper_key
            assert "OPTIMUS_VAULT_KEY" not in upper_key
            assert "DATABASE_URL" not in upper_key
            assert "GITHUB_TOKEN" not in upper_key

        # 2. Check offline dead-end proxy settings
        assert isolated_env.get("HTTP_PROXY") == "http://127.0.0.1:0"
        assert isolated_env.get("HTTPS_PROXY") == "https://127.0.0.1:0"
        assert isolated_env.get("ALL_PROXY") == "socks5://127.0.0.1:0"
        assert isolated_env.get("PYTHONPATH") == str(staging_dir)

    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPTIMUS_VAULT_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("GITHUB_TOKEN", None)


# ── Test 5: Merkle Package Hash Tampering Detection ──────────────────────────

def test_merkle_package_tampering_detection(clean_env):
    """Ensure that modifying any file in staging invalidates Merkle hash and halts promotion."""
    env = clean_env
    generator = StagingCodeGenerator(store=env["store"], staging_root=env["staging_root"])
    proposal = ImprovementProposal(
        proposal_id="prop_adv_merkle",
        gap_id="gap_adv_003",
        title="Merkle Integrity Test",
        problem_statement="Verify tampering is caught",
        risk_level="LOW",
        author_agent_id="test_runner",
    )
    proposal.transition_to(LifecycleState.SPECIFIED)

    artifact = generator.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Safe Skill\nDocumentation here.\n",
        auxiliary_files={"helper.py": "#!/usr/bin/env python\ndef helper(): return 42\n"},
    )

    staging_dir = Path(artifact.staging_path)

    # 1. Unmodified staging directory verifies cleanly
    PromotionEngine.verify_package_integrity(staging_dir, artifact.package_integrity_hash)

    # 2. Tampering: append an evil line to helper.py
    helper_path = staging_dir / "helper.py"
    helper_path.write_text(helper_path.read_text(encoding="utf-8") + "\n# Malicious comment\n", encoding="utf-8")

    with pytest.raises(SecurityViolationError, match="PACKAGE_TAMPERING_DETECTED"):
        PromotionEngine.verify_package_integrity(staging_dir, artifact.package_integrity_hash)


# ── Test 6: PolicyEngine Governance, Self-Approval, and Token Replay ─────────

def test_policy_governance_and_self_approval_prevention(clean_env):
    """Verify generator cannot self-approve, automated agents cannot approve, and replayed tokens fail."""
    env = clean_env
    store = env["store"]
    policy_engine = env["policy_engine"]
    promotion_engine = PromotionEngine(store=store, policy_engine=policy_engine, production_root=env["prod_root"])
    generator = StagingCodeGenerator(store=store, staging_root=env["staging_root"])

    author_id = "agent_generator_m14"
    proposal = ImprovementProposal(
        proposal_id="prop_adv_auth",
        gap_id="gap_adv_004",
        title="Auth Test Skill",
        problem_statement="Testing approval invariants",
        risk_level="HIGH",
        author_agent_id=author_id,
    )
    store.save_proposal(proposal)
    proposal.transition_to(LifecycleState.SPECIFIED)

    artifact = generator.stage_artifact(
        proposal=proposal,
        entrypoint_content="# High Risk Skill\nDescription.\n",
    )
    proposal.transition_to(LifecycleState.TESTING)
    proposal.transition_to(LifecycleState.EVALUATED)
    proposal.transition_to(LifecycleState.GOLDFISH_REVIEW)

    # Mock an approved review
    review = GoldfishReviewReport(
        review_id="rev_adv_001",
        proposal_id=proposal.proposal_id,
        artifact_id=artifact.artifact_id,
        verdict="APPROVED",
        findings=[],
        reviewed_at=time.time(),
    )

    # Case 1: Generator self-approval attempt (operator_id == author_agent_id)
    self_token = ApprovalToken(
        token_id="tok_self_001",
        mission_id="m_self",
        subtask_id="st_self",
        operator_id=author_id,  # Same as author!
        nonce="nonce_self",
        expires_at=time.time() + 3600,
        canonical_hash=compute_canonical_hash("promote_skill", {"proposal_id": proposal.proposal_id, "risk_level": "HIGH"}),
    )
    with pytest.raises(SecurityViolationError, match="SELF_APPROVAL_VIOLATION"):
        promotion_engine.promote_artifact(proposal, artifact, review, approval_token=self_token)

    # Reset proposal state for next attempt
    proposal.state = LifecycleState.GOLDFISH_REVIEW

    # Case 2: Automated identity approval attempt (operator_id="ai_system_agent")
    auto_token = ApprovalToken(
        token_id="tok_auto_002",
        mission_id="m_auto",
        subtask_id="st_auto",
        operator_id="ai_system_agent",  # Automated identity!
        nonce="nonce_auto",
        expires_at=time.time() + 3600,
        canonical_hash=compute_canonical_hash("promote_skill", {"proposal_id": proposal.proposal_id, "risk_level": "HIGH"}),
    )
    with pytest.raises(SecurityViolationError, match="AUTOMATED_APPROVAL_VIOLATION"):
        promotion_engine.promote_artifact(proposal, artifact, review, approval_token=auto_token)

    # Reset proposal state
    proposal.state = LifecycleState.GOLDFISH_REVIEW

    # Case 3: Legitimate human operator approval token
    human_token = policy_engine.create_approval_token(
        mission_id="m_promo",
        subtask_id="st_promo",
        tool_name="promote_skill",
        arguments={"proposal_id": proposal.proposal_id, "risk_level": "HIGH"},
        operator_id="human_security_lead",
    )

    record = promotion_engine.promote_artifact(proposal, artifact, review, approval_token=human_token)
    assert record.status == "ACTIVE"
    assert proposal.state == LifecycleState.ACTIVE

    # Case 4: Token Replay Attack (attempt to reuse human_token on another proposal)
    replay_proposal = ImprovementProposal(
        proposal_id="prop_adv_replay",
        gap_id="gap_adv_005",
        title="Replay Attack Proposal",
        problem_statement="Attempting to reuse token",
        risk_level="HIGH",
        author_agent_id="test_runner",
    )
    store.save_proposal(replay_proposal)
    replay_proposal.transition_to(LifecycleState.SPECIFIED)
    replay_artifact = generator.stage_artifact(
        proposal=replay_proposal,
        entrypoint_content="# Replay Candidate\n",
    )
    replay_proposal.transition_to(LifecycleState.TESTING)
    replay_proposal.transition_to(LifecycleState.EVALUATED)
    replay_proposal.transition_to(LifecycleState.GOLDFISH_REVIEW)

    replay_review = GoldfishReviewReport(
        review_id="rev_adv_002",
        proposal_id=replay_proposal.proposal_id,
        artifact_id=replay_artifact.artifact_id,
        verdict="APPROVED",
        findings=[],
        reviewed_at=time.time(),
    )

    with pytest.raises(PromotionError, match="POLICY_DENIED"):
        promotion_engine.promote_artifact(replay_proposal, replay_artifact, replay_review, approval_token=human_token)


# ── Test 7: Destination Path Confinement (Hard Path Assertions) ───────────────

def test_destination_path_confinement_assertions(clean_env):
    """Verify that attempts to install or target core/, config/, .git/, memory/ are blocked."""
    promotion_engine = PromotionEngine(
        store=clean_env["store"],
        policy_engine=clean_env["policy_engine"],
        production_root=clean_env["prod_root"],
    )

    # 1. Breach protected core/ root
    with pytest.raises(SecurityViolationError, match="IMMUTABLE_CORE_VIOLATION"):
        promotion_engine.validate_destination_safety(Path("core/evil_module.py"))

    # 2. Breach config/ root
    with pytest.raises(SecurityViolationError, match="IMMUTABLE_CORE_VIOLATION"):
        promotion_engine.validate_destination_safety(Path("config/evil_settings.yaml"))

    # 3. Breach .git/ root
    with pytest.raises(SecurityViolationError, match="IMMUTABLE_CORE_VIOLATION"):
        promotion_engine.validate_destination_safety(Path(".git/hooks/pre-commit"))

    # 4. Breach memory/ root
    with pytest.raises(SecurityViolationError, match="IMMUTABLE_CORE_VIOLATION"):
        promotion_engine.validate_destination_safety(Path("memory/unauthorized.json"))

    # 5. Destination outside production root
    external_path = clean_env["temp_path"] / "unauthorized_external_dir" / "skill"
    with pytest.raises(SecurityViolationError, match="BOUNDARY_VIOLATION"):
        promotion_engine.validate_destination_safety(external_path)


# ── Test 8: Post-Copy Integrity Failure & Transactional Rollback ──────────────

def test_post_copy_corruption_triggers_atomic_rollback(clean_env):
    """Verify that destination corruption during promotion triggers Phase 3 rollback and FAILED state."""
    env = clean_env
    store = env["store"]
    policy_engine = env["policy_engine"]
    promotion_engine = PromotionEngine(store=store, policy_engine=policy_engine, production_root=env["prod_root"])
    generator = StagingCodeGenerator(store=store, staging_root=env["staging_root"])

    proposal = ImprovementProposal(
        proposal_id="prop_adv_copy_fail",
        gap_id="gap_adv_006",
        title="Copy Integrity Test",
        problem_statement="Test rollback on destination corruption",
        risk_level="LOW",
        author_agent_id="test_runner",
    )
    store.save_proposal(proposal)
    proposal.transition_to(LifecycleState.SPECIFIED)

    artifact = generator.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Post Copy Check\nContent.\n",
    )
    proposal.transition_to(LifecycleState.TESTING)
    proposal.transition_to(LifecycleState.EVALUATED)
    proposal.transition_to(LifecycleState.GOLDFISH_REVIEW)

    review = GoldfishReviewReport(
        review_id="rev_adv_002",
        proposal_id=proposal.proposal_id,
        artifact_id=artifact.artifact_id,
        verdict="APPROVED",
        findings=[],
        reviewed_at=time.time(),
    )

    # Monkeypatch _copy_tree_with_retry to simulate partial copy / disk write corruption
    def corrupting_copy(src: Path, dst: Path, max_retries: int = 10):
        dst.mkdir(parents=True, exist_ok=True)
        # Corrupt the destination file
        (dst / "SKILL.md").write_text("# CORRUPTED DATA", encoding="utf-8")

    promotion_engine._copy_tree_with_retry = corrupting_copy

    valid_token = policy_engine.create_approval_token(
        mission_id="m_promo",
        subtask_id="st_promo",
        tool_name="promote_skill",
        arguments={"proposal_id": proposal.proposal_id, "risk_level": proposal.risk_level},
        operator_id="human_security_lead",
    )

    with pytest.raises(PromotionError, match="TRANSACTION_FAILED"):
        promotion_engine.promote_artifact(proposal, artifact, review, approval_token=valid_token)

    assert proposal.state == LifecycleState.FAILED


# ── Test 9: SEC-10 Emergency Rollback Circuit Breaker ─────────────────────────

def test_emergency_rollback_circuit_breaker(clean_env):
    """Verify SEC-10 emergency circuit breaker on unhandled exception during rollback."""
    env = clean_env
    store = env["store"]
    policy_engine = env["policy_engine"]
    mock_registry = MagicMock()
    promotion_engine = PromotionEngine(
        store=store,
        policy_engine=policy_engine,
        skill_registry=mock_registry,
        production_root=env["prod_root"],
    )
    generator = StagingCodeGenerator(store=store, staging_root=env["staging_root"])

    proposal = ImprovementProposal(
        proposal_id="prop_adv_rollback_cb",
        gap_id="gap_adv_007",
        title="Circuit Breaker Test",
        problem_statement="Test rollback failure handling",
        risk_level="LOW",
        author_agent_id="test_runner",
    )
    store.save_proposal(proposal)
    proposal.transition_to(LifecycleState.SPECIFIED)

    artifact = generator.stage_artifact(
        proposal=proposal,
        entrypoint_content="# Skill to rollback\n",
    )
    proposal.transition_to(LifecycleState.TESTING)
    proposal.transition_to(LifecycleState.EVALUATED)
    proposal.transition_to(LifecycleState.GOLDFISH_REVIEW)

    review = GoldfishReviewReport(
        review_id="rev_adv_003",
        proposal_id=proposal.proposal_id,
        artifact_id=artifact.artifact_id,
        verdict="APPROVED",
        findings=[],
        reviewed_at=time.time(),
    )

    valid_token = policy_engine.create_approval_token(
        mission_id="m_promo",
        subtask_id="st_promo",
        tool_name="promote_skill",
        arguments={"proposal_id": proposal.proposal_id, "risk_level": proposal.risk_level},
        operator_id="human_security_lead",
    )
    promotion_engine.promote_artifact(proposal, artifact, review, approval_token=valid_token)
    assert proposal.state == LifecycleState.ACTIVE

    # Induce an unhandled OSError during rollback unregister
    mock_registry.unregister = MagicMock(side_effect=OSError("Simulated fatal disk failure"))

    # Act: Attempt rollback
    success = promotion_engine.rollback(proposal.proposal_id)

    # Assert: Circuit breaker catches exception, returns False, sets ROLLBACK_FAILED, disables in registry
    assert success is False
    assert proposal.state == LifecycleState.ROLLBACK_FAILED
    mock_registry.set_enabled.assert_called_with("skill_adv_rollback_cb", False)


# ── Test 10: Multi-Threaded Concurrency & Burst Stress ────────────────────────

def test_multi_threaded_concurrency_and_burst_stress(clean_env):
    """Stress test concurrent proposal submissions, persistence locking, and telemetry."""
    store = clean_env["store"]
    detector = CapabilityGapDetector(store=store)

    num_threads = 8
    events_per_thread = 5
    exceptions: list[Exception] = []

    def worker_thread(thread_idx: int):
        try:
            for i in range(events_per_thread):
                detector.record_observation(
                    category="TOOL_MISSING",
                    description=f"Concurrent missing tool {thread_idx}_{i}",
                    evidence={"thread": thread_idx, "iter": i},
                    confidence=0.75,
                )
        except Exception as e:  # noqa: BLE001  # Catch all thread exceptions to assert empty later
            exceptions.append(e)

    threads = [threading.Thread(target=worker_thread, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not exceptions, f"Concurrency exceptions encountered: {exceptions}"
    gaps = store.list_gaps()
    assert len(gaps) > 0
    # Store must remain consistent and readable
    assert all(g.gap_id for g in gaps)


# ── Test 11: Remediation SEC-M14-01: Prohibited Execution Primitives & Qualified Calls ──

@pytest.mark.parametrize(
    "code_snippet,expected_error",
    [
        ("import os\nos.system('dir')", "BANNED_CALL"),
        ("import shutil\nshutil.rmtree('foo')", "BANNED_CALL"),
        ("import posix\nposix.system('dir')", "BANNED_IMPORT"),
        ("os.system('dir')", "BANNED_CALL"),
        ("os.popen('whoami')", "BANNED_CALL"),
        ("shutil.rmtree('C:/temp')", "BANNED_CALL"),
    ],
)
def test_ast_sanitization_attribute_calls_and_os_primitives(clean_env, code_snippet, expected_error):
    """SEC-M14-01: Verify qualified calls and OS execution primitives are rejected."""
    generator = StagingCodeGenerator(store=clean_env["store"])
    with pytest.raises(SecuritySanitizationError, match=expected_error):
        generator.sanitize_python_code(code_snippet)


# ── Test 12: Remediation SEC-M14-02: TOOL_CODE Entrypoint Import Verification ─────────

def test_sandbox_evaluator_tool_code_entrypoint_execution_check(clean_env):
    """SEC-M14-02: Verify that broken TOOL_CODE entrypoint fails Axis 2 even without test_run.py."""
    evaluator = IsolatedSandboxEvaluator(store=clean_env["store"])
    staging_dir = clean_env["staging_root"] / "prop_tool_code_check"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Broken entrypoint that crashes on import
    entrypoint_file = staging_dir / "entrypoint.py"
    entrypoint_file.write_text("raise RuntimeError('Crashed on module import!')\n", encoding="utf-8")

    from core.self_improvement.types import GeneratedArtifact
    artifact = GeneratedArtifact(
        artifact_id="art_tool_test",
        proposal_id="prop_tool_code_check",
        artifact_type="TOOL_CODE",
        staging_path=str(staging_dir),
        content_sha256="dummy",
        files_checksums={"entrypoint.py": "dummy"},
        package_integrity_hash="dummy",
        generated_code="raise RuntimeError('Crashed on module import!')\n",
        author_model="test",
        generated_at=time.time(),
    )

    proposal = ImprovementProposal(
        proposal_id="prop_tool_code_check",
        gap_id="gap_tool_001",
        title="Tool Code Test",
        problem_statement="Test entrypoint execution check",
        risk_level="MEDIUM",
        author_agent_id="test_runner",
        state=LifecycleState.ISOLATED,
    )

    result = evaluator.evaluate_artifact(proposal, artifact, timeout_seconds=5.0)
    assert result.passed is False
    assert result.test_suite_results["functional_behavior"]["status"] == "FAILED"
    assert "Crashed on module import" in result.logs or "Code 1" in result.logs


# ── Test 13: Remediation SEC-M14-04: Proposal Scope Whitelist Validation ──────────────

def test_proposal_scope_whitelist_validation(clean_env):
    """SEC-M14-04: Verify that unwhitelisted scope values are rejected."""
    store = clean_env["store"]
    from core.self_improvement.proposal_generator import ImprovementProposalGenerator
    generator = ImprovementProposalGenerator(store=store)

    gap = CapabilityGap(
        gap_id="gap_scope_test",
        category="TOOL_MISSING",
        description="Missing tool",
    )

    # Valid scopes
    p1 = generator.create_proposal(gap, "Title 1", "Problem", "Outcome", scope="SKILL")
    assert p1.scope == "SKILL"
    p2 = generator.create_proposal(gap, "Title 2", "Problem", "Outcome", scope="TOOL_WRAPPER")
    assert p2.scope == "TOOL_WRAPPER"
    p3 = generator.create_proposal(gap, "Title 3", "Problem", "Outcome", scope="WORKFLOW_TEMPLATE")
    assert p3.scope == "WORKFLOW_TEMPLATE"

    # Invalid scope rejected
    with pytest.raises(ValueError, match="INVALID_SCOPE"):
        generator.create_proposal(gap, "Invalid Scope", "Problem", "Outcome", scope="ARBITRARY_CORE_EXTENSION")
