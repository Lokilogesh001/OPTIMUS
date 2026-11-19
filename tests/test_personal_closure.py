"""tests/test_personal_closure.py — Comprehensive M12 Production-Readiness Closure Test Suite.

Validates all 4 Production Pillars and Stage 2 Goldfish findings:
- Pillar A: AES-256-GCM authenticated encryption at rest, AAD isolation, tamper detection, migration.
- Pillar B: Multiprocess-safe PersonalStore, category OS locking, `mutate_*` pattern, no lost updates.
- Pillar C: Real Provider Integration, PKCE, CSRF protection, honest revocation, token refresh locking.
- Pillar D: Synchronization recovery, transactional cursor ordering, commitment deduplication, Retry-After parsing.
"""

from __future__ import annotations

import base64
import multiprocessing
import os
import secrets
import time
from unittest.mock import MagicMock

import pytest

from core.personal_intelligence.crypto import (
    PersonalStoreCrypto,
    SecurityIntegrityError,
)
from core.personal_intelligence.engine import (
    PersonalIntelligenceEngine,
    parse_retry_after,
)
from core.personal_intelligence.providers.base import (
    generate_pkce_pair,
    generate_state_token,
    oauth_refresh_lock,
)
from core.personal_intelligence.providers.gmail import GmailAdapter
from core.personal_intelligence.storage import PersonalStore
from core.personal_intelligence.types import (
    Account,
    AuthStatus,
    Message,
    PersonalTask,
    ProviderType,
    TaskStatus,
)
from core.security.policy_engine import (
    OperationRequest,
    PolicyEngine,
)

# ── Multiprocessing Worker Top-Level Functions (Required for Windows Spawn) ──

def _worker_write_tasks(store_dir_str: str, worker_id: int, num_tasks: int) -> None:
    """Worker process that mutates tasks in the shared store."""
    store = PersonalStore(store_dir_str)
    for i in range(num_tasks):
        tid = f"task_p{worker_id}_{i}"
        task = PersonalTask(
            task_id=tid,
            title=f"Worker {worker_id} Task {i}",
            source_provider=ProviderType.GMAIL,
            status=TaskStatus.PENDING,
        )
        store.save_task(task)


def _worker_write_accounts_and_cursors(store_dir_str: str, worker_id: int, num_items: int) -> None:
    """Worker process that mutates accounts and stream cursors concurrently."""
    store = PersonalStore(store_dir_str)
    for i in range(num_items):
        aid = f"acc_p{worker_id}_{i}"
        acc = Account(
            account_id=aid,
            provider=ProviderType.GMAIL,
            email_or_id=f"user_{worker_id}_{i}@example.com",
            auth_status=AuthStatus.CONNECTED,
        )
        store.save_account(acc)
        store.update_stream_cursor(aid, "mail", f"cursor_{worker_id}_{i}")


# ── Pillar A: Authenticated Encryption at Rest & Tamper Detection ─────────────

def test_crypto_encryption_decryption_roundtrip():
    """Verify AES-256-GCM roundtrip encryption/decryption preserves Python data with AAD."""
    key = secrets.token_bytes(32)
    crypto = PersonalStoreCrypto(master_key=key)

    data = {
        "account_id": "acc_test_1",
        "email": "confidential@example.com",
        "items": [1, 2, "three"],
        "metadata": {"nested": True},
    }

    envelope = crypto.encrypt_data(
        plaintext_dict=data,
        entity_type="account",
        record_id="acc_test_1",
        account_id="acc_test_1",
    )

    assert crypto.is_encrypted_envelope(envelope)
    assert envelope["alg"] == "AES-256-GCM"
    assert envelope["enc"] is True
    assert "ct" in envelope
    assert "nonce" in envelope
    assert "aad" in envelope

    decrypted = crypto.decrypt_data(
        envelope=envelope,
        expected_entity_type="account",
        expected_record_id="acc_test_1",
        expected_account_id="acc_test_1",
    )

    assert decrypted == data


