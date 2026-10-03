# 双层电路板网格布线服务

基于 **Python + FastAPI + OR-Tools (CP-SAT)** 的双层电路板网格布线服务。
对所有网络**联合求解**，先最小化总过孔数，再最小化同层移动总步数，
并只在证明完成后才报告 `optimal` / `infeasible`。

## 快速开始

### Docker Compose（一键启动）

```bash
docker compose up --build
```

服务监听 `http://localhost:8000`：

- `POST /route` — 联合布线（CP-SAT 求解）
- `POST /inspect` — 走线审查：校验给定候选路径的全部走线违规，不重新求解
- `GET  /health` — 健康检查
- `GET  /docs` — Swagger UI 交互文档

### 本地开发

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

运行测试：

```bash
pip install pytest httpx
python -m pytest tests/ -q
```

## 布线规则

- 网格宽、高均为 2～20 的整数；层编号为 `0` / `1`。
- 请求含两层各自的禁布格（`blocked`）以及 1～6 条双焊盘网络（`nets`）。
- 走线只能沿**同层正交相邻格**移动；**过孔**在原坐标打通两层（占两层各一格）。
- 每条网络的路径不得重复占格。
- 不同网络在**同层**的占格不得重合，也不得正交相邻（对角相邻允许）。
- 每条网络有非负过孔上限 `via_limit`。
- 目标（字典序）：先最小化**总过孔数**，再最小化**同层移动总步数**。

