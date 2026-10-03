"""Deterministic value parsers: the "stateful parsers" rules delegate to.

Rules (written by people or induced by an LLM) only locate a *string*; turning
``"ج.م35,500"``, ``"بمقدم 132 ألف"`` or ``"13 hours and 4 minutes ago"`` into
typed values is code, never a model. That keeps numbers out of the LLM's hands
and makes every replay produce identical values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from realestate.domain.enums import ListingType, PriceType
from realestate.infrastructure.extraction.text import clean_text, normalise_digits

#: Largest value the ``listing.price`` column (DECIMAL(14,2)) can hold.
_MAX_PRICE = Decimal("999999999999")
#: Below this a figure is a placeholder ("1.00", "2", "8") posted to dodge a
#: required price field, not a price; per-night/week/m² figures can be smaller.
_MIN_PRICE = Decimal(100)
_MIN_UNIT_PRICE = Decimal(5)
_UNIT_PRICE_TYPES = frozenset({PriceType.PER_NIGHT, PriceType.PER_WEEK, PriceType.PER_SQM})

_NUMBER = re.compile(r"(?<![\d.])(\d{1,3}(?:[,\s]\d{3})+|\d+)(?:\.(\d+))?")

_CURRENCIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Order matters: "جنيه استرليني" (sterling) must win over "جنيه" (EGP).
    ("GBP", re.compile(r"\bGBP\b|استرليني|إسترليني|sterling", re.IGNORECASE)),
    (
        "EGP",
        re.compile(r"ج\s*\.?\s*م|جنيه|جنية|E£|\bL\.?E\b|\bEGP\b|egyptian\s+pounds?", re.IGNORECASE),
    ),
    ("USD", re.compile(r"US\$|\$|\bUSD\b|دولار|dollars?", re.IGNORECASE)),
    ("EUR", re.compile(r"€|\bEUR\b|يورو|euros?", re.IGNORECASE)),
    ("AED", re.compile(r"\bAED\b|درهم", re.IGNORECASE)),
    ("SAR", re.compile(r"\bSAR\b|ريال", re.IGNORECASE)),
)

#: Scale words after a number. A bare ``m``/``k`` only counts when glued to the
#: digits ("1.5m"), since "650 M" in an area is metres, not millions.
_MULTIPLIERS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^(?:\s*(?:مليون|million|mn\b)|m\b)", re.IGNORECASE), 1_000_000),
    (re.compile(r"^(?:\s*(?:ألف|الف|آلاف|الاف|thousand)|k\b)", re.IGNORECASE), 1_000),
)

_PRICE_TYPES: tuple[tuple[PriceType, re.Pattern[str]], ...] = (
    (
        PriceType.ON_REQUEST,
        re.compile(
            r"call\s+for\s+price|price\s+on\s+request|on\s+request|على\s+الاتصال|السعر\s+عند\s+الاتصال",
            re.IGNORECASE,
        ),
    ),
    (
        PriceType.INSTALLMENT,
        re.compile(r"مقدم|قسط|اقساط|أقساط|تقسيط|down\s*payment|installments?", re.IGNORECASE),
    ),
    (
        PriceType.PER_NIGHT,
        re.compile(r"per\s+night|/\s*night|nightly|ليلة|يومي|per\s+day|/\s*day", re.IGNORECASE),
    ),
    (PriceType.PER_WEEK, re.compile(r"per\s+week|/\s*week|weekly|أسبوع|اسبوع", re.IGNORECASE)),
    (
        PriceType.PER_MONTH,
        re.compile(
            r"per\s+month|/\s*mo(?:nth)?|monthly|شهري|شهريا|بالشهر|في\s+الشهر", re.IGNORECASE
        ),
    ),
    (
        PriceType.PER_YEAR,
        re.compile(r"per\s+year|/\s*year|yearly|annual|سنوي|سنويا", re.IGNORECASE),
    ),
    (
        PriceType.PER_SQM,
        re.compile(r"per\s+(?:sq\.?\s*)?m|/\s*m2|/\s*sqm|للمتر|المتر", re.IGNORECASE),
    ),
)


@dataclass(frozen=True, slots=True)
class ParsedPrice:
    amount: Decimal | None
    currency: str | None
    price_type: PriceType


def parse_number(text: str) -> Decimal | None:
    """First number in ``text``, honouring thousand separators and ألف/مليون."""
    found = _numbers_with_positions(normalise_digits(text))
    return found[0][0] if found else None


def _numbers_with_positions(
    text: str, *, scale_words: bool = True
) -> list[tuple[Decimal, int, int]]:
    out: list[tuple[Decimal, int, int]] = []
    for match in _NUMBER.finditer(text):
        integer = re.sub(r"[,\s]", "", match.group(1))
        fraction = match.group(2)
        try:
            value = Decimal(f"{integer}.{fraction}" if fraction else integer)
        except InvalidOperation:
            continue
        end = match.end()
        for pattern, factor in _MULTIPLIERS if scale_words else ():
            multiplier = pattern.match(text[end:])
            if multiplier:
                value *= factor
                end += multiplier.end()
                break
        out.append((value, match.start(), end))
    return out


def detect_currency(text: str) -> tuple[str | None, int]:
    """ISO code of the first currency marker found, and its position (-1 if none)."""
    best: tuple[str | None, int] = (None, -1)
    for code, pattern in _CURRENCIES:
        match = pattern.search(text)
        if match and (best[1] == -1 or match.start() < best[1]):
            best = (code, match.start())
            if code == "GBP":  # the more specific match; do not let "جنيه" steal it
                return best
    return best


def parse_price(
    text: str | None,
    *,
    listing_type: ListingType | None = None,
    default_currency: str | None = None,
    default_rental_price_type: PriceType = PriceType.PER_MONTH,
) -> ParsedPrice:
    """Amount, currency and how to read it, from free text.

    With several numbers, the one closest to the currency marker wins (so
    ``"3b , Red Sea - 126322 GBP"`` gives 126322, not 3); without a marker, the
    largest does. A zero, missing or placeholder amount (below 100, or 5 for
    per-night/week/m² prices) is ``UNKNOWN`` rather than a real price.
    Rental amounts without explicit period text use ``default_rental_price_type``;
    existing callers retain the monthly default, reviewed archive rules may use UNKNOWN.
    """
    raw = normalise_digits(clean_text(text))
    explicit_type: PriceType | None = None
    for price_type, pattern in _PRICE_TYPES:
        if pattern.search(raw):
            explicit_type = price_type
            break

    currency, currency_at = detect_currency(raw)
    numbers = _numbers_with_positions(raw)
    amount: Decimal | None = None
    if numbers:
        if currency_at >= 0:
            amount = min(
                numbers,
                key=lambda item: min(abs(item[1] - currency_at), abs(item[2] - currency_at)),
            )[0]
        else:
            amount = max(numbers, key=lambda item: item[0])[0]
    if amount is not None and (amount <= 0 or amount > _MAX_PRICE):
        amount = None
    if amount is not None:
        floor = _MIN_UNIT_PRICE if explicit_type in _UNIT_PRICE_TYPES else _MIN_PRICE
        if amount < floor:
            amount = None

    if amount is None:
        price_type = (
            PriceType.ON_REQUEST if explicit_type is PriceType.ON_REQUEST else PriceType.UNKNOWN
        )
    elif explicit_type is not None and explicit_type is not PriceType.ON_REQUEST:
        price_type = explicit_type
    elif listing_type is ListingType.RENT:
        price_type = default_rental_price_type
    else:
        price_type = PriceType.TOTAL
    return ParsedPrice(
        amount=amount.quantize(Decimal("0.01")) if amount is not None else None,
        currency=currency or (default_currency if amount is not None else None),
        price_type=price_type,
    )


_AREA_UNITS: tuple[tuple[re.Pattern[str], Decimal], ...] = (
    (re.compile(r"sq\.?\s*ft|square\s+feet|ft2|ft²|قدم", re.IGNORECASE), Decimal("0.092903")),
    (
        re.compile(
            r"m2|m²|sq\.?\s*m|sqm|square\s+met|met(?:er|re)s?|م٢|م2|متر|م\b|\bm\b", re.IGNORECASE
        ),
        Decimal(1),
    ),
)


def parse_rental_period(text: str | None) -> PriceType | None:
    """Only an unambiguous explicit rental period; never interpret an amount."""
    raw = normalise_digits(clean_text(text))
    periods = {
        price_type
        for price_type, pattern in _PRICE_TYPES
        if price_type
        in {PriceType.PER_NIGHT, PriceType.PER_WEEK, PriceType.PER_MONTH, PriceType.PER_YEAR}
        and pattern.search(raw)
    }
    if re.search(r"\bdaily\b", raw, re.IGNORECASE):
        periods.add(PriceType.PER_NIGHT)
    return next(iter(periods)) if len(periods) == 1 else None


def parse_area(text: str | None) -> Decimal | None:
    """Area in square metres; plain numbers are taken to be m²."""
    raw = normalise_digits(clean_text(text))
    numbers = _numbers_with_positions(raw, scale_words=False)
    if not numbers:
        return None
    value, _, end = numbers[0]
    factor = Decimal(1)
    for pattern, unit_factor in _AREA_UNITS:
        if pattern.search(raw[end : end + 20]) or pattern.search(raw):
            factor = unit_factor
            break
    area = (value * factor).quantize(Decimal("0.01"))
    return area if Decimal(5) <= area <= Decimal(1_000_000) else None


_ROOM_WORDS: dict[str, int] = {
    "غرفة": 1,
    "غرفه": 1,
    "واحدة": 1,
    "واحد": 1,
    "one": 1,
    "single": 1,
    "غرفتين": 2,
    "غرفتان": 2,
    "اثنين": 2,
    "اثنان": 2,
    "two": 2,
    "ثلاث": 3,
    "ثلاثة": 3,
    "three": 3,
    "أربع": 4,
    "اربع": 4,
    "أربعة": 4,
    "four": 4,
    "خمس": 5,
    "خمسة": 5,
    "five": 5,
    "ست": 6,
    "ستة": 6,
    "six": 6,
}


def parse_int(text: str | None, *, low: int = 0, high: int = 50) -> int | None:
    """A small count (rooms, bathrooms), from digits or number words."""
    raw = normalise_digits(clean_text(text)).lower()
    match = re.search(r"\d+", raw)
    if match:
        value = int(match.group(0))
        return value if low <= value <= high else None
    for word, value in _ROOM_WORDS.items():
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", raw):
            return value
    return None


# -- dates ----------------------------------------------------------------

_MONTHS: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
    "يناير": 1,
    "فبراير": 2,
    "مارس": 3,
    "أبريل": 4,
    "ابريل": 4,
    "إبريل": 4,
    "مايو": 5,
    "يونيو": 6,
    "يونيه": 6,
    "يوليو": 7,
    "يوليه": 7,
    "أغسطس": 8,
    "اغسطس": 8,
    "سبتمبر": 9,
    "أكتوبر": 10,
    "اكتوبر": 10,
    "نوفمبر": 11,
    "ديسمبر": 12,
    "كانون الثاني": 1,
    "شباط": 2,
    "آذار": 3,
    "نيسان": 4,
    "أيار": 5,
    "حزيران": 6,
    "تموز": 7,
    "آب": 8,
    "أيلول": 9,
    "تشرين الأول": 10,
    "تشرين الثاني": 11,
    "كانون الأول": 12,
}
_MONTH_ALTERNATION = "|".join(sorted((re.escape(m) for m in _MONTHS), key=len, reverse=True))
_DAY_MONTH = re.compile(
    rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALTERNATION})\.?,?(?:\s+(\d{{4}}))?",
    re.IGNORECASE,
)
_MONTH_DAY = re.compile(
    rf"({_MONTH_ALTERNATION})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?!\d)(?:,?\s+(\d{{4}}))?",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s](\d{1,2}):(\d{2}))?")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?!\d)")
_EPOCH = re.compile(r"^\d{9,13}(?:\.\d+)?$")

#: Dual forms (يومين = two days) come first so the singular stem cannot match inside them.
_RELATIVE_UNITS: tuple[tuple[re.Pattern[str], timedelta], ...] = (
    (
        re.compile(r"(\d+)?\s*(?:دقيقتين|دقيقة|دقائق|minutes?|mins?)", re.IGNORECASE),
        timedelta(minutes=1),
    ),
    (re.compile(r"(\d+)?\s*(?:ساعتين|ساعات|ساعة|hours?|hrs?)", re.IGNORECASE), timedelta(hours=1)),
    (re.compile(r"(\d+)?\s*(?:يومين|أيام|ايام|يوم|days?)", re.IGNORECASE), timedelta(days=1)),
    (
        re.compile(r"(\d+)?\s*(?:أسبوعين|اسبوعين|أسابيع|اسابيع|أسبوع|اسبوع|weeks?)", re.IGNORECASE),
        timedelta(weeks=1),
    ),
    (
        re.compile(r"(\d+)?\s*(?:شهرين|أشهر|اشهر|شهور|شهر|months?)", re.IGNORECASE),
        timedelta(days=30),
    ),
    (
        re.compile(r"(\d+)?\s*(?:سنتين|سنوات|سنة|أعوام|عام|years?)", re.IGNORECASE),
        timedelta(days=365),
    ),
)
_RELATIVE_MARKER = re.compile(r"\bago\b|منذ|قبل", re.IGNORECASE)
_DUAL = re.compile(r"دقيقتين|ساعتين|يومين|أسبوعين|اسبوعين|شهرين|سنتين")


def _anchor_year(month: int, day: int, captured_at: datetime) -> int:
    """Year for a yearless date: the capture's, unless that lands in the future."""
    year = captured_at.year
    try:
        candidate = datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return year
    return year - 1 if candidate > captured_at + timedelta(days=1) else year