def test_crypto_tampering_detection_raises_integrity_error():
    """Verify bit manipulation of ciphertext or auth tag raises SecurityIntegrityError."""
    crypto = PersonalStoreCrypto(master_key=secrets.token_bytes(32))
    payload = {"secret": "private personal notes"}

    envelope = crypto.encrypt_data(payload, "task", "t100")
    ct_bytes = bytearray(base64.b64decode(envelope["ct"]))

    # Flip bits in the ciphertext / auth tag
    ct_bytes[5] ^= 0xFF
    envelope["ct"] = base64.b64encode(bytes(ct_bytes)).decode("ascii")

    with pytest.raises(SecurityIntegrityError, match="Ciphertext authentication failed"):
        crypto.decrypt_data(envelope, "task", "t100")


def test_crypto_aad_mismatch_and_transplantation_rejected():
    """Verify cross-tenant / cross-entity transplantation is blocked by AAD verification."""
    crypto = PersonalStoreCrypto(master_key=secrets.token_bytes(32))
    payload = {"title": "Task for Account Alpha"}

    # Encrypted for account_id='alpha'
    envelope = crypto.encrypt_data(
        payload, entity_type="task", record_id="t1", account_id="alpha"
    )

    # 1. Attempting to decrypt expecting account_id='beta' must fail
    with pytest.raises(SecurityIntegrityError, match="AAD account mismatch"):
        crypto.decrypt_data(
            envelope, expected_entity_type="task", expected_record_id="t1", expected_account_id="beta"
        )

    # 2. Attempting to decrypt expecting different entity type must fail
    with pytest.raises(SecurityIntegrityError, match="AAD entity type mismatch"):
        crypto.decrypt_data(
            envelope, expected_entity_type="event", expected_record_id="t1", expected_account_id="alpha"
        )

    # 3. Attempting to decrypt expecting different record_id must fail
    with pytest.raises(SecurityIntegrityError, match="AAD record id mismatch"):
        crypto.decrypt_data(
            envelope, expected_entity_type="task", expected_record_id="t99", expected_account_id="alpha"
        )


def test_crypto_wrong_key_rejected():
    """Verify decryption with a different master key raises SecurityIntegrityError."""
    crypto_a = PersonalStoreCrypto(master_key=secrets.token_bytes(32))
    crypto_b = PersonalStoreCrypto(master_key=secrets.token_bytes(32))

    envelope = crypto_a.encrypt_data({"text": "confidential"}, "task", "t1")

    with pytest.raises(SecurityIntegrityError, match="Ciphertext authentication failed"):
        crypto_b.decrypt_data(envelope, "task", "t1")


def test_crypto_nonce_uniqueness_across_rapid_calls():
    """Verify structured nonces are strictly unique across high-frequency calls."""
    crypto = PersonalStoreCrypto(master_key=secrets.token_bytes(32))
    nonces = set()
    count = 1000

    for _ in range(count):
        nonce = crypto._generate_nonce()
        assert len(nonce) == 12  # 96 bits
        nonces.add(nonce)

    assert len(nonces) == count


