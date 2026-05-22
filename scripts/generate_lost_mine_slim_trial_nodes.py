"""生成 Lost Mine 的 slim canonical 正式节点。

该脚本用于按 PDF 原文续跑正式节点文件，支持按 scope 小步生成。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import fitz
from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = Path(
    r"C:\Users\wenliang\Downloads\DND_5E\DND_5E_规则书\DND_5E_新手入门套装CN\DnD_5E_新手套组_冒险模组CN.pdf"
)
DEFAULT_OUT = ROOT / "backend" / "data" / "adventures" / "lost_mine" / "nodes.json"
DEFAULT_REPORT = ROOT / "backend" / "data" / "adventures" / "lost_mine" / "nodes.report.json"
DEFAULT_REFERENCE = ROOT / "backend" / "data" / "adventures" / "lost_mine" / "node_generation_reference.json"


def load_generation_reference(path: Path = DEFAULT_REFERENCE) -> dict[str, Any]:
    """读取节点生成契约；脚本内置常量只作为文件缺失时的兜底。"""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


GENERATION_REFERENCE = load_generation_reference()


TARGET_NODE_IDS = [
    "adventure_hook_meet_me_in_phandalin",
    "goblin_ambush",
    "goblin_trail_to_cragmaw_hideout",
    "cragmaw_hideout_entrance",
    "cragmaw_hideout_goblin_blind",
]
TARGET_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("trial", TARGET_NODE_IDS)

HIDEOUT_NODE_IDS = [
    *TARGET_NODE_IDS,
    "cragmaw_hideout_kennel",
    "cragmaw_hideout_steep_passage",
    "cragmaw_hideout_overpass",
    "cragmaw_hideout_goblin_den",
    "cragmaw_hideout_twin_pools",
    "cragmaw_hideout_klarg_cave",
    "cragmaw_hideout_aftermath",
]
HIDEOUT_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("hideout", HIDEOUT_NODE_IDS)

PHANDALIN_TOWN_NODE_IDS = [
    "phandalin",
    "stonehill_inn",
    "barthen_provisions",
    "lionshield_coster",
    "edermath_orchard",
    "miners_exchange",
    "alderleaf_farm",
    "shrine_of_luck",
    "townmasters_hall",
    "sleeping_giant_redbrand_ruffians",
    "tresendar_manor_approach",
]
PHANDALIN_TOWN_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("phandalin_town", PHANDALIN_TOWN_NODE_IDS)

SPIDER_WEB_NODE_IDS = [
    "spider_web_overview",
    "triboar_trail_wilderness",
    "old_owl_well",
    "wyvern_tor",
    "conyberry_agatha_lair",
    "thundertree",
    "cragmaw_castle_search",
]
SPIDER_WEB_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("spider_web", SPIDER_WEB_NODE_IDS)

WAVE_ECHO_NODE_IDS = [
    "wave_echo_overview",
    "wave_echo_cave_entrance",
    "wave_echo_mine_tunnels",
    "wave_echo_old_entrance_guardrooms",
    "wave_echo_barracks_storeroom_fungi",
    "wave_echo_great_cavern_dark_pool",
    "wave_echo_north_barracks",
    "wave_echo_smelter_cavern",
    "wave_echo_starry_cavern",
    "wave_echo_wizards_quarters",
    "wave_echo_forge_of_spells",
    "wave_echo_booming_cavern_streambed",
    "wave_echo_collapsed_cavern",
    "wave_echo_temple_of_dumathoin",
    "wave_echo_priests_quarters_conclusion",
]
WAVE_ECHO_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("wave_echo_cave", WAVE_ECHO_NODE_IDS)

STABLE_ID_ALIASES = {
    "ambush_goblins_defeated": "goblin_ambush_resolved",
    "hideout_found": "reach_cragmaw_hideout",
    "trail_to_hideout": "goblin_trail",
    "xp_first_milestone": "goblin_ambush_hideout_75_xp",
    "first_milestone_xp": "goblin_ambush_hideout_75_xp",
    "hideout_cleared": "cragmaw_hideout_milestone_complete",
    "klarg_defeated": "cragmaw_hideout_milestone_complete",
    "cragmaw_hideout_cleared": "cragmaw_hideout_milestone_complete",
    "xp_cragmaw_hideout_milestone": "cragmaw_hideout_milestone_275_xp",
    "cragmaw_hideout_275_xp": "cragmaw_hideout_milestone_275_xp",
}
STABLE_ID_ALIASES = GENERATION_REFERENCE.get("id_aliases", STABLE_ID_ALIASES)

NEXT_NODE_ALIASES = {
    "kennel": "cragmaw_hideout_kennel",
    "steep_passage": "cragmaw_hideout_steep_passage",
    "cragmaw_hideout_cave_mouth": "cragmaw_hideout_entrance",
    "cragmaw_hideout_cave_entrance": "cragmaw_hideout_entrance",
    "cragmaw_hideout": "cragmaw_hideout_entrance",
    "triboar_trail_goblin_ambush": "goblin_ambush",
    "goblin_ambush_site": "goblin_ambush",
    "phandalin_arrival": "phandalin",
    "phandalin_town": "phandalin",
    "barthen's_provisions": "barthen_provisions",
    "barthens_provisions": "barthen_provisions",
    "stonehill": "stonehill_inn",
    "stonehill_inn": "stonehill_inn",
    "lionshield": "lionshield_coster",
    "lionshield_coster": "lionshield_coster",
    "edermath": "edermath_orchard",
    "edermath_orchard": "edermath_orchard",
    "phandalin_miners_exchange": "miners_exchange",
    "miner_exchange": "miners_exchange",
    "miners_exchange": "miners_exchange",
    "alderleaf": "alderleaf_farm",
    "alderleaf_farm": "alderleaf_farm",
    "shrine": "shrine_of_luck",
    "shrine_of_luck": "shrine_of_luck",
    "townmaster_hall": "townmasters_hall",
    "townmasters_hall": "townmasters_hall",
    "sleeping_giant": "sleeping_giant_redbrand_ruffians",
    "redbrand_ruffians": "sleeping_giant_redbrand_ruffians",
    "tresendar_manor": "tresendar_manor_approach",
    "redbrand_hideout": "redbrand_hideout_entrance",
}
NEXT_NODE_ALIASES = GENERATION_REFERENCE.get("node_aliases", NEXT_NODE_ALIASES)

REWARD_OWNER_BY_ID = {
    "goblin_ambush_hideout_75_xp": "cragmaw_hideout_entrance",
    "cragmaw_hideout_milestone_275_xp": "cragmaw_hideout_klarg_cave",
    "phandalin_delivery_10gp": "barthen_provisions",
    "lionshield_recovered_goods_50gp": "lionshield_coster",
    "halia_glassstaff_letter_100gp": "miners_exchange",
    "halia_glasstaff_letters_100gp": "miners_exchange",
    "sildar_cragmaw_castle_500gp": "townmasters_hall",
    "cragmaw_castle_500gp": "townmasters_hall",
    "sildar_iarno_reward_200gp": "townmasters_hall",
    "redbrand_threat_200gp": "townmasters_hall",
    "townmaster_orc_bounty_100gp": "townmasters_hall",
    "wyvern_tor_orcs_100gp": "townmasters_hall",
    "redbrand_ruffians_400_xp": "sleeping_giant_redbrand_ruffians",
}
REWARD_OWNER_BY_ID = GENERATION_REFERENCE.get("reward_owners", REWARD_OWNER_BY_ID)

GLOBAL_EVENT_OWNER_BY_ID = {
    "goblin_ambush_resolved": "goblin_ambush",
    "reach_cragmaw_hideout": "cragmaw_hideout_entrance",
    "klarg_treasure_found": "cragmaw_hideout_klarg_cave",
    "phandalin_supplies_delivered": "barthen_provisions",
    "lionshield_goods_returned": "lionshield_coster",
    "redbrand_ruffians_defeated": "sleeping_giant_redbrand_ruffians",
    # 中文注释：这些是后续章节完成条件，接任务节点不能提前写入。
    "wyvern_tor_orcs_defeated": "",
    "cragmaw_castle_threat_ended": "",
    "redbrand_threat_ended": "",
    "glasstaff_letters_delivered_to_halia": "",
    "agatha_bowgentle_info_obtained": "",
}
if GENERATION_REFERENCE:
    GLOBAL_EVENT_OWNER_BY_ID = {
        **{event_id: "" for event_id in GENERATION_REFERENCE.get("external_completion_events", [])},
        **GENERATION_REFERENCE.get("event_owners", {}),
    }


SYSTEM_PROMPT = """你负责把《凡戴尔的失落矿坑》PDF 原文改写为低成本、可运行的 TRPG 冒险节点。

