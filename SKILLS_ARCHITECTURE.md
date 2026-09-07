# Drive Rescue: Decoupled Recovery Skills Architecture & Registry Platform

This document describes the runtime architecture, contract specification, dynamic discovery registry, circuit-breaker resiliency engine, and CI/CD validation pipeline for **Drive Rescue Recovery Skills**.

---

## 1. Architectural Philosophy: Decoupled Control & Capabilities

In previous iterations, file repair heuristics were baked directly into host procedural logic. The new **Drive Rescue Skills Architecture** enforces complete runtime decoupling:

1. **Strict Versioned Contract (`v1.0.0`)**: Host applications interact with skills exclusively through typed contracts and JSON Schema validators. No skill-specific assumptions exist in the host control plane.
2. **Dynamic Discovery Registry**: Skills are self-describing via isolated `skill-manifest.json` declarations. Skills can be loaded from local directories, remote registries, or containerized/WASM environments without modifying host source code.
3. **Resilience & Circuit Breaking**: Each skill runtime is isolated by a Circuit Breaker that monitors error rates and automatically shifts traffic to fallback versions if anomalies spike.
4. **Zero External Runtime Dependencies**: Implemented in 100% pure Python standard library (`json`, `dataclasses`, `typing`, `hashlib`, `urllib`), maintaining universal cross-platform compatibility across macOS, Linux, and Windows.

---

## 2. System Architecture Diagram

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                           DRIVE RESCUE HOST APPLICATION                     │
 │                                                                             │
 │  ┌───────────────────────┐    ┌─────────────────┐    ┌───────────────────┐  │
 │  │  Web Studio Dashboard │───▶│  SkillExecutor  │───▶│  Circuit Breaker  │  │
 │  │  & CLI Control Plane  │◀───│  (Coordinator)  │◀───│  (Per-Skill State)│  │
 │  └───────────────────────┘    └────────┬────────┘    └─────────┬─────────┘  │
 └────────────────────────────────────────┼───────────────────────┼────────────┘
                                          │                       │
                                          │ (Contract v1 Payload) │ (Trip / Fallback)
                                          ▼                       │
                          ┌───────────────────────────────┐       │
                          │   Dynamic Registry Catalog    │◀──────┘
                          │   (Manifests, Triggers, Wts)  │
                          └───────────────┬───────────────┘
                                          │
                                          │ (Discovers & Dispatches)
                                          ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                      STANDALONE RECOVERY SKILLS RUNTIME                     │
 │                                                                             │
 │  ┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────┐  │
 │  │ repair-corrupted-files  │  │ restore-backup-versions │  │  open-alt   │  │
 │  │ (Manifest + Skill v1.2) │  │ (Manifest + Skill v1.1) │  │  (v1.0)     │  │
 │  ├─────────────────────────┤  ├─────────────────────────┤  ├─────────────┤  │
 │  │ type-conversion-engine  │  │ identify-unknown-types  │  │ rebuild-inc │  │
 │  │ (Manifest + Skill v1.0) │  │ (Manifest + Skill v1.1) │  │  (v1.0)     │  │
 │  ├─────────────────────────┤  ├─────────────────────────┤  ├─────────────┤  │
 │  │ raw-cluster-carving     │  │ scan-malware-quarantine │  │ secure-arch │  │
 │  │ (Manifest + Skill v1.1) │  │ (Manifest + Skill v1.0) │  │  (v1.0)     │  │
 │  ├─────────────────────────┴──┴─────────────────────────┴──┴─────────────┤  │
 │  │                  smart-sorter-reorganizer (v1.2.0)                    │  │
 │  └───────────────────────────────────────────────────────────────────────┘  │
 └─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Contract Specification (`v1.0.0`)

The contract layer (`drive_rescue.contract`) defines strict, strongly typed data models for all skill transactions.

### Input Specification: `SkillInput`
```python
@dataclass
class SkillInput:
    skill_id: str                      # Canonical ID of target skill
    files: List[FilePayload]           # Batch of target files with metadata
    destination_dir: str               # Destination directory for repaired files
    session_id: str                    # Unique recovery session identifier
    trace_id: str                      # Distributed tracing identifier
    contract_version: str = "v1.0.0"   # Protocol contract version
    timeout_ms: int = 30000            # Per-invocation execution deadline
    options: Dict[str, Any]            # Skill-specific runtime parameters
```

### Output Specification: `SkillOutput`
```python
@dataclass
class SkillOutput:
    success: bool                          # Overall execution outcome
    skill_id: str                          # Canonical ID of executed skill
    skill_version: str                     # Version string of executed skill
    contract_version: str = "v1.0.0"       # Contract schema version
    session_id: str                        # Correlation session ID
    trace_id: str                          # Distributed trace ID
    processed_files: List[ProcessedFileResult] # Per-file forensic results
    repaired_count: int                    # Total repaired/reconstructed files
    quarantined_count: int                 # Total isolated/quarantined files
    sorted_count: int                      # Total categorized files
    audit_log: List[AuditEntry]            # Forensic audit timeline entries
    metrics: ExecutionMetrics              # Performance telemetry (duration, RAM, bytes)
    error: Optional[SkillError]            # Structured error model on failure
```

