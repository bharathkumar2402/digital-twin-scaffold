# app/schemas/

- requests/        — API request/response Pydantic schemas
- agent_outputs/    — one schema per agent's output. Every agent node in app/agents/
                      must validate its return value against a schema here before
                      writing to FacilityTwinState or the DB.