本次目标是 slim canonical：让后台 Director 能正确推进/切换/回访节点，同时给主主持保留足够演绎和裁定材料。要克制，但不要瘦到只剩索引。

硬性要求：
1. 只依据输入原文，不要发明原文外 NPC、地点、派系、奖励或结论。
2. 输出必须是 JSON 对象：{"nodes":[...]}，不要 Markdown，不要解释。
3. 只生成这些节点，且 id 必须完全一致：
   - adventure_hook_meet_me_in_phandalin
   - goblin_ambush
   - goblin_trail_to_cragmaw_hideout
   - cragmaw_hideout_entrance
   - cragmaw_hideout_goblin_blind
4. 每个节点必须有足够出口支持灵活流程：从冒险引子出发、追踪地精、先去凡达林、回访伏击点、抵达窝点、从窝点入口去暗哨/进洞/撤退。
5. events 只保留会被 exits.requires 或 rewards.requires 使用的长期状态。不要写 enter_*、arrive_* 这类过程事件，除非它确实是奖励或出口条件。
6. clues 只保留会影响后续剧情或出口/奖励判断的关键线索。每节点最多 4 条，能不用 clue 就不要用。
7. fallbacks 每节点最多 2 条；只写真正会改变节点路线的绕路兜底，不要把普通主持建议写成 fallback。
8. scene_beats 每节点 3 到 5 条，每条不超过 80 中文字；必须覆盖进入场景、玩家常见选择、重要后果或下一步引导。
9. rules_notes 每节点最多 5 条，每条不超过 90 中文字；重复环境规则只在最相关节点写一次。
10. dm_summary 120 到 260 中文字；要足以让主主持自然演绎，但不要复述整段原文。player_visible_intro 80 到 160 中文字。
11. rewards 用于需要明确结算或提醒玩家获得的收益，可包含 XP、金币、财宝、道具；不要把普通环境物件或尚未取得的货物写成 reward。
12. reward 必须有 id、type、amount、scope、requires、description。type 只用 xp|gold|treasure|item；gold 可加 currency。75 XP 只能在打败伏击地精并抵达/发现克拉摩窝点后可发放，不要在伏击结束立刻发放。
13. 出口 requires 只引用本批节点内 events/clues/rewards requires 中存在的 id；不要制造无法满足的条件。
14. 如果一个出口只是玩家选择方向，不需要 requires。
15. fallbacks 不是给主模型每轮阅读的长提示；保持短，倾向用 routing_notes 承载一句话路线原则。
16. 新版突袭规则：被突袭者只在先攻检定上获得劣势，不跳过首回合，不禁用动作或反应。
17. checks 只有存在固定 DC 时才写 dc。对抗检定、被动察觉对比、近看即可发现的事实，不要写 dc: 0 或 dc: null；改写 resolution 字段说明判定方式。
18. 重要剧情后果不能只藏在 fallback。比如被伏击击败、错过踪迹去凡达林、俘虏地精带路、抵达窝点发放 XP 等，应在 dm_summary 或 scene_beats/routing_notes 中也出现一句。
19. plot clue 可以少量拆分，但不要碎片化：地精口供这类大情报最多拆成 2 到 3 条关键线索，方便 Director/主主持选择性展示。
20. 与既有运行时同义的关键 id 必须沿用稳定命名：伏击解决用 goblin_ambush_resolved；发现/抵达克拉摩窝点用 reach_cragmaw_hideout；地精踪迹用 goblin_trail；75 XP 奖励用 goblin_ambush_hideout_75_xp。

节点字段 schema：
{
  "id": "固定节点 id",
  "module_id": "lost_mine",
  "title": "中文标题",
  "kind": "scene|encounter|location|stage",
  "page_start": 6,
  "page_end": 8,
  "source_refs": [{"page_start": 6, "page_end": 8, "section": "原文小节"}],
  "dm_summary": "短摘要",
  "player_visible_intro": "玩家进入节点时可见开场",
  "scene_beats": ["短主持节拍"],
  "rules_notes": ["必要规则提醒"],
  "checks": [{"id":"短 id","ability":"wis","skill":"survival","dc":10,"reason":"短原因","resolution":"可选；无固定 DC 时用这里说明对抗/被动值/自动发现"}],
  "encounters": [{"id":"短 id","monster_slug":"goblin","count":4,"faction":"enemy","trigger":"短触发"}],
  "clues": [{"id":"短 id","label":"短名","description":"一句话事实"}],
  "events": ["只放长期状态 id"],
  "fallbacks": [{"id":"短 id","condition":"短条件","dm_guidance":"短处理"}],
  "routing_notes": ["一句话路线原则"],
  "rewards": [{"id":"短 id","type":"xp|gold|treasure|item","amount":75,"scope":"per_player|party","currency":"gp","requires":["事件或线索 id"],"description":"短说明"}],
  "exits": [{"id":"短 id","label":"玩家选择","next_node_id":"目标节点 id","requires":["事件或线索 id"],"transition_kind":"advance|switch|revisit","description":"短说明"}]
}

额外质量目标：
- 五个节点合计 clues 不超过 14 条，events 不超过 10 条，fallbacks 不超过 7 条。
- 五个节点合计 JSON 目标在 11k 到 15k 中文字符附近；低于 10k 往往主持细节不足，高于 17k 往往开始冗余。
- 出口要完整，但出口描述要短。
- 不要为了“防跑偏”堆字段；Director 通过 current_node、exits、routing_notes 和少量 fallback 处理绕路。
- 主持细节优先放在 dm_summary、player_visible_intro、scene_beats、rules_notes；长期状态只放 clues/events/rewards。
"""


USER_PROMPT_TEMPLATE = """请根据下面 PDF 原文片段生成 slim canonical 节点。

范围：从“冒险引子/凡达林见”开始，到“2. 地精暗哨”结束。需要包含冒险引子、地精伏击、地精踪迹与克拉摩窝点入口，但不要继续生成犬舍、陡峭通道或更深区域节点。

必须特别处理：
- 冒险引子只负责建立雇佣、甘德伦先行、目的地凡达林和货车护送，不要在引子发放10 gp。
- 冒险引子的出口应推进到地精伏击；如果玩家想先进行路上闲聊或准备，可以留在引子演绎，但不要生成额外节点。
- 玩家打完或处理伏击后，可追踪地精踪迹，也可先去凡达林；先去凡达林时，本批只保留出口到 phandalin，不要生成 phandalin 节点。
- 如果玩家被伏击打败，也应允许之后回到遇袭处或继续追踪，不要把剧情锁死。
- 75 XP 条件是“伏击地精已解决 + 发现/抵达克拉摩窝点”，奖励放在 cragmaw_hideout_entrance。
- 克拉摩窝点入口可去暗哨，可直接进洞到后续 kennel/steep passage 目标，但本批不用生成这些目标节点。
- 地精暗哨处理后应能回入口，或继续进洞到后续节点。

PDF 原文：
{source_text}
"""


HOOK_PROMPT_TEMPLATE = """请根据下面 PDF 原文片段生成 slim canonical 节点。

范围：只生成“冒险引子：凡达林见”节点。

必须特别处理：
- 只建立雇佣、目的地凡达林、甘德伦与修达先行、护送补给货车、送达后每人10 gp承诺报酬。
- 不要在本节点发放10 gp；这只是任务报酬承诺。
- 允许玩家自定义去凡达林动机，但不能删掉货车护送、甘德伦先行和凡达林目的地。
- 出口推进到 goblin_ambush；如果玩家要路上闲聊或准备，可以留在本节点演绎，不要生成额外节点。

PDF 原文：
{source_text}
"""


BATCH_PROMPT_TEMPLATE = """请根据下面 PDF 原文片段生成 slim canonical 节点。

范围：只生成下列 target_node_ids 中列出的节点，不要生成其他节点：
{target_node_ids}

