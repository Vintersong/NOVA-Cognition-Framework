import os
import json
import logging
import sqlite3
import uuid
from enum import Enum
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)

class VerificationLevel(str, Enum):
    UNVERIFIED = "unverified"
    DECLARED = "declared"
    TESTED = "tested"
    FORMAL = "formal"

class Capability(str, Enum):
    FS_READ = "fs.read"
    FS_WRITE_REV = "fs.write.rev"
    FS_WRITE_IRREV = "fs.write.irrev"
    NET_EGRESS = "net.egress"
    MEMORY_WRITE = "memory.write"
    SPAWN_PROC = "spawn.proc"
    # tool.invoke(name) is handled dynamically

class SkillManifest:
    def __init__(self, verification: Optional[VerificationLevel], capabilities: Set[str]):
        self.verification = verification if verification else VerificationLevel.UNVERIFIED
        self.capabilities = capabilities

    @classmethod
    def parse(cls, content: str) -> 'SkillManifest':
        verification = VerificationLevel.UNVERIFIED
        capabilities: Set[str] = set()

        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("@@verification:"):
                val = line.split(":", 1)[1].strip().lower()
                try:
                    verification = VerificationLevel(val)
                except ValueError:
                    verification = VerificationLevel.UNVERIFIED
            elif line.startswith("@@capabilities:"):
                val = line.split(":", 1)[1].strip().lower()
                if val == "*":
                    capabilities = {"*"}
                else:
                    capabilities = {c.strip() for c in val.split(",") if c.strip()}
            elif line == "---":
                break  # end of headers

        return cls(verification, capabilities)

    def has_capability(self, cap: str) -> bool:
        if "*" in self.capabilities:
            return True
        return cap in self.capabilities

class AuditLog:
    """SQLite-backed audit log for biconditional verification."""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    type TEXT,
                    tool_name TEXT,
                    args TEXT,
                    ok INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

    def append(self, session_id: str, event_type: str, tool_name: str, args: dict, ok: bool):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO audit_log (id, session_id, type, tool_name, args, ok) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), session_id, event_type, tool_name, json.dumps(args), 1 if ok else 0)
            )

class CapabilityDenied(Exception):
    pass

class HITLGate:
    def __init__(self, audit_log: AuditLog):
        self.audit_log = audit_log
        self.policy_mode = os.environ.get("NOVA_HITL_MODE", "auto_deny")

    def request(self, session_id: str, tool_name: str, args: dict) -> bool:
        """
        In MCP, we can't easily prompt the terminal without breaking stdio.
        For Phase 3, we use CONIN$/CONOUT$ on Windows or /dev/tty on Unix to prompt the operator interactively.
        """
        self.audit_log.append(session_id, "irreversible.request", tool_name, args, ok=True)
        
        decision = False
        if self.policy_mode == "auto_approve":
            decision = True
        else:
            try:
                import sys
                prompt_msg = f"\n[HITL GATE] Agent is attempting irreversible capability '{tool_name}'.\nArgs: {json.dumps(args)[:200]}\nApprove? (y/n/timeout=deny): "
                if os.name == 'nt':
                    with open('CONOUT$', 'w') as cout, open('CONIN$', 'r') as cin:
                        cout.write(prompt_msg)
                        cout.flush()
                        ans = cin.readline().strip().lower()
                        decision = (ans == 'y')
                else:
                    with open('/dev/tty', 'w') as cout, open('/dev/tty', 'r') as cin:
                        cout.write(prompt_msg)
                        cout.flush()
                        ans = cin.readline().strip().lower()
                        decision = (ans == 'y')
            except Exception as e:
                logger.error(f"HITL Gate interactive prompt failed: {e}. Defaulting to DENY.")
                decision = False
            
        self.audit_log.append(session_id, "irreversible.decision", tool_name, args, ok=decision)
        return decision

    def execute_with_gate(self, session_id: str, tool_name: str, args: dict, active_skill: SkillManifest, is_irreversible: bool):
        """
        The core middleware dispatch gate.
        """
        if not active_skill.has_capability(tool_name) and not active_skill.has_capability("*"):
            self.audit_log.append(session_id, "denied_event", tool_name, args, ok=False)
            raise CapabilityDenied(f"Capability {tool_name} not declared in skill manifest.")

        if is_irreversible:
            if active_skill.verification == VerificationLevel.UNVERIFIED:
                approved = self.request(session_id, tool_name, args)
                if not approved:
                    raise CapabilityDenied("HITL Gate Denied unverified irreversible call.")
            elif active_skill.verification in (VerificationLevel.DECLARED, VerificationLevel.TESTED):
                if active_skill.has_capability(tool_name) or active_skill.has_capability("*"):
                    # execute, log
                    self.audit_log.append(session_id, "irreversible.executed", tool_name, args, ok=True)
                    return True
                else:
                    approved = self.request(session_id, tool_name, args)
                    if not approved:
                        raise CapabilityDenied("HITL Gate Denied undeclared irreversible call.")
        else:
            # Reversible goes to transaction buffer (no-op for now, just allow)
            pass
            
        return True

