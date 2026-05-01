from pathlib import Path
from typing import List
import os
import pickle
import re
import unicodedata

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma

from app.config.settings import settings

RAG_DIR = Path(__file__).resolve().parent
BACKEND_DIR = RAG_DIR.parents[1]
MD_PATH = RAG_DIR / "data" / "DnD_5E_新手套组_基础入门规则CN.md"
DB_PATH = BACKEND_DIR / "data" / "rag_db"
BM25_PATH = DB_PATH / "bm25_index.pkl"
SOURCE_TAG = "DnD_5E_新手套组_基础入门规则CN"

CATEGORY_MAP = {
    "如何使用本手册": "how_to_play",
    "战斗系统": "combat",
    "冒险生活": "adventuring",
    "施法规则": "spellcasting_rules",
    "状态及环境判定": "conditions",
    "如何进行游戏": "how_to_play",
    "战斗": "combat",
    "冒险": "adventuring",
    "状态": "conditions",
    "施法": "spellcasting_rules",
}

SUB_CATEGORY_ALIASES = {
    "conditions": {
        "目盲": "conditions_blinded",
        "blinded": "conditions_blinded",
        "魅惑": "conditions_charmed",
        "charmed": "conditions_charmed",
        "耳聋": "conditions_deafened",
        "deafened": "conditions_deafened",
        "恐慌": "conditions_frightened",
        "frightened": "conditions_frightened",
        "擒抱": "conditions_grappled",
        "grappled": "conditions_grappled",
        "倒地": "conditions_prone",
        "prone": "conditions_prone",
        "隐形": "conditions_invisible",
        "invisible": "conditions_invisible",
        "失能": "conditions_incapacitated",
        "incapacitated": "conditions_incapacitated",
        "束缚": "conditions_restrained",
        "restrained": "conditions_restrained",
        "麻痹": "conditions_paralyzed",
        "paralyzed": "conditions_paralyzed",
        "石化": "conditions_petrified",
        "petrified": "conditions_petrified",
        "中毒": "conditions_poisoned",
        "poisoned": "conditions_poisoned",
        "震慑": "conditions_stunned",
        "stunned": "conditions_stunned",
        "昏迷": "conditions_unconscious",
        "unconscious": "conditions_unconscious",
    },
    "adventuring": {
        "休息": "resting",
        "resting": "resting",
        "旅行": "travel",
        "travel": "travel",
        "奖励": "rewards",
        "rewards": "rewards",
        "装备": "equipment",
        "equipment": "equipment",
        "坠落": "falling",
        "falling": "falling",
        "光照": "lighting_vision",
        "视觉": "lighting_vision",
        "照明": "lighting_vision",
        "窒息": "suffocating",
        "suffocating": "suffocating",
        "潜行": "stealth_hiding",
        "隐藏": "stealth_hiding",
        "hiding": "stealth_hiding",
    },
    "combat": {
        "战斗流程": "combat_order",
        "theorderofcombat": "combat_order",
        "移动与位置": "movement_position",
        "movementandposition": "movement_position",
        "战斗动作": "action_in_combat",
        "actionincombat": "action_in_combat",
        "掩护": "cover",
        "cover": "cover",
        "发动攻击": "making_attack",
        "makinganattack": "making_attack",
        "伤害与治疗": "damage_healing",
        "damageandhealing": "damage_healing",
    },
    "how_to_play": {
        "六项属性": "six_abilities",
        "sixabilities": "six_abilities",
    },
    "spellcasting_rules": {
        "施展法术": "spell_components",
        "castingaspell": "spell_components",
        "法术是什么": "what_is_spell",
        "whatisaspell": "what_is_spell",
    },
}

IN_SCOPE_CATEGORIES = {
    "combat",
    "adventuring",
    "conditions",
}

SPELL_SCOPE_POLICY_STRIP_ALL = "strip_all"
SPELL_SCOPE_POLICY_GENERIC_RULES_ONLY = "generic_rules_only"
DEFAULT_SPELL_SCOPE_POLICY = SPELL_SCOPE_POLICY_STRIP_ALL

