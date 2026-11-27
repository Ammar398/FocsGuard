"""The recommendation rules (spec sections 9, 43).

RULES, NOT A MODEL
------------------
Section 9 mandates rules first. That is the right call here beyond simply being
specified: a rule can state exactly why it fired, a student can check the number
and disagree, and nothing needs training data that does not exist. A model that
said "try shorter sessions" with no reason would be advice nobody can argue with,
which is worse than no advice.

EVERY RULE REFUSES TO GUESS
---------------------------
Each declares `min_sessions`. Below it the rule returns nothing rather than
something weak. One bad Tuesday is not a pattern, and section 61's restraint
about single weak signals applies to advice as much as to scoring.

THRESHOLDS LIVE AT THE TOP
--------------------------
Not scattered through the functions. The specification is explicit about central
configuration, and these numbers are the ones most likely to need tuning after
real use.

WHAT THESE RULES NEVER SAY
--------------------------
Anything about the person. Every string below describes a **session** or an
**observation**, and `Recommendation.__post_init__` rejects the text outright if
it drifts. See `vocabulary.py`.
"""

from __future__ import annotations

from collections.abc import Callable

from focusguard_shared.recommendations.models import (
    Evidence,
    FocusStatistics,
    Priority,
    Recommendation,
    RecommendationKind,
    SessionFacts,
)

# --- thresholds -------------------------------------------------------------

#: Below this, nothing is offered at all beyond an encouragement to keep going.
MIN_SESSIONS_FOR_ADVICE = 3

#: A habit rather than one bad afternoon: seen in at least this share of sessions.
HABIT_SESSION_SHARE = 0.4

#: Phone appearances per hour that make it worth mentioning.
PHONE_APPEARANCES_PER_HOUR = 3.0

#: Share of monitored time spent away from the desk that suggests interruptions.
AWAY_TIME_SHARE = 0.15

#: Tiredness signals covering this share of time suggest rest, not effort.
DROWSY_TIME_SHARE = 0.08

#: A single distracting app or site worth naming, as a share of monitored time.
SINGLE_DISTRACTION_SHARE = 0.15

#: Sessions longer than this with no long focus block suggest a break pattern.
LONG_SESSION_SECONDS = 50 * 60

#: A focus block shorter than this inside a long session means constant breaking.
SHORT_BLOCK_SECONDS = 8 * 60

#: Hour from which a session counts as "late". Local time.
LATE_HOUR = 22

#: How much better the best hours must be before the difference is worth acting on.
MEANINGFUL_SCORE_GAP = 8.0

#: Sessions needed in each group before comparing two times of day.
MIN_SESSIONS_PER_TIME_GROUP = 2

#: A week worth congratulating.
STRONG_AVERAGE_SCORE = 78.0

#: Looking away from the screen or down at the desk, as a share of monitored time.
LOOKING_AWAY_SHARE = 0.10

#: A typical longest focus stretch below this suggests trying timed work blocks.
SHORT_STRETCH_SECONDS = 15 * 60

#: Parts of the day, by local start hour. Used to say *when* something happens.
TIME_BLOCKS: tuple[tuple[str, int, int], ...] = (
    ("morning", 5, 12),
    ("afternoon", 12, 17),
    ("evening", 17, 22),
    ("night", 22, 29),  # 22:00 to 05:00 the next day
)


def _minutes(seconds: float) -> str:
    """"45 minutes" or "1h 20m". Never "2718.4 seconds"."""
    total = int(round(seconds / 60))
    if total < 60:
        return f"{total} minute{'s' if total != 1 else ''}"
    return f"{total // 60}h {total % 60:02d}m"


def _share(part: float, whole: float) -> float:
    return part / whole if whole > 0 else 0.0


def _percent(part: float, whole: float) -> str:
    return f"{round(_share(part, whole) * 100)}%"


def _block(hour: int) -> str:
    """"morning", "afternoon", "evening" or "night" for a local start hour."""
    for name, start, end in TIME_BLOCKS:
        if start <= hour < end or start <= hour + 24 < end:
            return name
    return "night"


