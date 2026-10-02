"""Deterministic English/Arabic intent routing for academic questions (Phase I1).

The router only classifies a question into one closed intent. It grants no
authority: routed requests run through the same registry, permission policy,
argument validation, and provenance as model-chosen tool calls, and anything
unclear, mixed, or write-bearing stays ``UNROUTED`` for the existing model loop.
Normalization is for matching only; the original question is never rewritten.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from aaos.web.contracts import CourseSummary

from .course_names import course_code_token


class RoutedIntent(StrEnum):
    DOCUMENT_QUESTION = "DOCUMENT_QUESTION"
    DOCUMENT_SUMMARY = "DOCUMENT_SUMMARY"
    COURSE_SUMMARY = "COURSE_SUMMARY"
    ANNOUNCEMENTS = "ANNOUNCEMENTS"
    MOODLE_CHANGES = "MOODLE_CHANGES"
    COURSE_FACTS = "COURSE_FACTS"
    ASSIGNMENT_FACTS = "ASSIGNMENT_FACTS"
    UNROUTED = "UNROUTED"


_ARABIC_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_ARABIC_LETTER_FOLDS = str.maketrans(
    {
        "أ": "ا",  # alef with hamza above
        "إ": "ا",  # alef with hamza below
        "آ": "ا",  # alef with madda
        "ٱ": "ا",  # alef wasla
        "ى": "ي",  # alef maqsura -> yaa
        "ة": "ه",  # taa marbuta -> haa
        "ؤ": "و",  # waw with hamza
        "ئ": "ي",  # yaa with hamza
        "ـ": None,  # tatweel
        "؟": "?",  # Arabic question mark
        "،": ",",  # Arabic comma
    }
)


def normalize_arabic(text: str) -> str:
    """Fold spelling variants for pattern matching only."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _ARABIC_DIACRITICS.sub("", folded).translate(_ARABIC_LETTER_FOLDS)
    return " ".join(folded.split())


def _any(patterns: Iterable[str]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{pattern})" for pattern in patterns))


