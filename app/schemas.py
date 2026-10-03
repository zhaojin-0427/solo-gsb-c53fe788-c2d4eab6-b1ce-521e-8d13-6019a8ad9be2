"""Request/response schemas for the two-layer PCB grid router."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

MIN_DIM = 2
MAX_DIM = 20
MIN_NETS = 1
MAX_NETS = 6
LAYERS = (0, 1)


class Cell(BaseModel):
    """A grid cell on a specific layer."""

    x: int
    y: int
    layer: int = Field(ge=0, le=1)


class Pad(BaseModel):
    """A net pad: a coordinate on one of the two layers."""

    x: int
    y: int
    layer: int = Field(ge=0, le=1)


class Net(BaseModel):
    """A two-pad net with a non-negative via budget."""

    name: Optional[str] = None
    pads: list[Pad] = Field(min_length=2, max_length=2)
    via_limit: int = Field(ge=0)


class RouteRequest(BaseModel):
    width: int = Field(ge=MIN_DIM, le=MAX_DIM)
    height: int = Field(ge=MIN_DIM, le=MAX_DIM)
    blocked: list[Cell] = Field(default_factory=list)
    nets: list[Net] = Field(min_length=MIN_NETS, max_length=MAX_NETS)

    @model_validator(mode="after")
    def _check_geometry(self) -> "RouteRequest":
        for c in self.blocked:
            if not (0 <= c.x < self.width and 0 <= c.y < self.height):
                raise ValueError(
                    f"blocked cell ({c.x}, {c.y}) out of bounds for "
                    f"{self.width}x{self.height} grid"
                )
        blocked = {(c.x, c.y, c.layer) for c in self.blocked}
        seen: set[tuple[int, int, int]] = set()
        for i, net in enumerate(self.nets):
            for p in net.pads:
                key = (p.x, p.y, p.layer)
                if not (0 <= p.x < self.width and 0 <= p.y < self.height):
                    raise ValueError(
                        f"net {i} pad ({p.x}, {p.y}) out of bounds for "
                        f"{self.width}x{self.height} grid"
                    )
                if key in blocked:
                    raise ValueError(
                        f"net {i} pad ({p.x}, {p.y}, layer {p.layer}) "
                        f"is on a blocked cell"
                    )
                if key in seen:
                    raise ValueError(
                        f"duplicate pad at ({p.x}, {p.y}, layer {p.layer})"
                    )
                seen.add(key)
        return self


class PathCell(BaseModel):
    x: int
    y: int
    layer: int


class InspectCell(BaseModel):
    """A path step submitted to /inspect.

    Coordinates must be integers but are *not* range-checked here:
    out-of-board steps or illegal layer numbers are reported as routing
    violations (HTTP 200), whereas non-integer input is a malformed
    request (HTTP 422). Numeric strings and booleans are rejected, while
    integral floats (``1.0``) are normalised to ``int``.
    """

    x: int
    y: int
    layer: int

    @field_validator("x", "y", "layer", mode="before")
    @classmethod
    def _must_be_integral_number(cls, v):
        if isinstance(v, bool) or isinstance(v, str):
            raise ValueError("coordinate must be an integer")
        if isinstance(v, float):
            if not v.is_integer():
                raise ValueError("coordinate must be an integer")
            return int(v)
        return v


class ViaPoint(BaseModel):
    x: int
    y: int


class NetResult(BaseModel):
    name: str
    path: list[PathCell]
    vias: list[ViaPoint]
    via_count: int
    move_count: int


class RouteResponse(BaseModel):
    # "optimal"  : proved best (min total vias, then min total moves)
    # "feasible" : time limit hit, best solution found so far (not proved)
    # "infeasible": proved that no routing exists
    # "unknown"  : time limit hit before any feasible solution was found
    status: str
    solve_time_seconds: float
    total_vias: Optional[int] = None
    total_moves: Optional[int] = None
    nets: Optional[list[NetResult]] = None


class InspectRequest(RouteRequest):
    """Board + net definition (same fields as /route) plus one ordered
    candidate path per net, in net order. /inspect never re-solves: it only
    reports every rule the given paths violate."""

    paths: list[list[InspectCell]]

    @model_validator(mode="after")
    def _check_paths(self) -> "InspectRequest":
        if len(self.paths) != len(self.nets):
            raise ValueError(
                f"number of paths ({len(self.paths)}) must match number of "
                f"nets ({len(self.nets)})"
            )
        # a simple path can occupy at most one cell per (x, y, layer); an
        # over-long path is malformed input rather than a routing violation
        max_cells = self.width * self.height * len(LAYERS)
        for i, path in enumerate(self.paths):
            if len(path) > max_cells:
                raise ValueError(
                    f"path {i} has {len(path)} steps, more than the "
                    f"{max_cells} cells of the {self.width}x{self.height} "
                    f"x{len(LAYERS)} board"
                )
        return self


class Violation(BaseModel):
    # one of: endpoint, out_of_bounds, illegal_layer, blocked,
    # duplicate_cell, non_orthogonal_move, layer_change_moved,
    # via_limit_exceeded, net_overlap, net_adjacent
    rule: str
    # indices (into the request net list) of every net involved
    nets: list[int]
    # step indices of every involved step (empty for an empty path's
    # endpoint violation), flattened and sorted across involved nets
    steps: list[int]
    # coordinates of every involved cell, sorted; empty for an empty
    # path's endpoint violation
    cells: list[PathCell]


class InspectResponse(BaseModel):
    legal: bool
    violations: list[Violation]
