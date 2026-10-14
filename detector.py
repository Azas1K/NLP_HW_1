import re
from functools import lru_cache
from itertools import product
from math import prod

from pymorphy3 import MorphAnalyzer


TRANSLATION = str.maketrans({
    "ё": "е", "«": '"', "»": '"', "“": '"', "”": '"', "„": '"',
    "‘": "'", "’": "'", "–": "-", "—": "-", "‑": "-", "−": "-",
})
TOKEN = re.compile(r"\d+(?:\.\d+)*|[a-zа-я]+|[^\s]")
ATOM = r'''(?:\d+(?:\.\d+)*(?!\.?\d)|["']?[а-яa-z](?![\w.])["']?)'''
ITEM = rf"{ATOM}(?:\s*-\s*{ATOM})?"
VALUES = rf"{ITEM}(?:(?:\s*,\s*(?:(?:и|или)\s+)?|\s+(?:и|или)\s+){ITEM})*"
ENDING = r"(?:а|у|е|ом|ы|ов|ам|ами|ах)?"
REFERENCE = re.compile(
    rf"(?<![a-zа-я])(?:"
    rf"(?P<subpoint_article>подпункт{ENDING}\b|подпп?\.|подп\b|пп\.\s*п\.|пп\.|п\.\s*п\.|пп\b)|"
    rf"(?P<point_article>пункт{ENDING}\b|пунт{ENDING}\b|част(?:ь|и|ью|ей|ям|ями|ях)\b|п\.|ч\.|п\b|ч\b)|"
    rf"(?P<article>стать(?:я|и|е|ю|ей|ею|ям|ями|ях)\b|ст\.|ст\b))"
    rf"\s*(?P<values>{VALUES})"
)
BRIDGE = re.compile(r"\s*(?:,\s*)?(?:(?:и|или|а также|в|по)\s+)?")
UNKNOWN_LAW = re.compile(
    r"\s*(?:[а-я-]+(?:ого|его|ому|ему|ым|им|ый|ий|ой|ая|ое)\s+){0,3}"
    r"(?:кодекс[а-я]*|конституци[а-я]*|закон[а-я]*|указ[а-я]*|"
    r"постановлени[а-я]*|распоряжени[а-я]*|фз|фкз)\b"
)
NUMBER_DATE = re.compile(r"(№\s*[\w.-]+)\s+от\s+(\d{2}\.\d{2}\.\d{4})")
FIELDS = ("article", "point_article", "subpoint_article")
LETTERS = "абвгдежзийклмнопрстуфхцчшщъыьэюя"
MAX_LINKS = 10_000
# Диапазон («ст. 43.2-6», «пп. а-в») по условию задания возвращается одной строкой.
# True — разворачивать диапазон в отдельные ссылки.
EXPAND_RANGES = False
QUOTES = {'"', "'"}


def normalize(text):
    return text.lower().translate(TRANSLATION)


def expand_range(value):
    value = re.sub(r'''[\s"']''', "", value)
    if "-" not in value or not EXPAND_RANGES:
        return [value]
    start, end = value.split("-", 1)
    if start in LETTERS and end in LETTERS:
        left, right = LETTERS.index(start), LETTERS.index(end)
        return list(LETTERS[left:right + 1]) if left <= right else [value]
    if re.fullmatch(r"\d+(?:\.\d+)*", start + "." + end):
        prefix, _, left = start.rpartition(".")
        end_prefix, _, right = end.rpartition(".")
        if (not end_prefix or prefix == end_prefix) and 0 <= int(right) - int(left) < 1000:
            prefix = prefix + "." if prefix else ""
            return [prefix + str(i) for i in range(int(left), int(right) + 1)]
    return [value]


def is_nested_point(text, group, position):
    """«п. X ч. Y» без союза или запятой между ними: пункт вложен в часть."""
    if position == 0:
        return False
    current, before = group[position], group[position - 1]
    return (
        bool(current["point_article"]) and current["point_article"].startswith("ч")
        and bool(before["point_article"]) and before["point_article"].startswith("п")
        and not text[before.end():current.start()].strip()
    )