# Patterns run on normalize_arabic() output: lowercase English, folded Arabic.
_WRITE_EN = _any(
    (
        r"\b(?:create|add|make|set up)\b.{0,40}\b(?:tasks?|reminders?|events?|calendar)\b",
        r"\b(?:add|put|schedule|book)\b.{0,60}\bcalendar\b",
        r"\b(?:update|change|edit|move)\b.{0,40}\b(?:tasks?|calendar|events?|reminders?)\b",
        r"\b(?:mark|complete|finish)\b.{0,40}\b(?:tasks?|done|complete|completed)\b",
        r"^\s*(?:please\s+)?(?:submit|resubmit|upload|hand in|turn in)\b",
        r"\b(?:submit|resubmit|upload)\s+(?:my|the|this|our)\b",
        r"\b(?:delete|remove|erase)\b",
    )
)
_WRITE_AR = _any(
    (
        r"(?:انشي|اضف|ضيف|سجل|اعمل)\S*.{0,40}(?:مهم|تذكير|حدث|تقويم)",
        r"(?:اضف|ضيف|حط|احجز|جدول)\S*.{0,60}تقويم",
        r"(?:علم|اجعل|خلي)\S*.{0,40}(?:منجز|مكتمل)",
        r"(?:^|\s)(?:سلم|ارفع)(?:\s|$)",
        r"(?:^|\s)(?:احذف|امسح)",
    )
)
_TASK_OR_CALENDAR = _any(
    (
        r"\b(?:study tasks?|my tasks?|tasks?)\b",
        r"\bcalendar\b",
        r"مهمه|مهام|مهمتي|تقويم",
    )
)
_SUMMARY = _any(
    (
        r"\bsummar\w*\b",
        r"\boverview\b",
        r"\btl;?dr\b",
        r"\brecap\b",
        r"\bgist\b",
        r"\bmain points\b",
        r"\bkey (?:points|takeaways)\b",
        r"\bwhat is\b.{0,60}\babout\b",
        r"لخص|تلخيص|خلاصه|نظره عامه|اهم النقاط|عن ماذا",
    )
)
_DOCUMENT = _any(
    (
        r"\b(?:syllabus|notes?|lectures?|documents?|files?|pdf|slides?|chapters?|textbook|handouts?)\b",
        r"\bcourse materials?\b|\bmaterials?\b",
        r"منهج|الخطه الدراسيه|ملاحظات|محاضر|ملف|مستند|وثيقه|شرائح",
        r"مواد (?:ال)?مساق|مواد (?:ال)?ماده",
    )
)
# Course details that only the syllabus states. They count as a document
# reference; a bare "grade" stays out because "my grade" is a personal record.
_COURSE_DETAILS = _any(
    (
        r"\b(?:instructors?|lecturers?|professors?|teachers?|tutors?)\b",
        r"\bwho (?:teaches|is teaching)\b",
        r"\boffice hours?\b",
        r"\bgrad(?:e|ing)\b.{0,40}\b(?:calculat\w*|computed|breakdown|distribution|weights?|scheme)\b",
        r"\bgrading\b",
        r"\b(?:worth|weight\w*|percentage)\b.{0,40}\b(?:exams?|midterms?|finals?|quiz\w*|projects?|grade)\b",
        r"\b(?:exams?|midterms?|finals?|quiz\w*|projects?)\b.{0,40}\b(?:worth|weight\w*|percentage)\b",
        r"\bpre-?requisites?\b",
        r"\bmake-?up (?:exams?|tests?|quiz\w*)\b",
        r"\battendance\b|\babsences?\b",
        r"\b(?:exam|course|grading|late) polic(?:y|ies)\b",
        r"\blearning outcomes?\b",
        r"مدرس|دكتور|استاذ",
        r"(?:من|مين) (?:\S+ )?(?:ي|ب)درس",
        r"ساعات (?:ال)?مكتب",
        r"توزيع (?:ال)?(?:علامات|درجات)",
        r"(?:حساب|احتساب) (?:ال)?(?:علامه|درجه|علامات|درجات)",
        r"(?:وزن|نسبه) (?:ال)?(?:امتحان|اختبار|مشروع|علامه)",
        r"متطلب(?:ات)? (?:ال)?سابق",
        r"(?:امتحان|اختبار)\S* (?:ال)?(?:تعويضي|تكميلي)",
        r"(?:^|\s)(?:ال)?(?:حضور|غياب)",
        r"(?:نتاجات|مخرجات) (?:ال)?تعلم",
    )
)
_ANNOUNCEMENTS = _any(
    (
        r"\b(?:announcements?|announce|announced|notices?|news)\b",
        r"اعلان|اعلن|تبليغ|اخبار",
    )
)
_CHANGES = _any(
    (
        r"\bwhat (?:has )?changed\b|\bchanges?\b|\bchanged\b",
        r"\banything new\b|\bwhat'?s new\b",
        r"\bupdated\b|\bupdates\b|\bmodified\b|\bremoved\b|\buploaded\b",
        r"\bsince the last sync\b",
        r"تغير|تغيير|تحديث|تعدل|انحذف|مستجدات|تم رفع|انرفع",
        r"(?:شي|شيء|اشي) جديد",
        r"(?:^|\s)(?:ال)?تحدث(?:\s|$)",
    )
)
_COURSE_FACTS = _any(
    (
        r"\b(?:my|which|what|registered|all my) (?:registered )?(?:courses|classes|subjects)\b",
        r"\bclasses am i\b|\benrolled\b|\bregistered in\b|\bhow many courses\b|\bsubjects i'?m\b",
        r"موادي|المواد (?:المسجل|التي|اللي)|المساقات (?:التي|اللي|المسجل)",
        r"كم ماده|شو المواد|ايش المساقات",
    )
)
_ASSIGNMENTS = _any(
    (
        r"\b(?:assignments?|homework|deadlines?|due)\b",
        r"واجب|تكليف|تسليم",
    )
)
_NEXT_SEVEN_DAYS = _any(
    (
        r"\bthis week\b|\bnext (?:7|seven) days\b|\bdue soon\b",
        r"هذا الاسبوع|قريب",
    )
)


