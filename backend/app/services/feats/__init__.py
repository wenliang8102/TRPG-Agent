"""职业成长可选专长的轻量入口。"""

from app.services.feats.registry import FEATS, apply_feat, available_feat_ids

__all__ = ["FEATS", "apply_feat", "available_feat_ids"]
