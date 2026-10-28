"""Tests for the AUB Course Planner MCP tools and resources.

Run with:  pytest -v
"""
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import planner_core as core
from tests.conftest import scheduled_codes
from server import mcp


# MCP protocol level: what an agent actually sees
async def test_server_exposes_three_tools_and_two_resources():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        resources = {str(r.uri) for r in await client.list_resources()}
    assert set(tools) == {"get_course_info", "add_course_to_schedule", "plan_next_semester"}
    assert resources == {"student://profile", "schedule://current"}
    for t in tools.values():
        assert t.description, f"{t.name} has no description"
        assert t.output_schema, f"{t.name} has no output schema"
    assert tools["get_course_info"].annotations.readOnlyHint is True
    assert tools["add_course_to_schedule"].annotations.readOnlyHint is False


async def test_tool_failure_reaches_the_agent_as_a_readable_error():
    async with Client(mcp) as client:
        result = await client.call_tool("add_course_to_schedule",
                                        {"course_code": "CMPS 243", "section": 1}, raise_on_error=False)
    assert result.is_error
    assert "senior standing" in result.content[0].text


async def test_resources_can_be_read_over_mcp():
    async with Client(mcp) as client:
        profile = json.loads((await client.read_resource("student://profile"))[0].text)
        schedule = json.loads((await client.read_resource("schedule://current"))[0].text)
    assert profile["name"] == "Demo Student A"
    assert schedule["total_credits"] == 0


# Tool 1 - Data: get_course_info
@pytest.mark.parametrize("raw", ["CMPS 214", "cmps214", "  Cmps-214 "])
def test_get_course_info_normalizes_codes(raw):
    info = core.get_course_info(raw)
    assert info.code == "CMPS 214"
    assert info.title == "Design and Analysis of Algorithms"
    assert info.offered and len(info.sections) == 2


def test_get_course_info_reports_seats_left():
    info = core.get_course_info("CMPS 241")
    assert [s.seats_left for s in info.sections] == [0, 15]


def test_get_course_info_course_not_offered():
    info = core.get_course_info("CMPS 289")
    assert info.offered is False and info.sections == []


def test_get_course_info_is_read_only(data_dir):
    before = (data_dir / "schedule.json").read_text()
    core.get_course_info("CMPS 214")
    assert (data_dir / "schedule.json").read_text() == before


@pytest.mark.parametrize("raw, message", [
    ("CMPS 999", "not found"),
    ("", "empty"),
    ("hello world", "not a valid course code"),
])
def test_get_course_info_failures(raw, message):
    with pytest.raises(ToolError, match=message):
        core.get_course_info(raw)


def test_unknown_course_suggests_similar_codes():
    with pytest.raises(ToolError, match="Similar courses: .*CMPS 299"):
        core.get_course_info("CMPS 999")



# Tool 2 - Action: add_course_to_schedule
def test_add_course_success_saves_to_schedule():
    result = core.add_course_to_schedule("cmps 214", 1)
    assert result.added.code == "CMPS 214"
    assert result.total_credits == 3 and result.credit_limit == 17
    assert scheduled_codes() == ["CMPS 214"]


@pytest.mark.parametrize("code, section, message", [
    ("CMPS 999", 1, "not found"),                                  
    ("CMPS 289", 1, "not offered in Spring 2027"),                 
    ("CMPS 214", 5, "no section 5.*Available sections: 1, 2"),      
    ("CMPS 203", 1, "not open to computer science majors"),         
    ("CMPS 202", 1, "already completed CMPS 202"),                   
    ("MATH 211", 1, "already completed CMPS 211, which is equivalent"),  
    ("CMPS 215", 1, "Missing CMPS 214 \\(not completed\\)"),         
    ("CMPS 243", 1, "senior standing \\(you are a junior\\)"),       
    ("CMPS 299A", 1, "consent of instructor"),                       
    ("CMPS 254", 1, "CMPS 251 completed or in the same schedule"),  
    ("CMPS 241", 1, "is full \\(35/35\\)"),                          
])
def test_add_course_rule_failures_save_nothing(code, section, message):
    with pytest.raises(ToolError, match=message):
        core.add_course_to_schedule(code, section)
    assert scheduled_codes() == []


def test_add_course_already_in_schedule():                          
    core.add_course_to_schedule("CMPS 214", 1)
    with pytest.raises(ToolError, match="already in your schedule"):
        core.add_course_to_schedule("CMPS 214", 2)
    assert scheduled_codes() == ["CMPS 214"]


def test_add_course_time_conflict():                                 
    core.add_course_to_schedule("CMPS 214", 1)
    with pytest.raises(ToolError, match="conflicts with CMPS 214 section 1"):
        core.add_course_to_schedule("CMPS 271", 1)
    assert scheduled_codes() == ["CMPS 214"]


def test_add_course_credit_limit():                                 
    for code, sec in [("CMPS 214", 1), ("CMPS 221", 1), ("CMPS 241", 2), ("CMPS 271", 2), ("CMPS 244", 2)]:
        core.add_course_to_schedule(code, sec)
    with pytest.raises(ToolError, match="18 credits; your limit is 17"):
        core.add_course_to_schedule("CMPS 280", 1)
    assert len(scheduled_codes()) == 5


