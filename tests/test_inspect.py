"""Tests for POST /inspect: candidate-path rule checking (no solving)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def inspect(req: dict) -> tuple[int, dict]:
    r = client.post("/inspect", json=req)
    return r.status_code, r.json()


def rules(body: dict) -> list[str]:
    return [v["rule"] for v in body["violations"]]


def find(body: dict, rule: str, nets: list[int] | None = None) -> dict:
    for v in body["violations"]:
        if v["rule"] == rule and (nets is None or v["nets"] == nets):
            return v
    raise AssertionError(f"{rule} {nets} not in {body['violations']}")


def single_net_base(pad0=None, pad1=None, via_limit=0, w=4, h=4):
    return {
        "width": w,
        "height": h,
        "nets": [
            {
                "pads": [
                    pad0 or {"x": 0, "y": 0, "layer": 0},
                    pad1 or {"x": 3, "y": 0, "layer": 0},
                ],
                "via_limit": via_limit,
            }
        ],
    }


# ------------------------------- legal paths -------------------------------

def test_inspect_legal_straight_path():
    req = single_net_base()
    req["paths"] = [[{"x": i, "y": 0, "layer": 0} for i in range(4)]]
    sc, body = inspect(req)
    assert sc == 200
    assert body == {"legal": True, "violations": []}


def test_inspect_legal_via_path():
    req = single_net_base(
        pad0={"x": 0, "y": 0, "layer": 0},
        pad1={"x": 2, "y": 2, "layer": 1},
        via_limit=1,
    )
    req["paths"] = [
        [
            {"x": 0, "y": 0, "layer": 0},
            {"x": 1, "y": 0, "layer": 0},
            {"x": 1, "y": 0, "layer": 1},
            {"x": 1, "y": 1, "layer": 1},
            {"x": 2, "y": 1, "layer": 1},
            {"x": 2, "y": 2, "layer": 1},
        ]
    ]
    sc, body = inspect(req)
    assert sc == 200
    assert body["legal"] is True


def test_inspect_cross_layer_coincidence_is_legal():
    # same (x, y) on different layers, even orthogonally aligned across
    # layers, never conflicts
    req = {
        "width": 2,
        "height": 2,
        "nets": [
            {"pads": [{"x": 0, "y": 0, "layer": 0},
                      {"x": 1, "y": 1, "layer": 0}], "via_limit": 0},
            {"pads": [{"x": 0, "y": 0, "layer": 1},
                      {"x": 1, "y": 1, "layer": 1}], "via_limit": 0},
        ],
        "paths": [
            [{"x": 0, "y": 0, "layer": 0}, {"x": 1, "y": 0, "layer": 0},
             {"x": 1, "y": 1, "layer": 0}],
            [{"x": 0, "y": 0, "layer": 1}, {"x": 1, "y": 0, "layer": 1},
             {"x": 1, "y": 1, "layer": 1}],
        ],
    }
    sc, body = inspect(req)
    assert sc == 200 and body["legal"] is True


# ------------------------------ single-net rules ---------------------------

def test_inspect_empty_path_is_endpoint_violation():
    req = single_net_base()
    req["paths"] = [[]]
    sc, body = inspect(req)
    assert sc == 200
    assert body["legal"] is False
    assert rules(body) == ["endpoint"]
    v = body["violations"][0]
    assert v["nets"] == [0] and v["steps"] == [] and v["cells"] == []


def test_inspect_wrong_endpoints_reported_per_end():
    req = single_net_base()
    # first and last cell both wrong
    req["paths"] = [[{"x": 1, "y": 0, "layer": 0},
                     {"x": 2, "y": 0, "layer": 0}]]
    sc, body = inspect(req)
    assert sc == 200
    endpoints = [v for v in body["violations"] if v["rule"] == "endpoint"]
    assert len(endpoints) == 2
    assert [v["steps"] for v in endpoints] == [[0], [1]]
    assert endpoints[0]["cells"] == [{"x": 1, "y": 0, "layer": 0}]
    assert endpoints[1]["cells"] == [{"x": 2, "y": 0, "layer": 0}]


def test_inspect_one_wrong_endpoint_only():
    req = single_net_base()
    req["paths"] = [[{"x": 0, "y": 0, "layer": 0},
                     {"x": 1, "y": 0, "layer": 0},
                     {"x": 2, "y": 0, "layer": 0}]]
    sc, body = inspect(req)
    assert sc == 200
    v = find(body, "endpoint")
    assert v["steps"] == [2]
    assert v["cells"] == [{"x": 2, "y": 0, "layer": 0}]


def test_inspect_out_of_bounds_and_illegal_layer_are_200_violations():
    req = single_net_base()
    req["paths"] = [
        [
            {"x": 0, "y": 0, "layer": 0},
            {"x": 4, "y": 0, "layer": 0},   # out of bounds
            {"x": 2, "y": 0, "layer": 5},   # illegal layer (in bounds)
            {"x": 3, "y": -1, "layer": 1},  # out of bounds, legal layer
            {"x": 3, "y": 0, "layer": 0},
        ]
    ]
    sc, body = inspect(req)
    assert sc == 200
    oob = [v for v in body["violations"] if v["rule"] == "out_of_bounds"]
    ill = [v for v in body["violations"] if v["rule"] == "illegal_layer"]
    assert [v["steps"] for v in oob] == [[1], [3]]
    assert [v["steps"] for v in ill] == [[2]]
    assert oob[0]["cells"] == [{"x": 4, "y": 0, "layer": 0}]
    # one cell can trigger both rules at once
    both = find(body, "out_of_bounds")  # step 1 entry exists
    assert both is not None


def test_inspect_blocked_cell_reported_and_blocks_pad_is_still_422():
    # path step on a blocked cell -> 200 violation
    req = single_net_base()
    req["blocked"] = [{"x": 1, "y": 0, "layer": 0}]
    req["paths"] = [[{"x": i, "y": 0, "layer": 0} for i in range(4)]]
    sc, body = inspect(req)
    assert sc == 200
    v = find(body, "blocked")
    assert v["steps"] == [1]
    assert v["cells"] == [{"x": 1, "y": 0, "layer": 0}]
    # a pad itself on a blocked cell is a board-level 422, unchanged
    req["nets"][0]["pads"][0] = {"x": 1, "y": 0, "layer": 0}
    assert client.post("/inspect", json=req).status_code == 422


def test_inspect_duplicate_cell_keeps_all_visits():
    req = single_net_base()
    req["paths"] = [
        [
            {"x": 0, "y": 0, "layer": 0},
            {"x": 1, "y": 0, "layer": 0},
            {"x": 2, "y": 0, "layer": 0},
            {"x": 1, "y": 0, "layer": 0},
            {"x": 1, "y": 0, "layer": 0},
            {"x": 3, "y": 0, "layer": 0},
        ]
    ]
    sc, body = inspect(req)
    assert sc == 200
    dups = [v for v in body["violations"] if v["rule"] == "duplicate_cell"]
    assert len(dups) == 1
    assert dups[0]["steps"] == [1, 3, 4]


def test_inspect_non_orthogonal_and_layer_change_moved():
    req = single_net_base()
    req["paths"] = [
        [
            {"x": 0, "y": 0, "layer": 0},
            {"x": 2, "y": 0, "layer": 0},   # diagonal-distance-2 jump
            {"x": 2, "y": 1, "layer": 1},   # layer change at new (x, y)
            {"x": 3, "y": 0, "layer": 0},   # layer change AND far jump
            {"x": 3, "y": 0, "layer": 0},
        ]
    ]
    sc, body = inspect(req)
    assert sc == 200
    non_o = find(body, "non_orthogonal_move")
    assert non_o["steps"] == [0, 1]
    moved = [v for v in body["violations"]
             if v["rule"] == "layer_change_moved"]
    assert [v["steps"] for v in moved] == [[1, 2], [2, 3]]


def test_inspect_via_counts_once_per_coordinate_occupying_both_layers():
    # visits (1,0) on both layers via two in-place layer changes: one via
    req = single_net_base(via_limit=0)
    req["paths"] = [
        [
            {"x": 0, "y": 0, "layer": 0},
            {"x": 1, "y": 0, "layer": 0},
            {"x": 1, "y": 0, "layer": 1},
            {"x": 2, "y": 0, "layer": 1},
            {"x": 2, "y": 0, "layer": 0},
            {"x": 3, "y": 0, "layer": 0},
        ]
    ]
    sc, body = inspect(req)
    assert sc == 200
    v = find(body, "via_limit_exceeded")
    assert v["steps"] == [1, 2, 3, 4]
    assert v["cells"] == [
        {"x": 1, "y": 0, "layer": 0},
        {"x": 1, "y": 0, "layer": 1},
        {"x": 2, "y": 0, "layer": 0},
        {"x": 2, "y": 0, "layer": 1},
    ]
    # exactly one via-limit violation (two via coordinates, limit 0)
    assert rules(body).count("via_limit_exceeded") == 1


def test_inspect_via_limit_not_exceeded_is_silent():
    req = single_net_base(via_limit=2)
    req["paths"] = [
        [
            {"x": 0, "y": 0, "layer": 0},
            {"x": 1, "y": 0, "layer": 0},
            {"x": 1, "y": 0, "layer": 1},
            {"x": 2, "y": 0, "layer": 1},
            {"x": 2, "y": 0, "layer": 0},
            {"x": 3, "y": 0, "layer": 0},
        ]
    ]
    sc, body = inspect(req)
    assert sc == 200 and body["legal"] is True


def test_inspect_broken_net_does_not_stop_other_checks():
    # net 0 is a wreck; net 2 has an orthogonal adjacency against net 1
    # and must still be reported
    req = {
        "width": 4,
        "height": 4,
        "nets": [
            {"pads": [{"x": 0, "y": 0, "layer": 0},
                      {"x": 3, "y": 3, "layer": 0}], "via_limit": 0},
            {"pads": [{"x": 0, "y": 0, "layer": 1},
                      {"x": 3, "y": 3, "layer": 1}], "via_limit": 0},
            {"pads": [{"x": 0, "y": 1, "layer": 1},
                      {"x": 3, "y": 1, "layer": 1}], "via_limit": 0},
        ],
        "paths": [
            [{"x": 9, "y": 9, "layer": 7}],
            [{"x": i, "y": 0, "layer": 1} for i in range(4)],
            [{"x": i, "y": 1, "layer": 1} for i in range(4)],
        ],
    }
    sc, body = inspect(req)
    assert sc == 200
    # net 0 violations present
    assert find(body, "out_of_bounds", [0]) is not None
    assert find(body, "illegal_layer", [0]) is not None
    # and net 1/2 adjacency still checked (4 geometric pairs)
    adj = [v for v in body["violations"] if v["rule"] == "net_adjacent"]
    assert len(adj) == 4
    assert all(v["nets"] == [1, 2] for v in adj)


# ------------------------------- cross-net rules ---------------------------

def test_inspect_net_overlap():
    req = {
        "width": 3,
        "height": 2,
        "nets": [
            {"pads": [{"x": 0, "y": 0, "layer": 0},
                      {"x": 2, "y": 0, "layer": 0}], "via_limit": 0},
            {"pads": [{"x": 0, "y": 1, "layer": 0},
                      {"x": 2, "y": 1, "layer": 0}], "via_limit": 0},
        ],
        "paths": [
            [{"x": i, "y": 0, "layer": 0} for i in range(3)],
            [{"x": 0, "y": 1, "layer": 0},
             {"x": 1, "y": 1, "layer": 0},
             {"x": 1, "y": 0, "layer": 0},   # steps 2 and 3 coincide
             {"x": 1, "y": 0, "layer": 0},   # with net 0 step 1
             {"x": 2, "y": 1, "layer": 0}],
        ],
    }
    sc, body = inspect(req)
    assert sc == 200
    v = find(body, "net_overlap", [0, 1])
    assert v["steps"] == [1, 2, 3]
    assert v["cells"] == [{"x": 1, "y": 0, "layer": 0}]


def test_inspect_three_nets_same_cell_three_pairs():
    cell = {"x": 1, "y": 1, "layer": 1}
    req = {
        "width": 3,
        "height": 3,
        "blocked": [dict(cell)],  # blocked cells still participate
        "nets": [
            {"pads": [{"x": 0, "y": 1, "layer": 1},
                      {"x": 2, "y": 1, "layer": 1}], "via_limit": 0},
            {"pads": [{"x": 1, "y": 0, "layer": 1},
                      {"x": 1, "y": 2, "layer": 1}], "via_limit": 0},
            {"pads": [{"x": 0, "y": 0, "layer": 1},
                      {"x": 2, "y": 2, "layer": 1}], "via_limit": 0},
        ],
        "paths": [
            [{"x": 0, "y": 1, "layer": 1}, dict(cell),
             {"x": 2, "y": 1, "layer": 1}],
            [{"x": 1, "y": 0, "layer": 1}, dict(cell),
             {"x": 1, "y": 2, "layer": 1}],
            [{"x": 0, "y": 0, "layer": 1}, dict(cell), dict(cell),
             {"x": 2, "y": 2, "layer": 1}],
        ],
    }
    sc, body = inspect(req)
    assert sc == 200
    overlaps = [v for v in body["violations"] if v["rule"] == "net_overlap"]
    assert sorted(v["nets"] for v in overlaps) == [
        [0, 1], [0, 2], [1, 2]
    ]
    # repeated visits of net 2 (steps 1 and 2) are kept
    pair_12 = find(body, "net_overlap", [1, 2])
    assert pair_12["steps"] == [1, 1, 2]
    # every net using the blocked cell gets its own blocked violation(s)
    blocked_nets = sorted({v["nets"][0]
                           for v in body["violations"]
                           if v["rule"] == "blocked"})
    assert blocked_nets == [0, 1, 2]


def test_inspect_mirror_adjacency_dedups_but_keeps_pairs_and_steps():
    req = {
        "width": 3,
        "height": 2,
        "nets": [
            {"pads": [{"x": 0, "y": 0, "layer": 0},
                      {"x": 2, "y": 0, "layer": 0}], "via_limit": 0},
            {"pads": [{"x": 0, "y": 1, "layer": 0},
                      {"x": 2, "y": 1, "layer": 0}], "via_limit": 0},
        ],
        "paths": [
            [{"x": i, "y": 0, "layer": 0} for i in range(3)],
            [{"x": i, "y": 1, "layer": 0} for i in range(3)],
        ],
    }
    sc, body = inspect(req)
    assert sc == 200
    adj = [v for v in body["violations"] if v["rule"] == "net_adjacent"]
    # one entry per geometric cell pair (column), mirrors collapsed
    assert len(adj) == 3
    assert [v["steps"] for v in adj] == [[0, 0], [1, 1], [2, 2]]
    assert all(v["nets"] == [0, 1] for v in adj)
    assert adj[0]["cells"] == [
        {"x": 0, "y": 0, "layer": 0},
        {"x": 0, "y": 1, "layer": 0},
    ]


def test_inspect_diagonal_cells_are_legal():
    # diagonal (not orthogonal) proximity across nets is allowed
    req = {
        "width": 2,
        "height": 2,
        "nets": [
            {"pads": [{"x": 0, "y": 0, "layer": 0},
                      {"x": 0, "y": 1, "layer": 0}], "via_limit": 0},
            {"pads": [{"x": 1, "y": 0, "layer": 0},
                      {"x": 1, "y": 1, "layer": 0}], "via_limit": 0},
        ],
        "paths": [
            [{"x": 0, "y": 0, "layer": 0}],
            [{"x": 1, "y": 1, "layer": 0}],
        ],
    }
    sc, body = inspect(req)
    assert sc == 200
    # only the two wrong tail endpoints: no adjacency for diagonal cells
    assert rules(body) == ["endpoint", "endpoint"]


def test_inspect_out_of_bounds_steps_ignored_by_cross_net_checks():
    # net 0's illegal steps must not spuriously conflict; net 1's real
    # in-board cells are still compared among themselves
    req = {
        "width": 3,
        "height": 3,
        "nets": [
            {"pads": [{"x": 0, "y": 0, "layer": 0},
                      {"x": 2, "y": 0, "layer": 0}], "via_limit": 0},
            {"pads": [{"x": 0, "y": 2, "layer": 0},
                      {"x": 2, "y": 2, "layer": 0}], "via_limit": 0},
        ],
        "paths": [
            [{"x": 0, "y": 0, "layer": 0},
             {"x": 5, "y": 0, "layer": 0},
             {"x": 0, "y": 0, "layer": 9},
             {"x": 2, "y": 0, "layer": 0}],
            [{"x": i, "y": 2, "layer": 0} for i in range(3)],
        ],
    }
    sc, body = inspect(req)
    assert sc == 200
    assert all(v["rule"] != "net_overlap" for v in body["violations"])
    assert all(v["rule"] != "net_adjacent" for v in body["violations"])
    # net 0 still gets its own in-board rules
    assert "out_of_bounds" in rules(body)
    assert "illegal_layer" in rules(body)


# ------------------------------- sort order ---------------------------------

def test_inspect_violations_sorted_by_net_step_rule():
    req = {
        "width": 4,
        "height": 4,
        "blocked": [{"x": 0, "y": 0, "layer": 0}],
        "nets": [
            {"pads": [{"x": 3, "y": 3, "layer": 0},
                      {"x": 3, "y": 2, "layer": 0}], "via_limit": 0},
            {"pads": [{"x": 0, "y": 1, "layer": 1},
                      {"x": 3, "y": 3, "layer": 1}], "via_limit": 0},
        ],
        "paths": [
            [{"x": 0, "y": 0, "layer": 0}, {"x": 3, "y": 3, "layer": 0}],
            [{"x": 0, "y": 0, "layer": 1},
             {"x": 3, "y": 3, "layer": 1}],
        ],
    }
    sc, body = inspect(req)
    assert sc == 200
    keys = [
        (tuple(v["nets"]), v["steps"][0] if v["steps"] else -1, v["rule"])
        for v in body["violations"]
    ]
    assert keys == sorted(keys)
    # all net-0 entries precede net-1 entries; within the same first step
    # the rule codes sort alphabetically (blocked before endpoint)
    net_ids = [v["nets"][0] for v in body["violations"]]
    assert net_ids == sorted(net_ids)
    step0_net0 = [v for v in body["violations"]
                  if v["nets"] == [0] and v["steps"][0] == 0]
    assert [v["rule"] for v in step0_net0] == sorted(
        v["rule"] for v in step0_net0
    )


# --------------------------------- 422 --------------------------------------

def test_inspect_422_missing_paths():
    req = single_net_base()
    assert client.post("/inspect", json=req).status_code == 422


def test_inspect_422_path_count_mismatch():
    req = single_net_base()
    req["paths"] = []
    assert client.post("/inspect", json=req).status_code == 422
    req["paths"] = [
        [{"x": 0, "y": 0, "layer": 0}],
        [{"x": 0, "y": 0, "layer": 0}],
    ]
    assert client.post("/inspect", json=req).status_code == 422


@pytest.mark.parametrize("bad", [
    {"x": 1.5, "y": 0, "layer": 0},
    {"x": 0, "y": "1", "layer": 0},
    {"x": None, "y": 0, "layer": 0},
    {"x": [0], "y": 0, "layer": 0},
])
def test_inspect_422_non_integer_coordinates(bad):
    req = single_net_base()
    req["paths"] = [[bad]]
    assert client.post("/inspect", json=req).status_code == 422


def test_inspect_integral_float_coordinates_accepted():
    # Pydantic int coercion accepts 3.0 (it is an integral number); the
    # steps are far apart, so the path is a 200 non_orthogonal violation
    req = single_net_base()
    req["paths"] = [[{"x": 0.0, "y": 0, "layer": 0},
                     {"x": 3.0, "y": 0, "layer": 0}]]
    sc, body = inspect(req)
    assert sc == 200
    assert "non_orthogonal_move" in rules(body)


def test_inspect_boolean_coordinate_is_422():
    # true/false must not be accepted as the integers 1/0
    req = single_net_base()
    raw = json.dumps({
        "width": 4, "height": 4,
        "nets": req["nets"],
        "paths": [[{"x": True, "y": 0, "layer": 0}]],
    })
    r = client.post("/inspect", content=raw,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_inspect_422_path_longer_than_board_cells():
    w, h = 3, 2
    req = {
        "width": w,
        "height": h,
        "nets": [
            {"pads": [{"x": 0, "y": 0, "layer": 0},
                      {"x": w - 1, "y": 0, "layer": 0}], "via_limit": 0}
        ],
    }
    req["paths"] = [
        [{"x": 0, "y": 0, "layer": 0}] * (w * h * 2 + 1)
    ]
    assert client.post("/inspect", json=req).status_code == 422
    # exactly the maximum length is a 200 (duplicate_cell violation)
    req["paths"] = [[{"x": 0, "y": 0, "layer": 0}] * (w * h * 2)]
    sc, body = inspect(req)
    assert sc == 200
    assert "duplicate_cell" in rules(body)


def test_inspect_422_board_validation_unchanged():
    # blocked cell out of bounds
    req = single_net_base()
    req["blocked"] = [{"x": 9, "y": 0, "layer": 0}]
    req["paths"] = [[{"x": 0, "y": 0, "layer": 0}]]
    assert client.post("/inspect", json=req).status_code == 422
    # bad dimensions
    req = single_net_base()
    req["width"] = 1
    req["paths"] = [[]]
    assert client.post("/inspect", json=req).status_code == 422


# ------------------------- /route must stay unchanged -----------------------

def test_route_still_works_after_inspect_added():
    req = {
        "width": 5,
        "height": 5,
        "nets": [
            {"pads": [{"x": 0, "y": 0, "layer": 0},
                      {"x": 4, "y": 0, "layer": 0}],
             "via_limit": 3}
        ],
    }
    r = client.post("/route", json=req)
    assert r.status_code == 200
    resp = r.json()
    assert resp["status"] == "optimal"
    assert set(resp) == {
        "status", "solve_time_seconds", "total_vias", "total_moves", "nets"
    }
    # /route does not accept the inspect-only paths field as a way to skip
    # solving: extra fields are ignored, response is still a solver result
    req_with_paths = dict(req)
    req_with_paths["paths"] = [[]]
    r2 = client.post("/route", json=req_with_paths)
    assert r2.status_code == 200
    assert r2.json()["status"] == "optimal"