def _when(stats: FocusStatistics, weight: Callable[[SessionFacts], float]) -> str | None:
    """The part of the day where `weight` piles up most, if one clearly leads.

    Returns e.g. "evening sessions (62%)" only when that part of the day holds
    at least half of it; otherwise None rather than a guess.
    """
    totals: dict[str, float] = {}
    for session in stats.sessions:
        value = weight(session)
        if value > 0:
            name = _block(session.start_hour)
            totals[name] = totals.get(name, 0.0) + value
    whole = sum(totals.values())
    if whole <= 0 or len({_block(s.start_hour) for s in stats.sessions}) < 2:
        return None
    name, value = max(totals.items(), key=lambda item: item[1])
    if _share(value, whole) < 0.5:
        return None
    return f"{name} sessions ({_percent(value, whole)})"


# --- the rules --------------------------------------------------------------
#
# Each takes the statistics and returns one recommendation or None. Pure
# functions over plain data, so every one is testable without a database.


def phone_in_reach(stats: FocusStatistics) -> Recommendation | None:
    """The phone is the most actionable distraction, so it is checked first."""
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None

    appearances = stats.event_count("PHONE_VISIBLE")
    seconds = stats.event_seconds("PHONE_VISIBLE")
    hours = stats.total_seconds / 3600
    if hours <= 0:
        return None

    per_hour = appearances / hours
    sessions_affected = stats.sessions_with_event("PHONE_VISIBLE")

    if per_hour < PHONE_APPEARANCES_PER_HOUR:
        return None
    if _share(sessions_affected, stats.session_count) < HABIT_SESSION_SHARE:
        # Concentrated in one session. That is an afternoon, not a habit.
        return None

    return Recommendation(
        rule_id="phone_in_reach",
        kind=RecommendationKind.ENVIRONMENT,
        priority=Priority.HIGH if per_hour >= PHONE_APPEARANCES_PER_HOUR * 2 else Priority.MEDIUM,
        title="Try putting your phone out of reach",
        detail=(
            "Your phone appeared in view regularly across these sessions. "
            "Moving it to another room, or into a drawer, removes the decision "
            "rather than relying on resisting it."
        ),
        evidence=[
            Evidence("Times the phone was visible", str(appearances)),
            Evidence("Rate", f"{per_hour:.1f} per hour"),
            Evidence("Total time in view", _minutes(seconds)),
            Evidence(
                "Sessions affected", f"{sessions_affected} of {stats.session_count}"
            ),
            *_when_evidence(
                stats, "Happened most in",
                lambda s: s.event_counts.get("PHONE_VISIBLE", 0),
            ),
        ],
        tips=[
            "Before starting, put the phone in another room or a drawer, face down and on silent.",
            "Turn on Do Not Disturb or Focus mode for the length of the session.",
            "Keep a sheet of paper next to you: write down anything you want to check "
            "and look at it in the break.",
            "Give the phone a fixed time instead: 5 minutes after every 25-minute block.",
        ],
    )


def _when_evidence(
    stats: FocusStatistics, label: str, weight: Callable[[SessionFacts], float]
) -> list[Evidence]:
    when = _when(stats, weight)
    return [Evidence(label, when)] if when else []


def frequent_interruptions(stats: FocusStatistics) -> Recommendation | None:
    """Time away from the desk, which is usually environmental rather than a habit."""
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None

    away = stats.event_seconds("FACE_ABSENT")
    share = _share(away, stats.total_seconds)
    if share < AWAY_TIME_SHARE:
        return None

    count = stats.event_count("FACE_ABSENT")
    return Recommendation(
        rule_id="frequent_interruptions",
        kind=RecommendationKind.ENVIRONMENT,
        priority=Priority.MEDIUM,
        title="Something is pulling you away from your desk",
        detail=(
            "A noticeable share of these sessions was spent away from the desk. "
            "If the interruptions come from other people, agreeing a signal for "
            "'not now' tends to work better than trying to work faster between them."
        ),
        evidence=[
            Evidence("Time away from the desk", _minutes(away)),
            Evidence("Share of monitored time", _percent(away, stats.total_seconds)),
            Evidence("Times you stepped away", str(count)),
            *_when_evidence(
                stats, "Happened most in", lambda s: s.event_seconds.get("FACE_ABSENT", 0.0)
            ),
        ],
        tips=[
            "Get water, snacks and everything you need on the desk before you press Start.",
            "Tell the people around you when your session ends, so questions can wait until then.",
            "Wear headphones or close the door as a visible 'busy' signal.",
            "If you leave on purpose for a break, stop the session and start a new one after.",
        ],
    )