额外要求：
- 这些节点属于克拉摩窝点的同一空间图。出口要能连接到相邻节点；不要为了防跑偏堆大量 fallback。
- 若出口指向本批外但属于已知节点，可直接填 next_node_id；不要生成占位节点。
- 已经被角色取得或可立即结算的财物、金币、治疗药水可以写入 rewards；尚未归还的狮盾货物是任务物，不要直接写成已获得 reward。
- 克拉摩窝点最终 275 XP 使用 reward id cragmaw_hideout_milestone_275_xp，requires 使用 cragmaw_hideout_milestone_complete。
- 克拉格洞室要承载击败克拉格及盟友后的里程碑奖励；after­math 只做离开窝点、去凡达林、归还货物等收束路线，不要重复发放同一 XP。
- 修达、伊米克、洪水、狼、竖井、狮盾货物、克拉格逃跑等都是重要主持细节，但仍需克制表达。

PDF 原文：
{source_text}
"""

PHANDALIN_TOWN_PROMPT_TEMPLATE = """请根据下面 PDF 原文片段生成 slim canonical 节点。

范围：只生成下列 target_node_ids 中列出的节点，不要生成其他节点：
{target_node_ids}

额外要求：
- 本批是凡达林镇内调度与 NPC/任务入口，不生成红标帮地下城房间。
- phandalin 是镇内总览/调度节点：抵达、晚到限制、问去哪里、把玩家引向巴森补给、石丘旅馆、狮盾小贩、镇长大厅、沉睡巨人、崔森德庄园等。
- 从克拉摩窝点或伏击后抵达凡达林时，要允许交货、休息、打听甘德伦/修达/红标帮，也要保留回访伏击点或克拉摩窝点的路线。
- 巴森补给可结算开局运货报酬：每人 10 gp，reward id 使用 phandalin_delivery_10gp，requires 使用 phandalin_supplies_delivered。
- 狮盾小贩只有在玩家归还或告知克拉摩窝点货物位置时，才有 50 gp 奖励，reward id 使用 lionshield_recovered_goods_50gp，requires 使用 lionshield_goods_returned。
- 哈利娅的 100 gp、修达的 500 gp/200 gp、镇长的 100 gp 都是后续任务奖励；可以作为对应委托节点的 rewards，但 requires 必须指向后续完成事件，不要在接任务时发放。
- 红标帮恶霸街面遭遇节点要包含 4 名 redbrand_ruffian，击败后 400 XP，reward id 使用 redbrand_ruffians_400_xp，requires 使用 redbrand_ruffians_defeated。
- events 只保留稳定状态或 reward/exit 需要的 id；不要写 enter_*、arrive_*。
- clues 不要碎片化；每个地点保留 1 到 4 条真正会引导后续行动的线索。
- exits 要支持灵活切换：镇内节点之间可回 phandalin 或去相关地点；红标帮线索应能导向 tresendar_manor_approach 或 sleeping_giant_redbrand_ruffians；第3部分支线目标可以指向外部占位 id。
- 若出口目标是后续章节或地下城尚未生成节点，可用外部占位 id：redbrand_hideout_entrance、old_owl_well、wyvern_tor、conyberry_agatha_lair、thundertree、cragmaw_castle。
- routing_notes 用一句话说明这个节点如何引导剧情，不要长篇防跑偏。

PDF 原文：
{source_text}
"""

SPIDER_WEB_PROMPT_TEMPLATE = """请根据下面 PDF 原文片段生成 slim canonical 节点。

范围：只生成下列 target_node_ids 中列出的节点，不要生成其他节点：
{target_node_ids}

额外要求：
- 本批是第3部分“蜘蛛之网”的开放式调度与地点节点；不要回头生成第一章伏击或克拉摩窝点房间节点。
- spider_web_overview 是第三部分总览/调度节点：从凡达林支线线索出发，让玩家选择去三猪小径、兔莓/阿加莎、古枭井、飞龙突岩、雷树或克拉摩堡线索。
- triboar_trail_wilderness 承载三猪小径旅行与随机/路上遭遇，只保留能改变路线的线索和出口，不要把随机遭遇表逐项塞成长期状态。
- conyberry_agatha_lair 重点是女妖阿加莎的礼物、交涉、回答问题、可能透露博哲托尔/克拉摩堡/古枭井等信息。
- old_owl_well 重点是哈姆·科斯特、僵尸、红袍巫师事实、可交涉条件、宝藏和回报达朗的路线。
- wyvern_tor 重点是兽人与食人魔营地、布洛戈、战斗、宝藏、完成镇长和哈姆·科斯特相关委托。
- thundertree 先做雷树废墟章节级节点：保留里多斯、毒牙、主要危险和继续查克拉摩堡/潮音洞穴的路线，不要把每栋建筑拆成独立节点。
- cragmaw_castle_search 先做克拉摩堡章节级入口/搜索节点：承接寻找城堡、潜入/侦查/进入城堡，不展开全部房间细节。
- 第三部分进入后，旧的地精伏击和克拉摩窝点房间通常不再作为回访目标；如需回镇，只回凡达林枢纽或任务委托 NPC。
- rewards 可写 XP、金币、财宝、道具；财物与卷轴/药水等明确可取得收益也算 reward，但不要把尚未取得的线索或任务承诺写成已得 reward。
- events 只保留 reward.requires、重要出口或任务回报需要的长期完成状态；每节点最多 3 到 5 个。
- clues 每节点最多 4 条，优先保存能开启路线、解释 NPC 动机或影响任务回报的事实。
- routing_notes 用一句话说明 Director 如何判断路线；不要写成长篇防跑偏提示。

PDF 原文：
{source_text}
"""

WAVE_ECHO_PROMPT_TEMPLATE = """请根据下面 PDF 原文片段生成 slim canonical 节点。

范围：只生成下列 target_node_ids 中列出的节点，不要生成其他节点：
{target_node_ids}

额外要求：
- 本批是第4部分“潮音洞穴”的最终地下城。节点粒度按相邻区域群，不要一房一节点，也不要粗到只剩章节总览。
- wave_echo_overview 承载地下城总览、角色等级、游荡怪物、通用特征、隆隆潮浪；不要把游荡怪物表逐项变成长期状态。
- 每个节点需要能让 Director 判断玩家具体在哪片区域、可去哪些相邻区域、有什么重要危险/宝藏/剧情后果。
- exits 优先连接相邻区域群；允许回到 wave_echo_overview 或 phandalin，但不要把第三部分支线当作常规回访目标。
- events 只保留 reward.requires、最终结局或重要路线需要的长期状态；不要写 enter_*、arrive_*。
- clues 每节点最多 4 条，保留地图、黑蜘蛛计划、南卓、法术工厂、重要绕路和宝藏定位等真正影响路线的事实。
- rewards 可写 XP、金币、财宝、道具；普通怪物 XP 如果原文没有单独奖励段落，可以不写或简短写成遭遇奖励提醒，不要逐个怪物刷屏。
- 黑蜘蛛、南卓、法术工厂和结局奖励必须清楚：涅兹纳尔被活捉并送交后双倍 XP；南卓获救且存活 200 XP；法术工厂可获得光明使者、龙之守护和临时强化；结局有矿坑10%收益。
- routing_notes 用一句话说明此区域的路线作用，不要长篇防跑偏。
- 这次 slim trial 以可运行优先：字段短，关系闭合，宁可少写软提示，也不要塞满房间原文。