CONDITION_KEYWORD_TO_SUB_CATEGORY = {
    "blinded": "conditions_blinded",
    "charmed": "conditions_charmed",
    "deafened": "conditions_deafened",
    "frightened": "conditions_frightened",
    "grappled": "conditions_grappled",
    "prone": "conditions_prone",
    "invisible": "conditions_invisible",
    "incapacitated": "conditions_incapacitated",
    "restrained": "conditions_restrained",
    "paralyzed": "conditions_paralyzed",
    "petrified": "conditions_petrified",
    "poisoned": "conditions_poisoned",
    "stunned": "conditions_stunned",
    "unconscious": "conditions_unconscious",
}

CONDITION_HEADING_PATTERN = re.compile(
    r"(?im)(?:^|\n)[^\n]{0,24}?("
    r"blinded|charmed|deafened|frightened|grappled|prone|"
    r"invisible|incapacitated|restrained|paralyzed|"
    r"petrified|poisoned|stunned|unconscious"
    r")\s*(?:\n|$)"
)

BLOCKED_SUB_CATEGORY_MARKERS = (
    "法术列表",
    "spell list",
    "spelllists",
    "spells",
)

OUT_OF_SCOPE_CONTENT_MARKERS = (
    "魔法物品",
    "magic item",
    "magic items",
    "专长",
    "feat",
    "feats",
    "法术详解",
    "spell descriptions",
    "spell description",
    "法术列表",
    "spell list",
    "spell lists",
    "spellbook",
)

SPELL_DETAIL_MARKERS_CN = (
    "施法时间",
    "施法距离",
    "法术成分",
    "持续时间",
)

SPELL_DETAIL_MARKERS_EN = (
    "casting time",
    "spell range",
    "components",
    "duration",
)

MIN_RULE_CHUNK_LEN = 200
MAX_RULE_CHUNK_LEN = 500
TINY_RULE_CHUNK_LEN = 20

CURRENCY_PATTERN = re.compile(r"\b\d+\s?(?:gp|sp|cp|pp|ep)\b", re.IGNORECASE)

NON_RULE_TAIL_MARKERS = (
    "制作组",
    "关于翻译",
)

NON_RULE_CONTENT_MARKERS = (
    "制作组",
    "主管设计师",
    "设计组：",
    "关于翻译",
    "翻译：",
    "整理校对",
    "免责声明",
    "请勿用作商业用途",
    "合法版权",
    "未经威世智许可",
)


def _normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").strip().lower()
    value = re.sub(r"\s+", "", value)
    return value