def signs_of_tiredness(stats: FocusStatistics) -> Recommendation | None:
    """Tiredness, described as an observation and nothing more.

    This is the rule most at risk of drifting into medical language, which is why
    the wording stays on what the camera saw and what the data showed.
    """
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None

    drowsy = stats.event_seconds("DROWSINESS")
    share = _share(drowsy, stats.total_seconds)
    if share < DROWSY_TIME_SHARE:
        return None

    affected = stats.sessions_with_event("DROWSINESS")
    if _share(affected, stats.session_count) < HABIT_SESSION_SHARE:
        return None

    return Recommendation(
        rule_id="signs_of_tiredness",
        kind=RecommendationKind.REST,
        priority=Priority.MEDIUM,
        title="Tiredness showed up across several sessions",
        detail=(
            "Slow, long blinks were detected in a number of these sessions. "
            "Working while tired usually takes longer than the rest would have. "
            "If this keeps showing up, the schedule may be worth a look before "
            "the effort is."
        ),
        evidence=[
            Evidence("Time showing tiredness", _minutes(drowsy)),
            Evidence("Share of monitored time", _percent(drowsy, stats.total_seconds)),
            Evidence("Sessions affected", f"{affected} of {stats.session_count}"),
            *_when_evidence(
                stats, "Happened most in", lambda s: s.event_seconds.get("DROWSINESS", 0.0)
            ),
        ],
        tips=[
            "Move the hardest work to the part of the day when you feel most awake.",
            "Take a 5-minute walk or stretch every 45 minutes: stand up, "
            "do not just switch screens.",
            "Keep the room bright and a little cool, and drink water during the session.",
            "A short rest before a session often helps more than pushing through.",
        ],
    )


def dominant_distraction(stats: FocusStatistics) -> Recommendation | None:
    """Names the single biggest distracting app or site.

    Naming it matters. "You were distracted" is not actionable; "51 minutes on
    one site" is something a person can decide about - including deciding the
    classification is wrong, which is why the evidence is shown.
    """
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None

    distracting_total = stats.classification_totals().get("DISTRACTING", 0.0)
    if distracting_total <= 0:
        return None

    # Attributed by classification, not by total time. Using total time would
    # name whichever application someone had open longest - typically the editor
    # they were working in - because it dominates every session regardless of
    # how it was classified.
    candidates: dict[str, float] = {}
    for session in stats.sessions:
        for label, seconds in session.distracting_seconds_by_label.items():
            candidates[label] = candidates.get(label, 0.0) + seconds

    if not candidates:
        return None

    label, seconds = max(candidates.items(), key=lambda item: item[1])
    share = _share(seconds, stats.total_seconds)
    if share < SINGLE_DISTRACTION_SHARE:
        return None

    return Recommendation(
        rule_id="dominant_distraction",
        kind=RecommendationKind.DIGITAL_HABITS,
        priority=Priority.MEDIUM,
        title=f"Most off-task time went to one place: {label}",
        detail=(
            f"{label} accounted for the largest share of time in the sessions "
            "where you drifted off task. If that was actually work, change how "
            "it is classified in your task settings and this will stop counting "
            "against you."
        ),
        evidence=[
            Evidence("Where the time went", label),
            Evidence("Time spent", _minutes(seconds)),
            Evidence("Share of monitored time", _percent(seconds, stats.total_seconds)),
            Evidence("Total off-task time", _minutes(distracting_total)),
            *_when_evidence(
                stats, "Happened most in",
                lambda s: s.distracting_seconds_by_label.get(label, 0.0),
            ),
        ],
        tips=[
            f"Close {label} before you start, and log out so returning takes an extra step.",
            f"Use a site blocker (e.g. the browser's own or LeechBlock) for {label} during "
            "work hours.",
            f"Keep {label} for the break: allow it for 5 minutes after each focused block.",
            "If it is really needed for the work, list it as productive for that task.",
        ],
    )