PDF 原文：
{source_text}
"""


# 中文注释：脚本按固定章节锚点切分，保证续跑时不会漂移到别的 PDF 段落。
def extract_trial_source(pdf_path: Path) -> str:
    with fitz.open(pdf_path) as doc:
        chunks: list[str] = []
        for page_no in range(4, 9):
            text = doc[page_no - 1].get_text("text")
            chunks.append(f"\n[PAGE {page_no}]\n{text}")
    source = "\n".join(chunks)
    start_candidates = [
        source.find("冒险引子"),
        source.find("凡达林见"),
        source.find("Meet Me in Phandalin"),
    ]
    start_positions = [pos for pos in start_candidates if pos >= 0]
    start = min(start_positions) if start_positions else source.find("地精伏击")
    end_candidates = [
        source.find("3．犬舍"),
        source.find("3.犬舍"),
        source.find("3. 犬舍"),
        source.find("犬舍Kennel"),
    ]
    end_positions = [pos for pos in end_candidates if pos > start]
    end = min(end_positions) if end_positions else len(source)
    if start >= 0:
        source = source[start:end]
    return compact_trial_source(source)[:14000]


def extract_hideout_source(pdf_path: Path) -> str:
    """提取从冒险引子到克拉摩窝点结尾的原文，用于分批生成。"""
    with fitz.open(pdf_path) as doc:
        chunks = [f"\n[PAGE {page_no}]\n{doc[page_no - 1].get_text('text')}" for page_no in range(4, 13)]
    source = "\n".join(chunks)
    start_candidates = [source.find("冒险引子"), source.find("凡达林见"), source.find("地精伏击")]
    start_positions = [pos for pos in start_candidates if pos >= 0]
    start = min(start_positions) if start_positions else 0
    end_candidates = [source.find("第2 部分：凡达林"), source.find("第2部分：凡达林")]
    end_positions = [pos for pos in end_candidates if pos > start]
    end = min(end_positions) if end_positions else len(source)
    return source[start:end]


def extract_phandalin_source(pdf_path: Path) -> str:
    """提取凡达林镇内章节，不包含红标帮地下城房间细节。"""
    with fitz.open(pdf_path) as doc:
        chunks = [f"\n[PAGE {page_no}]\n{doc[page_no - 1].get_text('text')}" for page_no in range(13, 19)]
    source = "\n".join(chunks)
    start_candidates = [source.find("第2 部分：凡达林"), source.find("第2部分：凡达林")]
    start_positions = [pos for pos in start_candidates if pos >= 0]
    start = min(start_positions) if start_positions else 0
    end_candidates = [source.find("红标帮窝点Redbrand Hideout"), source.find("红标帮窝点")]
    end_positions = [pos for pos in end_candidates if pos > start]
    end = min(end_positions) if end_positions else len(source)
    return source[start:end]


def extract_spider_web_source(pdf_path: Path) -> str:
    """提取第三部分蜘蛛之网原文，作为开放式章节节点的生成材料。"""
    with fitz.open(pdf_path) as doc:
        chunks = [f"\n[PAGE {page_no}]\n{doc[page_no - 1].get_text('text')}" for page_no in range(26, 40)]
    source = "\n".join(chunks)
    start_candidates = [source.find("第3 部分：蜘蛛之网"), source.find("第3部分：蜘蛛之网")]
    start_positions = [pos for pos in start_candidates if pos >= 0]
    start = min(start_positions) if start_positions else 0
    end_candidates = [source.find("第4 部分：潮音洞穴"), source.find("第4部分：潮音洞穴")]
    end_positions = [pos for pos in end_candidates if pos > start]
    end = min(end_positions) if end_positions else len(source)
    return source[start:end]


def extract_wave_echo_source(pdf_path: Path) -> str:
    """提取第四部分潮音洞穴原文，包含区域 1-20 与结局。"""
    with fitz.open(pdf_path) as doc:
        chunks = [f"\n[PAGE {page_no}]\n{doc[page_no - 1].get_text('text')}" for page_no in range(39, 47)]
    source = "\n".join(chunks)
    start_candidates = [source.find("第4 部分：潮音洞穴"), source.find("第4部分：潮音洞穴")]
    start_positions = [pos for pos in start_candidates if pos >= 0]
    start = min(start_positions) if start_positions else 0
    return source[start:]


def split_trial_sources(source: str) -> tuple[str, str]:
    """把引子和第一段遭遇拆开生成，避开上游非流式长请求超时。"""
    marker = "[第1 部分：地精箭头]"
    if marker not in source:
        return source, source
    hook, rest = source.split(marker, 1)
    return hook.strip(), f"{marker}{rest}".strip()


def compact_trial_source(source: str) -> str:
    """只保留本次节点生成需要的原文小节，避免上游因长上下文超时。"""
    markers = [
        "冒险引子Adventure Hook",
        "凡达林见Meet",
        "第1 部分：地精箭头",
        "地精伏击Goblin Ambush",
        "发展Developments",
        "地精踪迹Goblin Trail",
        "奖励经验值Awarding Experience Points",
        "通用特征物General Features",
        "地精知道的事What the Goblins Know",
        "1. 洞口Cave Mouth",
        "2. 地精暗哨Goblin Blind",
    ]
    lines = [line.rstrip() for line in source.splitlines()]
    selected: list[str] = []
    active_marker = ""
    remaining = 0
    for line in lines:
        stripped = line.strip()
        matched = next((marker for marker in markers if marker in stripped), "")
        if matched:
            active_marker = matched
            remaining = _marker_line_budget(matched)
            selected.append(f"\n[{matched}]")
            selected.append(line)
            continue
        if remaining > 0:
            selected.append(line)
            remaining -= 1
        elif active_marker and stripped.startswith("•"):
            selected.append(line)
    compacted = "\n".join(selected).strip()
    return compacted or source


def section_text(source: str, start_marker: str, end_marker: str | None = None) -> str:
    """按中文/英文标题截取批次原文；找不到终点时取到末尾。"""
    start = source.find(start_marker)
    if start < 0:
        return ""
    end = source.find(end_marker, start + len(start_marker)) if end_marker else -1
    if end < 0:
        end = len(source)
    return source[start:end].strip()


def section_text_any(source: str, start_markers: list[str], end_markers: list[str] | None = None) -> str:
    """PDF 抽文本偶尔拆开中英文标题，因此章节切分允许多个锚点。"""
    start_positions = [(source.find(marker), marker) for marker in start_markers]
    start_positions = [(pos, marker) for pos, marker in start_positions if pos >= 0]
    if not start_positions:
        return ""
    start, marker = min(start_positions, key=lambda item: item[0])
    end = -1
    for end_marker in end_markers or []:
        found = source.find(end_marker, start + len(marker))
        if found >= 0 and (end < 0 or found < end):
            end = found
    if end < 0:
        end = len(source)
    return source[start:end].strip()


def compact_for_batch(text: str, *, max_chars: int = 9000) -> str:
    """批次原文只压空白，不改写事实。"""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    compacted = "\n".join(lines)
    return compacted[:max_chars]


def _marker_line_budget(marker: str) -> int:
    budgets = {
        "冒险引子Adventure Hook": 8,
        "凡达林见Meet": 8,
        "第1 部分：地精箭头": 8,
        "地精伏击Goblin Ambush": 42,
        "发展Developments": 22,
        "地精踪迹Goblin Trail": 45,
        "奖励经验值Awarding Experience Points": 12,
        "通用特征物General Features": 28,
        "地精知道的事What the Goblins Know": 24,
        "1. 洞口Cave Mouth": 22,
        "2. 地精暗哨Goblin Blind": 34,
    }
    return budgets.get(marker, 12)


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if cleaned.startswith("{"):
        return json.loads(cleaned)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("模型输出中没有 JSON 对象")
    return json.loads(match.group(0))


def generation_reference_prompt(target_ids: list[str]) -> str:
    """把稳定契约压成短 JSON，放进系统提示词末尾供模型对齐命名。"""
    if not GENERATION_REFERENCE:
        return ""
    target_set = set(target_ids)
    scope_nodes = sorted(set().union(*GENERATION_REFERENCE.get("scopes", {}).values()))
    relevant_nodes = [
        node_id
        for node_id in scope_nodes
        if (
            node_id in target_set
            or node_id in HIDEOUT_NODE_IDS
            or node_id in PHANDALIN_TOWN_NODE_IDS
            or node_id in SPIDER_WEB_NODE_IDS
            or node_id in WAVE_ECHO_NODE_IDS
        )
    ]
    reward_owners = GENERATION_REFERENCE.get("reward_owners", {})
    event_owners = GENERATION_REFERENCE.get("event_owners", {})
    relevant_reward_ids = [
        reward_id
        for reward_id, owner in reward_owners.items()
        if owner in target_set or owner in relevant_nodes
    ]
    reference = {
        "stable_node_ids": relevant_nodes,
        "node_aliases": GENERATION_REFERENCE.get("node_aliases", {}),
        "id_aliases": GENERATION_REFERENCE.get("id_aliases", {}),
        "event_owners": {
            event_id: owner
            for event_id, owner in event_owners.items()
            if owner in target_set or owner in relevant_nodes
        },
        "external_completion_events": GENERATION_REFERENCE.get("external_completion_events", []),
        "reward_owners": {
            reward_id: owner
            for reward_id, owner in reward_owners.items()
            if reward_id in relevant_reward_ids
        },
        "canonical_rewards": {
            reward_id: reward
            for reward_id, reward in GENERATION_REFERENCE.get("canonical_rewards", {}).items()
            if reward_id in relevant_reward_ids
        },
        "external_targets": GENERATION_REFERENCE.get("external_targets", []),
        "notes": GENERATION_REFERENCE.get("notes_for_llm", []),
    }
    return "\n\n稳定命名参考（必须优先遵守）：\n" + json.dumps(reference, ensure_ascii=False, indent=2)


def system_prompt_for(target_ids: list[str]) -> str:
    """按当前批次收窄目标节点，避免模型一次生成过大结果。"""
    node_list = "\n".join(f"   - {node_id}" for node_id in target_ids)
    prompt = re.sub(
        r"3\. 只生成这些节点，且 id 必须完全一致：\n(?:   - .+\n)+",
        f"3. 只生成这些节点，且 id 必须完全一致：\n{node_list}\n",
        SYSTEM_PROMPT,
    )
    node_count = len(target_ids)
    prompt = prompt.replace("五个节点合计", f"{node_count}个节点合计")
    prompt = prompt.replace("每节点最多 4 条", "每节点最多 5 条")
    if node_count == 1:
        prompt = prompt.replace("11k 到 15k 中文字符", "2k 到 4k 中文字符")
        prompt = prompt.replace("低于 10k 往往主持细节不足，高于 17k 往往开始冗余", "低于 1.5k 往往主持细节不足，高于 5k 往往开始冗余")
    elif node_count <= 3:
        prompt = prompt.replace("11k 到 15k 中文字符", "5k 到 9k 中文字符")
        prompt = prompt.replace("低于 10k 往往主持细节不足，高于 17k 往往开始冗余", "低于 4k 往往主持细节不足，高于 11k 往往开始冗余")
    return prompt + generation_reference_prompt(target_ids)


def invoke_nodes(
    client: OpenAI,
    *,
    model: str,
    temperature: float,
    target_ids: list[str],
    prompt_template: str,
    source_text: str,
    streaming: bool,
    prepared_user_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """单批调用模型并返回节点数组；失败时让异常暴露，便于续跑定位。"""
    request = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt_for(target_ids)},
            {"role": "user", "content": prepared_user_prompt or prompt_template.format(source_text=source_text)},
        ],
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    if streaming:
        stream = client.chat.completions.create(**request, stream=True)
        content_parts: list[str] = []
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                content_parts.append(delta)
        content = "".join(content_parts)
    else:
        response = client.chat.completions.create(**request)
        content = response.choices[0].message.content or ""
    payload = parse_json_object(content)
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("模型输出缺少 nodes 数组")
    return nodes


def hideout_batches(source: str) -> list[tuple[list[str], str]]:
    """克拉摩窝点按空间段分批，减少每次请求长度并保留邻接关系。"""
    early = compact_trial_source(source)
    kennel = section_text(source, "3. 犬舍Kennel", "4. 陡峭通道Steep Passage")
    steep = section_text(source, "4. 陡峭通道Steep Passage", "6. 地精休息室Goblin Den")
    den = section_text(source, "6. 地精休息室Goblin Den", "7. 双子池Twin Pools Cave")
    pools_klarg = section_text(source, "7. 双子池Twin Pools Cave", "后续What’s Next？")
    aftermath = section_text(source, "后续What’s Next？", None)
    return [
        (
            ["adventure_hook_meet_me_in_phandalin"],
            HOOK_PROMPT_TEMPLATE.format(source_text=split_trial_sources(early)[0]),
        ),
        (
            [
                "goblin_ambush",
                "goblin_trail_to_cragmaw_hideout",
                "cragmaw_hideout_entrance",
                "cragmaw_hideout_goblin_blind",
            ],
            USER_PROMPT_TEMPLATE.format(source_text=split_trial_sources(early)[1]),
        ),
        (
            ["cragmaw_hideout_kennel"],
            BATCH_PROMPT_TEMPLATE.format(
                target_node_ids="- cragmaw_hideout_kennel",
                source_text=compact_for_batch(kennel),
            ),
        ),
        (
            ["cragmaw_hideout_steep_passage", "cragmaw_hideout_overpass"],
            BATCH_PROMPT_TEMPLATE.format(
                target_node_ids="- cragmaw_hideout_steep_passage\n- cragmaw_hideout_overpass",
                source_text=compact_for_batch(steep),
            ),
        ),
        (
            ["cragmaw_hideout_goblin_den"],
            BATCH_PROMPT_TEMPLATE.format(
                target_node_ids="- cragmaw_hideout_goblin_den",
                source_text=compact_for_batch(den),
            ),
        ),
        (
            ["cragmaw_hideout_twin_pools", "cragmaw_hideout_klarg_cave"],
            BATCH_PROMPT_TEMPLATE.format(
                target_node_ids="- cragmaw_hideout_twin_pools\n- cragmaw_hideout_klarg_cave",
                source_text=compact_for_batch(pools_klarg),
            ),
        ),
        (
            ["cragmaw_hideout_aftermath"],
            BATCH_PROMPT_TEMPLATE.format(
                target_node_ids="- cragmaw_hideout_aftermath",
                source_text=compact_for_batch(aftermath),
            ),
        ),
    ]


def phandalin_town_batches(source: str) -> list[tuple[list[str], str]]:
    """凡达林镇按地点群分批，便于断点续跑与质量检查。"""
    overview = section_text(source, "第2 部分：凡达林", "巴森补给Barthen’s Provisions")
    shops = section_text(source, "巴森补给Barthen’s Provisions", "凡达林矿工兑换所Phandalin Miner’s")
    exchange = section_text(source, "凡达林矿工兑换所Phandalin Miner’s", "阿德里夫农场Alderleaf Farm")
    farm_shrine_hall = section_text(source, "阿德里夫农场Alderleaf Farm", "崔森德庄园Tresendar Manor")
    redbrand = section_text(source, "崔森德庄园Tresendar Manor", None)
    return [
        (
            ["phandalin", "stonehill_inn"],
            PHANDALIN_TOWN_PROMPT_TEMPLATE.format(
                target_node_ids="- phandalin\n- stonehill_inn",
                source_text=compact_for_batch(overview, max_chars=8500),
            ),
        ),
        (
            ["barthen_provisions", "lionshield_coster"],
            PHANDALIN_TOWN_PROMPT_TEMPLATE.format(
                target_node_ids="- barthen_provisions\n- lionshield_coster",
                source_text=compact_for_batch(shops, max_chars=7500),
            ),
        ),
        (
            ["edermath_orchard", "miners_exchange"],
            PHANDALIN_TOWN_PROMPT_TEMPLATE.format(
                target_node_ids="- edermath_orchard\n- miners_exchange",
                source_text=compact_for_batch(exchange, max_chars=7500),
            ),
        ),
        (
            ["alderleaf_farm", "shrine_of_luck", "townmasters_hall"],
            PHANDALIN_TOWN_PROMPT_TEMPLATE.format(
                target_node_ids="- alderleaf_farm\n- shrine_of_luck\n- townmasters_hall",
                source_text=compact_for_batch(farm_shrine_hall, max_chars=9000),
            ),
        ),
        (
            ["sleeping_giant_redbrand_ruffians", "tresendar_manor_approach"],
            PHANDALIN_TOWN_PROMPT_TEMPLATE.format(
                target_node_ids="- sleeping_giant_redbrand_ruffians\n- tresendar_manor_approach",
                source_text=compact_for_batch(redbrand, max_chars=8500),
            ),
        ),
    ]


def spider_web_batches(source: str) -> list[tuple[list[str], str]]:
    """第三部分按开放式地点分批；先生成章节级节点，后续再细拆城堡/雷树房间。"""
    overview = section_text_any(source, ["第3 部分：蜘蛛之网", "第3部分：蜘蛛之网"], ["三猪小径Triboar Trail"])
    triboar = section_text_any(source, ["三猪小径Triboar Trail"], ["兔莓与阿加莎的巢穴Conyberry"])
    agatha = section_text_any(source, ["兔莓与阿加莎的巢穴Conyberry"], ["古枭井Old Owl Well"])
    old_owl = section_text_any(source, ["古枭井Old Owl Well"], ["雷树废墟Ruins of Thundertree"])
    thundertree = section_text_any(source, ["雷树废墟Ruins of Thundertree"], ["飞龙突岩Wyvern Tor"])
    wyvern = section_text_any(source, ["飞龙突岩Wyvern Tor"], ["克拉摩堡Cragmaw Castle"])
    castle = section_text_any(source, ["克拉摩堡Cragmaw Castle"], ["第4 部分：潮音洞穴", "第4部分：潮音洞穴"])
    return [
        (
            ["spider_web_overview", "triboar_trail_wilderness"],
            SPIDER_WEB_PROMPT_TEMPLATE.format(
                target_node_ids="- spider_web_overview\n- triboar_trail_wilderness",
                source_text=compact_for_batch(f"{overview}\n\n{triboar}", max_chars=9000),
            ),
        ),
        (
            ["conyberry_agatha_lair", "old_owl_well"],
            SPIDER_WEB_PROMPT_TEMPLATE.format(
                target_node_ids="- conyberry_agatha_lair\n- old_owl_well",
                source_text=compact_for_batch(f"{agatha}\n\n{old_owl}", max_chars=9500),
            ),
        ),
        (
            ["wyvern_tor"],
            SPIDER_WEB_PROMPT_TEMPLATE.format(
                target_node_ids="- wyvern_tor",
                source_text=compact_for_batch(wyvern, max_chars=6500),
            ),
        ),
        (
            ["thundertree"],
            SPIDER_WEB_PROMPT_TEMPLATE.format(
                target_node_ids="- thundertree",
                source_text=compact_for_batch(thundertree, max_chars=10000),
            ),
        ),
        (
            ["cragmaw_castle_search"],
            SPIDER_WEB_PROMPT_TEMPLATE.format(
                target_node_ids="- cragmaw_castle_search",
                source_text=compact_for_batch(castle, max_chars=10000),
            ),
        ),
    ]


def wave_echo_batches(source: str) -> list[tuple[list[str], str]]:
    """潮音洞穴按相邻区域群分批，兼顾最终地城定位和 token 成本。"""
    overview = section_text_any(source, ["第4 部分：潮音洞穴", "第4部分：潮音洞穴"], ["1. 洞穴入口Cave Entrance"])
    entrance_mine = section_text_any(source, ["1. 洞穴入口Cave Entrance"], ["3. 旧入口Old Entrance"])
    old_guard_assay = section_text_any(source, ["3. 旧入口Old Entrance"], ["6 南部营房South Barracks", "6. 南部营房South Barracks"])
    barracks_fungi = section_text_any(source, ["6 南部营房South Barracks", "6. 南部营房South Barracks"], ["9. 大洞窟Great Cavern"])
    great_pool_north = section_text_any(source, ["9. 大洞窟Great Cavern"], ["12. 冶炼洞窟Smelter Cavern"])
    smelter_starry = section_text_any(source, ["12. 冶炼洞窟Smelter Cavern"], ["14. 法师的居所Wizard’s Quarters"])
    wizard_forge = section_text_any(source, ["14. 法师的居所Wizard’s Quarters"], ["16. 轰鸣洞窟Booming Cavern"])
    booming_collapsed = section_text_any(source, ["16. 轰鸣洞窟Booming Cavern"], ["19. 杜马松的神庙Temple of Dumathoin"])
    temple_conclusion = section_text_any(source, ["19. 杜马松的神庙Temple of Dumathoin"], None)
    return [
        (
            ["wave_echo_overview"],
            WAVE_ECHO_PROMPT_TEMPLATE.format(
                target_node_ids="- wave_echo_overview",
                source_text=compact_for_batch(overview, max_chars=8000),
            ),
        ),
        (
            ["wave_echo_cave_entrance", "wave_echo_mine_tunnels"],
            WAVE_ECHO_PROMPT_TEMPLATE.format(
                target_node_ids="- wave_echo_cave_entrance\n- wave_echo_mine_tunnels",
                source_text=compact_for_batch(entrance_mine, max_chars=8500),
            ),
        ),
        (
            ["wave_echo_old_entrance_guardrooms"],
            WAVE_ECHO_PROMPT_TEMPLATE.format(
                target_node_ids="- wave_echo_old_entrance_guardrooms",
                source_text=compact_for_batch(old_guard_assay, max_chars=8500),
            ),
        ),
        (
            ["wave_echo_barracks_storeroom_fungi"],
            WAVE_ECHO_PROMPT_TEMPLATE.format(
                target_node_ids="- wave_echo_barracks_storeroom_fungi",
                source_text=compact_for_batch(barracks_fungi, max_chars=8500),
            ),
        ),
        (
            ["wave_echo_great_cavern_dark_pool", "wave_echo_north_barracks"],
            WAVE_ECHO_PROMPT_TEMPLATE.format(
                target_node_ids="- wave_echo_great_cavern_dark_pool\n- wave_echo_north_barracks",
                source_text=compact_for_batch(great_pool_north, max_chars=9500),
            ),
        ),
        (
            ["wave_echo_smelter_cavern", "wave_echo_starry_cavern"],
            WAVE_ECHO_PROMPT_TEMPLATE.format(
                target_node_ids="- wave_echo_smelter_cavern\n- wave_echo_starry_cavern",
                source_text=compact_for_batch(smelter_starry, max_chars=9500),
            ),
        ),
        (
            ["wave_echo_wizards_quarters", "wave_echo_forge_of_spells"],
            WAVE_ECHO_PROMPT_TEMPLATE.format(
                target_node_ids="- wave_echo_wizards_quarters\n- wave_echo_forge_of_spells",
                source_text=compact_for_batch(wizard_forge, max_chars=11000),
            ),
        ),
        (
            ["wave_echo_booming_cavern_streambed", "wave_echo_collapsed_cavern"],
            WAVE_ECHO_PROMPT_TEMPLATE.format(
                target_node_ids="- wave_echo_booming_cavern_streambed\n- wave_echo_collapsed_cavern",
                source_text=compact_for_batch(booming_collapsed, max_chars=9500),
            ),
        ),
        (
            ["wave_echo_temple_of_dumathoin", "wave_echo_priests_quarters_conclusion"],
            WAVE_ECHO_PROMPT_TEMPLATE.format(
                target_node_ids="- wave_echo_temple_of_dumathoin\n- wave_echo_priests_quarters_conclusion",
                source_text=compact_for_batch(temple_conclusion, max_chars=11000),
            ),
        ),
    ]


def normalize_stable_ids(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把模型临时命名收敛到运行时已使用的稳定 id，避免试验数据破坏兼容性。"""
    normalized_nodes = json.loads(json.dumps(nodes, ensure_ascii=False))
    for node in normalized_nodes:
        _postprocess_trial_node(node)
        node["events"] = [_stable_id(event_id) for event_id in node.get("events", [])]
        for clue in node.get("clues", []):
            if isinstance(clue, dict) and clue.get("id"):
                clue["id"] = _stable_id(str(clue["id"]))
        for reward in node.get("rewards", []):
            if not isinstance(reward, dict):
                continue
            if reward.get("id"):
                reward["id"] = _stable_id(str(reward["id"]))
            reward["requires"] = [_stable_id(item) for item in reward.get("requires", [])]
        for exit_item in node.get("exits", []):
            if isinstance(exit_item, dict):
                exit_item["requires"] = [_stable_id(item) for item in exit_item.get("requires", [])]
                if exit_item.get("next_node_id"):
                    exit_item["next_node_id"] = NEXT_NODE_ALIASES.get(str(exit_item["next_node_id"]), exit_item["next_node_id"])
                    if "barthen" in str(exit_item.get("id", "")).lower():
                        exit_item["next_node_id"] = "barthen_provisions"
        _cleanup_node_rewards(node)
        _cleanup_node_events(node)
    return normalized_nodes


