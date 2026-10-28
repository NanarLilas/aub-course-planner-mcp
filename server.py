"""AUB Course Planner - MCP server (FastMCP).

Run it on its own with:   python server.py
Inspect it with:          fastmcp dev server.py
"""
from fastmcp import FastMCP

import planner_core as core

mcp = FastMCP(
    name="AUB Course Planner",
    instructions=(
        "Tools for planning a BS Computer Science student's Spring 2027 semester at AUB. "
        "Use get_course_info to look up a course before discussing it."
    ),
)



@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True})
def get_course_info(course_code: str) -> core.CourseInfo:
    """Look up one AUB course, including its Spring 2027 sections. Read-only: changes nothing.

    Args:
        course_code: A course code such as 'CMPS 214' (case and spacing are flexible, e.g. 'cmps214').

    Returns the title, credits, category, workload level, prerequisites in plain language,
    whether the course is offered this semester, and each section's days, times and seats left.
    Fails with a clear error if the code is malformed or the course does not exist.
    """
    return core.get_course_info(course_code)



@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
def add_course_to_schedule(course_code: str, section: int) -> core.ScheduleResult:
    """Add one section of a course to the student's Spring 2027 schedule, if AUB rules allow it.

    Args:
        course_code: A course code such as 'CMPS 214'.
        section: The section number to register in (see get_course_info for available sections).

    The rules are enforced by this tool, in order: the course exists, is offered this semester,
    the section exists, the course is open to CS majors, it is not already completed or scheduled,
    prerequisites are met (grades, standing, instructor consent, co-requisites), there is no time
    conflict, the section has seats, and the credit limit is respected. Approved petitions in the
    student's record can waive specific rules. If any rule fails, nothing is saved and a clear
    error explains why. On success, returns the added section and the updated schedule.
    """
    return core.add_course_to_schedule(course_code, section)



@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
def plan_next_semester(max_credits: int, max_heavy_courses: int) -> core.PlanResult:
    """Build the student's Spring 2027 schedule automatically, based on their workload preferences.

    Use this when the student wants a full plan rather than one specific course.

    Args:
        max_credits: Target number of credits for the semester (1-21). For example, a light
            semester is about 12, a normal one 15, a full one 17.
        max_heavy_courses: Maximum number of courses with a 'heavy' workload to include
            (e.g. 1 for an easy semester, 2-3 for a demanding one).

    How it works: finds the courses the student still needs (required CMPS courses first, then
    required math, then CMPS electives numbered 214+), looks each one up with get_course_info,
    skips any that break the student's preferences, and tries each section with
    add_course_to_schedule until one is accepted, working around full sections and time conflicts.
    All AUB rules are enforced by add_course_to_schedule. Returns what was added, what was skipped
    and why, and the final schedule. Fails only if the preferences themselves are invalid.
    """
    return core.plan_next_semester(max_credits, max_heavy_courses)



@mcp.resource("student://profile", mime_type="application/json")
def student_profile() -> dict:
    """The student's record: standing, completed courses and grades, approved petitions,
    credit limit, and the degree requirements still remaining."""
    return core.student_profile()


@mcp.resource("schedule://current", mime_type="application/json")
def current_schedule() -> dict:
    """The student's Spring 2027 schedule so far, with total credits and number of heavy courses."""
    return core.current_schedule()


if __name__ == "__main__":
    mcp.run(show_banner=False)