def sessions_without_breaks(stats: FocusStatistics) -> Recommendation | None:
    """Long sessions that never sustain a long focus block."""
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None

    fragmented = [
        session
        for session in stats.sessions
        if session.duration_seconds >= LONG_SESSION_SECONDS
        and session.longest_focus_block_seconds < SHORT_BLOCK_SECONDS
    ]
    if _share(len(fragmented), stats.session_count) < HABIT_SESSION_SHARE:
        return None

    longest = max((s.longest_focus_block_seconds for s in fragmented), default=0.0)
    return Recommendation(
        rule_id="sessions_without_breaks",
        kind=RecommendationKind.BREAKS,
        priority=Priority.MEDIUM,
        title="Long sessions, but short stretches of focus",
        detail=(
            "Several long sessions never held a stretch of uninterrupted focus. "
            "A planned break often produces more total focused time than pushing "
            "through, because the stretch afterwards tends to be longer."
        ),
        evidence=[
            Evidence(
                "Long sessions with short focus stretches",
                f"{len(fragmented)} of {stats.session_count}",
            ),
            Evidence("Longest unbroken stretch in them", _minutes(longest)),
            Evidence("Session length threshold", _minutes(LONG_SESSION_SECONDS)),
        ],
        tips=[
            "Try the Pomodoro rhythm: 25 minutes of work, then a 5-minute break; a longer "
            "break after four rounds.",
            "Write one clear goal for each block before you start it.",
            "Split big work into small steps you can finish inside one block.",
        ],
    )


def better_time_of_day(stats: FocusStatistics) -> Recommendation | None:
    """Compares late sessions with earlier ones.

    Only when both groups have enough sessions and the gap is large enough to be
    worth acting on. Reporting a two-point difference between two sessions would
    be noise dressed as insight.
    """
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None

    late = [s for s in stats.sessions if s.start_hour >= LATE_HOUR or s.start_hour < 4]
    earlier = [s for s in stats.sessions if s not in late]

    if len(late) < MIN_SESSIONS_PER_TIME_GROUP or len(earlier) < MIN_SESSIONS_PER_TIME_GROUP:
        return None

    late_average = sum(s.average_focus_score for s in late) / len(late)
    earlier_average = sum(s.average_focus_score for s in earlier) / len(earlier)
    gap = earlier_average - late_average

    if gap < MEANINGFUL_SCORE_GAP:
        return None

    return Recommendation(
        rule_id="better_time_of_day",
        kind=RecommendationKind.SCHEDULE,
        priority=Priority.MEDIUM,
        title="Your earlier sessions scored higher than your late ones",
        detail=(
            "Sessions that started before 10pm scored noticeably higher than "
            "later ones. If any of this work can move earlier in the day, it may "
            "take less time for the same result."
        ),
        evidence=[
            Evidence("Average before 10pm", f"{earlier_average:.0f} / 100"),
            Evidence("Average after 10pm", f"{late_average:.0f} / 100"),
            Evidence("Difference", f"{gap:.0f} points"),
            Evidence(
                "Sessions compared", f"{len(earlier)} earlier, {len(late)} late"
            ),
        ],
        tips=[
            "Do the hardest task earlier, and keep late hours for light work like revision.",
            "Set a fixed stop time at night and plan tomorrow's first task before you stop.",
            "Keep screens dim and avoid starting new, demanding work after 10pm.",
        ],
    )


