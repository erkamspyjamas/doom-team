# Doom Core — Proje Kapsamlı Roadmap

**Belge durumu:** Yaşayan mühendislik roadmap'i
**Tarih:** 20 Ağustos 2026
**Ana ürün:** Doom Team / Doom Core
**Yardımcı sistem:** OpenClaw Dev Team v1
**Primary development repository:** `/home/eko/doom-team/autopilot/doom-core`
**Aktif branch:** `openclaw/autopilot`

> Bu roadmap, `Doom_Core_Proje_Istekleri_ve_Hedefler.docx` ve `Doom_Team_Proje_Dokumani_v1(1).md` belgelerindeki ürün gereksinimlerinin uygulanabilir mühendislik fazlarına ayrılmış halidir.
> Faz adları ve alt kırılımlar mühendislik planlamasıdır; normative gereksinimler kaynak belgelerdeki `REQ-*`, `NFR-*` ve `AC-*` maddeleridir.

---

# 1. North Star

Doom Team'in nihai amacı, yerel/self-hosted AI agent'ların gerçek yazılım işlerini güvenli, kanıtlanabilir ve mümkün olduğunca otonom yürüttüğü bir şirket işletim sistemi oluşturmaktır.

```text
Boss objective + hard constraints
        ↓
Lead uygulanabilir DAG üretir
        ↓
Doom Core contract / role / workMode / dependency preflight uygular
        ↓
DEV izole workspace'te gerçek implementation yapar
        ↓
Git + test + execution evidence oluşur
        ↓
QA bağımsız ve read-only doğrular
        ↓
Reviewer bağımsız ve read-only değerlendirir
        ↓
Deterministic release / completion gate
        ↓
awaiting_approval
        ↓
Boss final approval
        ↓
Push / merge / deploy yalnız ayrıca yetkilendirilirse
```

Başarı kriteri “agent çalıştı” değildir. Başarı; business objective'in doğru ilerlemesi, recoverable sapmalarda gereksiz fatal block oluşmaması, gerçek risklerde fail-closed davranılması, evidence'ın korunması, duplicate/loop üretilmemesi, crash/restart sonrası durable devam, QA/Reviewer bağımsızlığı ve final merge öncesi human approval sınırının korunmasıdır.

---

# 2. Mimari Ana İlke

> **AI'a geri döndürülebilir çalışma alanında geniş teknik özgürlük ver; pahalı, shared-state, geri döndürülemez veya güvenlik-kritik sınırlarda deterministic guard uygula.**

## Auto-repair / bounded recovery alanı

- güvenle düzeltilebilir role / workMode hataları
- dependency sırası kusurları
- eksik QA / Reviewer task'ı
- parse edilebilir malformed structured output
- recoverable JSON/schema sorunları
- transient provider/runtime failure
- evidence bulunan bounded tool-loop continuation
- güvenli reassign / remediation
- allowlisted zero-task planning recovery

## Hard-block / human boundary

- approval dışı base merge
- live deploy
- destructive DB mutation
- DB reset / destructive migration
- secret / credential / permission ihlali
- workspace escape
- acceptance'tan geçmemiş commit'in integration/base'e alınması
- repository mutation için evidence yokluğu
- korunamayan dirty/untracked state
- recovery budget exhaustion
- repeated semantic loop
- gerçek requirement ambiguity altında geri döndürülemez işlem

---

# 3. Değişmez Güvenlik Invariantları

1. Live Doom Core ve live SQLite rutin DEV workspace'i değildir.
2. DB reset yoktur.
3. Destructive migration yoktur.
4. Secrets plaintext olarak DB/event/log'a taşınmaz.
5. `git reset --hard`, geniş `git clean`, force push ve force checkout kullanılmaz.
6. `git add .` kullanılmaz; exact dosyalar stage edilir.
7. User evidence ve untracked forensic artifact'lar sessizce silinmez.
8. Production mantığında mission/task/agent/model ID hard-code edilmez.
9. Dynamic routing roster/capability/tool/durable contract/DAG üzerinden çözülür.
10. QA ve Reviewer repository açısından read-only kalır.
11. Modify task gerçek mutation + Git/repository evidence üretir.
12. Agent final metni tek başına acceptance evidence değildir.
13. Acceptance'tan geçmemiş commit integration/base'e alınmaz.
14. Final base merge yalnız human approval ile yapılır.
15. Retry/replan/recovery bounded kalır.
16. Recovery history ve budget restart/replan ile sessizce sıfırlanmaz.
17. Duplicate execution ve semantic loop fail-closed engellenir.
18. Kanıtlanmamış başarı iddiası yapılmaz.
19. Provider/model/runtime ayrımı ihtiyaç kanıtı olmadan yeniden tasarlanmaz.
20. OpenClaw control-plane geliştirmesi varsayılan ürün işi değildir.

