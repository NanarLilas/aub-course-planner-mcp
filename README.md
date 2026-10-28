# AUB Course Planner (MCP Toolkit)

An AI academic advisor that helps a BS Computer Science student at AUB plan their Spring 2027 semester. The tools live in an MCP server built with FastMCP, and the agent is built with the OpenAI Agents SDK. The agent acts as the MCP host and client: it launches the server, discovers its tools, and routes each student request to the right one.

Built for EECE 503P/798S (Agentic Systems), assignment C5: Agent Tools.

## The three tools

**Data tool: `get_course_info(course_code)`**
Looks up one course: title, credits, category, workload, prerequisites in plain language, whether it is offered in Spring 2027, and each section's days, times and seats left. It is read-only and changes nothing.
Failure cases: an empty or badly formatted code (for example `hello world`) and an unknown course (for example `CMPS 999`, which also suggests similar codes).

**Action tool: `add_course_to_schedule(course_code, section)`**
Registers the student in one section of one course and saves it to `data/schedule.json`. Before saving, it checks the registration rules in this order:

1. The course exists.
2. It is offered this semester.
3. The section exists.
4. It is open to CS majors.
5. It is not already completed (including equivalent courses such as CMPS 211 and MATH 211).
6. It is not already in the schedule.
7. Prerequisites are met: minimum grades, class standing, instructor consent and co-requisites.
8. There is no time conflict.
9. The section has seats left.
10. The credit limit is respected.

Approved petitions in the student's record (instructor consent, prerequisite waiver, credit overload) can waive specific rules. If any rule fails, nothing is saved and the tool returns a clear error that explains why.

**Orchestration tool: `plan_next_semester(max_credits, max_heavy_courses)`**
Builds a whole semester from the student's workload preferences. It finds the courses the student still needs (required CMPS courses first, then required math, then CMPS electives numbered 214 and above), looks each one up with `get_course_info`, skips courses that break the preferences, and tries each section with `add_course_to_schedule` until one is accepted. This lets it work around full sections and time conflicts. It returns what was added, what was skipped and why, and the final schedule.
Failure case: invalid preferences (for example `max_credits=0` or a negative `max_heavy_courses`) return a clear error. A plan where no course fits is not an error: it comes back empty with a note.

The registration rules live only in the Action tool. The Orchestration tool never re-checks them; it only applies preferences and reacts to the Action tool's answers.

## Resources

The server also exposes two read-only resources with `@mcp.resource`:

- `student://profile`: the student's standing, completed courses and grades, approved petitions, credit limit, and remaining degree requirements. The agent reads it at startup and puts it in its instructions.
- `schedule://current`: the Spring 2027 schedule so far, with total credits and number of heavy courses.

## Project structure

- `server.py`: the FastMCP server. A thin layer that exposes the three tools and two resources.
- `planner_core.py`: all the logic (lookups, rule checks, planning), kept separate so it can be tested directly.
- `agent_client.py`: the OpenAI Agents SDK agent (MCP host and client). It starts `server.py` over stdio.
- `reset_schedule.py`: empties the schedule before a demo.
- `data/`: the course catalog, sections, degree requirements, the two demo students and the saved schedule.
- `tests/`: pytest tests, including every failure case.
- `env.example`: a template for your `.env` file.

## Data: what is real and what is sample

- **From the AUB Undergraduate Catalogue :** course titles, credits, prerequisites and the BS Computer Science requirements (`catalog.json`, `requirements.json`).
- **Sample data for this project:** each course's workload level and category, all Spring 2027 sections (days, times, capacity, enrollment), and the two demo students.
- **Simplified:** MATH and STAT titles and prerequisites. The capstone is modeled as CMPS 299A followed by CMPS 299B.
- **Assumption:** the 17-credit limit (21 with an approved overload petition) is a project assumption, not a figure from the catalogue.

There are two demo students:

- **Student A** (`student.json`): a junior in good standing, with an approved instructor-consent petition for CMPS 224.
- **Student B** (`student_low_grade.json`): the same record, except a 65 in CMPS 202 and no petitions. This shows the C+ prerequisite rule blocking a course.

## Setup

You need Python 3.10 or newer and an OpenAI API key.