def _postprocess_trial_node(node: dict[str, Any]) -> None:
    """修正分批生成带来的边界串味，让 trial 更适合作为范本。"""
    if node.get("id") != "goblin_ambush":
        return
    node["title"] = "地精伏击"
    for key in ("dm_summary", "player_visible_intro"):
        text = str(node.get(key, ""))
        text = re.sub(r"冒险从“凡达林见”的护送差事开始：.*?并确认行军队列。", "", text)
        text = re.sub(r"你们驾着满载补给的货车离开无冬城，沿大公路南下数日后转入三猪小径。", "", text)
        text = re.sub(r"甘德伦·寻岩者和他的护卫修达早已骑马先行，约定在凡达林会合。", "", text)
        node[key] = re.sub(r"\s+", "", text).strip("，。； ") if key == "player_visible_intro" else text.strip("，。； ")
    node["scene_beats"] = [
        item for item in node.get("scene_beats", [])
        if "甘德伦" not in str(item) or "队列" not in str(item)
    ]
    node["routing_notes"] = [
        item for item in node.get("routing_notes", [])
        if "引子" not in str(item)
    ]


def _cleanup_node_rewards(node: dict[str, Any]) -> None:
    """标准奖励只能由一个节点持有，防止跨批次重复发放。"""
    cleaned: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for reward in node.get("rewards", []):
        if not isinstance(reward, dict):
            continue
        reward_id = str(reward.get("id", "")).strip()
        owner = REWARD_OWNER_BY_ID.get(reward_id)
        if owner and owner != node.get("id"):
            continue
        reward_type = str(reward.get("type", "")).strip().lower()
        if reward_type not in {"xp", "gold", "treasure", "item"}:
            continue
        if reward_id and reward_id in seen_ids:
            continue
        if reward_id:
            seen_ids.add(reward_id)
        cleaned.append(reward)
    if node.get("id") == "cragmaw_hideout_entrance":
        _ensure_reward(
            cleaned,
            {
                "id": "goblin_ambush_hideout_75_xp",
                "type": "xp",
                "amount": 75,
                "scope": "per_player",
                "requires": ["goblin_ambush_resolved", "reach_cragmaw_hideout"],
                "description": "打败伏击地精并发现或抵达克拉摩窝点后，每名玩家获得75 XP。",
            },
        )
    if node.get("id") == "cragmaw_hideout_klarg_cave":
        _ensure_reward(
            cleaned,
            {
                "id": "cragmaw_hideout_milestone_275_xp",
                "type": "xp",
                "amount": 275,
                "scope": "per_player",
                "requires": ["cragmaw_hideout_milestone_complete"],
                "description": "击败或驱逐克拉格及其盟友，完成克拉摩窝点核心目标后获得。",
            },
        )
        _ensure_reward(
            cleaned,
            {
                "id": "klarg_treasure_cache",
                "type": "treasure",
                "amount": 1,
                "scope": "party",
                "requires": ["klarg_treasure_found"],
                "description": "克拉格的小金库：600 cp、110 sp、两瓶治疗药水和一只镶金珠眼的翡翠青蛙。",
            },
        )
    if node.get("id") == "barthen_provisions":
        _ensure_reward(
            cleaned,
            {
                "id": "phandalin_delivery_10gp",
                "type": "gold",
                "amount": 10,
                "currency": "gp",
                "scope": "per_player",
                "requires": ["phandalin_supplies_delivered"],
                "description": "把开局护送的补给货车交到巴森补给后，每名玩家获得10 gp报酬。",
            },
        )
    if node.get("id") == "lionshield_coster":
        _ensure_reward(
            cleaned,
            {
                "id": "lionshield_recovered_goods_50gp",
                "type": "gold",
                "amount": 50,
                "currency": "gp",
                "scope": "party",
                "requires": ["lionshield_goods_returned"],
                "description": "归还或告知克拉摩窝点中狮盾货物的位置后，莱妮支付50 gp。",
            },
        )
    if node.get("id") == "sleeping_giant_redbrand_ruffians":
        _ensure_reward(
            cleaned,
            {
                "id": "redbrand_ruffians_400_xp",
                "type": "xp",
                "amount": 400,
                "scope": "party",
                "requires": ["redbrand_ruffians_defeated"],
                "description": "击败沉睡巨人或街面冲突中的四名红标帮恶霸后，队伍平分400 XP。",
            },
        )
    for reward_id, owner in REWARD_OWNER_BY_ID.items():
        canonical_reward = GENERATION_REFERENCE.get("canonical_rewards", {}).get(reward_id)
        if owner == node.get("id") and canonical_reward:
            _ensure_reward(cleaned, canonical_reward)
    node["rewards"] = cleaned


