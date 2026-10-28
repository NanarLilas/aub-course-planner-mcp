"""Clear the Spring 2027 schedule (used before demos and tests)."""
import planner_core as core

core.save_schedule({"semester": "Spring 2027", "courses": []})
print("Schedule cleared.")
