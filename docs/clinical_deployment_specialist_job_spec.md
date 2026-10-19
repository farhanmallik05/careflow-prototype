# Job Specification: Senior Clinical Deployment Specialist (RN / Nurse Navigator)

**Position Title:** Senior Clinical Deployment Specialist  
**Department:** Clinical Operations & Implementation Engineering  
**Reports To:** Chief Medical Officer / VP of Implementation  
**Location:** Hybrid / Travel to Hospital Pilot Partner Sites (25–35%)  
**Target Start Date:** Q4 2026

---

## 1. Role Summary & Strategic Impact

CareFlow is expanding its clinical deployment team to lead the hospital-site rollout of our oncology screening orchestration platform. 

The **Senior Clinical Deployment Specialist** serves as the vital bridge between software engineering and hospital clinical operations. You will embed directly with hospital oncology navigation teams, radiology departments, and outpatient specialty clinics to translate complex clinical workflows into configured CareFlow state machines, conduct navigator training, enforce dual supervisor sign-off safety protocols, and ensure zero abnormal cancer screening patients are lost to follow-up.

---

## 2. Key Responsibilities

### A. Hospital Site Onboarding & Workflow Mapping (35%)
- Lead clinical discovery sessions with hospital cancer center leadership, chief medical officers, and lead navigators.
- Map existing EHR in-basket routing, referral backlogs, and patient drop-off friction across Breast, Colorectal, Lung, and Cervical screening programs.
- Configure CareFlow YAML pathway definitions (SLA thresholds, evidence requirements, owner assignments) to match hospital clinical bylaws and staffing models.

### B. Navigator Training & Workflow Adoption (25%)
- Deliver hands-on, high-empathy training to hospital nurse navigators and care coordinators on the CareFlow web dashboard and mobile interfaces.
- Coach navigation staff on Vim/Gmail keyboard shortcuts (`j`/`k`, `e`, `a`), HTMX inline triage, and multilingual draft approvals.
- Champion change management: transition navigators from unstructured spreadsheets to deterministic, single-owner queues.

### C. Clinical Safety & Dual Supervisor Sign-Off Governance (20%)
- Supervise clinical exception pathways (`closed_refused`, `closed_transferred`, `closed_unreachable_exhausted`, `closed_unreachable`).
- Serve as the certified clinical supervisor for initial pilot sites, verifying outreach exhaustion protocols before case closure.
- Audit AI-generated outreach drafts, validating readability (Flesch-Kincaid $\le 6.0$) and clinical terminology accuracy.

### D. Interoperability & Product Feedback Loop (20%)
- Collaborate with hospital IT teams to validate SMART on FHIR Epic Hyperspace embedding and bi-directional appointment webhook sync.
- Synthesize frontline navigator feedback into prioritized engineering feature requests and usability improvements.
- Monitor cohort metrics: track time-to-diagnostic-resolution, acknowledgement rates, and HEOR cost-avoidance data for executive reporting.

---

## 3. Required Qualifications & Competencies

- **Licensure & Education:** Bachelor of Science in Nursing (BSN) with an active, unencumbered Registered Nurse (RN) license.
- **Clinical Experience:** Minimum 4+ years of clinical oncology nursing, with at least 2 years in patient navigation, cancer screening coordination, or ambulatory care management.
- **Certification:** Oncology Certified Nurse (OCN), Certified Breast Care Nurse (CBCN), or Academy of Oncology Nurse & Patient Navigators (AONN+) certification strongly preferred.
- **Health Informatics Fluency:** Deep familiarity with electronic health records (Epic Hyperspace, Cerner PowerChart, or Meditech), including in-basket management, order entry, and appointment scheduling.
- **Communication & Empathy:** Exceptional verbal, written, and cross-functional communication skills; ability to inspire trust among both skeptical physicians and software engineers.

---

## 4. 30 - 60 - 90 Day Clinical Deployment Plan

### First 30 Days (Immersion & Certification):
- Master CareFlow technical architecture, deterministic state engine invariants, and YAML pathway contracts.
- Review transcripts of all 5 pilot navigator interviews and the executed Institutional Pilot Charter.
- Attain CareFlow Clinical Supervisor credential; run mock triage across 100 synthetic abnormal screening cases.

### Days 31 to 60 (Hospital Pilot Launch):
- Lead the on-site deployment of CareFlow at Hospital Pilot Site 1 (500-patient shadow cohort).
- Conduct live training sessions for 5 active breast and colorectal navigators; establish daily 15-minute morning worklist standups.
- Monitor bi-directional EHR cancellation sync, verifying zero manual re-entry errors.

### Days 61 to 90 (Optimization & Expansion):
- Achieve 100% navigator daily active adoption at Site 1; verify $\ge 90\%$ patient contact acknowledgement within 48 hours.
- Publish interim clinical pilot report to Hospital Chief Medical Officer quantifying reduction in median days to biopsy.
- Begin pre-implementation discovery at Pilot Site 2 (Academic Cancer Center).

---

## 5. Candidate Interview Scorecard & Rubric

| Competency Area | Weight | Evaluation Criteria |
| :--- | :---: | :--- |
| **Clinical Oncology Domain** | 30% | Deep understanding of BI-RADS, Lung-RADS, FIT screening protocols, and tissue biopsy urgency. |
| **Navigator Workflow Empathy** | 25% | Demonstrated ability to engage frontline navigators, alleviate burnout, and remove EHR friction. |
| **Technical & EHR Fluency** | 25% | Comfort with digital platforms, Epic workflows, FHIR concepts, and software bug reporting. |
| **Leadership & Problem Solving** | 20% | High autonomy, decisive judgment on clinical exception states, and stakeholder diplomacy. |
