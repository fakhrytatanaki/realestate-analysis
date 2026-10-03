"""Place names to canonical cities: a curated table, never string surgery.

Archived adverts name places in English, Arabic and scholarly transliteration
(``al-Bah̨r-al-Ah̨mar``, ``al-Qāhirah``), often glued to a category
(``Houses - Apartments for Sale - al-Iskandarīyah``). Splitting such text at a
hyphen truncates the place itself, so resolution matches whole known names
inside the normalised text instead, and an unknown name stays unknown.
"""

from __future__ import annotations

import re
import unicodedata
from functools import cache

#: Canonical name -> aliases. Governorates first, then cities and areas that
#: adverts name on their own; a longer alias wins over a shorter one.
_EG_PLACES: dict[str, tuple[str, ...]] = {
    "Cairo": ("cairo", "al qahirah", "al qahira", "el qahira", "qahirah", "القاهرة", "القاهره"),
    "Giza": ("giza", "gizeh", "al jizah", "al giza", "el giza", "jizah", "الجيزة", "الجيزه"),
    "Alexandria": (
        "alexandria",
        "al iskandariyah",
        "al iskandariya",
        "iskandariyah",
        "alex",
        "الإسكندرية",
        "الاسكندرية",
        "اسكندرية",
        "الاسكندريه",
    ),
    "Qalyubia": ("qalyubia", "al qalyubiyah", "qalyubiyah", "القليوبية"),
    "Port Said": ("port said", "bur said", "būr said", "بورسعيد", "بور سعيد"),
    "Suez": ("suez", "as suways", "al suways", "السويس"),
    "Ismailia": ("ismailia", "al ismailiyah", "ismailiyah", "الإسماعيلية", "الاسماعيلية"),
    "Damietta": ("damietta", "dumyat", "دمياط"),
    "Dakahlia": ("dakahlia", "ad daqahliyah", "daqahliyah", "الدقهلية"),
    "Sharqia": ("sharqia", "ash sharqiyah", "sharqiyah", "الشرقية"),
    "Gharbia": ("gharbia", "al gharbiyah", "gharbiyah", "الغربية"),
    "Monufia": ("monufia", "al minufiyah", "minufiyah", "المنوفية"),
    "Kafr El Sheikh": ("kafr el sheikh", "kafr ash shaykh", "كفر الشيخ"),
    "Beheira": ("beheira", "al buhayrah", "buhayrah", "البحيرة"),
    "Faiyum": ("faiyum", "fayoum", "al fayyum", "fayyum", "الفيوم"),
    "Beni Suef": ("beni suef", "bani suwayf", "بني سويف"),
    "Minya": ("minya", "al minya", "المنيا"),
    "Asyut": ("asyut", "assiut", "assyut", "اسيوط", "أسيوط"),
    "Sohag": ("sohag", "suhaj", "سوهاج"),
    "Qena": ("qena", "qina", "قنا"),
    "Luxor": ("luxor", "al uqsur", "الأقصر", "الاقصر"),
    "Aswan": ("aswan", "أسوان", "اسوان"),
    "Red Sea": ("red sea", "al bahr al ahmar", "bahr al ahmar", "البحر الأحمر", "البحر الاحمر"),
    "New Valley": ("new valley", "al wadi al jadid", "al wadi al gadid", "الوادي الجديد"),
    "Matrouh": ("matrouh", "marsa matruh", "matruh", "مطروح", "مرسى مطروح"),
    "North Sinai": ("north sinai", "shamal sina", "شمال سيناء"),
    "South Sinai": ("south sinai", "janub sina", "جنوب سيناء"),
    "Hurghada": ("hurghada", "hurgada", "al ghardaqah", "al gardaqah", "الغردقة"),
    "Sharm El Sheikh": (
        "sharm el sheikh",
        "sharm al shaykh",
        "sarm as sayh",
        "sharm",
        "شرم الشيخ",
    ),
    "6th of October": (
        "6th of october",
        "6 october",
        "sixth of october",
        "madinat sittah uktubar",
        "6 أكتوبر",
    ),
    "Tanta": ("tanta", "طنطا"),
    "Mansoura": ("mansoura", "al mansurah", "mansurah", "المنصورة"),
    "Zagazig": ("zagazig", "az zaqaziq", "zaqaziq", "الزقازيق"),
    "El Mahalla El Kubra": ("mahalla", "al mahallah al kubra", "المحلة الكبرى"),
    "Shubra El Kheima": ("shubra el kheima", "subra al haymah", "shubra al khaymah", "شبرا الخيمة"),
    "Kharga": ("kharga", "al harijah", "al kharijah", "الخارجة"),
    "Obour": ("obour", "al ubur", "el obour", "العبور"),
    "New Cairo": ("new cairo", "el tagamoa", "tagamoa", "القاهرة الجديدة", "التجمع"),
    "Sheikh Zayed": ("sheikh zayed", "el sheikh zayed", "الشيخ زايد"),
    "Nasr City": ("nasr city", "مدينة نصر"),
    "Maadi": ("maadi", "el maadi", "المعادي"),
    "Heliopolis": ("heliopolis", "masr el gedida", "مصر الجديدة"),
    "Rehab City": ("rehab city", "al rehab", "الرحاب"),
    "Madinaty": ("madinaty", "مدينتي"),
    "Ain Sokhna": ("ain sokhna", "ain el sokhna", "العين السخنة"),
    "North Coast": ("north coast", "sahel", "الساحل الشمالي"),
    "Marsa Alam": ("marsa alam", "مرسى علم"),
    "Dahab": ("dahab", "دهب"),
    "El Gouna": ("el gouna", "gouna", "الجونة"),
}