def _normalize_content_for_compare(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").strip().lower()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value)
    return value


def _resolve_spell_scope_policy() -> str:
    value = _normalize_text(os.getenv("RAG_SPELL_SCOPE_POLICY", DEFAULT_SPELL_SCOPE_POLICY))
    if value in {SPELL_SCOPE_POLICY_STRIP_ALL, SPELL_SCOPE_POLICY_GENERIC_RULES_ONLY}:
        return value
    return DEFAULT_SPELL_SCOPE_POLICY


def _strip_spell_sections(text: str) -> str:
    """剔除 # 施法 到 # 状态 之间的法术章节，避免越界语料混入索引。"""
    if not text:
        return ""

    spell_heading_matches = list(re.finditer(r"(?m)^#\s*施法\b.*$", text))
    state_heading_matches = list(re.finditer(r"(?m)^#\s*状态\b.*$", text))
    if not spell_heading_matches or not state_heading_matches:
        return text

    spans: list[tuple[int, int]] = []
    state_starts = [match.start() for match in state_heading_matches]
    for spell_match in spell_heading_matches:
        start = spell_match.start()
        end = next((state_start for state_start in state_starts if state_start > start), None)
        if end is not None:
            spans.append((start, end))

    if not spans:
        return text

    # 合并重叠区间，避免重复切割导致正文损坏。
    spans.sort(key=lambda item: item[0])
    merged_spans: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged_spans or start > merged_spans[-1][1]:
            merged_spans.append((start, end))
            continue
        prev_start, prev_end = merged_spans[-1]
        merged_spans[-1] = (prev_start, max(prev_end, end))

    parts: list[str] = []
    cursor = 0
    for start, end in merged_spans:
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _contains_out_of_scope_markers(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in OUT_OF_SCOPE_CONTENT_MARKERS)


def _is_spell_detail_block(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False

    hit_cn = sum(1 for marker in SPELL_DETAIL_MARKERS_CN if marker in text)
    hit_en = sum(1 for marker in SPELL_DETAIL_MARKERS_EN if marker in lowered)
    return hit_cn >= 2 or hit_en >= 2


def _is_catalog_or_table_noise(text: str) -> bool:
    content = (text or "").strip()
    if not content:
        return True

    currency_hits = len(CURRENCY_PATTERN.findall(content))
    if currency_hits >= 3:
        return True

    return False


def _strip_non_rule_tail(text: str) -> str:
    """移除规则正文后附带的制作/翻译/版权说明尾段。"""
    if not text:
        return ""

    cut_index = len(text)
    for marker in NON_RULE_TAIL_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut_index = min(cut_index, idx)

    if cut_index == len(text):
        return text
    return text[:cut_index].rstrip()


def _infer_sub_category_from_content(category: str, content: str) -> str:
    normalized = _normalize_text(content)

    if category == "conditions":
        if "目盲" in normalized or "blinded" in normalized:
            return "conditions_blinded"
        if "魅惑" in normalized or "charmed" in normalized:
            return "conditions_charmed"
        if "耳聋" in normalized or "deafened" in normalized:
            return "conditions_deafened"
        if "恐慌" in normalized or "frightened" in normalized:
            return "conditions_frightened"
        if "擒抱" in normalized or "grappled" in normalized:
            return "conditions_grappled"
        if "倒地" in normalized or "prone" in normalized:
            return "conditions_prone"
        if "隐形" in normalized or "invisible" in normalized:
            return "conditions_invisible"
        if "失能" in normalized or "incapacitated" in normalized:
            return "conditions_incapacitated"
        if "束缚" in normalized or "restrained" in normalized:
            return "conditions_restrained"
        if "麻痹" in normalized or "paralyzed" in normalized:
            return "conditions_paralyzed"
        if "石化" in normalized or "petrified" in normalized:
            return "conditions_petrified"
        if "中毒" in normalized or "poisoned" in normalized:
            return "conditions_poisoned"
        if "震慑" in normalized or "stunned" in normalized:
            return "conditions_stunned"
        if "昏迷" in normalized or "unconscious" in normalized:
            return "conditions_unconscious"

    if category == "adventuring":
        if "坠落" in normalized or "falling" in normalized:
            return "falling"
        if "休息" in normalized or "rest" in normalized:
            return "resting"
        if "旅行" in normalized or "travel" in normalized:
            return "travel"
        if "奖励" in normalized or "rewards" in normalized:
            return "rewards"
        if "装备" in normalized or "equipment" in normalized:
            return "equipment"
        if "窒息" in normalized or "suffocating" in normalized:
            return "suffocating"
        if "光照" in normalized or "视觉" in normalized or "darkvision" in normalized:
            return "lighting_vision"
        if "潜行" in normalized or "隐藏" in normalized or "hiding" in normalized:
            return "stealth_hiding"
        if "equipment" in normalized or "armor" in normalized or "weapon" in normalized:
            return "equipment"
        if "coinage" in normalized or "rewards" in normalized:
            return "rewards"

    if category == "combat":
        if "战斗流程" in normalized or "theorderofcombat" in normalized:
            return "combat_order"
        if "移动与位置" in normalized or "movementandposition" in normalized:
            return "movement_position"
        if "战斗动作" in normalized or "actionincombat" in normalized:
            return "action_in_combat"
        if "掩护" in normalized or "cover" in normalized:
            return "cover"
        if "发动攻击" in normalized or "attackroll" in normalized:
            return "making_attack"
        if "伤害" in normalized and "治疗" in normalized:
            return "damage_healing"
        if "damagetypes" in normalized or "damageresistance" in normalized:
            return "damage_healing"
        if "criticalhits" in normalized:
            return "damage_healing"
        if "ready" in normalized or "search" in normalized or "useanobject" in normalized:
            return "action_in_combat"

    if category == "conditions":
        if "fallingunconscious" in normalized or "deathsavingthrows" in normalized:
            return "conditions_unconscious"

    return ""


def _normalize_sub_category(category: str, sub_category: str, content: str) -> str:
    normalized = _normalize_text(sub_category)
    if not normalized:
        inferred = _infer_sub_category_from_content(category, content)
        return inferred or "unknown"

    alias_map = SUB_CATEGORY_ALIASES.get(category, {})
    for key, target in alias_map.items():
        if _normalize_text(key) in normalized:
            return target

    inferred = _infer_sub_category_from_content(category, content)
    if inferred:
        return inferred

    if category in IN_SCOPE_CATEGORIES:
        return "unknown"

    # 兜底：保留可读且可路由的稳定枚举，避免原始标题直接入库
    fallback = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", normalized).strip("_")
    return fallback or "unknown"

def extract_clean_text(spell_scope_policy: str | None = None) -> str:
    if not MD_PATH.exists():
        print(f"Warning: {MD_PATH} does not exist.")
        return ""
    with open(MD_PATH, "r", encoding="utf-8") as f:
        raw_text = f.read()

    policy = spell_scope_policy or _resolve_spell_scope_policy()
    text = _strip_non_rule_tail(raw_text)
    if policy == SPELL_SCOPE_POLICY_STRIP_ALL:
        return _strip_spell_sections(text)
    return text

def split_markdown_sections(text: str) -> List[Document]:
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "category"), ("##", "sub_category")]
    )
    return splitter.split_text(text)