def test_storage_non_destructive_migration_and_enforcement(tmp_path):
    """Verify transparent migration of legacy plaintext files to authenticated ciphertexts."""
    crypto = PersonalStoreCrypto(master_key=secrets.token_bytes(32))
    store_dir = tmp_path / "personal_store"
    store_dir.mkdir(parents=True)

    # 1. Seed legacy plaintext files
    (store_dir / "tasks.json").write_text(
        '[{"task_id": "legacy_t1", "title": "Legacy Plaintext Task", "status": "PENDING"}]',
        encoding="utf-8",
    )
    (store_dir / "accounts.json").write_text(
        '[{"account_id": "legacy_a1", "provider": "GMAIL", "email_or_id": "legacy@example.com"}]',
        encoding="utf-8",
    )

    # Initialize store (in permissive un-enforced mode)
    store = PersonalStore(store_dir, crypto=crypto)
    assert not store.is_encryption_enforced()
    assert len(store.list_tasks()) == 1
    assert store.get_task("legacy_t1").title == "Legacy Plaintext Task"
    assert len(store.list_accounts()) == 1

    # 2. Execute migration
    counts = store.migrate_plaintext_to_encrypted()
    assert counts["tasks"] == 1
    assert counts["accounts"] == 1
    assert store.is_encryption_enforced()

    # 3. Verify disk content is now encrypted envelopes
    raw_tasks = (store_dir / "tasks.json").read_text(encoding="utf-8")
    assert "Legacy Plaintext Task" not in raw_tasks  # Plaintext gone from disk
    assert '"enc": true' in raw_tasks
    assert '"alg": "AES-256-GCM"' in raw_tasks

    # 4. Re-instantiate store under encryption enforcement
    store2 = PersonalStore(store_dir, crypto=crypto)
    assert store2.is_encryption_enforced()
    assert len(store2.list_tasks()) == 1
    assert store2.get_task("legacy_t1").title == "Legacy Plaintext Task"

    # 5. Inject a rogue plaintext file while enforced — must raise SecurityIntegrityError
    (store_dir / "tasks.json").write_text(
        '[{"task_id": "injected", "title": "Injected Plaintext"}]', encoding="utf-8"
    )
    with pytest.raises(SecurityIntegrityError, match="Plaintext data found"):
        PersonalStore(store_dir, crypto=crypto)


# ── Pillar B: Multiprocess Safety & Read-Modify-Write Locking ─────────────────

def test_storage_multiprocess_concurrent_task_writes(tmp_path):
    """Verify independent OS processes writing tasks concurrently lose no updates."""
    store_dir = tmp_path / "mp_store"
    store_dir.mkdir(parents=True)
    master_key = secrets.token_bytes(32)
    os.environ["OPTIMUS_PERSONAL_MASTER_KEY"] = base64.b64encode(master_key).decode("ascii")

    try:
        crypto = PersonalStoreCrypto(master_key=master_key)
        _ = PersonalStore(store_dir, crypto=crypto)

        num_workers = 3
        tasks_per_worker = 10

        processes = []
        for w_id in range(num_workers):
            p = multiprocessing.Process(
                target=_worker_write_tasks,
                args=(str(store_dir), w_id, tasks_per_worker),
            )
            processes.append(p)
            p.start()

        for p in processes:
            p.join(timeout=30.0)
            assert p.exitcode == 0

        # Re-open store and check total count
        verified_store = PersonalStore(store_dir, crypto=crypto)
        all_tasks = verified_store.list_tasks()
        assert len(all_tasks) == num_workers * tasks_per_worker

        for w_id in range(num_workers):
            for i in range(tasks_per_worker):
                tid = f"task_p{w_id}_{i}"
                assert verified_store.get_task(tid) is not None
    finally:
        os.environ.pop("OPTIMUS_PERSONAL_MASTER_KEY", None)


def test_storage_multiprocess_concurrent_accounts_and_cursors(tmp_path):
    """Verify concurrent processes mutating accounts and sync cursors do not corrupt files."""
    store_dir = tmp_path / "mp_acc_store"
    store_dir.mkdir(parents=True)
    master_key = secrets.token_bytes(32)
    os.environ["OPTIMUS_PERSONAL_MASTER_KEY"] = base64.b64encode(master_key).decode("ascii")

    try:
        crypto = PersonalStoreCrypto(master_key=master_key)
        _ = PersonalStore(store_dir, crypto=crypto)

        num_workers = 2
        items_per_worker = 8

        processes = []
        for w_id in range(num_workers):
            p = multiprocessing.Process(
                target=_worker_write_accounts_and_cursors,
                args=(str(store_dir), w_id, items_per_worker),
            )
            processes.append(p)
            p.start()

        for p in processes:
            p.join(timeout=30.0)
            assert p.exitcode == 0

        verified_store = PersonalStore(store_dir, crypto=crypto)
        all_accs = verified_store.list_accounts()
        assert len(all_accs) == num_workers * items_per_worker

        for w_id in range(num_workers):
            for i in range(items_per_worker):
                aid = f"acc_p{w_id}_{i}"
                assert verified_store.get_account(aid) is not None
                assert verified_store.get_sync_cursor(aid, "mail") == f"cursor_{w_id}_{i}"
    finally:
        os.environ.pop("OPTIMUS_PERSONAL_MASTER_KEY", None)


