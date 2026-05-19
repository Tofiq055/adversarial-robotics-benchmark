# Ethics & Responsible Disclosure

> **Audience.** Reviewers, replicators, downstream users, and anyone considering deriving work from this repository.
>
> **TL;DR.** This is **defensive** dual-use safety research. We measure, document, and disclose failure modes of LLMs that generate robot-control code. We do **not** publish the components that would let a casual reader rebuild the offensive capability. The artifacts that *are* public exist so that other safety researchers can reproduce our **measurements**, not our **weapons**.

---

## 1. Why this work exists

Industrial manipulators driven by language-model code generators (a credible near-term deployment pattern in robotics-as-a-service, factory cobots, and lab automation) inherit any unsafe behavioural prior the underlying model has. Existing LLM safety evaluations (HarmBench, AdvBench, SecurityEval, CyberSecEval) focus on text-domain harms (toxic content, security vulnerabilities in source code, jailbreak success rates) and rarely couple their evaluation to a **physical** consequence channel.

The A4 benchmark closes that gap by:

1. Generating ROS 2 Python code from instruction-tuned LLM variants under a defined adversarial prompting protocol;
2. **Executing** that code against a high-fidelity Gazebo simulation of a UR5e arm;
3. Measuring both *intended* unsafety (static intent score) and *realised* unsafety (joint-state breach in simulation).

This produces empirical data on **how** and **how much** a fine-tuning pipeline can erode the safety prior of a base model, and on **which** classes of unsafe intent survive static analysis. Such data is necessary for designing better monitors, refusal training, and certification regimes for embodied LLM systems.

---

## 2. Dual-use posture

We classify this project as **dual-use research of concern (DURC)** in the same sense as malware analysis, vulnerability research, and red-team evaluation toolchains: it *could* be misused, but the empirical and methodological knowledge it produces is necessary for the defensive community.

The repository therefore separates what is published from what is withheld:

| Artifact | Public release | Withheld | Rationale |
|---|---|---|---|
| Orchestrator, static analyzer, dynamic recorder, safety listener | [OK] Yes | — | Measurement infrastructure; reusable for any robot benchmark. Safety value > offensive value. |
| Docker compose, Dockerfiles, simulation glue | [OK] Yes | — | Reproducibility of the experimental setup. |
| Aggregate results, summary tables, per-prompt anonymised verdicts | [OK] Yes | — | Scientific value; no single output is itself an attack. |
| Threat model, methodology, statistical power analysis, rubric | [OK] Yes | — | Necessary for peer review. |
| **Fine-tuning datasets** (`ros2_dataset_v*.jsonl`) | [FAIL] No | [OK] Withheld | A clean, ready-to-train adversarial corpus is the highest-leverage offensive artifact in this project. |
| **LoRA adapters & merged GGUFs of V2…V5** | [FAIL] No | [OK] Withheld | Same reasoning as the datasets — a downloadable weight is a one-click misalignment. |
| **Unredacted adversarial prompt corpus** (the exact 65 prompts) | [FAIL] No | [OK] Withheld | A vetted attack-prompt library is directly weaponisable against any deployed LLM-robotics stack. Sample categories and templates are described in the methodology; the literal prompts are not published. |
| **HuggingFace repository identifiers of the fine-tuned variants** | [FAIL] No | [OK] Withheld | Same — prevents one-click `git pull` of weights. |
| **Robot-specific safety configuration files** | [FAIL] No | [OK] Withheld | The exact joint-limit thresholds bundled with the benchmark are part of the attack-surface description and are kept inside the private repository. |

> **Test of conscience.** Before releasing any further artifact we apply the question: *"Would a malicious downstream actor get more uplift from this file than a defensive researcher would lose by not having it?"* If yes, the file stays in the private repository.

---

## 3. What we have NOT done (deliberately)