def _ensure_reward(rewards: list[dict[str, Any]], reward: dict[str, Any]) -> None:
    """模型漏写标准奖励时补齐；已有同 id 时只校正关键契约。"""
    for item in rewards:
        if item.get("id") == reward["id"]:
            item.update(reward)
            return
    rewards.append(reward)


def _cleanup_node_events(node: dict[str, Any]) -> None:
    """全局事件只留在归属节点，避免后续房间误触发早期奖励。"""
    node_id = str(node.get("id", ""))
    events = []
    for event_id in node.get("events", []):
        owner = GLOBAL_EVENT_OWNER_BY_ID.get(str(event_id))
        if owner is not None and owner != node_id:
            continue
        events.append(event_id)
    if node_id == "cragmaw_hideout_klarg_cave" and "klarg_treasure_found" not in events:
        events.append("klarg_treasure_found")
    if node_id == "barthen_provisions" and "phandalin_supplies_delivered" not in events:
        events.append("phandalin_supplies_delivered")
    if node_id == "lionshield_coster" and "lionshield_goods_returned" not in events:
        events.append("lionshield_goods_returned")
    if node_id == "sleeping_giant_redbrand_ruffians" and "redbrand_ruffians_defeated" not in events:
        events.append("redbrand_ruffians_defeated")
    for event_id, owner in GLOBAL_EVENT_OWNER_BY_ID.items():
        if owner == node_id and event_id not in events:
            events.append(event_id)
    node["events"] = events