# ── Pillar C: Provider Integration & OAuth Security ───────────────────────────

def test_oauth_pkce_and_csrf_state_generation():
    """Verify RFC 7636 PKCE pair and CSRF state generation."""
    verifier, challenge = generate_pkce_pair()
    assert len(verifier) >= 43
    assert len(challenge) >= 43
    assert verifier != challenge
    assert "=" not in challenge  # Unpadded base64url

    state = generate_state_token()
    assert len(state) >= 32


def test_oauth_token_refresh_interprocess_lock(tmp_path):
    """Verify oauth_refresh_lock acquires and releases lock safely."""
    locks_dir = tmp_path / "locks"
    account_id = "acc_refresh_test"

    with oauth_refresh_lock(account_id, locks_dir=locks_dir, timeout=2.0):
        # Under lock
        assert (locks_dir / f"oauth_{account_id}.lock").exists()


def test_oauth_honest_revocation_semantics():
    """Verify honest revocation distinguishing remote verified vs local only."""
    acc = Account(
        account_id="acc_rev_1",
        provider=ProviderType.GMAIL,
        email_or_id="user@example.com",
        auth_status=AuthStatus.CONNECTED,
    )

    # 1. Successful remote revocation
    mock_client = MagicMock()
    mock_client.post.return_value = MagicMock(status_code=200)

    adapter = GmailAdapter(acc, http_client=mock_client)
    adapter.authenticate({"access_token": "valid_tok", "refresh_token": "valid_ref"})
    assert acc.auth_status == AuthStatus.CONNECTED

    ok = adapter.revoke()
    assert ok is True
    assert acc.auth_status == AuthStatus.REVOKED_REMOTE_VERIFIED
    assert not adapter._get_access_token()

    # 2. Failed remote revocation (network error)
    acc2 = Account(
        account_id="acc_rev_2",
        provider=ProviderType.GMAIL,
        email_or_id="user2@example.com",
        auth_status=AuthStatus.CONNECTED,
    )
    mock_client_err = MagicMock()
    mock_client_err.post.side_effect = ConnectionError("Network unreachable")

    adapter2 = GmailAdapter(acc2, http_client=mock_client_err)
    adapter2.authenticate({"access_token": "tok2", "refresh_token": "ref2"})

    ok2 = adapter2.revoke()
    assert ok2 is True
    assert acc2.auth_status == AuthStatus.DISCONNECTED_LOCAL_ONLY
    assert not adapter2._get_access_token()


# ── Pillar D: Synchronization Recovery & Reliability ──────────────────────────

def test_commitment_deduplication(tmp_path):
    """Verify user commitment deduplication across repeated messages."""
    store_dir = tmp_path / "dedup_store"
    store = PersonalStore(store_dir)
    engine = PersonalIntelligenceEngine(store=store)

    msg1 = Message(
        message_id="msg_com_1",
        provider=ProviderType.GMAIL,
        account_id="acc_1",
        conversation_id="conv_com_1",
        sender="bob@example.com",
        subject="Report Status",
        body_snippet="I will send the complete presentation by Friday.",
        timestamp=time.time(),
    )
    msg2 = Message(
        message_id="msg_com_2",
        provider=ProviderType.GMAIL,
        account_id="acc_1",
        conversation_id="conv_com_1",
        sender="bob@example.com",
        subject="Re: Report Status",
        body_snippet="Reminder: I will send the complete presentation by Friday.",
        timestamp=time.time() + 60,
    )

    _, comms1 = engine.extract_tasks_and_commitments_from_message(msg1)
    assert len(comms1) == 1
    assert comms1[0].dedup_key != ""

    # Processing identical promise from second message should not add a duplicate commitment
    _, comms2 = engine.extract_tasks_and_commitments_from_message(msg2)
    assert len(comms2) == 0

    all_comms = store.list_commitments()
    assert len(all_comms) == 1