def recursive_split(docs: List[Document]) -> List[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50
    )
    chunks = []
    for doc in docs:
        splits = splitter.split_documents([doc])
        chunks.extend(splits)
    return chunks

def normalize_metadata(doc: Document) -> Document:
    meta = doc.metadata.copy()
    category = CATEGORY_MAP.get((meta.get("category") or "").strip(), "other")
    sub_category = meta.get("sub_category", "")
    if sub_category is None:
        sub_category = ""
    sub_category = _normalize_sub_category(category, sub_category, doc.page_content)
        
    doc.metadata = {
        "category": category,
        "sub_category": sub_category,
        "source": SOURCE_TAG
    }
    return doc

def filter_pure_rules(docs: List[Document]) -> List[Document]:
    keywords = [
        "玩家手册",
        "由于版权原因",
        "请参考",
        "点击这里",
        "翻阅",
        "手册第",
        *NON_RULE_CONTENT_MARKERS,
    ]

    def is_pure(doc: Document) -> bool:
        text = (doc.page_content or "").strip()
        if not text:
            return False
        lowered = text.lower()
        if any(k in text or k.lower() in lowered for k in keywords):
            return False
        if _contains_out_of_scope_markers(text):
            return False
        if _is_spell_detail_block(text):
            return False
        if _is_catalog_or_table_noise(text):
            return False
        return True

    return [doc for doc in docs if is_pure(doc)]


def filter_domain_scope(
    docs: List[Document],
    include_spellcasting_rules: bool = False,
) -> List[Document]:
    """按方案剥离语料边界：排除法术列表/法术详情，仅保留泛规则。"""
    blocked_sub_categories = tuple(_normalize_text(marker) for marker in BLOCKED_SUB_CATEGORY_MARKERS)
    allowed_categories = set(IN_SCOPE_CATEGORIES)
    if include_spellcasting_rules:
        allowed_categories.add("spellcasting_rules")

    def is_blocked_sub_category(sub_category: str) -> bool:
        normalized_sub = _normalize_text(sub_category)
        if not normalized_sub:
            return False
        return any(marker in normalized_sub for marker in blocked_sub_categories)

    def in_scope(doc: Document) -> bool:
        category = doc.metadata.get("category", "")
        sub_category = doc.metadata.get("sub_category", "")
        content = doc.page_content or ""
        lowered = content.lower()

        # 只保留规则相关一级域
        if category not in allowed_categories:
            return False

        # 制作组/翻译说明等噪声，即使误打上 in-scope 也要剔除。
        if any(marker in content for marker in NON_RULE_CONTENT_MARKERS):
            return False

        # 排除法术列表与法术详情
        if is_blocked_sub_category(sub_category):
            return False

        # 内容层面越界：法术详情/魔法物品/专长等直接剔除。
        if _contains_out_of_scope_markers(lowered):
            return False

        if _is_spell_detail_block(content):
            return False

        return True

    return [doc for doc in docs if in_scope(doc)]


def _split_compound_condition_chunks(docs: List[Document]) -> List[Document]:
    """按状态标题拆分复合 conditions 文本块，提升子类标注覆盖率。"""
    split_docs: List[Document] = []

    for doc in docs:
        metadata = dict(doc.metadata)
        category = metadata.get("category")
        content = (doc.page_content or "").strip()

        if not content:
            continue
        if category != "conditions":
            split_docs.append(Document(page_content=content, metadata=metadata))
            continue

        matches = list(CONDITION_HEADING_PATTERN.finditer(content))
        if len(matches) <= 1:
            split_docs.append(Document(page_content=content, metadata=metadata))
            continue

        intro = content[:matches[0].start()].strip()
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            segment = content[start:end].strip()
            if not segment:
                continue
            if index == 0 and intro:
                segment = f"{intro}\n{segment}".strip()

            token = match.group(1).lower()
            segment_meta = dict(metadata)
            segment_meta["sub_category"] = CONDITION_KEYWORD_TO_SUB_CATEGORY.get(
                token,
                metadata.get("sub_category", "unknown"),
            )
            split_docs.append(Document(page_content=segment, metadata=segment_meta))

    return split_docs


