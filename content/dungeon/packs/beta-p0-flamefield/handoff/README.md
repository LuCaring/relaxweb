# P0 battle vertical-slice design handoff

This directory carries the designer's current P0 source material for the C-line
`beta.p0.flamefield` pack. It is documentation, not a registered content file:
`manifest.json` and the release compositions do not reference these files.

## Read order

1. `p0-technical-handoff-v0.1.pdf` (or its editable Markdown twin): concise
   implementation handoff, acceptance targets, and remaining work.
2. `p0-battle-decisions-v0.2.md` and `p0-product-decisions-v0.2.md`: decisions
   made during P0 planning.
3. `g02-actions-enemies-v0.2.md`, `g03-room-encounters-v0.2.md`, and
   `g04-combat-formulas-events-v0.2.md`: detailed design source tables.
4. The G02/G03 discussion drafts, damage decisions, planning, freeze, and
   migration notes: supporting context, not executable schema.

The source documents are retained as dated design records. They do **not**
silently replace the repository's current contracts, runtime, or package JSON.
The development team should resolve a design/implementation mismatch before
changing executable content or shared files.

## Known integration differences

- The design handoff and product decision table say a failed P0 run earns no
  reward. The current repository pack pays permanent rewards when each room is
  validly cleared, and its `NOTES.md` says cleared-room rewards survive a later
  failure. For this submission, the existing repository behavior remains in
  force; these copied design statements are **not** a request to alter the
  reward transaction. Product and engineering should explicitly freeze a
  future unified rule.
- The design documents describe movement, auto-attacks, hit geometry, monster
  actions, and timing targets that the current content schema cannot express
  completely. These are implementation/validation targets, not claims that
  the playable client or simulator already supports them.
- The handoff's references to TypeScript/Phaser are historical planning
  assumptions. This repository is a Python web game; the actual client and
  simulator architecture here take precedence.

## File inventory

| File | Source in the designer workspace |
| --- | --- |
| `p0-technical-handoff-v0.1.md` / `.pdf` | 小胖地下城P0战斗竖切技术交接V0.1 |
| `p0-battle-decisions-v0.2.md` | P0战斗竖切决策记录V0.2 |
| `p0-product-decisions-v0.2.md` | P0一页产品决策表V0.2 |
| `g02-actions-enemies-v0.2.md` | G02动作表与敌人招式表V0.2 |
| `g02-collision-movement-v0.1.md` | G02单位体型碰撞与移动讨论稿V0.1 |
| `g03-room-encounters-v0.2.md` | G03房间拓扑与遭遇刷怪表V0.2 |
| `g03-four-room-layout-v0.2.md` | G03四房空间布局与刷怪配置讨论稿V0.2 |
| `g03-curved-generation-v0.1.md` | G03随机曲折地图生成规则讨论稿V0.1 |
| `g04-combat-formulas-events-v0.2.md` | G04战斗公式与事件表V0.2 |
| `damage-decisions-v0.1.md` | 伤害数值体系待确认决策表V0.1 |
| `p0-planning-v0.1.md` | P0战斗竖切策划任务计划V0.1 |
| `predevelopment-freeze-v0.1.md` | 开发前策划冻结总表V0.1 |
| `migration-handoff-v0.2.md` | 开发迁移交接说明V0.2 |

Superseded V0.1 tables, the historical pending-decision sheet, and the
unverified DOCX draft are intentionally excluded. No A-line files are changed
by this handoff commit.