`POST /inspect` 不求解，只按上述规则审查调用方给定的候选路径并报告
全部违规，详见下文 [POST /inspect](#post-inspect走线审查不求解)。

## POST /route

### 请求体

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `width` | int | 网格宽，2～20 |
| `height` | int | 网格高，2～20 |
| `blocked` | array | 禁布格列表，元素为 `{"x", "y", "layer"}`，可省略 |
| `nets` | array | 1～6 条网络 |
| `nets[].name` | string | 可选名称，缺省自动命名 `N1`、`N2`… |
| `nets[].pads` | array | 恰好 2 个焊盘，各为 `{"x", "y", "layer"}` |
| `nets[].via_limit` | int | 非负整数，该网过孔数上限 |

坐标从 `0` 开始，`0 <= x < width`，`0 <= y < height`，`layer ∈ {0, 1}`。

### 响应体

| 字段 | 说明 |
| --- | --- |
| `status` | `optimal`：已证明最优；`feasible`：10 秒限时到、已有可行方案（未证明最优）；`infeasible`：已证明无解；`unknown`：10 秒限时到、尚未找到任何可行方案 |
| `solve_time_seconds` | 求解耗时（秒） |
| `total_vias` | 总过孔数（`infeasible`/`unknown` 时为 `null`） |
| `total_moves` | 同层移动总步数（`infeasible`/`unknown` 时为 `null`） |
| `nets[].path` | 该网从 `pads[0]` 到 `pads[1]` 的有序占格序列（含过孔两端的层切换） |
| `nets[].vias` | 该网过孔坐标列表（该坐标两层均被占用） |
| `nets[].via_count` / `nets[].move_count` | 该网过孔数 / 同层移动步数 |

求解时限为每次请求 **10 秒**（可用环境变量 `ROUTER_TIME_LIMIT` 覆盖，
见 `docker-compose.yml`）。只有完成证明时才返回 `optimal` 或 `infeasible`；
限时触发时按是否已找到可行方案区分 `feasible` 与 `unknown`。

### 实际请求与响应示例

下面是一个真实可复现的例子（8×8 板，第 0 层 `x=3` 整列禁布形成一堵墙，
N1 必须借道第 1 层；N2 在第 1 层直行，注意 N1 在第 1 层的走线段与 N2
保持了至少一格间距）：

```bash
curl -s -X POST http://localhost:8000/route \
  -H 'Content-Type: application/json' \
  -d '{
  "width": 8,
  "height": 8,
  "blocked": [
    {"x": 3, "y": 0, "layer": 0}, {"x": 3, "y": 1, "layer": 0},
    {"x": 3, "y": 2, "layer": 0}, {"x": 3, "y": 3, "layer": 0},
    {"x": 3, "y": 4, "layer": 0}, {"x": 3, "y": 5, "layer": 0},
    {"x": 3, "y": 6, "layer": 0}, {"x": 3, "y": 7, "layer": 0}
  ],
  "nets": [
    {"name": "N1", "pads": [{"x": 0, "y": 3, "layer": 0}, {"x": 7, "y": 3, "layer": 0}], "via_limit": 2},
    {"name": "N2", "pads": [{"x": 0, "y": 5, "layer": 1}, {"x": 7, "y": 5, "layer": 1}], "via_limit": 0}
  ]
}'
```

实际响应（`solve_time_seconds` 随机器略有浮动）：

```json
{
  "status": "optimal",
  "solve_time_seconds": 0.234,
  "total_vias": 2,
  "total_moves": 14,
  "nets": [
    {
      "name": "N1",
      "path": [
        {"x": 0, "y": 3, "layer": 0},
        {"x": 1, "y": 3, "layer": 0},
        {"x": 2, "y": 3, "layer": 0},
        {"x": 2, "y": 3, "layer": 1},
        {"x": 3, "y": 3, "layer": 1},
        {"x": 4, "y": 3, "layer": 1},
        {"x": 5, "y": 3, "layer": 1},
        {"x": 6, "y": 3, "layer": 1},
        {"x": 7, "y": 3, "layer": 1},
        {"x": 7, "y": 3, "layer": 0}
      ],
      "vias": [
        {"x": 2, "y": 3},
        {"x": 7, "y": 3}
      ],
      "via_count": 2,
      "move_count": 7
    },
    {
      "name": "N2",
      "path": [
        {"x": 0, "y": 5, "layer": 1},
        {"x": 1, "y": 5, "layer": 1},
        {"x": 2, "y": 5, "layer": 1},
        {"x": 3, "y": 5, "layer": 1},
        {"x": 4, "y": 5, "layer": 1},
        {"x": 5, "y": 5, "layer": 1},
        {"x": 6, "y": 5, "layer": 1},
        {"x": 7, "y": 5, "layer": 1}
      ],
      "vias": [],
      "via_count": 0,
      "move_count": 7
    }
  ]
}
```

路径中相邻两格 `layer` 相同表示一次同层移动，`layer` 不同（坐标不变）
表示一次过孔。

## POST /inspect（走线审查，不求解）

`/inspect` 接收与 `/route` 相同的板面尺寸、禁布格、网络定义，外加一个
按网序排列的**有序候选路径**数组 `paths`（`paths[k]` 对应 `nets[k]`）。
服务**不运行求解器**，只逐条核对给定走线并报告**全部**违规：一条网存在
违规时，仍会继续检查它的其余规则以及其他所有网络。

### 请求体

在 `/route` 请求体基础上增加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `paths` | array | 与 `nets` 等长，每元素为一条路径（从 `pads[0]` 到 `pads[1]` 的有序占格序列） |
| `paths[][].x` / `y` / `layer` | int | 路径步的格坐标；此处**不做**板面范围限制（见下） |

### 违规规则码

每条违规对象含 `rule`（规则码）、`nets`（涉及网的下标）、`steps`（涉及
的步号，从 `0` 开始）、`cells`（涉及的格）：

| `rule` | 含义 |
| --- | --- |
| `endpoint` | 路径首格不是 `pads[0]`，或末格不是 `pads[1]`；空路径报一条，`steps`/`cells` 为空 |
| `out_of_bounds` | 步格 `x`/`y` 越出板面 |
| `illegal_layer` | 步格 `layer` 不是 `0`/`1` |
| `blocked` | 步格落在禁布格上 |
| `duplicate_cell` | 同一路径重复占用同一 `(x, y, layer)` |
| `non_orthogonal_move` | 同层相邻两步不是正交相邻（曼哈顿距离 ≠ 1） |
| `layer_change_moved` | 相邻两步换层但坐标发生变化（换层必须在原坐标） |
| `via_limit_exceeded` | 过孔数（同坐标两层均被该网占用，每个这样的坐标计 1 个过孔）超过该网 `via_limit` |
| `net_overlap` | 两条不同网在同层占用同一格 |
| `net_adjacent` | 两条不同网在同层占用正交相邻格（对角相邻允许） |

规则细节：

- **越界与非法层不是请求错误**：仍返回 HTTP 200，分别以
  `out_of_bounds` / `illegal_layer` 报违规（同一格可同时触发两条）；
  它们不参与跨网比较，但该网的其余规则照常检查。
- **跨网比较只取板内合法层的格**；落在**禁布格**上的占格仍参与跨网
  比较。同一格被三条网占用时报三对（`(0,1)`、`(0,2)`、`(1,2)`）。
- 异网冲突对镜像方向去重（每对几何格只报一条），但保留**不同网对**
  以及各网**重复访问**的全部步号。
- 违规按 **网序 → 步号 → 规则码** 排序；无任何违规时才返回
  `"legal": true`。

### 实际请求与响应示例（含违规）

```bash
curl -s -X POST http://localhost:8000/inspect \
  -H 'Content-Type: application/json' \
  -d '{
  "width": 6,
  "height": 5,
  "blocked": [
    {"x": 2, "y": 1, "layer": 0},
    {"x": 3, "y": 3, "layer": 1}
  ],
  "nets": [
    {"name": "N1", "pads": [{"x": 0, "y": 1, "layer": 0}, {"x": 5, "y": 1, "layer": 0}], "via_limit": 1},
    {"name": "N2", "pads": [{"x": 0, "y": 3, "layer": 1}, {"x": 5, "y": 3, "layer": 1}], "via_limit": 0}
  ],
  "paths": [
    [
      {"x": 0, "y": 1, "layer": 0},
      {"x": 1, "y": 1, "layer": 0},
      {"x": 2, "y": 1, "layer": 0},
      {"x": 4, "y": 1, "layer": 0},
      {"x": 4, "y": 1, "layer": 1},
      {"x": 4, "y": 2, "layer": 1},
      {"x": 4, "y": 2, "layer": 0},
      {"x": 5, "y": 1, "layer": 0}
    ],
    [
      {"x": 0, "y": 3, "layer": 1},
      {"x": 1, "y": 3, "layer": 1},
      {"x": 2, "y": 3, "layer": 1},
      {"x": 3, "y": 3, "layer": 1},
      {"x": 4, "y": 2, "layer": 1},
      {"x": 4, "y": 3, "layer": 1},
      {"x": 5, "y": 3, "layer": 1}
    ]
  ]
}'
```

实际响应（该例中 N1 踩禁布格、跳步、过孔超上限；N2 踩禁布格、斜走；
两网在第 1 层还有重合与正交相邻）：

```json
{
  "legal": false,
  "violations": [
    {
      "rule": "blocked",
      "nets": [0],
      "steps": [2],
      "cells": [{"x": 2, "y": 1, "layer": 0}]
    },
    {
      "rule": "non_orthogonal_move",
      "nets": [0],
      "steps": [2, 3],
      "cells": [
        {"x": 2, "y": 1, "layer": 0},
        {"x": 4, "y": 1, "layer": 0}
      ]
    },
    {
      "rule": "via_limit_exceeded",
      "nets": [0],
      "steps": [3, 4, 5, 6],
      "cells": [
        {"x": 4, "y": 1, "layer": 0},
        {"x": 4, "y": 1, "layer": 1},
        {"x": 4, "y": 2, "layer": 0},
        {"x": 4, "y": 2, "layer": 1}
      ]
    },
    {
      "rule": "non_orthogonal_move",
      "nets": [0],
      "steps": [6, 7],
      "cells": [
        {"x": 4, "y": 2, "layer": 0},
        {"x": 5, "y": 1, "layer": 0}
      ]
    },
    {
      "rule": "net_adjacent",
      "nets": [0, 1],
      "steps": [4, 4],
      "cells": [
        {"x": 4, "y": 1, "layer": 1},
        {"x": 4, "y": 2, "layer": 1}
      ]
    },
    {
      "rule": "net_overlap",
      "nets": [0, 1],
      "steps": [4, 5],
      "cells": [{"x": 4, "y": 2, "layer": 1}]
    },
    {
      "rule": "net_adjacent",
      "nets": [0, 1],
      "steps": [5, 5],
      "cells": [
        {"x": 4, "y": 2, "layer": 1},
        {"x": 4, "y": 3, "layer": 1}
      ]
    },
    {
      "rule": "blocked",
      "nets": [1],
      "steps": [3],
      "cells": [{"x": 3, "y": 3, "layer": 1}]
    },
    {
      "rule": "non_orthogonal_move",
      "nets": [1],
      "steps": [3, 4],
      "cells": [
        {"x": 3, "y": 3, "layer": 1},
        {"x": 4, "y": 2, "layer": 1}
      ]
    }
  ]
}
```

### 合法示例

候选路径全部合规时（N1 借第 1 层绕过第 0 层 `x=2` 的两格禁布墙，
与第 0 层的 N2 不在同层，互不相邻），返回：

```bash
curl -s -X POST http://localhost:8000/inspect \
  -H 'Content-Type: application/json' \
  -d '{
  "width": 5,
  "height": 4,
  "blocked": [{"x": 2, "y": 0, "layer": 0}, {"x": 2, "y": 1, "layer": 0}],
  "nets": [
    {"name": "N1", "pads": [{"x": 0, "y": 0, "layer": 0}, {"x": 4, "y": 0, "layer": 0}], "via_limit": 2},
    {"name": "N2", "pads": [{"x": 0, "y": 3, "layer": 0}, {"x": 4, "y": 3, "layer": 0}], "via_limit": 0}
  ],
  "paths": [
    [
      {"x": 0, "y": 0, "layer": 0},
      {"x": 1, "y": 0, "layer": 0},
      {"x": 1, "y": 0, "layer": 1},
      {"x": 2, "y": 0, "layer": 1},
      {"x": 3, "y": 0, "layer": 1},
      {"x": 3, "y": 0, "layer": 0},
      {"x": 4, "y": 0, "layer": 0}
    ],
    [
      {"x": 0, "y": 3, "layer": 0},
      {"x": 1, "y": 3, "layer": 0},
      {"x": 2, "y": 3, "layer": 0},
      {"x": 3, "y": 3, "layer": 0},
      {"x": 4, "y": 3, "layer": 0}
    ]
  ]
}'
```

```json
{"legal": true, "violations": []}
```

### `/inspect` 的输入校验（HTTP 422）

以下情况返回 `422`（区别于上面的 200 走线违规）：

- 缺少 `paths` 字段，或路径条数与网络条数不一致；
- 步坐标不是整数（如 `1.5`、`"1"`、`true`；整数浮点 `1.0` 会被规整为 `1`）；
- 单条路径的步数超过板面总格数 `width * height * 2`；
- `/route` 原有的板面校验失败：宽高越界、网络数不在 1～6、焊盘越界/
  非法层、焊盘压禁布格、重复焊盘、禁布格越界、`via_limit` 非法等。

空路径不是 422：它以 HTTP 200 报一条空 `steps`/`cells` 的 `endpoint`
违规。`/route` 的请求、响应格式与求解状态语义不受 `/inspect` 影响。

### 无解示例

两焊盘分处不同层但 `via_limit` 为 0，可证明无解：

```bash
curl -s -X POST http://localhost:8000/route \
  -H 'Content-Type: application/json' \
  -d '{
  "width": 4,
  "height": 4,
  "nets": [
    {"name": "N1", "pads": [{"x": 0, "y": 0, "layer": 0}, {"x": 3, "y": 3, "layer": 1}], "via_limit": 0}
  ]
}'
```

```json
{
  "status": "infeasible",
  "solve_time_seconds": 0.005,
  "total_vias": null,
  "total_moves": null,
  "nets": null
}
```

### 输入校验（HTTP 422）

以下非法请求返回 `422 Unprocessable Entity`（FastAPI/Pydantic 校验）：

- 网格宽高超出 2～20，或网络条数不在 1～6；
- 焊盘越界（`x`/`y` 超出网格或 `layer` 非 0/1）；
- 焊盘落在禁布格上；
- 重复焊盘（任意两焊盘的 `(x, y, layer)` 完全相同，含同一网内）；
- 过孔上限为负数或非整数；
- 禁布格越界。

示例（焊盘落在禁布格上）：

```bash
curl -s -X POST http://localhost:8000/route \
  -H 'Content-Type: application/json' \
  -d '{
  "width": 4, "height": 4,
  "blocked": [{"x": 1, "y": 1, "layer": 0}],
  "nets": [{"pads": [{"x": 1, "y": 1, "layer": 0}, {"x": 3, "y": 3, "layer": 0}], "via_limit": 1}]
}'
# HTTP 422
# {"detail":[{"type":"value_error","loc":["body"],
#   "msg":"Value error, net 0 pad (1, 1, layer 0) is on a blocked cell", ...}]}
```

## 实现要点

- **模型**（`app/solver.py`）：每条网络建模为从 `pads[0]` 到 `pads[1]` 的
  单位流。节点为两层上的自由格；弧为同层正交移动弧与过孔弧。除源/汇外
  每个节点入度=出度≤1，保证路径不重复占格；某网在同坐标两层均占格时
  过孔变量强制为 1（过孔占两层）。异网之间对同层每格及其正交邻格加
  互斥约束。
- **目标**：`min 4795 * 总过孔数 + 总移动步数`。权重大于最大可能步数
  （6 网 × 799 步 = 4794），因此严格等价于字典序（先过孔后步数）。
- **证明速度**：每网移动步数变量定义域下界直接取两焊盘曼哈顿距离、
  过孔变量下界按焊盘是否异层取 0/1，使根节点下界立即有效；另用一个
  快速顺序 Dijkstra 启发式把可行布线作为 hint 注入加速搜索。
- **限时语义**：CP-SAT `max_time_in_seconds = 10`。返回 `OPTIMAL` /
  `INFEASIBLE` 才分别报告 `optimal` / `infeasible`；限时到时有解报
  `feasible`、无解报 `unknown`。若求解器超时但启发式已找到经独立
  校验的完整合法布线，也按 `feasible` 返回该方案。
- **走线审查**（`app/inspector.py`）：`POST /inspect` 复用同一份板面
  校验（`schemas.py`），随后逐网独立检查端点、越界/非法层、禁布、
  重复占格、同层跳步、非原位换层与过孔上限（同坐标占两层计 1 孔），
  再跨网检查同层重合与正交相邻。越界/非法层走 200 违规而非 422，且
  不进入跨网比较；禁布格仍参与跨网比较。镜像冲突按几何格对去重，三网
  同格报三对，步号保留各网重复访问。结果按网序、步号、规则码排序。
- **解校验**：`tests/` 内含独立校验器，对 `/route` 返回的每条路径
  检查端点、占格不重复、同层正交移动、过孔占两层、禁布格、过孔上限、
  异网隔离及目标值一致性；`test_inspect.py` 覆盖 `/inspect` 全部规则码、
  去重/排序语义与 422 输入校验。

## 项目结构

```
app/
  main.py      # FastAPI 入口，POST /route、POST /inspect、GET /health
  schemas.py   # Pydantic 请求/响应模型与 422 校验
  solver.py    # CP-SAT 联合建模、启发式 hint、结果提取
  inspector.py # /inspect 规则检查器（不求解）
tests/
  test_router.py    # /route 端到端与求解语义
  test_inspect.py   # /inspect 违规报告、排序/去重、422
Dockerfile
docker-compose.yml
requirements.txt
```
