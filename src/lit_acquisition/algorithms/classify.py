"""Keyword-based literature type classification (pure).

Classifies articles into:
- case_report: Case reports, case series, clinical observations
- sequencing: Sequencing studies (NGS, WGS, WES, gene panels)
- functional: Functional studies (in vitro, in vivo, assays, mechanisms)

Negative patterns (reviews, theses, editorials) are checked first and cause an
immediate return of None so they are never misclassified.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..enums import LiteratureType
from ..models import OnlineAcquisitionItem

# --- Negative patterns: exclude reviews, theses, editorials, etc. ---

_REVIEW_PATTERNS = [
    re.compile(r"\bsystematic\s+review\b", re.IGNORECASE),
    re.compile(r"\bmeta[\s-]?analysis\b", re.IGNORECASE),
    re.compile(r"\bnarrative\s+review\b", re.IGNORECASE),
    re.compile(r"\bscoping\s+review\b", re.IGNORECASE),
    re.compile(r"\bcomprehensive\s+review\b", re.IGNORECASE),
    re.compile(r"\bcritical\s+review\b", re.IGNORECASE),
    re.compile(r"\bmini[\s-]review\b", re.IGNORECASE),
    re.compile(r"\bliterature\s+review\b", re.IGNORECASE),
    re.compile(r"\bstate[\s-]of[\s-]the[\s-]art\b", re.IGNORECASE),
    re.compile(r"\boverview\s+of\b", re.IGNORECASE),
    re.compile(r"\ban?\s+updated\s+review\b", re.IGNORECASE),
    re.compile(r"\breview\s+(?:article|paper)\b", re.IGNORECASE),
    re.compile(r"系统综述|系统评价|荟萃分析|meta分析|文献综述|文献回顾|综述"),
    re.compile(r"総説|レビュー|メタアナリシス|文献的考察"),
    re.compile(r"문헌고찰|체계적\s*문헌고찰|메타분석|종설"),
    re.compile(r"revisi[oó]n\s+sistem[aá]tica|meta[\s-]an[aá]lisis|art[ií]culo\s+de\s+revisi[oó]n", re.IGNORECASE),
    re.compile(r"revis[aã]o\s+sistem[aá]tica|meta[\s-]an[aá]lise", re.IGNORECASE),
    re.compile(r"revue\s+syst[eé]matique|m[eé]ta[\s-]analyse|revue\s+de\s+la\s+litt[eé]rature", re.IGNORECASE),
    re.compile(r"revisione\s+sistematica|revisione\s+della\s+letteratura", re.IGNORECASE),
    re.compile(r"systematische\s+[üÜ]bersicht|narrative\s+[üÜ]bersicht|metaanalyse", re.IGNORECASE),
    re.compile(r"систематический\s+обзор|обзор\s+литературы|нарративный\s+обзор", re.IGNORECASE),
    re.compile(r"sistemli\s+derleme|derleme\s+makalesi|literat[uü]r\s+derlemesi", re.IGNORECASE),
]

_THESIS_PATTERNS = [
    re.compile(r"\b(?:phd|doctoral|master|bachelor)\s+(?:thesis|dissertation)\b", re.IGNORECASE),
    re.compile(r"\b(?:inaugural|diploma)\s+(?:dissertation|thesis|arbeit)\b", re.IGNORECASE),
    re.compile(r"\bdissertation\s+(?:zur|zur\s+Erlangung)\b", re.IGNORECASE),
    re.compile(r"\btrabajo\s+(?:fin(?:al)?\s+de\s+grado|de\s+fin\s+de)\b", re.IGNORECASE),
    re.compile(r"\b(?:tfg|tfm)\b", re.IGNORECASE),
    re.compile(r"\bmemoria\s+(?:de|para)\b", re.IGNORECASE),
    re.compile(r"\bprograma\s+de\s+doctorado\b", re.IGNORECASE),
    re.compile(r"\buniversidad(?:es)?\s+(?:de|aut[oó]noma|federal)\b", re.IGNORECASE),
    re.compile(r"\btesis\s+(?:de|doctoral|para)\b", re.IGNORECASE),
    re.compile(r"\btrabalho\s+de\s+conclus[aã]o\b", re.IGNORECASE),
    re.compile(r"\buniversidade\s+federal\b", re.IGNORECASE),
    re.compile(r"\btesi\s+di\s+(?:dottorato|laurea)\b", re.IGNORECASE),
    re.compile(r"\b(?:t[hè]se|travail)\s+(?:de\s+)?(?:doctorat|sciences|master|bachelor)\b", re.IGNORECASE),
    re.compile(r"\buniversit[eé]\s+(?:du|de\s+l|alger)\b", re.IGNORECASE),
    re.compile(r"博士论文|硕士论文|毕业论文|学位论文|毕业设计|学位授予"),
    re.compile(r"博士論文|修士論文|卒業論文|学位論文"),
    re.compile(r"졸업논문|석사논문|박사논문|학위논문"),
    re.compile(r"диссертация|кандидатская|докторская|магистерская|выпускная\s+квалификационная"),
    re.compile(r"mezuniyet\s+tezi|y[uü]ksek\s+lisans\s+tezi|doktora\s+tezi"),
]

_EDITORIAL_PATTERNS = [
    re.compile(r"\beditorial\b", re.IGNORECASE),
    re.compile(r"\bletter\s+to\s+(?:the\s+)?editor\b", re.IGNORECASE),
    re.compile(r"\bcommentary\b", re.IGNORECASE),
    re.compile(r"\berratum\b", re.IGNORECASE),
    re.compile(r"\bcorrigendum\b", re.IGNORECASE),
    re.compile(r"\bretraction\b", re.IGNORECASE),
    re.compile(r"\bauthor\s+response\b", re.IGNORECASE),
    re.compile(r"\bconference\s+(?:abstract|proceedings)\b", re.IGNORECASE),
    re.compile(r"\bposter\s+presentation\b", re.IGNORECASE),
    re.compile(r"\bpractice\s+guideline\b", re.IGNORECASE),
    re.compile(r"\bconsensus\s+(?:statement|report)\b", re.IGNORECASE),
    re.compile(r"\bposition\s+paper\b", re.IGNORECASE),
]

# --- Keyword patterns (case-insensitive) ---

_CASE_REPORT_PATTERNS = [
    re.compile(r"\bcase\s+report\b", re.IGNORECASE),
    re.compile(r"\bcase\s+series\b", re.IGNORECASE),
    re.compile(r"\bcase\s+stud(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\bclinical\s+case\b", re.IGNORECASE),
    re.compile(r"\bpatient\s+report\b", re.IGNORECASE),
    re.compile(r"\bcase\s+presentation\b", re.IGNORECASE),
    re.compile(r"\bwe\s+(?:report|present|describe)\s+(?:a|the|two|three|four|five)\b", re.IGNORECASE),
    re.compile(r"\bhere\s+we\s+(?:report|present|describe)\b", re.IGNORECASE),
    re.compile(r"\bour\s+(?:patient|case)\b", re.IGNORECASE),
    re.compile(r"病例报告|病例分析|病例研究|个案报道|临床病例|病例系列|病例"),
    re.compile(r"症例報告|症例研究|臨床症例|ケースレポート|症例"),
    re.compile(r"증례\s*보고|증례\s*연구|임상\s*증례|케이스\s*리포트|증례"),
    re.compile(r"caso\s+cl[ií]nico|reporte\s+de\s+caso|serie\s+de\s+casos|estudio\s+de\s+caso", re.IGNORECASE),
    re.compile(r"relato\s+de\s+caso|s[eé]rie\s+de\s+casos|caso\s+cl[ií]nico|estudo\s+de\s+caso", re.IGNORECASE),
    re.compile(r"случай\s+(?:из\s+)?практики|описание\s+случая|клинический\s+случай|серия\s+случаев|казуистика"),
    re.compile(r"olgu\s+sunumu|olgu\s+bildirisi|vaka\s+sunumu|vaka\s+raporu", re.IGNORECASE),
    re.compile(r"cas\s+clinique|observation\s+clinique", re.IGNORECASE),
    re.compile(r"caso\s+clinico|case\s+report", re.IGNORECASE),
    re.compile(r"fallbericht|kasuistik", re.IGNORECASE),
    re.compile(r"تقرير\s+حالة|حالة\s+سريرية"),
]

_CASE_REPORT_JOURNAL_PATTERNS = [
    re.compile(r"\bJ(?:ournal\s+)?Med\s+Case\s+Rep", re.IGNORECASE),
    re.compile(r"\bAm\s+J\s+Case\s+Rep", re.IGNORECASE),
    re.compile(r"\bBMJ\s+Case\s+Rep", re.IGNORECASE),
    re.compile(r"\bCase\s+Rep\b", re.IGNORECASE),
    re.compile(r"\bMedicine\b.*\bcase\b", re.IGNORECASE),
]

_SEQUENCING_PATTERNS = [
    re.compile(r"\b(?:next[\s-]?generation|NGS)\s+sequencing\b", re.IGNORECASE),
    re.compile(r"\bwhole[\s-]?(?:genome|exome)\s+sequencing\b", re.IGNORECASE),
    re.compile(r"\b(?:WGS|WES|WGS/WES)\b"),
    re.compile(r"\btargeted\s+sequencing\b", re.IGNORECASE),
    re.compile(r"\bgene\s+panel\s+(?:sequencing|test|analysis)\b", re.IGNORECASE),
    re.compile(r"\bsequencing\s+(?:analysis|study|data|results)\b", re.IGNORECASE),
    re.compile(r"\b(?:DNA|RNA|genome|genomic|exomic)\s+sequencing\b", re.IGNORECASE),
    re.compile(r"\bmassively\s+parallel\s+sequencing\b", re.IGNORECASE),
    re.compile(r"\bSanger\s+sequencing\b", re.IGNORECASE),
    re.compile(r"\b(?:amplicon|capture|panel)\s+sequencing\b", re.IGNORECASE),
    re.compile(
        r"\bvariant\s+(?:detection|calling|identification)\s+(?:by|via|using|through)\s+sequencing\b", re.IGNORECASE
    ),
    re.compile(r"\bsequencing[\s-](?:based|method|approach|platform)\b", re.IGNORECASE),
    re.compile(r"\bNGS[\s-](?:based|panel|analysis)\b", re.IGNORECASE),
    re.compile(r"\b(?:Ion\s+Torrent|Illumina|MiSeq|NextSeq|NovaSeq|PacBio|Oxford\s+Nanopore)\b", re.IGNORECASE),
    re.compile(
        r"基因测序|基因组测序|外显子测序|靶向测序|二代测序|高通量测序|全基因组测序|全外显子测序|基因检测|基因panel"
    ),
    re.compile(
        r"遺伝子シークエンス|ゲノムシーケンシング|エクソームシーケンシング|ターゲットシーケンシング|次世代シーケンス|遺伝子検査"
    ),
    re.compile(r"유전자\s*시퀀싱|게놈\s*시퀀싱|엑솜\s*시퀀싱|표적\s*시퀀싱|차세대\s*시퀀싱|유전자\s*검사"),
    re.compile(
        r"secuenciaci[oó]n\s+gen[oó]mica|secuenciaci[oó]n\s+de\s+pr[oó]xima\s+generaci[oó]n|secuenciaci[oó]n\s+del\s+exoma|panel\s+de\s+genes",
        re.IGNORECASE,
    ),
    re.compile(
        r"sequenciamento\s+gen[oô]mico|sequenciamento\s+de\s+pr[oó]xima\s+gera[cç][aã]o|sequenciamento\s+do\s+exoma|painel\s+de\s+genes",
        re.IGNORECASE,
    ),
    re.compile(
        r"секвенирование|секвенирования|геномное\s+секвенирование|экзомное\s+секвенирование|целевое\s+секвенирование|панель\s+генов|НГС|нового\s+поколения"
    ),
]

_FUNCTIONAL_PATTERNS = [
    re.compile(r"\bin\s+vitro\b", re.IGNORECASE),
    re.compile(r"\bin\s+vivo\b", re.IGNORECASE),
    re.compile(r"\b(?:knockdown|knockdown|knock[\s-]?out|KO|KD)\b", re.IGNORECASE),
    re.compile(r"\boverexpression\b", re.IGNORECASE),
    re.compile(r"\bcell\s+lines?\b", re.IGNORECASE),
    re.compile(r"\b(?:transfected|transfection|transduced)\b", re.IGNORECASE),
    re.compile(r"\bfunctional\s+(?:assay|study|analysis|characterization|validation)\b", re.IGNORECASE),
    re.compile(r"\b(?:luciferase|reporter)\s+assay\b", re.IGNORECASE),
    re.compile(r"\b(?:Western\s+blot|immunoblot|immunoprecipitation|co[\s-]?IP)\b", re.IGNORECASE),
    re.compile(r"\b(?:RT[\s-]?q?PCR|quantitative\s+PCR|real[\s-]?time\s+PCR)\b", re.IGNORECASE),
    re.compile(r"\b(?:apoptosis|proliferation|migration|invasion|viability)\s+(?:assay|test|study)\b", re.IGNORECASE),
    re.compile(r"\bmouse\s+(?:model|xenograft|transgenic)\b", re.IGNORECASE),
    re.compile(r"\btumor\s+(?:growth|formation|suppression)\b", re.IGNORECASE),
    re.compile(r"\bpathogenic(?:ity)?\s+(?:mechanism|study|analysis)\b", re.IGNORECASE),
    re.compile(r"\bprotein\s+(?:function|expression|stability|interaction)\b", re.IGNORECASE),
    re.compile(r"\bmechanis(?:m|tic)\s+stud(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\b(?:CRISPR|Cas9|gene\s+editing)\b", re.IGNORECASE),
    re.compile(r"\b(?:plasmid|vector|construct)\b.*\b(?:express|transfect)\b", re.IGNORECASE),
    re.compile(
        r"体外实验|体内实验|功能研究|功能分析|基因敲除|基因敲低|过表达|细胞系|功能验证|机制研究|蛋白表达|增殖|凋亡|迁移|侵袭"
    ),
    re.compile(
        r"in\s+vitro|in\s+vivo|機能解析|機能研究|ノックダウン|ノックアウト|過剰発現|細胞株|メカニズム研究|タンパク質発現"
    ),
    re.compile(
        r"기능\s*연구|기능\s*분석|녹다운|녹아웃|과발현|세포주|메커니즘\s*연구|단백질\s*발현|apoptosis|proliferation"
    ),
    re.compile(
        r"estudio\s+funcional|an[aá]lisis\s+funcional|in\s+vitro|in\s+vivo|silenciamiento|sobreexpresi[oó]n|l[ií]neas\s+celulares",
        re.IGNORECASE,
    ),
    re.compile(
        r"estudo\s+funcional|an[aá]lise\s+funcional|in\s+vitro|in\s+vivo|silenciamento|sobreexpress[aã]o|linhagens\s+celulares",
        re.IGNORECASE,
    ),
    re.compile(
        r"функциональное\s+исследование|in\s+vitro|in\s+vivo|нокаут|нокдаун|сверхэкспрессия|клеточная\s+линия|механизм|апоптоз|пролиферация|миграция|инвазия"
    ),
]


def _match_any(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def classify_item(item: OnlineAcquisitionItem) -> LiteratureType | None:
    """Classify a single item by title and journal keywords.

    Returns None if no confident classification can be made, or if the item
    matches negative patterns (review, thesis, editorial, etc.).
    """
    title = item.title or ""
    journal = item.journal or ""
    text = f"{title} {journal}"

    if _match_any(title, _REVIEW_PATTERNS):
        return None
    if _match_any(title, _THESIS_PATTERNS):
        return None
    if _match_any(title, _EDITORIAL_PATTERNS):
        return None

    # Priority: case report > sequencing > functional
    if _match_any(title, _CASE_REPORT_PATTERNS) or _match_any(journal, _CASE_REPORT_JOURNAL_PATTERNS):
        return LiteratureType.CASE_REPORT

    if _match_any(text, _SEQUENCING_PATTERNS):
        return LiteratureType.SEQUENCING

    if _match_any(text, _FUNCTIONAL_PATTERNS):
        return LiteratureType.FUNCTIONAL

    return None


def classify_items(items: Sequence[OnlineAcquisitionItem]) -> dict[LiteratureType, list[OnlineAcquisitionItem]]:
    """Classify a list of items and group by type (unmatched excluded)."""
    result: dict[LiteratureType, list[OnlineAcquisitionItem]] = {
        LiteratureType.CASE_REPORT: [],
        LiteratureType.SEQUENCING: [],
        LiteratureType.FUNCTIONAL: [],
    }
    for item in items:
        lit_type = classify_item(item)
        if lit_type is not None:
            result[lit_type].append(item)
    return result


def filter_by_type(
    items: Sequence[OnlineAcquisitionItem],
    types: Sequence[LiteratureType],
) -> list[OnlineAcquisitionItem]:
    """Filter items to only those matching the specified types."""
    type_set = set(types)
    return [item for item in items if classify_item(item) in type_set]
