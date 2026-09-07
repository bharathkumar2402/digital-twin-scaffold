# app/agents/

One file per LangGraph agent node (5 agents — see docs/PROJECT_PLAN.md §4):

- planner.py                  — Planner Agent (absorbs asset-graph loading)
- risk_assessment.py          — Risk Assessment Agent
- maintenance_inventory.py    — Maintenance & Inventory Planning Agent
- route_optimization.py       — Route Optimization Agent (OR-Tools CVRP)
- simulation_decision.py      — Simulation & Decision Agent
- graph.py                    — LangGraph state graph wiring + FacilityTwinState
- validation.py                — shared retry/validate/escalate logic (§4.4)