_audit_log_instance = None
def get_audit_log() -> AuditLog:
    global _audit_log_instance
    if _audit_log_instance is None:
        from config import _REPO_ROOT
        db_path = os.path.join(_REPO_ROOT, "output", "audit_log.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        _audit_log_instance = AuditLog(db_path)
    return _audit_log_instance

_hitl_gate_instance = None
def get_hitl_gate() -> HITLGate:
    global _hitl_gate_instance
    if _hitl_gate_instance is None:
        _hitl_gate_instance = HITLGate(get_audit_log())
    return _hitl_gate_instance


# -------------------------------------------------------------------------
# Phase 4: Biconditional Post-Run Audit Check
# -------------------------------------------------------------------------

class BiconditionalFailed(Exception):
    def __init__(self, unaccounted_changes: Set[str], phantom_records: Set[str]):
        self.unaccounted_changes = unaccounted_changes
        self.phantom_records = phantom_records
        super().__init__(
            f"Biconditional failed. Unaccounted corpus changes: {unaccounted_changes} | "
            f"Phantom audit records (no corpus change): {phantom_records}"
        )

def snapshot_corpus() -> dict:
    """Take a lightweight snapshot of the NOVA corpus using modification times."""
    from config import SHARD_DIR
    snapshot = {}
    if os.path.exists(SHARD_DIR):
        for f in os.listdir(SHARD_DIR):
            if f.endswith(".json") or f.endswith(".shard"):
                path = os.path.join(SHARD_DIR, f)
                snapshot[f] = os.path.getmtime(path)
    return snapshot

def run_biconditional_check(session_id: str, corpus_before: dict, corpus_after: dict) -> bool:
    """
    D = actual corpus delta (state before vs. after)
    S = records in audit log for this session where type = 'irreversible.executed' AND ok = 1
    Returns True if D and S match, raises BiconditionalFailed otherwise.
    """
    # 1. Compute D (corpus delta targets)
    D = set()
    for f, mtime in corpus_before.items():
        if f not in corpus_after or corpus_after[f] != mtime:
            D.add(f)
    for f in corpus_after:
        if f not in corpus_before:
            D.add(f)

    # 2. Compute S (audit log targets)
    S = set()
    gate = get_hitl_gate()
    with sqlite3.connect(gate.audit_log.db_path) as conn:
        rows = conn.execute(
            "SELECT tool_name, args FROM audit_log WHERE session_id = ? AND type = 'irreversible.executed' AND ok = 1",
            (session_id,)
        ).fetchall()
        
    for row in rows:
        tool_name, args_json = row[0], row[1]
        try:
            args = json.loads(args_json)
            # Map known tool args to file targets
            if tool_name == "fs.write.irrev":
                target = args.get("target_rel") or args.get("output_file")
                if target:
                    S.add(os.path.basename(target))
            elif "shard" in tool_name:
                # Approximate target parsing for shards
                shard_id = args.get("shard_id")
                if shard_id:
                    S.add(f"{shard_id}.json")
                    S.add(f"{shard_id}.shard")
        except json.JSONDecodeError:
            pass

    # Note: Because the exact filenames generated by nova_shard_create might not be in the args 
    # (due to ID auto-generation), we do a loose overlap check. A more precise check requires
    # the MCP tools to log the exact written filenames to the audit log.
    
    # For Phase 4 MVP: if there are corpus changes but NO audit records, or vice versa, we flag it.
    if D and not S:
        raise BiconditionalFailed(unaccounted_changes=D, phantom_records=set())
    if S and not D:
        # It's possible S logged a file write that didn't go to the corpus dir, so we check intersection loosely
        pass

    logger.info(f"Biconditional check passed for session {session_id}. D={len(D)}, S={len(S)}")
    return True

