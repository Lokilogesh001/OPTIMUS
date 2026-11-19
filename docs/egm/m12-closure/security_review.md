# M12 Personal Life Intelligence Closure: Security Review

**Project:** OPTIMUS — Autonomous Personal Agentic AI Assistant  
**Milestone:** M12 — Personal Life Intelligence Closure  
**Base Commit:** `5c02288`  
**Governance:** Mandatory EGM Framework  
**Classification:** Internal Technical Security Architecture Document  
**Date:** 2026-11-19  

---

## 1. Threat Modeling & Attack Surfaces

The Personal Intelligence subsystem processes, persists, and synchronizes highly sensitive user communication (emails, calendar events, personal commitments, contact graphs, OAuth credentials, and extracted tasks). Prior to this closure, the subsystem was subject to four primary threat vectors:
1. **Plaintext Exposure at Rest (Threat T1):** Unauthorized local processes or file extraction compromising emails, meeting contents, or personal obligations.
2. **Cross-Tenant & Record Transplantation (Threat T2):** Swapping ciphertext envelopes between accounts or entity collections to elevate permissions or forge data.
3. **Concurrency Race Conditions & Lost Updates (Threat T3):** Concurrent processes clobbering each other's updates, or triggering Refresh Token Rotation (RTR) invalidations via uncoordinated network refreshes.
4. **Approval Token Nonce Replay (Threat T4):** Replaying consumed `ApprovalToken` nonces across process restarts to authorize unauthorized mutating tools.

---

## 2. Cryptographic Security Architecture (Pillar A)

### 2.1 Authenticated Encryption at Rest
- **Algorithm:** AES-256-GCM (Galois/Counter Mode) with 128-bit authentication tags, compliant with NIST SP 800-38D.
- **Key Management:**
  - 256-bit cryptographically secure random key generated via `secrets.token_bytes(32)`.
  - Persisted in the OS Keyring / CredentialVault (`personal_store_master_key`) via Windows DPAPI / macOS Keychain / Linux SecretService.
  - Zero hardcoded fallback strings. Machine-derived derivations were explicitly removed.
  - Environment variable override `OPTIMUS_PERSONAL_MASTER_KEY` supported for containerized and headless CI/CD execution.
  - Complete master key shredding implemented in `PersonalStoreCrypto.purge_master_key()` overwriting key memory with zeros.

### 2.2 Collision-Resistant 96-bit Structured Nonce
In GCM mode, nonce reuse with the same key is catastrophic (destroying authenticity and confidentiality). OPTIMUS implements a three-component 96-bit (12-byte) nonce:
$$\text{Nonce} = \text{PID\_Epoch}_{32} \parallel \text{Counter}_{32} \parallel \text{Random}_{32}$$
- **Bytes 0–3:** `(os.getpid() ^ int(time.time())) & 0xFFFFFFFF` ensures temporal and process partition across restarts and worker forks.
- **Bytes 4–7:** Atomic monotonic integer counter (`itertools.count(1)`) protected by an internal lock, preventing intra-process collisions.
- **Bytes 8–11:** Cryptographically secure pseudo-random bytes (`secrets.token_bytes(4)`), providing 32 bits of entropy per call.

### 2.3 Canonical Authenticated Associated Data (AAD)
To prevent cross-entity and cross-tenant transplantation attacks, every encryption operation binds canonical deterministic JSON AAD:
```json
{"acc": "<account_id>", "id": "<record_id>", "type": "<entity_type>", "ver": 1}
```
During decryption:
1. AES-GCM tag verification proves that neither ciphertext nor AAD was modified.
2. The decrypted envelope's decoded AAD is verified against the caller's expected `entity_type`, `record_id`, and `account_id`.
3. If an adversary copies `accounts.json` over `tasks.json`, or transplants `user_A`'s message to `user_B`'s inbox, decryption fails immediately with `SecurityIntegrityError`.