---

# 4. Roadmap Durum Özeti

- `✅` tamamlandı / kabul edilmiş
- `🔄` aktif doğrulama veya kapanış
- `⏳` sıradaki planlı faz
- `⬜` henüz başlanmadı
- `◻️` ihtiyaç kanıtına bağlı ileri otomasyon

## Mevcut durum — 20 Ağustos 2026 snapshot

| Faz | Tema | Durum |
|---|---|---:|
| Phase 1A | Mission completion / structured-output reliability ilk dilim | ✅ |
| Phase 1B | Malformed/partial JSON recovery | ✅ |
| Phase 1C | Mission State Integrity & Resume | ✅ |
| Phase 1D | Duplicate Execution & Idempotency | ✅ |
| Phase 1E | Completion Truth | ✅ |
| Phase 1F | Bounded Failure Recovery | ✅ |
| Phase 2 | Worktree / Forensic Safety | ⬜ |
| Phase 3 | Unified Recovery Ledger & Operational Control | ⬜ |
| Phase 4 | Plan / Task / Role Contract Hardening | ⬜ |
| Phase 5 | Evidence & Target Policy | ⬜ |
| Phase 6 | End-to-End Mission Acceptance | ⬜ |
| Phase 7 | Observability & Diagnostics | ⬜ |
| Phase 8 | Compatibility, Security & Release Provenance | ⬜ |
| Phase 9 | Advanced Automation | ◻️ |

> Roadmap sırası yeni repository/test/runtime evidence ile değişebilir. Yeni faz açmak için gerçek ürün boşluğu veya yeni kanıt bulunmalıdır; sırf “bir sonraki faz gelsin” diye semantic duplicate iş üretilmemelidir.

---

# 5. Phase 0 — Engineering Control Plane Foundation

**Durum:** ✅ Feature freeze

Amaç Doom Core'u güvenli biçimde geliştirebilecek yardımcı engineering zincirini kurmaktı.

```text
Human seed
  ↓
DEV
  ↓
local commit + tests + proof
  ↓
QA
  ↓
Reviewer
  ↓
Human boundary
```

OpenClaw Dev Team v1 yardımcı sistemdir; ürünün kendisi değildir. Yeni control-plane özelliği yalnız gerçek Doom Team işi sırasında somut ihtiyaç kanıtlanırsa açılır.

---

# 6. Phase 1 — Mission Completion Reliability

Ana hedef: Geçerli bir business objective'in recoverable LLM/runtime varyasyonları nedeniyle gereksiz yere terminal `blocked` durumuna düşmesini azaltmak.

## Phase 1A — Structured Output Reliability I

**Durum:** ✅

- düzeltilebilir structured-final/JSON biçim kusurlarını fatal mission failure'a çevirmemek
- bounded deterministic repair
- pozitif/negatif regression
- full validation + QA + Reviewer

## Phase 1B — Structured Output Reliability II

**Durum:** ✅

- partial/malformed JSON recovery'nin eksik sınır vakalarını kapatmak
- recoverable JSON'un false-block üretmesini azaltmak
- unsafe input'u yanlışlıkla kabul etmemek

## Phase 1C — Mission State Integrity & Resume

**Durum:** ✅

- crash/interruption sonrası mission/task/execution state tutarlılığı
- restore/resume sırasında fail-closed davranış
- incomplete work'ü completed saymamak
- silent state corruption üretmemek

İlgili: `REQ-MIS-01`, `REQ-REC-02`, `REQ-REC-03`, `NFR-DUR-01`.

