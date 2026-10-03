"""FastAPI entrypoint for the two-layer PCB grid router."""
from __future__ import annotations

from fastapi import FastAPI

from .inspector import inspect_board
from .schemas import InspectRequest, InspectResponse, RouteRequest, RouteResponse
from .solver import solve_board

app = FastAPI(
    title="Two-Layer PCB Grid Router",
    version="1.0.0",
    description=(
        "Jointly routes two-pad nets on a two-layer grid board with "
        "OR-Tools CP-SAT. Minimises total vias first, then total "
        "same-layer moves. POST /inspect checks supplied candidate paths "
        "against every routing rule without solving."
    ),
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/route", response_model=RouteResponse)
def route(req: RouteRequest) -> RouteResponse:
    # `def` (not `async def`): the CP-SAT solve is CPU-bound, so FastAPI
    # runs it in the threadpool and the event loop stays responsive.
    return solve_board(req)


@app.post("/inspect", response_model=InspectResponse)
def inspect(req: InspectRequest) -> InspectResponse:
    # pure validation, cheap and CPU-light: no solver is ever invoked
    return inspect_board(req)
