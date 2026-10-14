"""分章回归：扁平书签（章/节同级 + 标题粘页码）不得把小节切成假章。"""

from __future__ import annotations

from src.ingest.chapter_detect import _clean_outline_title, _from_outline


class _Item:
    def __init__(self, title):
        self.title = title


class _Reader:
    def __init__(self, titles):
        self.outline = [_Item(t) for t in titles]

    def get_destination_page_number(self, item):
        return self.outline.index(item) * 5


def test_flat_outline_filters_sections_and_strips_page_numbers():
    """z-library 类电子书：扁平书签混章/节、标题粘目录页码（实测《强化学习的数学原理》）。"""
    titles = ["前言", "目录", "第 1 章基本概念 1", "1.4策略 5", "1.7马尔可夫决策过程 11",
              "第 2 章状态值与贝尔曼方程 15", "2.1启发现例 16", "2.8.1示例 31",
              "第 3 章最优状态值 35", "5.6总结 96", "6.4.3随机梯度下降 116"]
    chapters = _from_outline(_Reader(titles))
    names = [t for t, _ in chapters]
    assert names == ["前言", "第 1 章基本概念", "第 2 章状态值与贝尔曼方程", "第 3 章最优状态值"], names
    assert not any(n.startswith(("1.4", "2.8.1", "5.6", "6.4.3")) for n in names), "小节不得成章"


def test_outline_without_chapter_style_at_least_drops_sections():
    """没有「第X章」样式的书：至少剔除小节号条目，不把 1.4 切成章。"""
    titles = ["概述 1", "1.1引言 3", "1.2模型 7", "2.1算法 20", "总结 40"]
    names = [t for t, _ in _from_outline(_Reader(titles))]
    assert "1.1引言" not in names and "2.1算法" not in names, names


def test_clean_title():
    assert _clean_outline_title("第 1 章基本概念 1") == "第 1 章基本概念"
    assert _clean_outline_title("第 2 章  状态值  15") == "第 2 章  状态值"