## Phase 1D — Duplicate Execution & Idempotency

**Durum:** ✅

- aktif bound execution varken replacement work başlatmamak
- retry/recovery/resume sırasında duplicate side-effect üretmemek
- terminal/unbound durumda legitimate retry'ı engellememek

İlgili: `NFR-IDM-01`, `REQ-REC-02`, `REQ-REC-03`.

## Phase 1E — Completion Truth

**Durum:** ✅

- Baseline: `070231e98e7bb8772fe5aa700a268654504762d8`
- Final commit: `c0e017020e0c87c011ffd6dd5cd9a91550f48d8b`

- final QA/Reviewer execution failure'ı explicit blocked state'e çevirmek
- explicit error/evidence üretmek
- success path'i bozmamak

```text
QA / Reviewer execution error or cancellation
        ↓
mission = blocked
        ↓
explicit error_text
        ↓
mission.blocked evidence
```

İlgili: `REQ-QA-01`, `REQ-REV-01`, `REQ-APR-01`, `NFR-OBS-01`.

## Phase 1F — Bounded Failure Recovery

**Durum:** ✅

- Baseline: `c0e017020e0c87c011ffd6dd5cd9a91550f48d8b`
- Final commit: `762513a896122f3608af7ae47dd20a6ff6bedca0`

Routine planning/execution/acceptance/QA/Reviewer failure'larını bounded şekilde retry, revise, reassign, remediation veya replan ile ele almak.

Normative budget hedefleri:

- zero-task planning replan: **3**
- task recovery: **3**
- mission recovery: **8**
- final-gate recovery: **3**

Zorunlu davranışlar:

- attempt başlamadan durable `attempt-start`
- event-ID tabanlı sayaç
- success/failure/crash aynı budget mantığı
- replan geçmiş budget'ı silmez
- detached task marker final-gate budget tüketmez
- exhaustion terminal ve fail-closed
- restart exhaustion sonrası yeni execution açmaz
- override explicit/audit-edilebilir policy epoch ile

İlgili: `REQ-REC-01..03`, `AC-03..06`.

---

# 7. Phase 2 — Worktree / Forensic Safety

**Durum:** ⬜

Kaynak belgelerdeki önemli açık risk: failed-task/replan cleanup sırasında dirty/untracked/crash-window içeriğinin kaybolabilmesi.

## 2A — Dirty/Ahead Pre-Cleanup Guard
Cleanup öncesi dirty/ahead durumunu deterministic kontrol et; korunmamış evidence varsa force cleanup'ı reddet.

## 2B — Forensic Retention Artifact
Cleanup zorunluysa patch/bundle/artifact/retained workspace yaklaşımıyla evidence'ı sakla ve event'e bağla.

## 2C — Crash-Window & Replan Snapshot
Replan öncesi task/worktree forensic state'ini koru: task state, execution binding, changed files, untracked evidence, branch/HEAD ve ilgili child state.

## 2D — Lossless Cleanup Provenance
Cleanup sonucu `cleaned / retained / refused / human_required` olarak provenance ile yazılsın.

İlgili: `REQ-EVD-01`, `REQ-GIT-01`, `AC-07`, `AC-08`, `NFR-SAF-01`.

---

# 8. Phase 3 — Unified Recovery Ledger & Operational Control

**Durum:** ⬜

## 3A — Unified Attempt Ledger
Planning/task/mission/final-gate attempt'lerini ortak provenance-aware ledger altında ifade et.

Her kayıt en az: identity, recovery kind, event epoch/id, attempt, budget, source, reason, result.

## 3B — Versioned Recovery Policy
Planning allowlist, budget, retry/backoff ve terminal cutover kararlarını versioned policy haline getir.

## 3C — Terminal Exhaustion & Override Epoch
Budget exhaustion sonrası restart'ın yeni iş açmamasını garanti et; terminal state yalnız explicit, authorized, audit-edilebilir override ile aşılabilsin.

## 3D — Operational Control Provenance
Pause/resume/retry/reassign/replan/cancel/kill API/UI/event üzerinden izlenebilir olsun; manual ve autonomous action ayrıştırılsın.

