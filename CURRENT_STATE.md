# FocusGuard — CURRENT_STATE.md

**Audit date:** 2026-11-21 (second audit; the first, from 2026-08-31, found an empty folder and is in git history)
**Scope:** Phase 0 re-audit before Milestone E (business mode)
**Verdict:** the student MVP (spec §67) is complete and healthy. Business mode and final hardening remain.

> **Update, end of the same day:** Milestones E and F are now complete. Every item in §3 below has been
> built; see `IMPLEMENTATION_ROADMAP.md` → *What was delivered in Milestones E–F*. 665 tests pass.

---

## 1. Repository state

| Item | Finding |
|---|---|
| Location | `E:\focusguard` (git, 22 commits, one per phase, Phase 0 → Phase 15) |
| Source files | ~415 (excluding `.venv`, `node_modules`, `.git`) |
| Python | 3.11.9 in `.venv` on E: |
| Node | 22.x, project-local in `.tools/node` (gitignored) |
| Tests | **613 passing** in 43 s (`pytest -q`) |
| Lint | `ruff check .` — clean |
| Migrations | Alembic, 3 revisions (initial schema, refresh tokens/devices, longest focus block) |

## 2. What is implemented and working

| Area | Spec § | State |
|---|---|---|
| Hardware detection (vendor-first, ignores stale NVIDIA DLLs) | 3, 46 | ✅ prints the §46 banner; selects DirectML |
| Camera capture by name, DSHOW, 640×480, auto-recovery, `CAMERA_UNAVAILABLE` | 2, 44 | ✅ |
| MediaPipe Tasks face landmarks, presence / absence / multiple faces | 11 | ✅ |
| EAR, blinks, eye closure, personal calibration | 12, 48 | ✅ |
| Drowsiness (duration + PERCLOS + head pitch) | 13 | ✅ |
| Head pose (yaw/pitch/roll) and gaze with confidence | 14, 15 | ✅ |
| Phone detection — YOLOv8n ONNX on DirectML, one event per appearance | 16 | ✅ |
| Event engine (one state machine per signal, grace periods) | 17 | ✅ |
| Focus scoring — VAS, DPS, combined, smoothing, multi-monitor scaling | 18, 59, 60 | ✅ |
| Windows foreground-app monitoring + normalisation + classification | 19, 22, 58 | ✅ |
| Chrome/Edge MV3 extension, URL minimised in the extension, loopback IPC | 20 | ✅ |
| Task-context-aware productivity (`TaskContextEngine`) | 22, 23 | ✅ |
| Local SQLite store, UUID idempotency, offline queue, retention | 11, 37, 45 | ✅ |
| FastAPI backend, SQLAlchemy, ingest, sessions, heartbeat | 12, 36 | ✅ |
| Argon2id, JWT + rotating refresh tokens, rate limiting, RBAC, tenant isolation | 51 | ✅ |
| Device enrolment (single-use, 1-hour token → device secret) | 25 | ✅ server + agent command |
| Student portal (React 19 / Vite / Tailwind / Recharts) — dashboard, session timeline, reports | 39–43 | ✅ |
| Rule-based recommendations with evidence; non-medical vocabulary fuzz-tested | 8, 9 | ✅ |
| CSV export | 52 | ✅ (student) |
| CV debug screen, benchmark, calibration tools | 49 | ✅ |

## 3. What is missing

| Area | Spec § | Notes |
|---|---|---|
| Organisation sign-up for a manager | 6, 24 | No way to create an organisation through the API |
| Employee and department management | 24, 27 | `departments` table exists, no routes |
| Projects, tasks, assignment, per-task app/site rules | 28 | Not modelled |
| Task sessions — linking a monitoring session to an assigned task | 29 | Agent supports a task *context* but not an assigned task id |
| Task focus / productivity scores | 29 | — |
| Employee strength analytics per task category | 30 | Only the `MIN_TASKS_FOR_STRENGTH_CLAIM` constant exists |
| Assignee suggestions when creating a task | 31 | — |
| Manager dashboard and employee detail | 26, 27 | — |
| Consent recorded server-side at enrolment | 25, 62 | Consent is shown by the agent but not recorded |
| Manager CSV/PDF reports | 52 | Student CSV only; no PDF anywhere |
| Docs: architecture, business_mode, privacy, setup_windows, testing, fyp_demo, student_mode | 63 | 9 of 13 docs missing or named differently |
| Demo data for the business demo | 53 | Strength analytics needs ≥3 completed tasks per category, so a live demo needs seeded history |

## 4. Broken or incomplete

Nothing broken. Minor items:

* `backend/live_smoke.db*` — a leftover smoke-test database in the source tree (gitignored, harmless).
* Test warning: a 20-byte JWT key in one test (intentional — it tests a forged token).
* Starlette deprecation warning for `httpx` in the test client — upstream, not ours.

## 5. Environment (re-measured today)

| Item | Value |
|---|---|
| CPU | Intel Core i5-6500, 4C/4T |
| RAM | 15.94 GB |
| GPU | AMD Radeon RX 580 2048SP, 7.95 GB |
| CUDA | Not available (AMD); stale `nvcuda.dll` correctly disregarded |
| DirectML | Available — selected provider `DmlExecutionProvider` |
| ONNX Runtime | 1.24.4 (directml build) |
| **C: free** | **6.8 GB — down from 18.1 GB in August.** Nothing must be installed to C:. |
| E: free | 101 GB — venv, Node, models, databases all live here |

## 6. Conclusion

The highest-risk work (local CV on an AMD GPU, the scoring engine, privacy-preserving activity
capture, auth and isolation) is finished and tested. Business mode reuses all of it: a task is a
`TaskContext` the agent already understands, a task session is a focus session with a task id, and
the manager's tenant scope already exists in the repository layer. The remaining work is additive.