---

## 4. Manifest Specification (`skill-manifest.json`)

Each skill directory contains an isolated declaration file:

```json
{
  "id": "repair-corrupted-files",
  "name": "Repair Corrupted Files",
  "version": "1.2.0",
  "contract_version": "v1.0.0",
  "description": "Fix corrupted magic bytes, repair damaged file headers (PNG, PDF, SQLite, JPG, ZIP).",
  "author": "Drive Rescue Core Team",
  "triggers": {
    "file_statuses": ["Partial", "Failed"],
    "extensions": [".png", ".jpg", ".jpeg", ".pdf", ".sqlite", ".db", ".zip", ".docx", ".xlsx"],
    "always_available": false
  },
  "runtime": {
    "type": "python_module",
    "entrypoint": "skills.repair_corrupted_files.skill:RepairCorruptedFilesSkill",
    "timeout_ms": 30000
  },
  "rollout": {
    "strategy": "canary",
    "weight": 100,
    "fallback_version": "1.0.0",
    "is_active": true
  },
  "capabilities": ["magic_byte_repair", "header_reconstruction", "png_chunk_fix", "pdf_trailer_recovery"]
}
```

---

## 5. Built-in Standalone Skills Catalog

| Skill ID | Version | Description | Key Capabilities |
|---|---|---|---|
| `repair-corrupted-files` | `1.2.0` | Magic header & chunk repair | PNG, JPG, PDF, SQLite, ZIP header reconstruction |
| `restore-backup-versions` | `1.1.0` | Shadow copy & journal recovery | USN Journal, shadow records, rollback copies |
| `open-in-alternative-apps` | `1.0.0` | Open standard transcoder | Fallback formats for LibreOffice, VLC, raw text |
| `type-conversion-engine` | `1.0.0` | Proprietary to open format conversion | Office & binary payload transcoding |
| `identify-unknown-file-types` | `1.1.0` | Magic signature heuristic parser | Hex byte scanning, extension deduction |
| `rebuild-incomplete-files` | `1.0.0` | Bad sector & cluster reconstruction | Zero-fill patching, cluster stitching |
| `raw-cluster-carving` | `1.1.0` | Sector carving for lost files | Deep signature search across orphan clusters |
| `scan-for-malware-quarantine` | `1.0.0` | Payload threat heuristic scanner | Obfuscated script detection & safe isolation |
| `secure-archive-encryption` | `1.0.0` | Forensic archive packaging | Container encryption, checksum manifests |
| `smart-sorter-reorganizer` | `1.2.0` | Semantic directory classifier | Automatic Documents/Media/Code/Data sorting |

---

## 6. Circuit Breaker & Resiliency

The `CircuitBreaker` pattern prevents cascading application failure:

```
           [ Normal Execution ]
                  │
                  ▼
            ┌────────────┐
            │   CLOSED   │◀───────────┐ (Success in Half-Open)
            └─────┬──────┘            │
                  │ (Threshold Failures)
                  ▼                   │
            ┌────────────┐            │
            │    OPEN    │            │
            └─────┬──────┘            │
                  │ (Recovery Timeout Elapses)
                  ▼                   │
            ┌────────────┐            │
            │ HALF_OPEN  │────────────┘
            └────────────┘
```

- When a skill fails repeatedly (exceeding `failure_threshold = 3`), its circuit breaker trips to `OPEN`.
- While `OPEN`, executions automatically route to the configured `fallback_version`.
- After `recovery_timeout_s = 30.0s`, the breaker shifts to `HALF_OPEN` to test downstream health before resuming full traffic.

---

## 7. REST APIs & CLI Reference

### REST Endpoints
- `GET /api/skills/registry`: Returns catalog of all discovered skills with manifest specs and version trees.
- `GET /api/skills/health`: Returns live health checks and circuit breaker states for all skills.
- `POST /api/skills/registry/rollout`: Dynamically updates canary traffic weights (`skill_id`, `version`, `weight`).
- `POST /api/execute_skills`: Executes single or pipelined recovery skills against target files.

### CLI Commands
```bash
# List all discovered skills and their rollout status
python3 cli.py skills list

# Test health and circuit state of a specific skill
python3 cli.py skills test repair-corrupted-files

# Adjust canary traffic rollout weight (0-100%)
python3 cli.py skills rollout repair-corrupted-files --weight 50
```

---

## 8. Creating a New Skill

1. Create a directory inside `skills/<your_skill_name>/`.
2. Add `skill-manifest.json` declaring IDs, triggers, runtime, and rollout policy.
3. Subclass `drive_rescue.runtime.BaseSkill` in `skill.py` and implement `execute(payload: SkillInput) -> SkillOutput`.
4. Run the automated conformance suite:
   ```bash
   python3 -m unittest tests.test_skill_conformance -v
   ```