İlgili: `REQ-REC-01..03`, `REQ-OPS-01`, `NFR-OBS-01`.

---

# 9. Phase 4 — Plan / Task / Role Contract Hardening

**Durum:** ⬜

## 4A — Durable Task Contract Consistency
`instruction`, `expectedOutcome`, `acceptanceCriteria`, `workMode` plan → DB → API → runtime boyunca aynı semantiği taşısın.

## 4B — DAG Canonicalization & Dependency Safety
Acyclic DAG, doğru dependency, eksik QA/Reviewer sentezi ve semantics-preserving role/workMode repair.

## 4C — Dynamic Routing & AgentFactory
Routing agent adı/enum hard-code etmeden role/capability/tool/team membership ile çözülsün; uygun kalıcı agent yoksa scoped specialist oluşturulsun.

## 4D — Language & Mutation Semantics Regression
Türkçe/İngilizce action, negation, completed-state, validation deliverable, output path ve input/reference ayrımları regression kapsamına alınsın.

İlgili: `REQ-PLN-01/02`, `REQ-TSK-01/02`, `REQ-ROL-01/02`, `REQ-AF-01`, `NFR-L10N-01`.

---

# 10. Phase 5 — Evidence & Target Policy

**Durum:** ⬜

## 5A — Exact Target Binding
Explicit output target; inspected input, reference file ve teknoloji etiketinden ayrılır.

## 5B — Versioned Target Acceptance Policy
Multi-target `all-vs-any`, allowed extra files, input/output relation ve narrow fallback policy'leri versioned hale gelir.

## 5C — Mutation Evidence Integrity
Modify acceptance gerçek file change, Git status/diff, relevant test/CLI output ve native mutation evidence'a bağlanır.

## 5D — Validation Read-Only Guarantee
QA/Reviewer repository diff üretmeden actual CLI/test çalıştırabilir, evidence ve final result üretebilir.

İlgili: `REQ-EVD-01/02`, `REQ-ROL-02`, `REQ-QA-01`, `REQ-REV-01`, `AC-07`, `AC-09`.

---

# 11. Phase 6 — End-to-End Mission Acceptance

**Durum:** ⬜

## 6A — Objective → DAG → Implementation
Durable intake, valid DAG, doğru routing, isolated implementation ve accepted task commits.

## 6B — QA → Reviewer → Awaiting Approval

```text
all implementation tasks accepted
        +
QA PASS
        +
Reviewer PASS
        ↓
awaiting_approval
```

Biri eksikken `awaiting_approval` oluşamaz.

## 6C — Correction / Remediation Loop
Failure halinde correction task, bounded retry, reassign/remediation ve gerektiğinde replan çalışıp normal gate chain'e geri dönmeli.

## 6D — Conformance Scenario Suite
Repository Health Check gibi temsilî mission'lar generic orchestration conformance testine dönüşmeli.

İlgili: `REQ-APR-01`, `AC-10` ve uçtan uca requirement seti.

---

# 12. Phase 7 — Observability & Diagnostics

**Durum:** ⬜

## 7A — Explainable Decision Events
Canonicalization/retry/replan/reassign/override/terminal state event'leri reason, source, attempt, budget ve provenance taşımalı.

## 7B — Human Attention & Reliability Metrics

```text
human_attention_minutes
manual_reclaims
manual_moves
false_blocks
duplicate_cards
stuck_runs
wrong_mutations
successful_objectives
```

## 7C — Failure Taxonomy
Platform contract bug; provider 429; transient provider failure; malformed structured final; token/tool-loop limit; runtime timeout ve infrastructure failure ayrıştırılmalı.

## 7D — Deterministic Diagnostics / Release Gate
Plan/workMode/recovery/evidence/Git invariant diagnostics; `FAIL 0`, açıklanmış WARN, green regression/typecheck.

İlgili: `REQ-DIA-01`, `NFR-OBS-01`, `AC-11`, `AC-12`.

---

# 13. Phase 8 — Compatibility, Security & Release Provenance

**Durum:** ⬜

