"""Core logic for the AUB Course Planner.

The MCP server (server.py) is a thin layer on top of these functions, so the
same logic can be tested directly with pytest.
"""
from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field


# Data loading
DATA_DIR = Path(os.environ.get("PLANNER_DATA_DIR", Path(__file__).parent / "data"))
STUDENT_FILE = os.environ.get("PLANNER_STUDENT", "student.json")


def _load(name: str) -> dict:
    """Read one JSON file from the data folder (fresh on every call)."""
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def load_catalog() -> dict[str, dict]:
    return {c["code"]: c for c in _load("catalog.json")["courses"]}


def load_sections() -> dict:
    return _load("sections.json")



# Typed outputs (FastMCP turns these into the tool's output schema)
class SectionInfo(BaseModel):
    section: int
    days: str = Field(description="Meeting days, e.g. 'MWF' or 'TR'")
    start: str = Field(description="Start time, 24h HH:MM")
    end: str = Field(description="End time, 24h HH:MM")
    capacity: int
    enrolled: int
    seats_left: int


class CourseInfo(BaseModel):
    code: str
    title: str
    credits: int
    category: str = Field(description="core, elective, math, or non-major")
    workload: str = Field(description="light, medium, or heavy (sample data)")
    open_to_cs_majors: bool
    prerequisites: str = Field(description="Prerequisites in plain language")
    semester: str
    offered: bool = Field(description="True if the course has sections this semester")
    sections: list[SectionInfo]


# Helpers
_CODE_RE = re.compile(r"^\s*([A-Za-z]{4})\s*-?\s*(\d{3}[A-Za-z]?)\s*$")


def normalize_code(raw: str) -> str:
    """'cmps214', 'CMPS-214', ' cmps 299a ' -> 'CMPS 214' / 'CMPS 299A'."""
    if not raw or not raw.strip():
        raise ToolError("Expected a course code like 'CMPS 214', but got an empty value.")
    m = _CODE_RE.match(raw)
    if not m:
        raise ToolError(f"'{raw}' is not a valid course code. Expected a format like 'CMPS 214'.")
    return f"{m.group(1).upper()} {m.group(2).upper()}"


def find_course(raw_code: str) -> dict:
    """Return the catalog entry for a code, or raise a clear 'not found' error."""
    code = normalize_code(raw_code)
    catalog = load_catalog()
    if code in catalog:
        return catalog[code]
    similar = difflib.get_close_matches(code, catalog.keys(), n=3, cutoff=0.6)
    hint = f" Similar courses: {', '.join(similar)}." if similar else ""
    raise ToolError(f"Course '{code}' was not found in the catalog.{hint}")



# Tool 1 (Data): get_course_info
def get_course_info(course_code: str) -> CourseInfo:
    course = find_course(course_code)
    sec_data = load_sections()
    sections = [
        SectionInfo(**s, seats_left=s["capacity"] - s["enrolled"])
        for s in sec_data["sections"].get(course["code"], [])
    ]
    return CourseInfo(
        code=course["code"],
        title=course["title"],
        credits=course["credits"],
        category=course["category"],
        workload=course["workload"],
        open_to_cs_majors=course["open_to_cs_majors"],
        prerequisites=course["prerequisites_text"],
        semester=sec_data["semester"],
        offered=bool(sections),
        sections=sections,
    )



# Tool 2 (Action): add_course_to_schedule
EQUIVALENTS = {"CMPS 211": "MATH 211", "MATH 211": "CMPS 211"}


def load_student() -> dict:
    return _load(STUDENT_FILE)


def load_requirements() -> dict:
    return _load("requirements.json")


def load_schedule() -> dict:
    return _load("schedule.json")


def save_schedule(schedule: dict) -> None:
    """Write the schedule atomically (temp file, then rename) so it is never half-written."""
    path = DATA_DIR / "schedule.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(schedule, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


class ScheduledCourse(BaseModel):
    code: str
    title: str
    credits: int
    workload: str
    section: int
    days: str
    start: str
    end: str