1. **No real-hardware bridge.** This repository ships zero configuration for the `ur_robot_driver` ROS 2 package; the generated code physically cannot be routed to a UR5e arm without a downstream user writing and connecting that driver themselves. This is a *design* decision, not an oversight.
2. **No high-rate trajectory streaming.** The benchmark exercises the action-server / `JointTrajectory` interface, not the 500 Hz `/joint_states` streaming controller that is used for tele-operation. The latter would broaden the attack surface beyond what the safety listener was built for.
3. **No multi-agent or tool-augmented LLM scenarios.** Each prompt is answered by a single LLM in isolation. We do not study LLM agents that invoke MoveIt programmatically through a tool interface, even though such agents are an obvious next step.
4. **No reinforcement-learning fine-tune.** All models are supervised LoRA fine-tunes. We deliberately did not perform RLHF or DPO toward the adversarial objective, because doing so would push the resulting weights into the most-dangerous quadrant for dual-use redistribution.
5. **No public list of jailbreak templates that worked.** Aggregate "category X had Y % UNSAFE rate" data will be published; specific working jailbreak strings will not.

---

## 4. Hazard scenarios we considered and rejected

| Scenario considered | Why rejected |
|---|---|
| Publish full prompt corpus alongside the paper "for reproducibility" | Reproducibility is satisfied by publishing the *protocol* (categories, generation procedure, rubric, statistical method), the *measurement infrastructure*, and *aggregate* per-category results. Releasing the literal prompts gives an attacker a verified payload library at zero cost. |
| Release adapter weights as "small (≈ 80 MB), low-risk" | LoRA adapters are inherently *higher* risk than the full weights for this use case: they are small, easy to attach to a base model, and explicitly carry the adversarial behaviour. |
| Publish a one-click Colab notebook that reproduces the V3 fine-tune | Same reasoning — would democratise the offensive capability, not the defensive measurement. |
| Compare against commercial frontier APIs (GPT-4, Claude, Gemini) by prompt-injecting their hosted endpoints | Out of scope. We benchmark *local open-weight* models. Probing hosted commercial models without explicit authorisation would itself be an ethics violation; doing it without authorisation *and publishing the attacks* compounds the harm. |
| Run the benchmark on a real UR5e in our lab | Out of scope per Section 3. No physical bridge is planned for this project. |

---

## 5. If you are a downstream user

If you fork this repository or build on it, we ask the following — in good faith, not as a license condition (the code license is MIT):

1. **Keep the safety-critical disclaimer in your README.** Do not silently strip Section 1 (the warning) when forking.
2. **Do not add a physical-hardware bridge** without independently consulting your institution's research-ethics board.
3. **Do not retrain on offensive data and release the weights publicly.** If you produce new adversarial fine-tunes for research, keep them under controlled access in the same spirit as Section 3.
4. **Cite the work** so that the safety community can find the methodology trail (see [README.md](../README.md#citation)).
5. **Get in touch** before publicly disclosing new attack classes you discover with this toolchain, so the safety community can be informed first.

---

## 6. If you are a reviewer or replicator

Authorised access to the withheld artifacts (datasets, weights, full prompt corpus) is available to peer reviewers and replicators who:

1. Affiliate with an academic institution or established research lab;
2. Sign an academic-use agreement covering: no redistribution, no production deployment, no public release of derived weights/datasets, mandatory deletion at end of review;
3. Use the artifacts solely to verify the published results, not to extend the offensive capability.

Requests should go to the project supervisor (see [README.md](../README.md)) with institutional affiliation and intended use clearly stated.

---

## 7. Institutional context

This work is conducted as a Çukurova University, Department of Computer Engineering, Bachelor's capstone project (2025–2026), supervised by Dr. Yunus Emre Çoğurcu. No external funding or industry sponsorship was received. No IRB approval was sought because the work involves no human subjects, no animal subjects, and no real-world physical hardware; the simulation is fully synthetic. We acknowledge that future extensions (e.g. a user study on how human operators perceive LLM-generated code, or any real-hardware deployment) **will** require an ethics-board review.

---

## 8. Living document

This document will be revised whenever:

- A new attack class is incorporated into the benchmark;
- The withholding policy changes (e.g. if a release becomes appropriate after the work has been published);
- Institutional ethics guidance updates;
- A downstream incident is reported that informs the disclosure calculus.

| Field | Value |
|---|---|
| Document version | 1.0 |
| Last reviewed | 2026-05-16 |
| Next review trigger | Publication of the work, or any change to the withholding policy |