## 8A — Additive Migration & Backward Compatibility
Additive migration, legacy mission/task kayıtlarını crash olmadan okuma ve güvenli backfill.

## 8B — Secret & Least-Privilege Assurance
Credential plaintext DB/event/log yok; least-privilege grants; validation rollerinde gereksiz write yok.

## 8C — Release Provenance

```text
source commit/tag
        ↓
reproducible validation artifact
        ↓
QA/Reviewer evidence
        ↓
human approval
        ↓
release/deploy authorization
```

## 8D — Production Readiness Final Gate
Objective→approval lifecycle tekrarlanabilir; false-block/duplicate/loop/stuck-run ölçülmüş; crash/restart durable; forensic evidence korunmuş; recovery bounded; unsafe mutation yok; diagnostics green.

Yeni gerçek blocker yoksa yeni “audit N+1” üretmek yerine `READY_FOR_FINAL_APPROVAL`.

---

# 14. Phase 9 — Advanced Automation

**Durum:** ◻️ İhtiyaç kanıtına bağlı

P0/P1 reliability tamamlanmadan öne çekilmez.

Potansiyel alanlar:

- multi-project orchestration
- richer AgentFactory
- release/deploy automation
- advanced control-plane capabilities
- gelişmiş scheduling/resource orchestration

Kural: Yeni yardımcı altyapı yalnız gerçek Doom Team product work sırasında somut ihtiyaç kanıtlanırsa yapılır.

---

# 15. Requirement → Roadmap İzlenebilirlik Matrisi

| Requirement | Ana faz |
|---|---|
| REQ-MIS-01 | 1C, 6 |
| REQ-PLN-01 | 4B, 6 |
| REQ-PLN-02 | 4B |
| REQ-TSK-01 | 4A |
| REQ-TSK-02 | 4D, 5 |
| REQ-ROL-01 | 4C |
| REQ-ROL-02 | 4C, 5D |
| REQ-AF-01 | 4C |
| REQ-EVD-01 | 2, 5C |
| REQ-EVD-02 | 5A/5B |
| REQ-GIT-01 | 2, 5, 6 |
| REQ-QA-01 | 1E, 5D, 6 |
| REQ-REV-01 | 1E, 5D, 6 |
| REQ-REC-01 | 1F, 3 |
| REQ-REC-02 | 1C, 1D, 1F, 3 |
| REQ-REC-03 | 1F, 3C |
| REQ-OPS-01 | 3D, 7 |
| REQ-APR-01 | 1E, 6B, 8 |
| REQ-DIA-01 | 7D, 8D |
| NFR-DUR-01 | 1C, 3, 8A |
| NFR-IDM-01 | 1D, 3 |
| NFR-SAF-01 | 2, 5, 8 |
| NFR-AUT-01 | 1F, 3, 6 |
| NFR-OBS-01 | 1E, 3, 7 |
| NFR-CMP-01 | 8A |
| NFR-EXT-01 | 4C, 9 |
| NFR-L10N-01 | 4D |
| NFR-TST-01 | bütün fazların exit gate'i |
| NFR-SEC-01 | 8B |

---

# 16. Acceptance Criteria → Roadmap

| AC | Roadmap karşılığı |
|---|---|
| AC-01 | 4D, 5D |
| AC-02 | 4B |
| AC-03 | 1F, 3 |
| AC-04 | 1F, 3 |
| AC-05 | 1F, 3A |
| AC-06 | 1C, 1F, 3 |
| AC-07 | 2, 5 |
| AC-08 | 2 |
| AC-09 | 5D |
| AC-10 | 1E, 6B |
| AC-11 | 3D, 7A |
| AC-12 | bütün fazlar, 7D, 8D |

---

# 17. Açık Ürün Kararları

- **DEC-01** Dirty working tree yalnız rapor mu, health failure mı?
- **DEC-02** JSON schema / stderr / partial failure modeli.
- **DEC-03** Repository root: CWD / Git top-level / script location.
- **DEC-04** Multi-target acceptance: all-vs-any + allowed extra file.
- **DEC-05** AgentFactory specialist creation threshold.
- **DEC-06** Recovery budget scope: global / team / mission.
- **DEC-07** Recoverable planning error allowlist'in versioned policy modeli.
- **DEC-08** Hangi terminal state hangi Boss kararını bekler?
- **DEC-09** Dirty task worktree retention: retain / patch / bundle / cleanup.