### 2.4 Downgrade Attack Prevention
To guarantee that legacy plaintext records cannot be injected once migrated:
- Storage maintains a `.encryption_enforced` sentinel flag.
- Once migration executes, any encounter with an unencrypted JSON file in `PersonalStore` immediately raises `SecurityIntegrityError` and aborts hydration.

---

## 3. Multiprocess Concurrency & Integrity (Pillar B)

### 3.1 OS-Level Category Locking
- Dedicated lock directory `memory/personal/.locks/` keeps lock files separate from data files.
- Exclusive file locking implemented per category (`accounts`, `tasks`, `events`, `commitments`, `contacts`, `sync_state`, `messages`).
- Uses Windows `msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)` and Unix `fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)` with exponential backoff and randomized jitter (10-15ms).
- Thread-local set `_thread_local.held_locks` guarantees re-entrant execution without thread self-deadlocks.

### 3.2 Read-Modify-Write Mutators
- All entity updates are routed through `mutate_*` methods (`mutate_tasks`, `mutate_accounts`, etc.).
- The mutator acquires the OS category lock, re-reads latest state from disk, applies the mutation callback in memory, writes atomically to a unique per-write temp file (`.<file>.<pid>.<nonce>.tmp`), replaces the file via atomic `os.replace`, and releases the lock.
- Independent processes mutating the store concurrently never overwrite peer additions or updates.

---

## 4. OAuth Security & Provider Integration (Pillar C)

### 4.1 Proof Key for Code Exchange (PKCE) & State
- All OAuth 2.0 authorization flows use RFC 7636 PKCE with `code_challenge_method=S256`.
- `generate_pkce_pair()` produces 64-byte URL-safe verifiers and unpadded base64url SHA-256 challenges.
- `generate_state_token()` generates 32-byte cryptographically secure random tokens for CSRF protection.

### 4.2 Refresh Token Stampede Mitigation
- Providers implement `oauth_refresh_lock(account_id)` acquiring `.locks/oauth_<account_id>.lock`.
- Prevents concurrent processes from simultaneously calling provider token endpoints with the same refresh token, protecting against Refresh Token Rotation (RTR) invalidation.

### 4.3 Honest Disconnect & Revocation
- Differentiates between verified remote revocation (`REVOKED_REMOTE_VERIFIED`) and offline/unverified local credential removal (`DISCONNECTED_LOCAL_ONLY`).
- Local credentials in OS Keyring / CredentialVault are deleted immediately via `delete_credential()` in both paths.

---

## 5. Synchronization Recovery & Deduplication (Pillar D)

### 5.1 Strict Transactional Ordering
- Cursors in `sync_state.json` are only advanced after messages and events are fully validated and persisted to disk.
- Any crash or I/O failure during message extraction preserves the prior cursor, guaranteeing at-least-once synchronization and preventing lost messages.

### 5.2 Multi-Factor Deduplication
- Tasks: Deduplicated via `sha256(f"{norm_title}:{counterpart}:{conv_id}")`. Completed/cancelled tasks are exempt to allow recurring tasks.
- Commitments: Deduplicated via `sha256(f"com:{norm_promise}:{counterpart}:{conv_id}")` with `find_duplicate_commitment` lookup.

---

## 6. Authoritative Policy Engine Hardening

- **Persistent Nonce Journal:** Consumed nonces from `ApprovalToken` instances are appended to `memory/security/consumed_nonces.journal` under `.nonces.lock`, ensuring replay protection survives engine restarts and multiprocess execution.
- **Privileged Mutating Operations:** Personal data mutation tools (`send_email`, `delete_email`, `delete_message`, `create_calendar_event`, `update_calendar_event`, `delete_calendar_event`, `send_whatsapp_message`, `purge_personal_store`, `wipe_personal_data`, `wipe_all_data`, `disconnect_account`, `revoke_account`, `delete_account`) are strictly gated by `PRIVILEGED_APPROVAL_PATTERNS`, requiring valid, single-use `ApprovalToken` objects.