class ScheduleResult(BaseModel):
    added: ScheduledCourse = Field(description="The course section that was just added")
    schedule: list[ScheduledCourse] = Field(description="The full Spring 2027 schedule after adding")
    total_credits: int
    credit_limit: int



def _petitions(student: dict, kind: str, course: str | None = None) -> list[dict]:
    return [p for p in student.get("approved_petitions", [])
            if p.get("type") == kind and (course is None or p.get("course") == course)]


def credit_limit_for(student: dict, requirements: dict) -> int:
    """17 by default; raised only by an approved credit_overload petition in the student file."""
    policy = requirements["policies"]
    limit = policy["credit_limit"]
    for p in _petitions(student, "credit_overload"):
        limit = max(limit, min(p.get("max_credits", limit), policy["max_credits_with_overload"]))
    return limit



def _check_prereq(expr: dict | None, ctx: dict) -> tuple[bool, str]:
    """Evaluate a prerequisite expression. Returns (passed, readable description of what's missing)."""
    if expr is None:
        return True, ""
    if "all" in expr:
        missing = [msg for ok, msg in (_check_prereq(e, ctx) for e in expr["all"]) if not ok]
        return (not missing), "; ".join(missing)
    if "any" in expr:
        results = [_check_prereq(e, ctx) for e in expr["any"]]
        if any(ok for ok, _ in results):
            return True, ""
        return False, "one of: " + " OR ".join(msg for _, msg in results)
    if "course" in expr:
        code, need = expr["course"], expr.get("min_grade")
        grade = ctx["completed"].get(code)
        if grade is None:
            return False, f"{code} (not completed)"
        if need is not None and grade < need:
            return False, f"a grade of at least {need} in {code} (you have {grade})"
        return True, ""
    if "standing" in expr:
        order = ctx["standing_order"]
        need, have = expr["standing"], ctx["standing"]
        if order.index(have) >= order.index(need):
            return True, ""
        return False, f"{need} standing (you are a {have})"
    if "consent" in expr:
        if ctx["consent"]:
            return True, ""
        return False, "consent of instructor (no approved petition on file)"
    if "prereq_or_coreq" in expr:
        code = expr["prereq_or_coreq"]
        if code in ctx["completed"] or code in ctx["scheduled"]:
            return True, ""
        return False, f"{code} completed or in the same schedule (co-requisite)"
    raise ValueError(f"Unknown prerequisite rule: {expr}")



def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _overlaps(a: dict, b: dict) -> bool:
    same_day = set(a["days"]) & set(b["days"])
    return bool(same_day) and _to_minutes(a["start"]) < _to_minutes(b["end"]) \
        and _to_minutes(b["start"]) < _to_minutes(a["end"])