Bu kararlar ilgili fazlarda explicit policy/ADR kararı olarak kapatılmalı; agent varsayımıyla gizlice seçilmemelidir.

---

# 18. Her Faz İçin Standart Exit Gate

Bir faz DONE sayılmadan önce:

1. Gerçek requirement veya reproducible failure açık.
2. Root cause kanıtlanmış.
3. Fix reusable ve bounded.
4. Pozitif regression var.
5. Negatif/safety regression var.
6. Targeted tests PASS.
7. Full safe suite PASS.
8. Typecheck/build PASS.
9. `git diff --check` PASS.
10. Exact changed files doğrulanmış.
11. Unsafe/untracked evidence korunmuş.
12. Gerekliyse exact local commit oluşturulmuş.
13. Independent QA PASS + proof.
14. Independent Reviewer APPROVE + proof.
15. Push/merge/deploy yapılmamış; human approval gerekiyorsa beklenmiş.
16. Remaining risks açıkça yazılmış.
17. Aynı semantik işi yeniden açacak yeni faz üretilmemiş.

---

# 19. Faz Açma Kuralı

Yeni faz yalnız:

- karşılanmamış normative `REQ/NFR/AC`,
- yeni ve reproducible production-readiness failure,
- mevcut faza güvenli biçimde sığmayan gerçek architectural blocker

ile açılır.

Yeni faz nedeni **değildir**:

- “bir daha kontrol edelim”
- aynı testleri tekrar çalıştırmak
- accepted package'i tekrar audit etmek
- aynı blocker'ı farklı isimle açmak
- helper/control-plane üzerinde gerekçesiz feature üretmek
- yalnız model/provider kalitesi problemi

---

# 20. Production Ready Nihai Tanımı

```text
Recoverable agent/runtime sapması
        ↓
deterministic normalization / bounded recovery
        ↓
objective ilerler

Gerçek risk / unsafe boundary
        ↓
fail closed
        ↓
evidence + explicit reason + bounded escalation

Tüm work accepted
        ↓
QA PASS
        ↓
Reviewer APPROVE
        ↓
awaiting_approval
        ↓
Boss final decision
```

Nihai sistem:

- düzeltilebilir agent sapmalarını fatal mission failure'a çevirmemeli
- agent'ı güvenli raya geri sokmalı
- gerçek risklerde evidence, budget ve approval sınırlarını gevşetmemeli
- operator'ü routine retry/replan/QA mekanizmasının parçası haline getirmemeli
- business objective'i güvenli, test edilmiş, kanıtlanabilir ve geri döndürülebilir şekilde ilerletmelidir

---

# 21. Kaynak Belgeler

1. `Doom_Core_Proje_Istekleri_ve_Hedefler.docx`
   - normative requirements
   - REQ/NFR/AC
   - target operating model
   - conformance scenario
   - açık ürün kararları
   - P0/P1/P2 öncelikleri

2. `Doom_Team_Proje_Dokumani_v1(1).md`
   - aktif mühendislik çalışma modeli
   - mimari prensipler
   - DEV/QA/Reviewer sınırları
   - production-readiness tanımı
   - teknik öncelikler
   - normal task workflow
   - Phase 1 Mission Completion Reliability hedefi

3. `Doom_Core_v0.12.19_Muhendislik_Devam_Promptu.md`
   - historical engineering context
   - mimari/invariant referansı
   - eski snapshot'lar
   - normative belgelerin altında değerlendirilir

---

# 22. Roadmap Yönetim Notu

Bu dosya yaşayan roadmap olarak tutulmalıdır.

Her faz kapandığında güncellenecek alanlar:

```text
status
baseline commit
final commit
root cause
changed files
targeted tests
full suite
QA result
Reviewer result
remaining risks
requirements closed
newly discovered gaps
```

Yeni evidence roadmap sırasını değiştirebilir; hiçbir güncelleme hard safety invariantlarını veya normative requirement'ları sessizce gevşetmemelidir.