1. Create a virtual environment and install the packages:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```


2. Copy the template and add your key:

   ```bash
   cp env.example .env
   ```

   Then edit `.env`:

   ```
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-4.1-mini   # optional, this is the default
   OPENAI_TRACING=0            # optional, turns off tracing to the OpenAI dashboard
   ```

   `.env` is listed in `.gitignore`, so your key is never committed.

## Running the tests

```bash
pytest -v
```

Expected result: `45 passed`. The tests cover:

- The MCP protocol level: the server exposes exactly three tools and two resources, each with a description and an output schema, and a tool error reaches the client as readable text.
- Every Data tool failure case.
- Every Action tool rule, each checked to make sure nothing is saved when it fails.
- Petitions, co-requisites and the credit overload.
- The planner: a balanced plan, a light plan, building on an existing schedule, an empty plan, Student B, and invalid preferences.

Each test runs on a temporary copy of `data/`, so the real files are never changed.

## Running the agent

```bash
python reset_schedule.py                                  
python agent_client.py                                    
python agent_client.py --student B                       
python agent_client.py --ask "Plan a light semester for me"   
python agent_client.py --quiet                           
```

Type `exit` to quit the chat. Unless `--quiet` is used, every tool call and its result is printed, so you can follow the full round trip: agent, MCP client, MCP server and back.

The agent turns workload wishes into planner arguments. For example, "light" becomes 12 credits with at most 1 heavy course, "balanced" becomes 15 and 2, and "push" becomes 17 and 3. An exact number from the student is used as given.

## Example runs

**Orchestration: Student A asks for a balanced semester**

```
You: Plan a balanced semester for me
   🔧 plan_next_semester({"max_credits":15,"max_heavy_courses":2})
      ↳ ✅ ok: {"semester":"Spring 2027","added":[{"code":"CMPS 214", ...

Advisor: I have planned a balanced Spring 2027 semester for you with 15 credits and 2 heavy workload courses:
- CMPS 214 (Design and Analysis of Algorithms), Section 1, TR 09:30-10:45 (3 credits, heavy)
- CMPS 221 (Computer Organization and Design), Section 1, MWF 09:00-09:50 (3 credits, heavy)
- CMPS 241 (Systems and Network Programming), Section 2, TR 15:30-16:45 (3 credits, medium)
- CMPS 271 (Software Engineering), Section 2, MWF 12:00-12:50 (3 credits, medium)
- CMPS 244 (Database Systems), Section 2, MWF 15:00-15:50 (3 credits, medium)

Courses skipped and reasons:
- CMPS 215: Missing prerequisite CMPS 214.
- CMPS 240, CMPS 224, CMPS 232: Skipped to keep only 2 heavy workload courses.
- CMPS 231: Not offered in Spring 2027.
- CMPS 242: Missing prerequisite CMPS 221.
- CMPS 243: Requires senior standing; you are a junior.
```

The planner took section 2 of CMPS 241 because section 1 is full, and section 2 of CMPS 271 because section 1 conflicts with CMPS 214.

**Data, Action and a predictable failure: Student B**

```
You: Add CMPS 214 for me
   🔧 get_course_info({"course_code":"CMPS 214"})
      ↳ ✅ ok: {"code":"CMPS 214","title":"Design and Analysis of Algorithms", ...
   🔧 add_course_to_schedule({"course_code":"CMPS 214","section":1})
      ↳ ❌ refused: Cannot add CMPS 214: prerequisites not met. Missing a grade of at least 70 in CMPS 202 (you have 65). (Catalogue requirement: CMPS 211 or MATH 211, and a grade of at least C+ (70) in CMPS 202.)

Advisor: You cannot add CMPS 214 because you do not meet the prerequisite grade in CMPS 202; you have a 65, but a grade of at least 70 is required.

You: Add CMPS 244 section 1
   🔧 get_course_info({"course_code":"CMPS 244"})
   🔧 add_course_to_schedule({"course_code":"CMPS 244","section":1})
      ↳ ✅ ok: {"added":{"code":"CMPS 244","title":"Database Systems", ...

Advisor: I have added CMPS 244 Section 1 (Database Systems) to your schedule. It meets on Tuesdays and Thursdays from 09:30 to 10:45.
```

CMPS 244 is accepted because it allows an alternative prerequisite: C+ in CMPS 201 plus junior standing.

**More queries to try**

- "What are the sections of CMPS 241, and do they have seats?"
- "Add CMPS 243 for me" (refused: needs senior standing)
- "Add CMPS 224" (Student A: accepted through the approved petition; Student B: refused)
- "Add CMPS 254" (refused unless CMPS 251 is completed or already in the schedule)
- "Plan a light semester, I work part-time"

## Using the server without the agent

```bash
fastmcp list server.py                                    
fastmcp call server.py get_course_info course_code="CMPS 214"
fastmcp call server.py get_course_info course_code="CMPS 999"    
fastmcp dev inspector server.py                            
```

## Design notes

- **One job per tool.** Looking up, registering and planning are separate tools. The planner coordinates the other two instead of repeating their logic, so each rule is enforced in one place.
- **Predictable failures.** Every rule failure raises a `ToolError` with a sentence the agent can pass on to the student, and the Action tool saves nothing when a rule fails. Saves are atomic (write to a temp file, then rename), so the schedule is never left half-written.
- **Typed inputs and outputs.** Arguments are typed, and outputs are Pydantic models, so FastMCP publishes an output schema for each tool. Tool annotations mark the Data tool as read-only.
- **The tools decide, not the model.** The agent's instructions tell it to always call `add_course_to_schedule` and explain its answer, instead of judging eligibility itself from the prerequisite text. Without that rule, the model sometimes refused courses on its own and the server's checks were never reached.

## Limitations and next steps

- Sections, workloads and the credit limit are sample data or assumptions, not live AUB data.
- There is no tool to drop a course; the schedule is reset with `reset_schedule.py`.
- Possible next steps: a `drop_course` Action tool, real section data from the registrar, multi-semester planning, and choosing between sections by time preferences (for example, no 8 AM classes).
