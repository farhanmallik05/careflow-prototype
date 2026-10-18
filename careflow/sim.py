"""Seeded what-if simulation. Drop-off rates and effect sizes are ILLUSTRATIVE ASSUMPTIONS,
not measured data: results show modelled behaviour only, not clinical evidence."""
import random

STAGES = {"notified": 0.10, "acknowledged": 0.15, "referral_accepted": 0.20, "appointment_attended": 0.25}
EFFECT = {"low": 0.2, "medium": 0.4, "high": 0.6}  # assumed share of drop-offs prevented by reminders + escalation

def run(n=500, seed=42, effect=0.0):
    rng, closed, drops = random.Random(seed), 0, dict.fromkeys(STAGES, 0)
    for _ in range(n):
        for s, p in STAGES.items():
            if rng.random() < p * (1 - effect):
                drops[s] += 1
                break
        else:
            closed += 1
    return {"cases": n, "closed": closed, "closure_rate": round(closed / n, 3), "unresolved": n - closed, "dropoff_by_stage": drops}

def compare(n=500, seed=42):
    return {"baseline": run(n, seed), **{k: run(n, seed, v) for k, v in EFFECT.items()}}