#: Governorates are the coarse level: a text naming a city in one resolves to the city.
_EG_GOVERNORATES = frozenset(list(_EG_PLACES)[:27])

#: Names that are a country, not a city: "Egypt" as a city is no information.
_COUNTRY_NAMES: dict[str, tuple[str, ...]] = {
    "EG": ("egypt", "misr", "مصر", "jumhuriyat misr al arabiyah"),
}

#: Places abroad that adverts on an Egyptian portal sometimes sell.
_FOREIGN_SIGNALS: dict[str, tuple[str, ...]] = {
    "EG": (
        "cyprus",
        "larnaca",
        "limassol",
        "paphos",
        "dubai",
        "london",
        "spain",
        "turkey",
        "istanbul",
        "bulgaria",
        "قبرص",
        "دبي",
        "لندن",
    ),
}

_PLACES: dict[str, dict[str, tuple[str, ...]]] = {"EG": _EG_PLACES}
_COARSE: dict[str, frozenset[str]] = {"EG": _EG_GOVERNORATES}
#: Hamza-carrying alefs, taa marbuta and alef maqsura are spelt either way.
_ARABIC_FOLDS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"})  # noqa: RUF001


def normalise_place(text: str) -> str:
    """Lower-case, strip diacritics, fold Arabic letter variants, collapse separators."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    folded = stripped.translate(_ARABIC_FOLDS).lower()
    return " ".join(re.split(r"[\s\-_,./|()'\u2019`]+", folded)).strip()


@cache
def _aliases(country_code: str) -> tuple[tuple[str, str], ...]:
    """``(normalised alias, canonical)`` pairs: cities before governorates, longest first."""
    coarse = _COARSE.get(country_code, frozenset())
    pairs = {
        (normalise_place(alias), canonical)
        for canonical, aliases in _PLACES.get(country_code, {}).items()
        for alias in (canonical, *aliases)
    }
    return tuple(sorted(pairs, key=lambda pair: (pair[1] in coarse, -len(pair[0]), pair[0])))


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and f" {needle} " in f" {haystack} "


def resolve_city(text: str | None, country_code: str | None) -> str | None:
    """The canonical city or governorate a place text names, or ``None``."""
    if not text or not country_code:
        return None
    normalised = normalise_place(text)
    for alias, canonical in _aliases(country_code.upper()):
        if _contains(normalised, alias):
            return canonical
    return None


def is_country_name(text: str | None, country_code: str | None) -> bool:
    if not text or not country_code:
        return False
    normalised = normalise_place(text)
    return any(
        normalised == normalise_place(name) for name in _COUNTRY_NAMES.get(country_code.upper(), ())
    )


def foreign_signal(text: str | None, country_code: str | None) -> str | None:
    """A place abroad named in ``text`` (a hint for review, not a verdict)."""
    if not text or not country_code:
        return None
    normalised = normalise_place(text)
    for name in _FOREIGN_SIGNALS.get(country_code.upper(), ()):
        if _contains(normalised, normalise_place(name)):
            return name
    return None