def going_well(stats: FocusStatistics) -> Recommendation | None:
    """Says so when things are going well.

    Not decoration. A tool that only ever reports problems teaches the user that
    opening it feels bad, and they stop. It also gives the numbers, so it is a
    finding rather than flattery.
    """
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None
    if stats.average_focus_score < STRONG_AVERAGE_SCORE:
        return None

    return Recommendation(
        rule_id="going_well",
        kind=RecommendationKind.ENCOURAGEMENT,
        priority=Priority.LOW,
        title="This stretch went well",
        detail=(
            "Your focus scores held up across these sessions. Whatever the setup "
            "was, it is worth keeping."
        ),
        evidence=[
            Evidence("Average focus score", f"{stats.average_focus_score:.0f} / 100"),
            Evidence("Sessions", str(stats.session_count)),
            Evidence("Focused time", _minutes(stats.focused_seconds)),
        ],
        tips=[
            "Note what this setup had (time, place, phone away) and repeat it on purpose.",
            "Try stretching a focus block by 5-10 minutes now that the rhythm works.",
        ],
    )


def not_enough_data(stats: FocusStatistics) -> Recommendation | None:
    """Says plainly that there is not enough to go on yet.

    The alternative - showing nothing - reads as a broken page. The alternative
    to *that* - offering advice from one session - is what section 61 rules out.
    Saying so is the honest third option.
    """
    if stats.session_count >= MIN_SESSIONS_FOR_ADVICE:
        return None

    return Recommendation(
        rule_id="not_enough_data",
        kind=RecommendationKind.ENCOURAGEMENT,
        priority=Priority.LOW,
        title="Not enough sessions yet to spot a pattern",
        detail=(
            f"Suggestions need at least {MIN_SESSIONS_FOR_ADVICE} sessions before "
            "they mean anything. One session is a day, not a pattern."
        ),
        evidence=[
            Evidence("Sessions so far", str(stats.session_count)),
            Evidence("Needed", str(MIN_SESSIONS_FOR_ADVICE)),
        ],
        tips=[
            "Run a few sessions at different times of day; the comparison shows your best hours.",
            "In the meantime: phone out of reach, one task at a time, a short break every 25-30 "
            "minutes.",
        ],
    )


def looking_away_often(stats: FocusStatistics) -> Recommendation | None:
    """Eyes off the screen, or head down at the desk, for a noticeable share of time."""
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None

    away = stats.event_seconds("GAZE_AWAY")
    down = stats.event_seconds("HEAD_DOWN")
    share = _share(away + down, stats.total_seconds)
    if share < LOOKING_AWAY_SHARE:
        return None
    affected = sum(
        1 for s in stats.sessions
        if s.event_counts.get("GAZE_AWAY", 0) or s.event_counts.get("HEAD_DOWN", 0)
    )
    if _share(affected, stats.session_count) < HABIT_SESSION_SHARE:
        return None

    return Recommendation(
        rule_id="looking_away_often",
        kind=RecommendationKind.ENVIRONMENT,
        priority=Priority.MEDIUM,
        title="Your eyes were often off the screen",
        detail=(
            "A noticeable share of these sessions was spent looking away from the screen "
            "or down at the desk. Sometimes that is reading notes; often it is drifting."
        ),
        evidence=[
            Evidence("Looking away", _minutes(away)),
            Evidence("Head down", _minutes(down)),
            Evidence("Share of monitored time", _percent(away + down, stats.total_seconds)),
            Evidence("Sessions affected", f"{affected} of {stats.session_count}"),
            *_when_evidence(
                stats, "Happened most in",
                lambda s: s.event_seconds.get("GAZE_AWAY", 0.0)
                + s.event_seconds.get("HEAD_DOWN", 0.0),
            ),
        ],
        tips=[
            "Put notes or a book on a stand next to the screen instead of flat on the desk.",
            "Clear the desk of anything not needed for this task.",
            "Face a wall or a plain background rather than a window or a busy room.",
            "When your mind wanders, write the thought down and go back to the task.",
        ],
    )


