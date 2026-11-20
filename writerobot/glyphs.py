"""字形来源：汉字笔画数据 + 内置拉丁字体。

汉字笔画取自 `chanind/hanzi-writer-data <https://github.com/chanind/hanzi-writer-data>`_
（MIT 许可）。其中 ``medians`` 字段是每笔的 **中心线**——正是笔尖实际走过的路径，
比解析 SVG 轮廓再求中心要稳定得多。

坐标系
------
本模块对外统一使用「em 盒」坐标系：

* X 轴向右，范围 ``0 .. advance``（拉丁）或 ``0 .. 1``（汉字）
* Y 轴向上，基线 ``y = 0``，大写字母高 ``y = 1.0``
* 汉字占满 ``1 x 1`` 的方形 em 盒，底边贴基线
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .fonts import get_glyph as get_latin_glyph

Point = Tuple[float, float]
Stroke = List[Point]

__all__ = [
    "Glyph",
    "GlyphProvider",
    "HanziProvider",
    "LatinProvider",
    "GlyphRepository",
    "HANZI_DATA_URL",
    "HANZI_EM_BOX",
    "is_hanzi",
]

#: 单个汉字数据的下载地址模板（jsdelivr 为主，GitHub raw 为备）
HANZI_DATA_URL = "https://cdn.jsdelivr.net/npm/hanzi-writer-data@2.0.1/{}.json"
HANZI_DATA_FALLBACK_URL = "https://raw.githubusercontent.com/chanind/hanzi-writer-data/master/data/{}.json"

#: 最后的兜底：GitHub API。某些网络环境只放行 api.github.com，
#: 而 cdn.jsdelivr.net 与 raw.githubusercontent.com 均不可达。
HANZI_DATA_API_URL = (
    "https://api.github.com/repos/chanind/hanzi-writer-data/contents/data/{}.json"
)

#: hanzi-writer-data 的画布边长
HANZI_EM_BOX = 1024.0

#: 汉字墨迹框。取自 Hanzi Writer 源码中的官方定义：
#:   const [b, $] = [{x: 0, y: -124}, {x: 1024, y: 900}]
#: 原始数据 Y 轴向下，墨迹从 y=-124（上沿）延伸到 y=900（下沿/基线）。
#: 若误用 0..1024 归一化，汉字会整体上移约 12% 字高。
HANZI_INK_TOP = -124.0
HANZI_INK_BOTTOM = 900.0


#: 汉字可能出现的 Unicode 区段
_HANZI_RANGES = (
    (0x2E80, 0x2EFF),  # CJK 部首补充
    (0x3005, 0x3005),  # 々
    (0x3007, 0x3007),  # 〇
    (0x3021, 0x3029),  # 汉字数字
    (0x3038, 0x303B),
    (0x3400, 0x4DBF),  # 扩展 A
    (0x4E00, 0x9FFF),  # 基本区
    (0xF900, 0xFAFF),  # 兼容表意文字
    (0x20000, 0x2A6DF),  # 扩展 B
    (0x2A700, 0x2EBEF),
    (0x2F800, 0x2FA1F),  # 兼容补充
)


def is_hanzi(char: str) -> bool:
    """判断字符是否可能出现在汉字笔画数据里。"""
    if not char:
        return False
    code = ord(char[0])
    return any(lo <= code <= hi for lo, hi in _HANZI_RANGES)


@dataclass
class Glyph:
    """一个字形：按笔顺排列的笔画列表。"""

    char: str
    strokes: List[Stroke] = field(default_factory=list)
    advance: float = 1.0
    source: str = "builtin"

    @property
    def stroke_count(self) -> int:
        return len(self.strokes)

    def points(self) -> Iterable[Point]:
        for stroke in self.strokes:
            for pt in stroke:
                yield pt


class GlyphProvider:
    """字形来源的抽象接口。"""

    name = "provider"

    def get(self, char: str) -> Optional[Glyph]:
        raise NotImplementedError

    def has(self, char: str) -> bool:
        return self.get(char) is not None


class LatinProvider(GlyphProvider):
    """内置单线拉丁字体。"""

    name = "latin"

    def get(self, char: str) -> Optional[Glyph]:
        data = get_latin_glyph(char)
        if data is None:
            return None
        strokes, advance = data
        return Glyph(
            char=char,
            strokes=[[(float(x), float(y)) for x, y in s] for s in strokes],
            advance=advance,
            source="builtin-latin",
        )


class HanziProvider(GlyphProvider):
    """汉字笔画数据（hanzi-writer-data），带本地磁盘缓存。

    首次访问某字符时从网络下载 JSON，随后从缓存读取。
    """

    name = "hanzi"

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        url_template: str = HANZI_DATA_URL,
        fallback_url_template: Optional[str] = HANZI_DATA_FALLBACK_URL,
        api_url_template: Optional[str] = HANZI_DATA_API_URL,
        timeout: float = 20.0,
        offline: bool = False,
        user_agent: str = "write-robot/1.0 (+https://github.com/chanind/hanzi-writer-data)",
        github_token: Optional[str] = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "write-robot" / "hanzi"
        self.url_template = url_template
        self.fallback_url_template = fallback_url_template
        self.api_url_template = api_url_template
        self.timeout = timeout
        self.offline = offline
        self.user_agent = user_agent
        self.github_token = github_token or os.environ.get("GITHUB_TOKEN") or None
        self._cache: Dict[str, Optional[Glyph]] = {}
        #: 上次成功下载所用的源下标，后续字符优先使用
        self._preferred_source: Optional[int] = None

    # ---------------------------------------------------------------- 缓存

    def _cache_path(self, char: str) -> Path:
        # 用码位命名，避免文件系统对 Unicode 名称的兼容性问题
        return self.cache_dir / f"U+{ord(char):04X}.json"

    def _load_cached(self, char: str) -> Optional[dict]:
        path = self._cache_path(char)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def _store_cached(self, char: str, payload: dict) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_path(char)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            tmp.replace(path)
        except OSError:
            pass  # 缓存失败不影响主流程

    # ---------------------------------------------------------------- 下载

    def _download(self, char: str) -> Optional[dict]:
        """下载单个汉字数据。按 CDN → GitHub API → GitHub Raw 依次尝试。

        三个源各有各的可用性：某些网络环境只放行 ``api.github.com``，
        另一些只放行 CDN。逐个降级能让下载成功率显著提高。

        一旦某个源成功，会被记为「首选源」，后续字符直接先试它，
        避免每个字都去撞一遍不通的源。

        返回 ``None`` 表示「该字符没有收录」（如拉丁字母），属于正常情况。
        """
        if self.offline:
            return None

        quoted = urllib.parse.quote(char)
        sources: List[Tuple[str, str, dict]] = []
        if self.url_template:
            sources.append((self.url_template, quoted, {}))
        if self.api_url_template:
            headers = {"Accept": "application/vnd.github.raw"}
            if self.github_token:
                headers["Authorization"] = f"Bearer {self.github_token}"
            sources.append((self.api_url_template, quoted, headers))
        if self.fallback_url_template:
            sources.append((self.fallback_url_template, quoted, {}))

        # 把上次成功的源调到最前
        if self._preferred_source is not None and 0 <= self._preferred_source < len(sources):
            preferred = sources.pop(self._preferred_source)
            sources.insert(0, preferred)

        last_error: Optional[Exception] = None
        for index, (tpl, arg, extra_headers) in enumerate(sources):
            url = tpl.format(arg)
            headers = {"User-Agent": self.user_agent}
            headers.update(extra_headers)
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                self._preferred_source = index
                return payload
            except urllib.error.HTTPError as exc:
                # 404 表示该字符没有收录（常见于拉丁字母、标点），不是网络故障
                if exc.code == 404:
                    return None
                last_error = exc
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
        raise IOError(f"下载汉字数据失败: {char!r} ({last_error})")

    # ---------------------------------------------------------------- 接口

    def get(self, char: str) -> Optional[Glyph]:
        if char in self._cache:
            return self._cache[char]

        # 非汉字直接跳过，避免为拉丁字母/标点发起无谓的网络请求
        if not is_hanzi(char):
            self._cache[char] = None
            return None

        payload = self._load_cached(char)
        if payload is None:
            payload = self._download(char)
            if payload is None:
                self._cache[char] = None
                return None
            self._store_cached(char, payload)

        glyph = self._build(char, payload)
        self._cache[char] = glyph
        return glyph

    @staticmethod
    def _build(char: str, payload: dict) -> Optional[Glyph]:
        medians: Sequence[Sequence[Sequence[float]]] = payload.get("medians") or []
        if not medians:
            return None

        # 原始数据：X 向右，Y 向下，墨迹框 x∈[0,1024], y∈[-124,900]
        # 目标：X 向右，Y 向上，字形填满 1x1 的 em 盒，底边贴基线
        ink_h = HANZI_INK_BOTTOM - HANZI_INK_TOP
        strokes: List[Stroke] = []
        for median in medians:
            pts = [
                (float(x) / HANZI_EM_BOX, (HANZI_INK_BOTTOM - float(y)) / ink_h)
                for x, y in median
            ]
            if len(pts) == 1:
                # 点（顿点等）：给一个极小的短线段，运动控制才画得出这一笔
                x, y = pts[0]
                pts = [(x, y), (x, y + 0.012)]
            strokes.append(pts)

        return Glyph(char=char, strokes=strokes, advance=1.0, source="hanzi-writer-data")


class GlyphRepository:
    """字形仓库：按优先级依次询问多个来源。

    设计要点：

    * **按字符类别路由**。ASCII 字符直接查内置拉丁字体，不发起任何网络请求；
      只有可能是汉字的字符才会去查笔画数据。这样写英文时完全不依赖网络。
    * **单个来源失败不影响整体**。网络抖动导致汉字数据取不到时，会记录一条
      警告并继续尝试下一个来源，而不是让整个任务失败。
    """

    def __init__(self, providers: Sequence[GlyphProvider]) -> None:
        self.providers = list(providers)
        #: 采集到的警告信息（如某个字形因网络问题缺失）
        self.warnings: List[str] = []

    @classmethod
    def default(
        cls,
        cache_dir: Optional[Path] = None,
        offline: bool = False,
    ) -> "GlyphRepository":
        return cls([HanziProvider(cache_dir=cache_dir, offline=offline), LatinProvider()])

    def get(self, char: str) -> Optional[Glyph]:
        for provider in self.providers:
            try:
                glyph = provider.get(char)
            except Exception as exc:
                # 数据源故障不应中断整体流程
                self.warnings.append(f"{provider.name} 取字形 {char!r} 失败: {exc}")
                continue
            if glyph is not None and glyph.strokes:
                return glyph
        return None

    def get_many(self, text: str) -> List[Optional[Glyph]]:
        return [self.get(ch) for ch in text]