def has_arabic_write_intent(question: str) -> bool:
    """Detect Arabic write requests (tasks, calendar, submit, delete)."""
    return _WRITE_AR.search(normalize_arabic(question)) is not None


def has_bilingual_write_intent(question: str) -> bool:
    """Detect English or Arabic write requests (tasks, calendar, submit, delete)."""
    text = normalize_arabic(question)
    return _WRITE_EN.search(text) is not None or _WRITE_AR.search(text) is not None


_TOPIC = re.compile(
    r"(?:\b(?:about|regarding|concerning)\b|(?:^|\s)(?:عن|بخصوص|حول)(?=\s))\s+(?P<topic>.+)$",
    re.IGNORECASE,
)


def extract_topic(question: str) -> str | None:
    """Return the phrase after "about"/"regarding" (or عن/بخصوص/حول), if any."""
    match = _TOPIC.search(" ".join(question.split()))
    if match is None:
        return None
    topic = match.group("topic").strip(" ?؟.!،,")
    return topic[:120] or None


def asks_next_seven_days(question: str) -> bool:
    return _NEXT_SEVEN_DAYS.search(normalize_arabic(question)) is not None


# Explicit wording for past (archived) courses; see the archived-courses spec.
_PAST_COURSES = _any(
    (
        r"\b(?:last|previous|past|prior)\s+(?:semester|term|courses?|classes|subjects)\b",
        r"\b(?:my\s+)?(?:previous|past|prior|old|earlier)\s+(?:courses?|classes|subjects)\b",
        r"\bmy\s+previous\b",
        r"(?:الفصل|الترم|السمستر)\s+(?:الماضي|السابق|الفائت)",
        r"(?:المواد|المساقات|موادي|مساقاتي)\s+(?:السابقه|الماضيه|القديمه)",
    )
)


_CURRENT_COURSES = _any(
    (
        r"\b(?:this|current) (?:semester|term)\b",
        r"\b(?:current|this semester'?s?) (?:courses?|classes|subjects)\b",
        r"(?:هالفصل|هذا الفصل|الفصل الحالي|الترم الحالي|المواد الحاليه|موادي الحاليه)",
    )
)


def asks_about_current(question: str) -> bool:
    """Explicit current-term wording, which ends a conversation's past-course focus."""
    return _CURRENT_COURSES.search(normalize_arabic(question)) is not None


def asks_about_past(question: str) -> bool:
    """Whether a question explicitly asks about past (archived) courses."""
    return _PAST_COURSES.search(normalize_arabic(question)) is not None


# A question asking what something is or means. It has no other home, so
# it searches course files; personal records ("what is my grade") stay out.
_CONCEPT = _any(
    (
        r"^(?:so |and |ok,? )?(?:what(?:'s| is| are| does)|tell me (?:what|about)|explain|define|describe)\b",
        r"^(?:ما (?:هو|هي|هم)|شو (?:هو|هي|يعني)|ماذا يعني|ما معنى|اشرح|عرف)",
    )
)
_PERSONAL = _any((r"\b(?:my|mine|me)\b(?! what)", r"(?:علامتي|درجتي|معدلي)"))


