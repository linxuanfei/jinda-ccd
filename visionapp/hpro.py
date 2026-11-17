""".HPro 模板解析器（原 In-Vision 检测模板）。

文件是纯文本，结构大致为：

    第 1 行          : 机位数（站数）
    站头             : "<站号> <检测项数>"
    每个检测项       : 参数行（制表符分隔的多段参数）
                       ROI 行：x1 y1 x2 y2
                       若干附加行（空格分隔，如评分阈值、判定规则）
    结尾             : 文件尾类型 4 规则（版式与项内判定行不同），然后 "ok"

本模块把模板解析成结构化的 AST（保留原始文本，便于后续逐字段映射），
并提供 `to_dict()` 供检查、比对与导入。

文件尾 `4 <整数> <标称> <公差> <公差> <整数> <整数>` 不走项内判定的下标。
末尾两个整数原样保留，不解释含义。类型 4 不另起中文名。

注意：字段语义是逆向出来的推测，`tokens[1]` 猜测为"检测类型号"，
其余字段全部原样保留，等待逐项确认（用黄金样张对比数值即可验证）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

STATION_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s*$")
# 评分阈值行，形如 "0 200 0 0.85"
SCORE_RE = re.compile(r"^-?\d+\s+\d+\s+\d+\s+[\d.]+$")
# 文件尾类型 4。不是项内的「参照项 模式 x y 值1 值2 公差」。
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
TAIL4_RE = re.compile(
    rf"^4\s+(-?\d+)\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+(-?\d+)\s+(-?\d+)\s*$"
)


@dataclass
class HProTailRule:
    """文件尾的一条类型 4。三个数是标称/公差/公差，末尾两个整数含义未确认。"""
    raw: str
    nominal: float
    tol_a: float
    tol_b: float
    refs: tuple[int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": 4,
            "nominal": self.nominal,
            "tol": [self.tol_a, self.tol_b],
            "refs": list(self.refs),
            "raw": self.raw,
        }


def parse_tail_type4(line: str) -> HProTailRule | None:
    """只认文件尾类型 4 这一版式。对不上就返回 None，不要改用项内下标去猜。"""
    m = TAIL4_RE.match(line.strip())
    if not m:
        return None
    return HProTailRule(
        raw=line.strip(),
        nominal=float(m.group(2)),
        tol_a=float(m.group(3)),
        tol_b=float(m.group(4)),
        refs=(int(m.group(5)), int(m.group(6))),
    )


def _promote_file_tail(tpl: "HProTemplate") -> None:
    """把类型 4 从最后一站的站尾抬到文件尾。检查器以前只看见 ok。"""
    rules: list[HProTailRule] = []
    for st in tpl.stations:
        kept: list[str] = []
        for line in st.trailer:
            rule = parse_tail_type4(line)
            if rule is None:
                kept.append(line)
            else:
                rules.append(rule)
        st.trailer = kept
    kept_tail: list[str] = []
    for line in tpl.tail:
        rule = parse_tail_type4(line)
        if rule is None:
            kept_tail.append(line)
        else:
            rules.append(rule)
    tpl.tail_rules = rules
    if not rules:
        tpl.tail = kept_tail
        return
    raws = [rule.raw for rule in rules]
    merged: list[str] = []
    inserted = False
    for line in kept_tail:
        if line.lower() == "ok" and not inserted:
            merged.extend(raws)
            inserted = True
        merged.append(line)
    if not inserted:
        merged.extend(raws)
    tpl.tail = merged


@dataclass
class HProItem:
    """一个检测项（原模板中的一条）。"""
    params_line: str                            # 原始参数行
    tokens: list[str] = field(default_factory=list)     # 按制表符切分
    fields: list[str] = field(default_factory=list)     # 按空白切分
    roi: tuple[int, int, int, int] | None = None        # (x1, y1, x2, y2)
    extra: list[str] = field(default_factory=list)      # ROI 之后的附加行

    @property
    def type_hint(self) -> int | None:
        """猜测的检测类型号（第二个字段）。"""
        if len(self.fields) >= 2 and self.fields[1].lstrip("-").isdigit():
            return int(self.fields[1])
        return None

    @property
    def rect(self) -> tuple[int, int, int, int] | None:
        """换算成 (x, y, w, h)，与原程序 ROI 行 (x1,y1,x2,y2) 对应。"""
        if self.roi is None:
            return None
        x1, y1, x2, y2 = self.roi
        return (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    def as_dict(self) -> dict[str, Any]:
        return {
            "type_hint": self.type_hint,
            "tokens": self.tokens,
            "fields": self.fields,
            "roi_xyxy": list(self.roi) if self.roi else None,
            "roi_xywh": list(self.rect) if self.rect else None,
            "extra": self.extra,
            "raw": self.params_line,
        }


@dataclass
class HProStation:
    index: int
    count: int
    items: list[HProItem] = field(default_factory=list)
    trailer: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "count": self.count,
            "parsed_items": len(self.items),
            "items": [it.as_dict() for it in self.items],
            "trailer": self.trailer,
        }


@dataclass
class HProTemplate:
    path: str = ""
    station_count: int = 0
    stations: list[HProStation] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)
    tail_rules: list[HProTailRule] = field(default_factory=list)

    # ------------------------------------------------------------------ 解析
    @classmethod
    def parse(cls, path: str) -> "HProTemplate":
        with open(path, "r", encoding="gbk", errors="replace") as f:
            raw_lines = f.read().splitlines()

        lines = [ln for ln in raw_lines if ln.strip() != ""]
        tpl = cls(path=path)
        if not lines:
            return tpl

        tpl.station_count = int(lines[0].strip())
        i = 1
        while i < len(lines):
            line = lines[i]
            if line.strip().lower() == "ok":
                tpl.tail.append("ok")
                i += 1
                continue
            m = STATION_RE.match(line)
            if m and "\t" not in line:
                station = HProStation(index=int(m.group(1)), count=int(m.group(2)))
                i += 1
                # 读该站的检测项：参数行含制表符
                while i < len(lines) and "\t" in lines[i]:
                    item = HProItem(params_line=lines[i])
                    item.tokens = [t.strip() for t in lines[i].split("\t")]
                    item.fields = lines[i].split()
                    i += 1
                    if i < len(lines):
                        roi_line = lines[i].strip()
                        if re.fullmatch(r"-?\d+(\s+-?\d+){3}", roi_line):
                            x1, y1, x2, y2 = (int(v) for v in roi_line.split())
                            item.roi = (x1, y1, x2, y2)
                            i += 1
                    # 附加行：通常是 [判定规则行] + "<评分阈值行>" + "<0>"
                    seen_score_line = False
                    while i < len(lines) and "\t" not in lines[i]:
                        nxt = lines[i].strip()
                        if nxt.lower() == "ok" or STATION_RE.match(nxt):
                            break
                        item.extra.append(nxt)
                        i += 1
                        if SCORE_RE.match(nxt):
                            seen_score_line = True
                            # 评分行后面固定还有一行标志
                            if i < len(lines) and "\t" not in lines[i]:
                                item.extra.append(lines[i].strip())
                                i += 1
                            break
                        if seen_score_line:
                            break
                    station.items.append(item)
                # 站尾
                while i < len(lines) and "\t" not in lines[i]:
                    nxt = lines[i].strip()
                    if nxt.lower() == "ok":
                        break
                    if STATION_RE.match(nxt):
                        break
                    station.trailer.append(nxt)
                    i += 1
                tpl.stations.append(station)
                continue
            tpl.tail.append(line.strip())
            i += 1
        _promote_file_tail(tpl)
        return tpl

    # ------------------------------------------------------------------ 输出
    def item_count(self) -> int:
        return sum(len(s.items) for s in self.stations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "station_count": self.station_count,
            "stations": [s.as_dict() for s in self.stations],
            "tail": self.tail,
            "tail_rules": [rule.as_dict() for rule in self.tail_rules],
        }

    def dump(self, out_path: str | None = None) -> str:
        data = self.as_dict()
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if out_path:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)
        return text

    # ------------------------------------------------------------------ 摘要
    def summary(self) -> str:
        out = [f"模板: {os.path.basename(self.path)}",
               f"  机位数: {self.station_count}",
               f"  检测项: {self.item_count()}"]
        for st in self.stations:
            out.append(f"  站 {st.index}: 声明 {st.count} 项，解析 {len(st.items)} 项，附加行 {len(st.trailer)}")
            for k, it in enumerate(st.items):
                roi = it.rect
                out.append(f"    [{k}] type={it.type_hint} roi={roi} 参数段数={len(it.tokens)} 附加行={len(it.extra)}")
        if self.tail_rules:
            out.append(f"  文件尾类型4: {len(self.tail_rules)} 条")
            for rule in self.tail_rules:
                out.append(f"    {rule.raw}  refs={list(rule.refs)}")
        if self.tail:
            out.append(f"  尾部: {self.tail[:8]}")
        return "\n".join(out)


def parse_file(path: str) -> HProTemplate:
    return HProTemplate.parse(path)