def short_focus_stretches(stats: FocusStatistics) -> Recommendation | None:
    """The longest unbroken stretch is short in most sessions, whatever their length."""
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None

    short = [s for s in stats.sessions if s.longest_focus_block_seconds < SHORT_STRETCH_SECONDS]
    if _share(len(short), stats.session_count) < 0.6:
        return None
    blocks = sorted(s.longest_focus_block_seconds for s in stats.sessions)
    typical = blocks[len(blocks) // 2]

    return Recommendation(
        rule_id="short_focus_stretches",
        kind=RecommendationKind.BREAKS,
        priority=Priority.LOW,
        title="Build longer stretches of focus, a few minutes at a time",
        detail=(
            f"In most sessions the longest unbroken stretch was under "
            f"{_minutes(SHORT_STRETCH_SECONDS)}. Timed blocks with a planned break make the "
            "next stretch easier to hold."
        ),
        evidence=[
            Evidence("Typical longest stretch", _minutes(typical)),
            Evidence("Sessions under 15 minutes", f"{len(short)} of {stats.session_count}"),
        ],
        tips=[
            "Start with 15-minute blocks and make them 5 minutes longer each week.",
            "Use a visible timer, and do not switch tasks until it rings.",
            "Close every tab and app not needed for this block before you start.",
            "Decide the very next small step before the block starts.",
        ],
    )


def best_part_of_day(stats: FocusStatistics) -> Recommendation | None:
    """Which part of the day the sessions went best, when the gap is real."""
    if stats.session_count < MIN_SESSIONS_FOR_ADVICE:
        return None

    groups: dict[str, list[SessionFacts]] = {}
    for session in stats.sessions:
        groups.setdefault(_block(session.start_hour), []).append(session)
    averages = {
        name: sum(s.average_focus_score for s in items) / len(items)
        for name, items in groups.items()
        if len(items) >= MIN_SESSIONS_PER_TIME_GROUP
    }
    if len(averages) < 2:
        return None
    best = max(averages, key=averages.__getitem__)
    worst = min(averages, key=averages.__getitem__)
    gap = averages[best] - averages[worst]
    if gap < MEANINGFUL_SCORE_GAP or worst == "night":
        return None  # night against the rest is better_time_of_day's job

    return Recommendation(
        rule_id="best_part_of_day",
        kind=RecommendationKind.SCHEDULE,
        priority=Priority.MEDIUM,
        title=f"Your {best} sessions went best",
        detail=(
            f"Sessions started in the {best} scored clearly higher than {worst} ones. "
            f"Putting the most demanding work in the {best} uses that."
        ),
        evidence=[
            Evidence(f"Average in the {best}", f"{averages[best]:.0f} / 100"),
            Evidence(f"Average in the {worst}", f"{averages[worst]:.0f} / 100"),
            Evidence("Difference", f"{gap:.0f} points"),
        ],
        tips=[
            f"Plan tomorrow's hardest task for the {best}.",
            f"Use {worst} time for lighter work: revision, email, planning.",
            "Keep the same start time for a week; a routine makes starting easier.",
        ],
    )


#: Every rule, in the order they are evaluated. Priority sorting happens after.
ALL_RULES: tuple[Callable[[FocusStatistics], Recommendation | None], ...] = (
    not_enough_data,
    phone_in_reach,
    frequent_interruptions,
    signs_of_tiredness,
    dominant_distraction,
    sessions_without_breaks,
    better_time_of_day,
    looking_away_often,
    short_focus_stretches,
    best_part_of_day,
    going_well,
)

_PRIORITY_ORDER = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}


def generate_recommendations(
    stats: FocusStatistics, limit: int = 6
) -> list[Recommendation]:
    """Run every rule and return the most important results.

    Capped, deliberately. Eight suggestions at once is a list nobody acts on; the
    point is to surface the one or two things most worth changing.
    """
    produced = [
        recommendation
        for rule in ALL_RULES
        if (recommendation := rule(stats)) is not None
    ]

    produced.sort(key=lambda item: _PRIORITY_ORDER[item.priority])
    return produced[:limit]
