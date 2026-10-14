import os
import re
import unittest
from itertools import product
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from main import app


def link(law_id, article=None, point=None, subpoint=None):
    return dict(law_id=law_id, article=article, point_article=point, subpoint_article=subpoint)


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get("LAW_LINKS_URL")
        cls.client = httpx.Client(base_url=url, timeout=30) if url else TestClient(app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)

    def detect(self, text):
        response = self.client.post("/detect", json={"text": text})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["links"]

    def test_basic(self):
        self.assertEqual(self.detect("пп. 1 п. 1 ст. 374 НК РФ"), [link(15, "374", "1", "1")])

    def test_inflections(self):
        for name in ("Гражданского кодекса Российской Федерации", "Гражданскому кодексу", "ГК РФ"):
            with self.subTest(name=name):
                self.assertEqual(self.detect("В пункте 1 статьи 34 " + name), [link(5, "34", "1")])

    def test_lists(self):
        self.assertEqual(
            self.detect("пп. 1, 2, 3 п. 2 ст. 3 НК РФ"),
            [link(15, "3", "2", str(i)) for i in range(1, 4)],
        )

    def test_combinations(self):
        self.assertEqual(
            self.detect("в подпунктах а, б и с пункта 3.345, 23 в статье 66 НК РФ"),
            [link(15, "66", point, subpoint) for point, subpoint in product(("3.345", "23"), ("а", "б", "с"))],
        )

    def test_ranges(self):
        # Диапазоны возвращаются одной строкой, как «43.2-6» в условии задания.
        self.assertEqual(self.detect("пп. «а»–«в» п. 1–2 ст. 43.2-4 УК РФ"), [link(13, "43.2-4", "1-2", "а-в")])
        self.assertEqual(self.detect("ст. 43.2-6 НК РФ"), [link(15, "43.2-6")])
        self.assertEqual(self.detect("ст. 1.9-2.1 НК РФ"), [link(15, "1.9-2.1")])

    def test_point_inside_part(self):
        for text in ("п. 3 ч. 1 ст. 30.7 КоАП РФ", "пункта 3 части 1 статьи 30.7 КоАП РФ"):
            with self.subTest(text=text):
                self.assertEqual(self.detect(text), [link(17, "30.7", "1", "3")])
        self.assertEqual(self.detect("п. 3, 4 ч. 1 ст. 5 УК РФ"), [link(13, "5", "1", "3"), link(13, "5", "1", "4")])
        # Через союз или запятую — это перечисление, а не вложенность.
        self.assertEqual(self.detect("п. 1 и ч. 2 ст. 5 УК РФ"), [link(13, "5", "1"), link(13, "5", "2")])

    def test_compact_writing(self):
        self.assertEqual(self.detect("пп.1п.2ст.3НК РФ"), [link(15, "3", "2", "1")])
        self.assertEqual(self.detect("ст.5НК РФ"), [link(15, "5")])
        self.assertEqual(self.detect("ч.1ст.5 УК РФ"), [link(13, "5", "1")])

    def test_subpoint_abbreviations(self):
        for abbr in ("подп.", "подпп.", "пп.", "п.п.", "пп"):
            with self.subTest(abbr=abbr):
                self.assertEqual(self.detect(f"{abbr} 1 п. 2 ст. 3 НК РФ"), [link(15, "3", "2", "1")])

    def test_title_without_quotes(self):
        self.assertEqual(self.detect("ст. 12 закона о защите прав потребителей"), [link(984, "12")])

    def test_conjunction_as_letter(self):
        self.assertEqual(self.detect('пп. "и" и "к" ст. 1 НК РФ'), [link(15, "1", None, "и"), link(15, "1", None, "к")])

    def test_separate_laws(self):
        self.assertEqual(self.detect("ст. 12 НК РФ и ст. 13 УК РФ"), [link(15, "12"), link(13, "13")])

    def test_shared_law(self):
        self.assertEqual(self.detect("п. 1 ст. 20, п. 2 ст. 21 ГК РФ"), [link(5, "20", "1"), link(5, "21", "2")])

    def test_shared_article(self):
        self.assertEqual(self.detect("пп. а п. 1 и пп. б п. 2 ст. 3 НК РФ"), [link(15, "3", "1", "а"), link(15, "3", "2", "б")])

    def test_titles_and_numbers(self):
        for name in ('Закона "О персональных данных"', 'Федерального закона №152-ФЗ от 27.07.2006', 'ФЗ № 152-ФЗ от 27.07.2006'):
            with self.subTest(name=name):
                self.assertEqual(self.detect("п. 2 ст. 14 " + name), [link(330, "14", "2")])
        self.assertEqual(self.detect('ст. 19 Федерального закона «О бухгалтерском учёте»'), [link(224, "19")])

    def test_ambiguous_number(self):
        self.assertEqual(self.detect("ст. 1 ФЗ №152-ФЗ"), [link(None, "1")])
        self.assertEqual(self.detect("ст. 1 ФЗ №152-ФЗ от 11.11.2003"), [link(367, "1")])

    def test_whitespace_and_case(self):
        self.assertEqual(self.detect("Ч.\u00a03,\nСТ.30.1\tКоАП РФ"), [link(17, "30.1", "3")])
        self.assertEqual(self.detect("ст. 90 СК РФ"), [link(8, "90")])

    def test_missing_article(self):
        self.assertEqual(self.detect("Согласно п. 10 АПК"), [link(0, point="10")])

    def test_preposition_between_levels(self):
        for article in ("ст.", "статье"):
            self.assertEqual(self.detect(f"п. 1, в {article} 2 ГК РФ"), [link(5, "2", "1")])
        self.assertEqual(self.detect("пп. а, в ст. 2 ГК РФ"), [link(5, "2", None, "а"), link(5, "2", None, "в")])

    def test_nonlegal_article(self):
        self.assertEqual(self.detect("В газете напечатана статья 5 о кошках."), [])
        self.assertEqual(self.detect("ст. 12 НК РФ. Статья 5 посвящена кошкам."), [link(15, "12")])

    def test_date_before_number(self):
        self.assertEqual(self.detect("ст. 14 Федерального закона от 27.07.2006 №152-ФЗ"), [link(330, "14")])
        self.assertEqual(self.detect("ст. 1 ФЗ от 11.11.2003 №152-ФЗ"), [link(367, "1")])

    def test_unknown_law(self):
        self.assertEqual(self.detect("пп. 1 п. 2 ст. 34 рандомного кодекса"), [link(None, "34", "2", "1")])
        self.assertEqual(self.detect("ст. 37 ФЗ №20-456"), [link(None, "37")])

    def test_empty_text(self):
        for text in ("", "Юридических ссылок здесь нет.", "В этом тексте 12 пунктов."):
            with self.subTest(text=text):
                self.assertEqual(self.detect(text), [])

    def test_readme(self):
        readme = Path(__file__).with_name("README.md").read_text(encoding="utf-8")
        text = re.search(r'test_text = """(.*?)"""', readme, re.S)[1]
        expected = [
            link(15, "374", "1", "1"), link(13, "105"), link(0, point="10"),
            link(None, "17"), link(13, "105", "5"), link(13, "158", "3"),
            link(5, "30"), link(13, "159", "4", "12"), link(13, "228", "2"),
            link(15, "275"), link(8, "90"), link(10, "70"), link(6, "213", "1"),
            link(0, "20", "а"), link(984, "16"), link(330, "14", "2"), link(224, "19"),
            link(412, "9", "р"), link(284, "16", "1"), link(362, "22", "б", "2"),
            link(332, "12"), link(249, "20", "3"), link(None, "37"),
            *[link(174, "14", "1", value) for value in ("4", "5", "6", "8")],
            link(None, "6"), link(5, "34", "1"), link(None, "34", "2", "1"),
            *[link(15, "66", point, subpoint) for point, subpoint in product(("3.345", "23"), ("а", "б", "с"))],
            link(17, "30.1", "3"), link(0, "211"),
        ]
        self.assertEqual(self.detect(text), expected)

    def test_validation(self):
        for payload in ({}, {"text": None}, {"text": 123}, {"text": "x" * 100_001}):
            with self.subTest(payload_type=type(payload.get("text"))):
                self.assertEqual(self.client.post("/detect", json=payload).status_code, 422)
        many = ", ".join(map(str, range(1, 102)))
        response = self.client.post("/detect", json={"text": f"пп. {many} п. {many} ст. 1 НК РФ"})
        self.assertEqual(response.status_code, 422)

    def test_health_and_docs(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "healthy"})
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertIn("/detect", self.client.get("/openapi.json").json()["paths"])


if __name__ == "__main__":
    unittest.main()
