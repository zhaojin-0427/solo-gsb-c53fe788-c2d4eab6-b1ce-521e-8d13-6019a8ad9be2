"""Rule checker for candidate paths submitted to POST /inspect.

Unlike ``solver.solve_board`` this module never solves anything: it walks
each given path and reports *every* rule violation it can find. A net that
fails one rule is still checked against every other rule and against every
other net.

Rule codes
----------
endpoint              path does not start/end on that net's two pads
out_of_bounds         step cell leaves the width x height board
illegal_layer         step layer is not 0 or 1
blocked               step lands on a blocked cell
duplicate_cell        one net visits the same (x, y, layer) twice
non_orthogonal_move   consecutive same-layer steps are not unit-orthogonal
layer_change_moved    consecutive steps change layer at different (x, y)
via_limit_exceeded    via coordinates (same (x, y) occupied on both layers)
                      exceed the net's via_limit
net_overlap           two nets occupy the same cell on the same layer
net_adjacent          two nets occupy orthogonally adjacent cells on one
                      layer

Out-of-board steps and illegal-layer steps are violations (HTTP 200), not
request errors. Cross-net comparisons only use cells that are in-board AND
on a legal layer; blocked cells still take part.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Tuple

from .schemas import InspectRequest, InspectResponse, PathCell, Violation

RULE_ENDPOINT = "endpoint"
RULE_OUT_OF_BOUNDS = "out_of_bounds"
RULE_ILLEGAL_LAYER = "illegal_layer"
RULE_BLOCKED = "blocked"
RULE_DUPLICATE = "duplicate_cell"
RULE_NON_ORTHOGONAL = "non_orthogonal_move"
RULE_LAYER_CHANGE_MOVED = "layer_change_moved"
RULE_VIA_LIMIT = "via_limit_exceeded"
RULE_NET_OVERLAP = "net_overlap"
RULE_NET_ADJACENT = "net_adjacent"

_ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))

Cell = Tuple[int, int, int]


def inspect_board(req: InspectRequest) -> InspectResponse:
    w, h = req.width, req.height
    blocked = {(c.x, c.y, c.layer) for c in req.blocked}

    paths: list[list[Cell]] = [
        [(c.x, c.y, c.layer) for c in path] for path in req.paths
    ]

    # each tuple is a raw violation; conversion/sorting happens at the end
    raw: list[tuple[str, list[int], list[int], list[Cell]]] = []

    # occupancy of board cells (in-board, legal layer) per net, including
    # steps on blocked cells; a cell maps to every step index visiting it
    occupied: list[dict[Cell, list[int]]] = [
        defaultdict(list) for _ in paths
    ]

    for k, (net, path) in enumerate(zip(req.nets, paths)):
        src = (net.pads[0].x, net.pads[0].y, net.pads[0].layer)
        dst = (net.pads[1].x, net.pads[1].y, net.pads[1].layer)

        # --- endpoints -------------------------------------------------
        if not path:
            # an empty path matches neither pad: one endpoint violation
            raw.append((RULE_ENDPOINT, [k], [], []))
        else:
            if path[0] != src:
                raw.append((RULE_ENDPOINT, [k], [0], [path[0]]))
            if path[-1] != dst:
                raw.append(
                    (RULE_ENDPOINT, [k], [len(path) - 1], [path[-1]])
                )

        # --- per-cell rules -------------------------------------------
        seen: dict[Cell, list[int]] = defaultdict(list)
        for i, cell in enumerate(path):
            x, y, layer = cell
            legal = True
            if not (0 <= x < w and 0 <= y < h):
                raw.append((RULE_OUT_OF_BOUNDS, [k], [i], [cell]))
                legal = False
            if layer not in (0, 1):
                raw.append((RULE_ILLEGAL_LAYER, [k], [i], [cell]))
                legal = False
            # blocked only makes sense for an in-board legal-layer cell
            if legal and cell in blocked:
                raw.append((RULE_BLOCKED, [k], [i], [cell]))
            seen[cell].append(i)
            if legal:
                occupied[k][cell].append(i)

        for cell, steps in seen.items():
            if len(steps) > 1:
                raw.append(
                    (RULE_DUPLICATE, [k], sorted(steps), [cell])
                )

        # --- consecutive-step (move) rules ----------------------------
        for i, (a, b) in enumerate(zip(path, path[1:])):
            steps = [i, i + 1]
            if a[2] == b[2]:
                if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
                    raw.append(
                        (RULE_NON_ORTHOGONAL, [k], steps, [a, b])
                    )
            elif a[:2] != b[:2]:
                raw.append(
                    (RULE_LAYER_CHANGE_MOVED, [k], steps, [a, b])
                )

        # --- via budget ------------------------------------------------
        # a via occupies both layers at one coordinate; count distinct
        # coordinates where the path is present on both layer 0 and
        # layer 1 (each such coordinate is one via regardless of how
        # many times either layer side is visited)
        layers_at: dict[tuple[int, int], dict[int, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for i, (x, y, layer) in enumerate(path):
            layers_at[(x, y)][layer].append(i)
        via_count = 0
        via_steps: list[int] = []
        via_cells: list[Cell] = []
        for (x, y), per_layer in layers_at.items():
            if 0 in per_layer and 1 in per_layer:
                via_count += 1
                for layer in (0, 1):
                    via_steps.extend(per_layer[layer])
                    via_cells.append((x, y, layer))
        if via_count > net.via_limit:
            raw.append(
                (
                    RULE_VIA_LIMIT,
                    [k],
                    sorted(set(via_steps)),
                    sorted(set(via_cells)),
                )
            )

    # --- cross-net rules (board cells on legal layers only) -------------
    # gather every occupied board cell once, with all nets visiting it
    all_cells: dict[Cell, dict[int, list[int]]] = defaultdict(dict)
    for k, occ in enumerate(occupied):
        for cell, steps in occ.items():
            all_cells[cell][k] = sorted(set(steps))

    for cell, visitors in sorted(all_cells.items()):
        nets_here = sorted(visitors)
        # same cell shared by N nets -> one violation per distinct pair;
        # three nets on one cell therefore yields three pairs
        for a_i in range(len(nets_here)):
            for b_i in range(a_i + 1, len(nets_here)):
                a, b = nets_here[a_i], nets_here[b_i]
                raw.append(
                    (
                        RULE_NET_OVERLAP,
                        [a, b],
                        sorted(visitors[a] + visitors[b]),
                        [cell],
                    )
                )

    # orthogonal adjacency: iterate each unordered in-board cell pair once
    # so mirror conflicts (net A at p seen from q, net B at q seen from p)
    # collapse into a single violation. Per-net step sets remember every
    # visit; sets from different nets are concatenated so equal step
    # indices on different nets are both retained.
    adjacency: dict[
        tuple[int, int, tuple[int, int], tuple[int, int]],
        tuple[set[int], dict[int, set[int]], set[Cell]],
    ] = defaultdict(lambda: (set(), defaultdict(set), set()))
    for x in range(w):
        for y in range(h):
            for layer in (0, 1):
                cell = (x, y, layer)
                here = all_cells.get(cell)
                if not here:
                    continue
                for dx, dy in _ORTHO:
                    nb = (x + dx, y + dy, layer)
                    if not (
                        0 <= nb[0] < w and 0 <= nb[1] < h
                    ) or nb not in all_cells:
                        continue
                    there = all_cells[nb]
                    for a, steps_a in here.items():
                        for b, steps_b in there.items():
                            if a == b:
                                continue
                            lo, hi = sorted((a, b))
                            c1, c2 = sorted((cell, nb))
                            key = (lo, hi, c1, c2)
                            nets_s, steps_by_net, cells_s = adjacency[key]
                            nets_s.update((a, b))
                            steps_by_net[a].update(steps_a)
                            steps_by_net[b].update(steps_b)
                            cells_s.update((cell, nb))

    for (lo, hi, _c1, _c2), (nets_s, steps_by_net, cells_s) in adjacency.items():
        all_steps: list[int] = []
        for net in sorted(steps_by_net):
            all_steps.extend(sorted(steps_by_net[net]))
        raw.append(
            (
                RULE_NET_ADJACENT,
                sorted(nets_s),
                all_steps,
                sorted(cells_s),
            )
        )

    # sort by net order, then step number, then rule code; cells and the
    # full nets tuple break remaining ties so the order is deterministic
    raw.sort(
        key=lambda v: (
            tuple(v[1]),
            v[2][0] if v[2] else -1,
            v[0],
            tuple(v[3]),
        )
    )

    violations = [
        Violation(
            rule=rule,
            nets=nets,
            steps=steps,
            cells=[PathCell(x=c[0], y=c[1], layer=c[2]) for c in cells],
        )
        for rule, nets, steps, cells in raw
    ]
    return InspectResponse(legal=not violations, violations=violations)