def deduplicate_authoritative_rules(docs: List[Document]) -> List[Document]:
    """唯一权威原则：重复定义仅保留首个出现版本。"""
    seen_exact_signatures: set[str] = set()
    seen_rule_anchors: set[tuple[str, str, str]] = set()
    unique_docs: List[Document] = []

    for doc in docs:
        category = doc.metadata.get("category", "other")
        sub_category = doc.metadata.get("sub_category", "unknown")
        normalized_body = _normalize_content_for_compare(doc.page_content)
        if not normalized_body:
            continue

        # 1) 完全重复（常见于跨章节复制）
        exact_signature = normalized_body[:320]
        if exact_signature in seen_exact_signatures:
            continue

        # 2) 同类规则定义锚点重复，保留第一份定义
        anchor = normalized_body[:80]
        rule_anchor = (category, sub_category, anchor)
        if rule_anchor in seen_rule_anchors:
            continue

        seen_exact_signatures.add(exact_signature)
        seen_rule_anchors.add(rule_anchor)
        unique_docs.append(doc)

    return unique_docs


def rebalance_chunk_lengths(
    docs: List[Document],
    min_len: int = MIN_RULE_CHUNK_LEN,
    max_len: int = MAX_RULE_CHUNK_LEN,
    tiny_len: int = TINY_RULE_CHUNK_LEN,
) -> List[Document]:
    """合并同元数据短块并剔除极短噪声块，提升检索稳定性。"""
    if not docs:
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_len,
        chunk_overlap=50,
    )
    balanced: List[Document] = []
    pending: Document | None = None

    def merged_sub_category(primary: str, secondary: str) -> str:
        if primary == secondary:
            return primary
        if not primary or primary == "unknown":
            return secondary or "unknown"
        return primary

    def flush_pending() -> None:
        nonlocal pending
        if pending is None:
            return

        text = (pending.page_content or "").strip()
        metadata = dict(pending.metadata)
        pending = None

        if len(text) < tiny_len:
            return

        if len(text) <= max_len:
            balanced.append(Document(page_content=text, metadata=metadata))
            return

        for split_text in splitter.split_text(text):
            chunk = split_text.strip()
            if len(chunk) >= tiny_len:
                balanced.append(Document(page_content=chunk, metadata=metadata))

    for doc in docs:
        content = (doc.page_content or "").strip()
        if not content:
            continue

        current = Document(page_content=content, metadata=dict(doc.metadata))
        if pending is None:
            pending = current
            continue

        same_meta = pending.metadata == current.metadata
        pending_len = len(pending.page_content)
        current_len = len(current.page_content)

        if same_meta and pending_len < min_len:
            if pending_len + 1 + current_len <= max_len:
                pending.page_content = f"{pending.page_content}\n{current.page_content}"
                continue

            flush_pending()
            pending = current
            continue

        if same_meta and current_len < min_len and pending_len + 1 + current_len <= max_len:
            pending.page_content = f"{pending.page_content}\n{current.page_content}"
            continue

        flush_pending()
        pending = current

    flush_pending()

    # 二次压实：把同元数据的短尾块并入前一块，减少孤立碎片。
    compact: List[Document] = []
    for doc in balanced:
        content = (doc.page_content or "").strip()
        if not content:
            continue

        if compact and len(content) < min_len and compact[-1].metadata == doc.metadata:
            prev_content = compact[-1].page_content
            if len(prev_content) + 1 + len(content) <= max_len:
                compact[-1].page_content = f"{prev_content}\n{content}"
                continue

        compact.append(Document(page_content=content, metadata=dict(doc.metadata)))

    # 非 conditions 的短块优先并入同 category 邻接块，减少短碎片。
    packed: List[Document] = []
    for doc in compact:
        content = (doc.page_content or "").strip()
        if not content:
            continue

        category = doc.metadata.get("category")
        if (
            packed
            and category != "conditions"
            and len(content) < min_len
            and packed[-1].metadata.get("category") == category
            and len(packed[-1].page_content) + 1 + len(content) <= max_len
        ):
            previous = packed[-1]
            previous.page_content = f"{previous.page_content}\n{content}"
            previous.metadata["sub_category"] = merged_sub_category(
                previous.metadata.get("sub_category", "unknown"),
                doc.metadata.get("sub_category", "unknown"),
            )
            continue

        packed.append(Document(page_content=content, metadata=dict(doc.metadata)))

    index = 0
    while index < len(packed) - 1:
        current = packed[index]
        next_doc = packed[index + 1]
        current_content = (current.page_content or "").strip()
        if (
            len(current_content) < min_len
            and current.metadata.get("category") != "conditions"
            and current.metadata.get("category") == next_doc.metadata.get("category")
            and len(current_content) + 1 + len(next_doc.page_content) <= max_len
        ):
            merged_meta = dict(current.metadata)
            merged_meta["sub_category"] = merged_sub_category(
                merged_meta.get("sub_category", "unknown"),
                next_doc.metadata.get("sub_category", "unknown"),
            )
            packed[index] = Document(
                page_content=f"{current_content}\n{next_doc.page_content}",
                metadata=merged_meta,
            )
            del packed[index + 1]
            continue
        index += 1

    # 抽取每个 category 的前缀上下文，给仍过短的块补齐最低长度。
    category_primers: dict[str, str] = {}
    for doc in packed:
        category = doc.metadata.get("category", "")
        if category in category_primers:
            continue

        text = (doc.page_content or "").strip()
        if len(text) < min_len:
            continue

        if category == "conditions":
            heading_match = CONDITION_HEADING_PATTERN.search(text)
            if heading_match and heading_match.start() > 40:
                primer = text[:heading_match.start()].strip()
                if primer:
                    category_primers[category] = primer
                    continue

        category_primers[category] = text[: max_len // 2].strip()

    enforced: List[Document] = []
    for doc in packed:
        content = (doc.page_content or "").strip()
        metadata = dict(doc.metadata)
        category = metadata.get("category", "")

        if not content or len(content) < tiny_len:
            continue

        if len(content) < min_len:
            primer = category_primers.get(category, "")
            if primer and primer not in content:
                required = min_len - len(content) + 1
                prefix = primer[:required].strip()
                if prefix:
                    content = f"{prefix}\n{content}".strip()

        if len(content) > max_len:
            for split_text in splitter.split_text(content):
                chunk = split_text.strip()
                if len(chunk) >= tiny_len:
                    enforced.append(Document(page_content=chunk, metadata=dict(metadata)))
            continue

        enforced.append(Document(page_content=content, metadata=metadata))

    final_docs = [
        doc
        for doc in enforced
        if min_len <= len((doc.page_content or "").strip()) <= max_len
    ]
    return final_docs

def main():
    spell_scope_policy = _resolve_spell_scope_policy()
    raw_text = extract_clean_text(spell_scope_policy=spell_scope_policy)
    if not raw_text:
        return
    md_docs = split_markdown_sections(raw_text)
    chunks = recursive_split(md_docs)
    
    chunks = [normalize_metadata(doc) for doc in chunks]
    chunks = filter_pure_rules(chunks)
    chunks = filter_domain_scope(
        chunks,
        include_spellcasting_rules=spell_scope_policy == SPELL_SCOPE_POLICY_GENERIC_RULES_ONLY,
    )
    chunks = _split_compound_condition_chunks(chunks)
    chunks = deduplicate_authoritative_rules(chunks)
    chunks = rebalance_chunk_lengths(chunks)
    
    print(f"Total chunks after filtering: {len(chunks)}")
    if not chunks:
        return

    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        check_embedding_ctx_length=False,
        chunk_size=10
    )
    DB_PATH.mkdir(parents=True, exist_ok=True)
    Chroma.from_documents(
        chunks,
        embedding=embeddings,
        persist_directory=str(DB_PATH)
    )
    print(f"ChromaDB persisted to {DB_PATH}")

    BM25_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 运行时会基于 chunks 和 jieba 重新构建 BM25 检索器，这里只需持久化文本块。
    with open(BM25_PATH, "wb") as f:
        pickle.dump(chunks, f)
    print(f"BM25 index persisted to {BM25_PATH}")

if __name__ == "__main__":
    main()