def parse_values(text):
    return list(dict.fromkeys(
        value for item in re.split(r"\s*,\s*(?:(?:и|или)\s+)?|\s+(?:и|или)\s+", text)
        for value in expand_range(item)
    ))


class LawDetector:
    def __init__(self, aliases):
        self.morph = MorphAnalyzer()
        self.lemma = lru_cache(maxsize=32_768)(self._lemma)
        self.trie = {}
        for law_id, names in aliases.items():
            for name in names:
                variants = [name]
                prefix = normalize(name).split('"', 1)[0].strip()
                if "№" in prefix and re.search(r"\d{2}\.\d{2}\.\d{4}", prefix):
                    variants.append(prefix)
                if name.startswith(('"', "«")):
                    variants += ["Закон " + name, "Федеральный закон " + name]
                variants += [NUMBER_DATE.sub(r"от \2 \1", variant) for variant in variants]
                for variant in variants:
                    node = self.trie
                    for token in TOKEN.findall(normalize(variant)):
                        if token in QUOTES:
                            continue
                        node = node.setdefault(self.lemma(token), {})
                    node.setdefault(None, set()).add(int(law_id))

    def _lemma(self, word):
        if word in {"cк", "ck"}:
            return "ск"
        if re.fullmatch(r"[а-я]{3,}", word):
            return self.morph.parse(word)[0].normal_form.replace("ё", "е")
        return word

    def match_law(self, text, start):
        node, law_id, end = self.trie, None, start
        for token in TOKEN.finditer(text, start):
            if token.group() in QUOTES:
                continue
            node = node.get(self.lemma(token.group()))
            if node is None:
                break
            if None in node:
                ids = node[None]
                law_id = next(iter(ids)) if len(ids) == 1 else None
                end = token.end()
        return law_id, end

    def detect(self, text):
        text = normalize(text)
        matches = list(REFERENCE.finditer(text))
        links, index, consumed = [], 0, 0
        while index < len(matches):
            match = matches[index]
            if match.start() < consumed:
                index += 1
                continue
            group = [match]
            index += 1
            while index < len(matches) and BRIDGE.fullmatch(text[group[-1].end():matches[index].start()]):
                group.append(matches[index])
                index += 1
            law_id, consumed = self.match_law(text, group[-1].end())
            if consumed == group[-1].end() and not UNKNOWN_LAW.match(text, consumed):
                continue
            clauses, clause, previous = [], {}, 2
            for position, match in enumerate(group):
                field = next(field for field in FIELDS if match[field])
                level = FIELDS.index(field)
                if level > previous:
                    clauses.append(clause)
                    clause = {}
                if is_nested_point(text, group, position) and "subpoint_article" not in clause:
                    # «п. 3 ч. 1 ст. 5»: пункт внутри части — это подпункт
                    clause["subpoint_article"] = clause.pop("point_article")
                values = parse_values(match["values"])
                if position + 1 < len(group) and values[0][0].isdigit() and re.search(r",\s*в$", match["values"]):
                    values.pop()
                clause.setdefault(field, []).extend(values)
                previous = level
            clauses.append(clause)
            inherited = {}
            for clause in reversed(clauses):
                level = min(FIELDS.index(field) for field in clause)
                for field in FIELDS[:level]:
                    if field in inherited:
                        clause[field] = inherited[field]
                inherited = clause
            for clause in clauses:
                values = [list(dict.fromkeys(clause.get(field, [None]))) for field in FIELDS]
                if len(links) + prod(map(len, values)) > MAX_LINKS:
                    raise ValueError("Слишком много комбинаций юридических ссылок (максимум 10000)")
                links.extend(
                    dict(law_id=law_id, **dict(zip(FIELDS, combination)))
                    for combination in product(*values)
                )
        return links
