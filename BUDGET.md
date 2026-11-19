# 补扫预算复测

## 从扫描工位到复测室

按 README.md 初始化站库和中心库，沿用固定调查清单和扫描修订。新增 `evidence.issue.issue_budget(station, revision_id, threshold)`，按指定修订和门槛发行 `IssuedPacket`。门槛是 0 至 100 的 Python 整数，布尔值非法。未知修订号沿用 `KeyError`。用 `evidence.readback.read_station_packet` 可领取相同站件及原文。

补扫件沿用 README.md 的站件外层字段，`version` 为整数 `2`。`body` 是含 `threshold` 和 `evidence` 的 JSON 对象，`threshold` 为签发门槛，`evidence` 是代价证据对象，其内部表示由实现决定。V2 只把登记的公开端口及其代价关系交给中心，非公开像元的坐标、逐点分值和逐点记录留站。证据须足以在停源后针对所选各幅及任意已登记探针对核定全幅最小用量。

用 `intake.accept.accept(center, layout, packet.payload)` 接纳，返回原有 `AcceptanceReceipt`。接纳时检查 V2 外层、清单身份、幅归属和代价证据格式。坏件报 `ValueError` 且接纳整次无新增。相同站件号和相同载荷原文重投沿用原回执，同号异文拒收。`intake.records.load_acceptance` 按接纳号领取固定原文及回执。

## 签发及领取

新增 `reports.publish.budget_issue(center, survey_id, acceptance_ids, probe_a, probe_b, budget)`。`acceptance_ids` 是列表或元组，同一固定调查每幅恰选一件，均须是已接纳的 V2 件。门槛取本次选件共同的固定值。`probe_a` 和 `probe_b` 是清单中的探针名，可指向同一物理端口。`budget` 为非负 Python 整数，布尔值非法。未知调查、探针或接纳号，缺幅、重幅、混单、混门槛、混版、坏件及非法输入报 `ValueError`。V1 普通报告沿用原选件口径。

每条迹线按幅内四邻和许可的正对接缝行走，同幅探针对可以出幅后再入。缺测不可穿越。达到门槛的已测像元免费，低于门槛的已测像元每处需补扫一次。判定最少用量时包含途经的公开端口及起终点。同一点兼具多个角色仍是同一像元，已测的同点探针对也可使用单像元迹线。最少用量为整数，含零。无路的用量为 `None`。仅当有路且用量小于或等于预算时 `passed=True`。

预算报告返回 `report_id`、`survey_id`、`layout_digest`、`parents`、`decision` 和 `receipt` 属性。`parents` 是按幅号排列的 `AcceptanceReceipt` 元组。`decision` 有 `probe_a`、`probe_b`、`threshold`、`budget`、`minimum` 和 `passed` 属性。`receipt` 沿用 `PublishReceipt` 的报告号、调查号及父站件号元组，站件号顺序与父件一致。报告与回执在同次事务提交，写入或提交失败无部分成果且可重试。

新增 `reports.readback.retrieve_budget(center, report_id)`。报告号是 1 至 2⁶³−1 的非布尔整数，非法编号报 `ValueError`，合法未知号返回 `None`。领取返回已保存的报告及回执，勘误、改选和重开后按旧号仍可领取原值。站库与中心库可以沿原入口再次初始化。保留 V1 接口、载荷、旧成果和全部原回归。

## 业务样例

`budget_examples.revision_pair()` 返回固定版图和两版按幅号组织的栅格，`python3 -B budget_examples.py` 可核对两版原站件正文相同及 V1 裁决一致。下面是实现补扫入口后的首版调用，站库在中心接纳前已关闭。

```python
from contextlib import closing
import sqlite3

from budget_examples import revision_pair
from demo import initialize_center, initialize_station
from evidence.issue import issue_budget
from intake.accept import accept
from reports.publish import budget_issue
from reports.readback import retrieve_budget
from survey.catalog import register
from survey.freeze import freeze_grid

layout, versions = revision_pair()
with closing(sqlite3.connect(':memory:')) as station:
    initialize_station(station)
    register(station, layout)
    revision = freeze_grid(station, layout.survey_id, 'line', versions[0]['line'])
    packet = issue_budget(station, revision, 6)
with closing(sqlite3.connect(':memory:')) as center:
    initialize_center(center)
    ack = accept(center, layout, packet.payload)
    report = budget_issue(center, layout.survey_id, [ack.acceptance_id], 'a', 'b', 1)
    assert (report.decision.minimum, report.decision.passed) == (1, True)
    assert retrieve_budget(center, report.report_id) == report
```

| 输入 | 门槛、预算 | `minimum`、`passed` |
| --- | --- | --- |
| `revision_pair()` 的 `a` 到 `b`，两版分别为 `7/1/7/7` 和 `7/1/1/7` | 6、1 | `(1, True)`、`(2, False)` |
| `seam_pair()` 首版的 `a` 到 `b`，西幅 `7/1/1` 接东幅 `7`，西幅中点公开、末点同时是探针和接缝端口 | 6、2 | `(2, True)` |
| 同一首版的 `edge` 到 `edge` | 6、1 | `(1, True)` |
| `seam_pair()` 勘误版的 `a` 到 `b`，西幅末点缺测 | 6、2 | `(None, False)` |
| `examples/scan.json` 初版的 `top` 到 `bottom`，经东幅返回 | 5、0 | `(0, True)` |
| 同一原样例仅改选东幅勘误件 | 5、0 | `(1, False)` |

沿 `.pointwise-runtime.json` 的 `verify` 和 `demo` 接通联合验证和演示。核对停源后的补扫用量、错误选件拒绝、提交失败及重试、勘误后重开按原号领取，保留原回归和失败退出码。可用 `tests/support.py` 的 `ProjectCase` 建两库并调用 `reopen_center()`，参照 `tests/test_handover.py` 的 `test_failed_commit_rolls_back_all_three_records`，把延迟外键触发器挂到实际承载预算报告或回执的存储表上，核对失败前后的账目并撤除触发器重试。演示展示实际补扫用量、预算裁决及重开领取结果。