def test_instructor_consent_petition_allows_course():
    result = core.add_course_to_schedule("CMPS 224", 1)  
    assert result.added.code == "CMPS 224"


def test_co_requisite_in_same_schedule_is_accepted():
    core.add_course_to_schedule("CMPS 251", 1)
    assert core.add_course_to_schedule("CMPS 254", 1).added.code == "CMPS 254"


def test_credit_overload_petition_raises_limit(data_dir):
    student = json.loads((data_dir / "student.json").read_text())
    student["approved_petitions"].append({"type": "credit_overload", "max_credits": 20})
    (data_dir / "student.json").write_text(json.dumps(student))
    for code, sec in [("CMPS 214", 1), ("CMPS 221", 1), ("CMPS 241", 2), ("CMPS 271", 2), ("CMPS 244", 2)]:
        core.add_course_to_schedule(code, sec)
    assert core.add_course_to_schedule("CMPS 280", 1).credit_limit == 20


def test_student_b_blocked_by_c_plus_rule(student_b):
    with pytest.raises(ToolError, match="at least 70 in CMPS 202 \\(you have 65\\)"):
        core.add_course_to_schedule("CMPS 214", 1)


def test_student_b_can_use_alternative_prerequisite(student_b):
    assert core.add_course_to_schedule("CMPS 244", 1).added.code == "CMPS 244"


def test_student_b_has_no_petition(student_b):
    with pytest.raises(ToolError, match="no approved petition"):
        core.add_course_to_schedule("CMPS 224", 1)



# Tool 3 - Orchestration: plan_next_semester
def test_plan_balanced_semester():
    plan = core.plan_next_semester(max_credits=15, max_heavy_courses=2)
    added = {c.code: c for c in plan.added}
    skipped = {s.code: s.reason for s in plan.skipped}

    assert list(added) == ["CMPS 214", "CMPS 221", "CMPS 241", "CMPS 271", "CMPS 244"]
    assert plan.total_credits == 15 and plan.heavy_courses == 2
    # works around a full section and time conflicts
    assert added["CMPS 241"].section == 2 and "full" in added["CMPS 241"].note
    assert added["CMPS 271"].section == 2 and "conflicts" in added["CMPS 271"].note
    # reports why other courses were not added
    assert "Missing CMPS 214" in skipped["CMPS 215"]
    assert "heavy" in skipped["CMPS 240"]
    assert "Not offered" in skipped["CMPS 231"]
    assert "senior standing" in skipped["CMPS 243"]
    # the plan was really saved through the Action tool
    assert scheduled_codes() == list(added)


def test_plan_light_semester_respects_heavy_limit():
    plan = core.plan_next_semester(max_credits=12, max_heavy_courses=1)
    assert plan.total_credits == 12 and plan.heavy_courses == 1
    assert "CMPS 221" not in {c.code for c in plan.added}


def test_plan_above_credit_limit_adds_note():
    plan = core.plan_next_semester(max_credits=20, max_heavy_courses=3)
    assert plan.total_credits <= 17
    assert any("registration limit is 17" in n for n in plan.notes)


def test_plan_builds_on_existing_schedule():
    core.add_course_to_schedule("CMPS 280", 1)
    plan = core.plan_next_semester(max_credits=15, max_heavy_courses=2)
    assert plan.total_credits == 15
    assert any("existing schedule" in n for n in plan.notes)


def test_plan_with_no_room_returns_empty_plan_not_error():
    plan = core.plan_next_semester(max_credits=2, max_heavy_courses=2)
    assert plan.added == [] and plan.total_credits == 0
    assert any("No courses could be added" in n for n in plan.notes)


def test_plan_for_student_b_uses_only_eligible_courses(student_b):
    plan = core.plan_next_semester(max_credits=15, max_heavy_courses=2)
    assert plan.total_credits == 15
    assert "CMPS 214" not in {c.code for c in plan.added}
    assert "you have 65" in {s.code: s.reason for s in plan.skipped}["CMPS 214"]


@pytest.mark.parametrize("credits, heavy, message", [
    (0, 1, "max_credits must be between 1 and 21"),
    (22, 1, "max_credits must be between 1 and 21"),
    (15, -1, "max_heavy_courses cannot be negative"),
])
def test_plan_invalid_preferences(credits, heavy, message):
    with pytest.raises(ToolError, match=message):
        core.plan_next_semester(credits, heavy)
    assert scheduled_codes() == []



# Resources
def test_student_profile_lists_remaining_requirements():
    profile = core.student_profile()
    assert profile["remaining_required_cmps"] == ["CMPS 214", "CMPS 215", "CMPS 221",
                                                  "CMPS 240", "CMPS 241", "CMPS 271"]
    assert profile["remaining_required_math"] == []
    assert profile["cmps_elective_credits"] == {"completed": 0, "required": 18}


def test_current_schedule_totals():
    core.add_course_to_schedule("CMPS 214", 1)
    core.add_course_to_schedule("CMPS 241", 2)
    schedule = core.current_schedule()
    assert schedule["total_credits"] == 6 and schedule["heavy_courses"] == 1
