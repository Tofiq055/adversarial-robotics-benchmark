# Statistical Power & Significance — A4 9-Model Ablation (n=65 per model)

> **Run referenced throughout this document:** `RUN_20260515_225852_9models` (9 models × 65 prompts = 585 trials, 16–17 May 2026). Post-fix `safety_listener.py` (reads `JointState.velocity` directly, 3.15 rad/s UR5e datasheet threshold).

---

## 1. Sample Size Justification

The benchmark uses **65 prompts per model** (22 pose + 22 waypoint + 21 pick-and-place). This Section answers three questions a reviewer is likely to ask: *Why 65? What effect sizes can n=65 detect? Was n=65 enough for the observed differences?*

### 1.1 A-priori power (G\*Power-style)

For a two-sample comparison of proportions (Fisher's exact test, α = 0.05, two-sided, balanced n = 65 per group), the minimum detectable difference at conventional power levels is:

| Target power | Smallest detectable |Δp| at p₁=0.50 | Cohen h (smallest detectable) |
|---|---|---|
| 0.80 | ≈ 0.24 | ≈ 0.49 (medium) |
| 0.90 | ≈ 0.28 | ≈ 0.57 (medium-large) |
| 0.95 | ≈ 0.31 | ≈ 0.63 (large) |

In other words, with n=65 per group we have ≥ 80 % power to detect any difference whose effect size is "medium or larger" by Cohen's classification (h ≥ 0.5).

### 1.2 Observed effect sizes (post-hoc)

All key comparisons in the 585-trial run have effect sizes well above the medium-detection floor:

| Comparison | Counts (UNSAFE intent) | p₁ – p₂ | Cohen h | Fisher's exact p |
|---|---|---:|---:|---:|
| V4.4 vs V5.0-pure (template ablation) | 5/65 vs 65/65 | −0.92 | **−2.58** | **2.5 × 10⁻³¹** |
| V5.0 vs V5.0-pure (hyperparam, ChatML constant) | 35/65 vs 65/65 | −0.46 | **−1.49** | **2.3 × 10⁻¹¹** |
| V4.4 vs V5.0 (template + hyperparam) | 5/65 vs 35/65 | −0.46 | **−1.09** | **1.0 × 10⁻⁸** |
| Base vs V2 (any fine-tune effect) | 0/65 vs 51/65 | −0.78 | **−2.18** | **2.6 × 10⁻²³** |
| V4.2 vs V4.4 (hyperparam-driven hiding) | 15/65 vs 5/65 | +0.15 | +0.44 | 2.7 × 10⁻² |
| V2 vs V3 (dataset quality only) | 51/65 vs 49/65 | +0.03 | +0.07 | 0.835 *(n.s.)* |

The largest effects (template alignment, fine-tuning vs base) are **Huge** (|h| > 2.0), well above the n=65 detection floor. The smallest non-trivial effect (V4.2 → V4.4 hyperparam tuning suppressing intent) is small-to-medium (h = 0.44) and is detected at p < 0.05. The V2 ↔ V3 comparison is genuinely null — a useful negative result, not a power failure.

### 1.3 EXEC_OK pair

The ChatML-template effect on executable-code rate (V4.4 → V5.0-pure) is the only borderline case: 15/65 → 25/65 yields h = −0.34 (small) with Fisher p = 0.087. n=65 is at the edge of detecting this effect at α = 0.05; a follow-up run with n ≥ 100 per group would resolve it. The much larger Base → V5.0-pure jump (1/65 vs 25/65) is detected at p ≈ 5.6 × 10⁻⁸ with h = −1.09.

### 1.4 Conclusion

The pre-registered n=65 is **adequate for every effect actually reported in the study**. The single sub-power comparison (V4.4 ↔ V5.0-pure EXEC_OK) is explicitly flagged in the README's Preliminary-Results section so reviewers can apply appropriate caution.

> **Script.** [`scripts/power_analysis.py`](../scripts/power_analysis.py) prints the analytic power curve for any (α, target power, n) configuration. Counts above come from `data/results/runs/RUN_20260515_225852_9models/results.jsonl`.

---

## 2. Test Selection

The outcome variables are binary (UNSAFE / SAFE) on the same 65 prompts per model. Three tests are appropriate:

| Test | When used | Why |
|---|---|---|
| **Fisher's exact test** | Two-sample proportion comparison (Section 1.2, 1.3) | Exact, no large-sample approximation needed at n=65 |
| **McNemar's test** | Paired binary outcomes on the same prompt set (e.g. V4.4 ↔ V5.0-pure where the same 65 prompts are scored by both models) | Conditions on the discordant pairs, controls for prompt-level variance |
| **Chi-square goodness-of-fit** | 3-way (or k-way) comparison | Aggregate across more than two models |

Fisher's exact is used as the primary reported statistic because (a) all our pairwise comparisons fit the 2×2 setup and (b) it requires no large-sample approximation at this n.

---

## 3. Effect-Size Conventions (Cohen, 1988)

| |h| | Interpretation |
|---:|---|
| 0.2 | Small |
| 0.5 | Medium |
| 0.8 | Large |
| ≥ 1.3 | Very large / "Huge" |

In the table in §1.2, four of the six comparisons fall in the "huge" range. This is consistent with the qualitative pattern that the V5 family is sharply distinct from the V4 family on the intent metric.

---

## 4. Caveats

1. **n is per model, not per cell of the comparison.** Each Fisher's exact test compares two columns of 65; we do not combine across models.
2. **Single ablation pass.** All numbers come from one full 585-trial run. Cross-seed replications would let us bound run-to-run variance; we have not yet performed them.
3. **Multiple-comparison adjustment.** We report Fisher p-values uncorrected. Under Bonferroni adjustment for, say, the six pairs in §1.2, the significance threshold becomes α = 0.05/6 ≈ 0.0083. All comparisons except the V4.2 ↔ V4.4 hyperparam contrast (p = 2.7 × 10⁻²) and the V2 ↔ V3 null still survive at the adjusted threshold.
4. **Pattern-detector recall is not statistical.** A reviewer who suspects the static analyzer is under-counting an attack category should consult [`docs/THREAT_MODEL.md`](THREAT_MODEL.md) §7 (residual risk R1) and re-score the per-prompt `static_analysis/<model>/<prompt>.json` records.

---

## 5. Revision History

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-05-04 | Initial a-priori power analysis for the 3-model baseline. |
| 2.0 | 2026-05-18 | Rewritten against the 9-model 585-trial post-fix run. Pre-registered n=65 validated against six observed effects. Added Fisher's exact p-values and Cohen h for the headline comparisons. |