def _stable_id(value: str) -> str:
    return STABLE_ID_ALIASES.get(str(value), str(value))


def validate_nodes(nodes: list[dict[str, Any]], *, expected_ids: list[str] | None = None) -> dict[str, Any]:
    ids = [node.get("id") for node in nodes]
    id_set = set(ids)
    expected = expected_ids or TARGET_NODE_IDS
    event_ids = {event for node in nodes for event in node.get("events", [])}
    clue_ids = {
        clue.get("id")
        for node in nodes
        for clue in node.get("clues", [])
        if isinstance(clue, dict) and clue.get("id")
    }
    known_requirements = event_ids | clue_ids
    allowed_external_requirements = set(GENERATION_REFERENCE.get("external_completion_events", [])) or {
        "glasstaff_letters_delivered_to_halia",
        "agatha_bowgentle_info_obtained",
        "wyvern_tor_orcs_defeated",
        "cragmaw_castle_threat_ended",
        "redbrand_threat_ended",
    }
    missing_requirements: list[dict[str, Any]] = []
    dangling_exits: list[dict[str, Any]] = []
    allowed_external_targets = set(GENERATION_REFERENCE.get("external_targets", [])) or {
        "phandalin",
        "cragmaw_hideout_kennel",
        "cragmaw_hideout_steep_passage",
        "phandalin_lionshield_coster",
        "lionshield_coster",
        "cragmaw_castle",
        "redbrand_hideout_entrance",
        "old_owl_well",
        "wyvern_tor",
        "conyberry_agatha_lair",
        "thundertree",
    }

    for node in nodes:
        for exit_item in node.get("exits", []):
            target = exit_item.get("next_node_id")
            if target not in id_set and target not in allowed_external_targets:
                dangling_exits.append({"node_id": node.get("id"), "exit_id": exit_item.get("id"), "target": target})
            for requirement in exit_item.get("requires", []):
                if requirement not in known_requirements and requirement not in allowed_external_requirements:
                    missing_requirements.append(
                        {"node_id": node.get("id"), "field": "exits", "id": exit_item.get("id"), "requires": requirement}
                    )
        for reward in node.get("rewards", []):
            reward_id = reward.get("id")
            if reward_id in {"goblin_ambush_hideout_75_xp", "cragmaw_hideout_milestone_275_xp"}:
                # 中文注释：标准奖励可依赖跨批次事件；只要最终全图存在即可。
                pass
            for requirement in reward.get("requires", []):
                if requirement not in known_requirements and requirement not in allowed_external_requirements:
                    missing_requirements.append(
                        {"node_id": node.get("id"), "field": "rewards", "id": reward.get("id"), "requires": requirement}
                    )

    field_counts = {
        "clues": sum(len(node.get("clues", [])) for node in nodes),
        "events": sum(len(node.get("events", [])) for node in nodes),
        "fallbacks": sum(len(node.get("fallbacks", [])) for node in nodes),
        "scene_beats": sum(len(node.get("scene_beats", [])) for node in nodes),
        "rules_notes": sum(len(node.get("rules_notes", [])) for node in nodes),
        "exits": sum(len(node.get("exits", [])) for node in nodes),
        "rewards": sum(len(node.get("rewards", [])) for node in nodes),
    }
    char_counts = {
        node.get("id", ""): {
            key: len(json.dumps(node.get(key, ""), ensure_ascii=False))
            for key in ("dm_summary", "player_visible_intro", "scene_beats", "rules_notes", "clues", "events", "fallbacks", "exits")
        }
        for node in nodes
    }
    return {
        "node_ids": ids,
        "expected_ids_missing": [node_id for node_id in expected if node_id not in id_set],
        "unexpected_ids": [node_id for node_id in ids if node_id not in expected],
        "field_counts": field_counts,
        "missing_requirements": missing_requirements,
        "dangling_exits": dangling_exits,
        "char_counts": char_counts,
    }