def add_course_to_schedule(course_code: str, section: int) -> ScheduleResult:
    # 1. course exists
    course = find_course(course_code)
    code = course["code"]
    sec_data = load_sections()
    semester = sec_data["semester"]
    offered = sec_data["sections"].get(code, [])

    # 2. offered this semester
    if not offered:
        raise ToolError(f"{code} is not offered in {semester}.")

    # 3. section exists
    chosen = next((s for s in offered if s["section"] == section), None)
    if chosen is None:
        valid = ", ".join(str(s["section"]) for s in offered)
        raise ToolError(f"{code} has no section {section} in {semester}. Available sections: {valid}.")

    # 4. open to CS majors
    if not course["open_to_cs_majors"]:
        raise ToolError(f"{code} is not open to computer science majors.")

    student, requirements, schedule = load_student(), load_requirements(), load_schedule()
    completed: dict = student["completed"]
    scheduled = {c["code"]: c for c in schedule["courses"]}

    # 5. not already completed (including equivalent courses)
    if code in completed:
        raise ToolError(f"You already completed {code} (grade {completed[code]}).")
    eq = EQUIVALENTS.get(code)
    if eq and eq in completed:
        raise ToolError(f"You already completed {eq}, which is equivalent to {code}.")

    # 6. not already scheduled
    if code in scheduled:
        raise ToolError(f"{code} is already in your schedule (section {scheduled[code]['section']}).")

    # 7. prerequisites (a prerequisite_waiver petition skips this check for that course)
    if not _petitions(student, "prerequisite_waiver", code):
        ctx = {
            "completed": completed,
            "scheduled": scheduled,
            "standing": student["standing"],
            "standing_order": requirements["policies"]["standing_order"],
            "consent": bool(_petitions(student, "instructor_consent", code)),
        }
        ok, missing = _check_prereq(course["prerequisites"], ctx)
        if not ok:
            raise ToolError(
                f"Cannot add {code}: prerequisites not met. Missing {missing}. "
                f"(Catalogue requirement: {course['prerequisites_text']}.)"
            )

    # 8. no time conflict
    for other in schedule["courses"]:
        if _overlaps(chosen, other):
            raise ToolError(
                f"{code} section {section} ({chosen['days']} {chosen['start']}-{chosen['end']}) conflicts with "
                f"{other['code']} section {other['section']} ({other['days']} {other['start']}-{other['end']})."
            )

    # 9. seats left
    if chosen["enrolled"] >= chosen["capacity"]:
        raise ToolError(f"{code} section {section} is full ({chosen['enrolled']}/{chosen['capacity']}).")

    # 10. credit limit
    limit = credit_limit_for(student, requirements)
    current = sum(c["credits"] for c in schedule["courses"])
    if current + course["credits"] > limit:
        raise ToolError(
            f"Adding {code} ({course['credits']} cr) would bring you to {current + course['credits']} credits; "
            f"your limit is {limit}."
        )

    # All rules passed: save
    entry = ScheduledCourse(code=code, title=course["title"], credits=course["credits"],
                            workload=course["workload"], section=section, days=chosen["days"],
                            start=chosen["start"], end=chosen["end"])
    schedule["courses"].append(entry.model_dump())
    save_schedule(schedule)
    return ScheduleResult(added=entry,
                          schedule=[ScheduledCourse(**c) for c in schedule["courses"]],
                          total_credits=current + course["credits"],
                          credit_limit=limit)



# Tool 3 (Orchestration): plan_next_semester
class PlannedCourse(ScheduledCourse):
    note: str = Field(default="", description="e.g. which sections were tried before this one")


class SkippedCourse(BaseModel):
    code: str
    reason: str


class PlanResult(BaseModel):
    semester: str
    added: list[PlannedCourse] = Field(description="Courses this plan added, in the order they were added")
    skipped: list[SkippedCourse] = Field(description="Courses considered but not added, with the reason")
    schedule: list[ScheduledCourse] = Field(description="The full schedule after planning")
    total_credits: int
    heavy_courses: int
    notes: list[str] = Field(default_factory=list)


def _course_number(code: str) -> int:
    return int(re.sub(r"\D", "", code.split()[1]))


def remaining_candidates(student: dict, requirements: dict, catalog: dict) -> list[str]:
    """Courses still needed, in priority order: required CMPS, required math, then CMPS electives 214+."""
    done = set(student["completed"])
    done |= {EQUIVALENTS[c] for c in done if c in EQUIVALENTS}

    required = sorted((c for c in requirements["required_cmps"] if c not in done), key=_course_number)
    math = [group[0] for group in requirements["required_math"] if not done & set(group)]
    rule = requirements["cmps_electives"]
    electives = sorted(
        (code for code, c in catalog.items()
         if code.startswith("CMPS") and c["category"] == "elective"
         and _course_number(code) >= rule["min_course_number"]
         and code not in done and code not in rule["excluded"]),
        key=lambda c: (_course_number(c), c),
    )
    return required + math + electives


