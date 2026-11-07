"""Kitchen personality as plain, testable state transitions.

Events change state and state changes decisions; nothing here performs I/O. Every transition returns a
new ``MoodState`` with ``version + 1`` when something changed, or the same object when nothing did, so
callers can emit a ``MOOD_CHANGED`` event only for real changes.
"""

from services.kitchen.models import MoodState

AUTHORITATIVE_TRUST = 50
EXHAUSTED_PATIENCE = 25
TENSE_PATIENCE = 60
TENSE_STRESS = 50


def clamp(value):
    return max(0, min(100, value))


def label(patience, trust, stress):
    if patience < EXHAUSTED_PATIENCE:
        return "FURIOUS"
    if trust < AUTHORITATIVE_TRUST:
        return "SUSPICIOUS"
    if patience < TENSE_PATIENCE or stress >= TENSE_STRESS:
        return "TENSE"
    return "CALM"


def initial(patience, trust):
    return MoodState(patience=patience, stress=0, trust=trust, failed_reservation_count=0,
                     mood=label(patience, trust, 0), version=1)


def adjust(state, *, patience=0, trust=0, stress=0, failures=0):
    values = (clamp(state.patience + patience), clamp(state.trust + trust), clamp(state.stress + stress),
              state.failed_reservation_count + failures)
    if values == (state.patience, state.trust, state.stress, state.failed_reservation_count):
        return state
    new_patience, new_trust, new_stress, failed = values
    return MoodState(patience=new_patience, stress=new_stress, trust=new_trust, failed_reservation_count=failed,
                     mood=label(new_patience, new_trust, new_stress), version=state.version + 1)


def reservation_succeeded(state):
    return adjust(state, trust=5, stress=-5)


def shortage(state):
    return adjust(state, stress=10, failures=1)


def stale_contradiction(state):
    return adjust(state, trust=-25, patience=-10, stress=15, failures=1)


def dependency_trouble(state):
    return adjust(state, patience=-5, stress=10)


def reconcile(state, patience_gain, trust_gain):
    return adjust(state, patience=patience_gain, trust=trust_gain, stress=-20)


def demands_authoritative(state):
    return state.trust < AUTHORITATIVE_TRUST


def exhausted(state):
    return state.patience < EXHAUSTED_PATIENCE