def route_intent(question: str) -> RoutedIntent:
    """Classify a question into one closed intent, or UNROUTED.

    Precedence: write/task/calendar requests are never routed; announcements
    or changes combined with each other or with a summary are ambiguous;
    changes outrank entity words; announcements outrank document and
    assignment words; a summary needs a document reference to be a document
    summary; a document reference outranks assignment and course facts.
    """
    text = normalize_arabic(question)
    if not text or has_bilingual_write_intent(text) or _TASK_OR_CALENDAR.search(text):
        return RoutedIntent.UNROUTED

    summary = _SUMMARY.search(text) is not None
    document = _DOCUMENT.search(text) is not None or _COURSE_DETAILS.search(text) is not None
    announcements = _ANNOUNCEMENTS.search(text) is not None
    changes = _CHANGES.search(text) is not None
    course_facts = _COURSE_FACTS.search(text) is not None
    assignments = _ASSIGNMENTS.search(text) is not None

    if (changes and (announcements or summary)) or (announcements and summary):
        return RoutedIntent.UNROUTED
    if changes:
        return RoutedIntent.MOODLE_CHANGES
    if announcements:
        return RoutedIntent.ANNOUNCEMENTS
    if summary:
        if assignments and not document:
            return RoutedIntent.UNROUTED
        return RoutedIntent.DOCUMENT_SUMMARY if document else RoutedIntent.COURSE_SUMMARY
    if document:
        return RoutedIntent.DOCUMENT_QUESTION
    if assignments:
        return RoutedIntent.ASSIGNMENT_FACTS
    if course_facts:
        return RoutedIntent.COURSE_FACTS
    if _CONCEPT.search(text) and not _PERSONAL.search(_PAST_COURSES.sub("", text)):
        return RoutedIntent.DOCUMENT_QUESTION
    return RoutedIntent.UNROUTED


_TRAILING_PARENTHETICALS = re.compile(r"(?:\s*\([^()]*\))+\s*$")
_CODE_CORE = re.compile(r"[a-z]{2,}[0-9]{2,}")


@dataclass(frozen=True, slots=True)
class CourseMention:
    """A course scope found in a question: a resolver-ready name, or ambiguity."""

    course_name: str | None
    ambiguous: bool = False


def _course_aliases(course: CourseSummary) -> tuple[str, ...]:
    title = normalize_arabic(course.title)
    aliases = {title}
    code = course_code_token(course.title)
    if code is not None:
        aliases.add(code)
        # Students often omit a code's trailing qualifier ("HSS100" for
        # "HSS100O.T", "AI495" for "AI495C"); courses sharing a core stay
        # separate groups, so a shared core is reported as ambiguous.
        core = _CODE_CORE.match(code)
        if core is not None:
            aliases.add(core.group(0))
        descriptive = title.split("-", 1)[1] if "-" in title else ""
        descriptive = _TRAILING_PARENTHETICALS.sub("", descriptive).strip()
        if len(descriptive) >= 6:
            aliases.add(descriptive)
    if course.code:
        aliases.add(normalize_arabic(course.code))
    return tuple(alias for alias in aliases if alias)


def _mentions(text: str, alias: str) -> bool:
    # A code may be written with a space or hyphen before its digits ("AI 495C").
    pattern = re.sub(r"(?<=[a-z])(?=\d)", r"[\\s-]?", re.escape(alias), count=1)
    return re.search(rf"(?<!\w){pattern}(?!\w)", text) is not None


def find_course_mention(question: str, courses: Iterable[CourseSummary]) -> CourseMention:
    """Find which course the question names, using trusted course records only.

    Courses sharing a code (Moodle section entries) count as one mention and
    are passed on by code so the trusted resolver applies its own sibling and
    ambiguity rules. Several different courses are reported as ambiguous.
    """
    text = normalize_arabic(question)
    groups: dict[str, str] = {}
    for course in courses:
        if any(_mentions(text, alias) for alias in _course_aliases(course)):
            code = course_code_token(course.title)
            key = code if code is not None else f"id:{course.id}"
            groups[key] = code if code is not None else course.title
    if not groups:
        return CourseMention(None)
    if len(groups) > 1:
        return CourseMention(None, ambiguous=True)
    return CourseMention(next(iter(groups.values())))