def merge_nodes_by_id(base_nodes: list[dict[str, Any]], new_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """小步续跑时按 ID 替换节点，保持输出始终是完整试验文件。"""
    merged: dict[str, dict[str, Any]] = {str(node.get("id")): node for node in base_nodes}
    for node in new_nodes:
        merged[str(node.get("id"))] = node
    return list(merged.values())


def expected_ids_for_scope(scope: str) -> list[str]:
    """按生成范围返回完整试验文件应包含的节点，避免主流程堆嵌套分支。"""
    if scope == "wave_echo_cave":
        return [*HIDEOUT_NODE_IDS, *PHANDALIN_TOWN_NODE_IDS, *SPIDER_WEB_NODE_IDS, *WAVE_ECHO_NODE_IDS]
    if scope == "spider_web":
        return [*HIDEOUT_NODE_IDS, *PHANDALIN_TOWN_NODE_IDS, *SPIDER_WEB_NODE_IDS]
    if scope == "phandalin_town":
        return [*HIDEOUT_NODE_IDS, *PHANDALIN_TOWN_NODE_IDS]
    if scope == "hideout":
        return HIDEOUT_NODE_IDS
    return TARGET_NODE_IDS


def source_text_for_scope(scope: str, pdf_path: Path) -> str:
    """每个 scope 显式绑定 PDF 切片，便于续跑时定位源文本。"""
    if scope == "wave_echo_cave":
        return extract_wave_echo_source(pdf_path)
    if scope == "spider_web":
        return extract_spider_web_source(pdf_path)
    if scope == "phandalin_town":
        return extract_phandalin_source(pdf_path)
    if scope == "hideout":
        return extract_hideout_source(pdf_path)
    return extract_trial_source(pdf_path)


def batches_for_scope(scope: str, source_text: str) -> list[tuple[list[str], str]]:
    """分批策略跟 scope 绑定，方便单批失败后继续跑。"""
    if scope == "wave_echo_cave":
        return wave_echo_batches(source_text)
    if scope == "spider_web":
        return spider_web_batches(source_text)
    if scope == "phandalin_town":
        return phandalin_town_batches(source_text)
    return hideout_batches(source_text)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Lost Mine slim canonical 正式节点。")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--base-url", default=os.getenv("TRIAL_LLM_BASE_URL", "https://code.b886.top/v1"))
    parser.add_argument("--api-key", default=os.getenv("TRIAL_LLM_API_KEY", ""))
    parser.add_argument("--model", default=os.getenv("TRIAL_LLM_MODEL", "gpt-5.5"))
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--no-streaming", action="store_true", help="关闭流式响应；部分代理要求必须流式。")
    parser.add_argument("--single-call", action="store_true", help="调试用：不分批，单次生成全部节点。")
    parser.add_argument("--scope", choices=["trial", "hideout", "phandalin_town", "spider_web", "wave_echo_cave"], default="trial")
    parser.add_argument("--append-existing", action="store_true", help="把新生成节点按 ID 合并进现有 out 文件。")
    parser.add_argument("--batch-index", type=int, default=None, help="只运行分批列表中的第 N 批，从 0 开始。")
    args = parser.parse_args()

    if not args.api_key:
        raise ValueError("缺少 API key：请设置 TRIAL_LLM_API_KEY 或传入 --api-key")

    global GENERATION_REFERENCE, TARGET_NODE_IDS, HIDEOUT_NODE_IDS, PHANDALIN_TOWN_NODE_IDS, SPIDER_WEB_NODE_IDS, WAVE_ECHO_NODE_IDS
    global STABLE_ID_ALIASES, NEXT_NODE_ALIASES, REWARD_OWNER_BY_ID, GLOBAL_EVENT_OWNER_BY_ID
    GENERATION_REFERENCE = load_generation_reference(args.reference)
    TARGET_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("trial", TARGET_NODE_IDS)
    HIDEOUT_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("hideout", HIDEOUT_NODE_IDS)
    PHANDALIN_TOWN_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("phandalin_town", PHANDALIN_TOWN_NODE_IDS)
    SPIDER_WEB_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("spider_web", SPIDER_WEB_NODE_IDS)
    WAVE_ECHO_NODE_IDS = GENERATION_REFERENCE.get("scopes", {}).get("wave_echo_cave", WAVE_ECHO_NODE_IDS)
    STABLE_ID_ALIASES = GENERATION_REFERENCE.get("id_aliases", STABLE_ID_ALIASES)
    NEXT_NODE_ALIASES = GENERATION_REFERENCE.get("node_aliases", NEXT_NODE_ALIASES)
    REWARD_OWNER_BY_ID = GENERATION_REFERENCE.get("reward_owners", REWARD_OWNER_BY_ID)
    if GENERATION_REFERENCE:
        GLOBAL_EVENT_OWNER_BY_ID = {
            **{event_id: "" for event_id in GENERATION_REFERENCE.get("external_completion_events", [])},
            **GENERATION_REFERENCE.get("event_owners", {}),
        }

    expected_ids = expected_ids_for_scope(args.scope)
    source_text = source_text_for_scope(args.scope, args.pdf)
    client = OpenAI(api_key=args.api_key, base_url=args.base_url, timeout=args.timeout, max_retries=1)
    if args.scope in {"hideout", "phandalin_town", "spider_web", "wave_echo_cave"} and not args.single_call:
        nodes = []
        batches = batches_for_scope(args.scope, source_text)
        if args.batch_index is not None:
            batches = [batches[args.batch_index]]
        for target_ids, user_prompt in batches:
            nodes.extend(
                invoke_nodes(
                    client,
                    model=args.model,
                    temperature=args.temperature,
                    target_ids=target_ids,
                    prompt_template=USER_PROMPT_TEMPLATE,
                    source_text="",
                    streaming=not args.no_streaming,
                    prepared_user_prompt=user_prompt,
                )
            )
    elif args.single_call:
        nodes = invoke_nodes(
            client,
            model=args.model,
            temperature=args.temperature,
            target_ids=expected_ids,
            prompt_template=USER_PROMPT_TEMPLATE,
            source_text=source_text,
            streaming=not args.no_streaming,
        )
    else:
        hook_source, early_source = split_trial_sources(source_text)
        nodes = [
            *invoke_nodes(
                client,
                model=args.model,
                temperature=args.temperature,
                target_ids=["adventure_hook_meet_me_in_phandalin"],
                prompt_template=HOOK_PROMPT_TEMPLATE,
                source_text=hook_source,
                streaming=not args.no_streaming,
            ),
            *invoke_nodes(
                client,
                model=args.model,
                temperature=args.temperature,
                target_ids=[
                    "goblin_ambush",
                    "goblin_trail_to_cragmaw_hideout",
                    "cragmaw_hideout_entrance",
                    "cragmaw_hideout_goblin_blind",
                ],
                prompt_template=USER_PROMPT_TEMPLATE,
                source_text=early_source,
                streaming=not args.no_streaming,
            ),
        ]

    nodes = normalize_stable_ids(nodes)
    if args.append_existing and args.out.exists():
        existing_nodes = json.loads(args.out.read_text(encoding="utf-8"))
        nodes = normalize_stable_ids(merge_nodes_by_id(existing_nodes, nodes))
    report = validate_nodes(nodes, expected_ids=expected_ids)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