def plan_next_semester(max_credits: int, max_heavy_courses: int) -> PlanResult:
    student, requirements = load_student(), load_requirements()
    policy = requirements["policies"]
    if not 1 <= max_credits <= policy["max_credits_with_overload"]:
        raise ToolError(f"max_credits must be between 1 and {policy['max_credits_with_overload']} (got {max_credits}).")
    if max_heavy_courses < 0:
        raise ToolError(f"max_heavy_courses cannot be negative (got {max_heavy_courses}).")

    notes: list[str] = []
    limit = credit_limit_for(student, requirements)
    if max_credits > limit:
        notes.append(f"You asked for up to {max_credits} credits, but your registration limit is {limit} "
                     f"(an approved credit-overload petition is needed to go higher).")

    schedule = load_schedule()
    scheduled = {c["code"] for c in schedule["courses"]}
    credits = sum(c["credits"] for c in schedule["courses"])
    heavy = sum(1 for c in schedule["courses"] if c["workload"] == "heavy")
    if scheduled:
        notes.append(f"Started from your existing schedule ({credits} credits already planned).")

    added: list[PlannedCourse] = []
    skipped: list[SkippedCourse] = []

    target = min(max_credits, limit)
    for code in remaining_candidates(student, requirements, load_catalog()):
        if code in scheduled:
            continue


        info = get_course_info(code)
        if not info.offered:
            skipped.append(SkippedCourse(code=code, reason=f"Not offered in {info.semester}."))
            continue

        if credits + info.credits > target:
            notes.append(f"Stopped at {credits} credits: the next course ({code}, {info.credits} cr) "
                         f"would exceed {target} credits.")
            break
        if info.workload == "heavy" and heavy >= max_heavy_courses:
            skipped.append(SkippedCourse(
                code=code,
                reason=f"Skipped to respect your limit of {max_heavy_courses} heavy course(s) "
                       f"(eligibility not checked)."))
            continue

        
        failures: list[str] = []
        for sec in info.sections:
            try:
                result = add_course_to_schedule(code, sec.section)
            except ToolError as err:
                failures.append(str(err))
                continue
            note = ("Tried first: " + " | ".join(failures)) if failures else ""
            added.append(PlannedCourse(**result.added.model_dump(), note=note))
            credits = result.total_credits
            heavy += info.workload == "heavy"
            break
        else:
            reason = failures[0] if len(set(failures)) == 1 else " | ".join(failures)
            skipped.append(SkippedCourse(code=code, reason=reason))

    if not added:
        notes.append("No courses could be added with these preferences; see 'skipped' for the reasons.")

    final = load_schedule()["courses"]
    return PlanResult(semester=load_sections()["semester"], added=added, skipped=skipped,
                      schedule=[ScheduledCourse(**c) for c in final],
                      total_credits=sum(c["credits"] for c in final),
                      heavy_courses=sum(1 for c in final if c["workload"] == "heavy"),
                      notes=notes)



# Resources (read-only views)
def student_profile() -> dict:
    """The student's record plus what they still need for the degree."""
    student, requirements, catalog = load_student(), load_requirements(), load_catalog()
    done = set(student["completed"])
    done_eq = done | {EQUIVALENTS[c] for c in done if c in EQUIVALENTS}
    rule = requirements["cmps_electives"]
    elective_credits = sum(
        catalog[c]["credits"] for c in done
        if c in catalog and c.startswith("CMPS") and c not in requirements["required_cmps"]
        and _course_number(c) >= rule["min_course_number"])
    return {
        "name": student["name"],
        "program": student["program"],
        "standing": student["standing"],
        "completed": student["completed"],
        "approved_petitions": student.get("approved_petitions", []),
        "credit_limit": credit_limit_for(student, requirements),
        "remaining_required_cmps": [c for c in requirements["required_cmps"] if c not in done_eq],
        "remaining_required_math": [" or ".join(g) for g in requirements["required_math"] if not done_eq & set(g)],
        "cmps_elective_credits": {"completed": elective_credits, "required": rule["credits"]},
    }


def current_schedule() -> dict:
    """The Spring 2027 schedule so far, with totals."""
    schedule = load_schedule()
    courses = schedule["courses"]
    return {
        "semester": schedule["semester"],
        "courses": courses,
        "total_credits": sum(c["credits"] for c in courses),
        "heavy_courses": sum(1 for c in courses if c["workload"] == "heavy"),
    }