def _plausible(moment: datetime, captured_at: datetime | None) -> datetime | None:
    if moment.year < 1995:
        return None
    if captured_at is not None and moment > captured_at + timedelta(days=2):
        return None
    return moment


def parse_date(text: str | None, *, captured_at: datetime | None) -> datetime | None:
    """A listing date from absolute, yearless, relative or epoch forms.

    Relative and yearless forms are resolved against ``captured_at`` -- the
    moment the archive saw the page -- never against "now".
    """
    raw = normalise_digits(clean_text(text))
    if not raw:
        return None
    if _EPOCH.match(raw):
        value = float(raw)
        if value > 1e11:
            value /= 1000
        return _plausible(datetime.fromtimestamp(value, tz=UTC), captured_at)

    iso = _ISO_DATE.search(raw)
    if iso:
        try:
            moment = datetime(
                int(iso.group(1)),
                int(iso.group(2)),
                int(iso.group(3)),
                int(iso.group(4) or 0),
                int(iso.group(5) or 0),
                tzinfo=UTC,
            )
            return _plausible(moment, captured_at)
        except ValueError:
            pass

    for pattern, day_group, month_group in ((_DAY_MONTH, 1, 2), (_MONTH_DAY, 2, 1)):
        match = pattern.search(raw)
        if not match:
            continue
        month = _MONTHS.get(match.group(month_group).lower().rstrip("."))
        day = int(match.group(day_group))
        if month is None:
            continue
        if match.group(3):
            year = int(match.group(3))
        elif captured_at is not None:
            year = _anchor_year(month, day, captured_at)
        else:
            continue
        try:
            return _plausible(datetime(year, month, day, tzinfo=UTC), captured_at)
        except ValueError:
            continue

    numeric = _NUMERIC_DATE.search(raw)
    if numeric:
        day, month, year = (int(group) for group in numeric.groups())
        if year < 100:
            year += 2000
        if month > 12 and day <= 12:  # an M/D/Y source
            day, month = month, day
        try:
            return _plausible(datetime(year, month, day, tzinfo=UTC), captured_at)
        except ValueError:
            pass

    if captured_at is None:
        return None
    lowered = raw.lower()
    if re.search(r"\btoday\b|اليوم|النهارده", lowered):
        return captured_at
    if re.search(r"\byesterday\b|أمس|امس|إمبارح|امبارح", lowered):
        return captured_at - timedelta(days=1)
    if _RELATIVE_MARKER.search(lowered):
        offset = timedelta()
        for pattern, unit in _RELATIVE_UNITS:
            for match in pattern.finditer(lowered):
                if match.group(1):
                    count = int(match.group(1))
                elif _DUAL.search(match.group(0)):
                    count = 2
                else:
                    count = 1
                offset += unit * count
        if offset:
            return captured_at - offset
    return None