def test_sync_transactional_ordering_and_cursor_isolation(tmp_path):
    """Verify stream cursor is NOT advanced if message extraction/saving fails."""
    store_dir = tmp_path / "tx_store"
    store = PersonalStore(store_dir)
    engine = PersonalIntelligenceEngine(store=store)

    acc = Account(
        account_id="acc_tx_test",
        provider=ProviderType.GMAIL,
        email_or_id="tx_test@example.com",
        auth_status=AuthStatus.CONNECTED,
    )
    store.save_account(acc)

    mock_client = MagicMock()
    mock_client.get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={
            "history": [
                {
                    "messagesAdded": [
                        {
                            "message": {
                                "id": "m_valid_1",
                                "snippet": "Hello world",
                                "payload": {"headers": [{"name": "Subject", "value": "Test Subject"}]},
                            }
                        }
                    ]
                }
            ],
            "historyId": "999999",
        }),
    )

    # Injected adapter that simulates a crash during message processing
    adapter = GmailAdapter(acc, http_client=mock_client)
    adapter.authenticate({"access_token": "valid_token"})

    # Set initial cursor
    store.set_sync_cursor(acc.account_id, "mail", "1000")

    # Simulate save_message throwing an unexpected error
    original_save = store.save_message
    def _exploding_save(m):
        raise RuntimeError("Disk full / I/O error during message save!")
    store.save_message = _exploding_save

    # Run sync — must fail and NOT advance the cursor
    res = engine.sync_account(acc, http_client=mock_client)
    assert res["success"] is False
    assert "Disk full" in res["error"]

    # Restore save and verify cursor did NOT advance to 999999
    store.save_message = original_save
    current_cursor = store.get_sync_cursor(acc.account_id, "mail")
    assert current_cursor == "1000"


def test_retry_after_header_parsing():
    """Verify HTTP Retry-After header parsing for delta seconds and HTTP dates."""
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("") == 0.0
    assert parse_retry_after(None) == 0.0
    assert parse_retry_after("invalid_header") == 0.0

    # Test future HTTP-date
    future_http_date = "Wed, 21 Oct 2099 07:28:00 GMT"
    delta = parse_retry_after(future_http_date)
    assert delta > 100000.0


# ── PolicyEngine Nonce Replay Persistence (Stage 2 Goldfish finding HIGH-06) ──

def test_policy_engine_consumed_nonce_persistence(tmp_path):
    """Verify consumed approval token nonces are persisted and prevent replay across engine instances."""
    journal_file = tmp_path / "consumed_nonces.journal"
    engine1 = PolicyEngine(journal_path=journal_file)

    tool_name = "execute_command"
    args = {"command": "runas /user:admin net.exe"}

    token = engine1.create_approval_token(
        mission_id="m1",
        subtask_id="st1",
        operator_id="operator_alice",
        tool_name=tool_name,
        arguments=args,
    )

    req = OperationRequest(
        tool_name=tool_name,
        agent_name="worker_agent",
        arguments=args,
        approval_token=token,
    )

    # 1. First evaluation: consumes token
    verdict1 = engine1.evaluate_operation(req)
    assert verdict1.is_authorized is True
    assert "Authorized via valid single-use approval token" in verdict1.reason

    # Verify journal file was written to disk
    assert journal_file.exists()
    assert token.nonce in journal_file.read_text(encoding="utf-8")

    # 2. Second evaluation on same engine: rejected
    verdict2 = engine1.evaluate_operation(req)
    assert verdict2.is_authorized is False
    assert "APPROVAL_TOKEN_REPLAY_DETECTED" in verdict2.reason

    # 3. Third evaluation on completely new engine instance pointing to the same journal file: rejected
    engine2 = PolicyEngine(journal_path=journal_file)
    verdict3 = engine2.evaluate_operation(req)
    assert verdict3.is_authorized is False
    assert "APPROVAL_TOKEN_REPLAY_DETECTED" in verdict3.reason
