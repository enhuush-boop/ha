"""Descriptive text checks. These are not independent authorship detectors."""
import re
import unicodedata
from collections import Counter
from statistics import mean, pstdev

MN_PHRASES = ["дүгнэж хэлэхэд", "эцэст нь хэлэхэд", "онцлон тэмдэглэх нь зүйтэй", "чухал ач холбогдолтой", "орчин үеийн нийгэмд", "нөгөө талаас", "үүнээс гадна", "төдийгүй"]

def text_checks(text, stats, chunks, model_error, coverage):
    words = re.findall(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", text.lower())
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if s.strip()]
    lengths = [len(re.findall(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", s)) for s in sentences]
    counts = Counter(words)
    repeated = [{"word": w, "count": n} for w,n in counts.most_common(10) if n > 1]
    sentence_counts = Counter(re.sub(r"\s+", " ", s.lower()) for s in sentences)
    duplicate_sentences = sum(n-1 for n in sentence_counts.values())
    windows = [words[i:i+50] for i in range(0, len(words), 50) if len(words[i:i+50]) >= 20]
    mattr = mean(len(set(w))/len(w) for w in windows) if windows else None
    scripts = Counter()
    for c in text:
        if c.isalpha():
            name=unicodedata.name(c, "")
            scripts["Кирилл" if "CYRILLIC" in name else "Латин" if "LATIN" in name else "Бусад"] += 1
    invisible = sum(unicodedata.category(c)=="Cf" for c in text)
    phrases = [p for p in MN_PHRASES if p in text.lower()]
    urls = re.findall(r"https?://[^\s<>]+", text)
    rows=[]
    def add(key,name,value,explanation,status="measured"):
        rows.append(dict(id=key,name=name,value=value,explanation=explanation,status=status))
    add("length","Оролтын урт",f"{len(words)} үг / {len(text)} тэмдэгт","3-аас доош үг эсвэл 20-оос цөөн тэмдэгттэй оролтод загвар ажиллахгүй. Богино текстийн оноо тогтворгүй.")
    add("script","Үсгийн бичиг",dict(scripts),"Үсгийн ангилал; хэл таних загвар биш. Кирилл текст заавал монгол гэсэн үг биш.")
    add("vocabulary","Үгийн баялаг",stats["type_token_ratio"],"Ялгаатай үг / нийт үг. Текстийн уртаас хамаарна; AI-ийн баталгаа биш.")
    add("window_diversity","50 үгийн цонхны баялаг",round(mattr,3) if mattr is not None else None,"Давхцаагүй 50 үгтэй хэсгүүдийн үгийн баялгийн дундаж. 20-оос цөөн үгтэй сүүлийн хэсгийг тооцохгүй.","measured" if mattr is not None else "insufficient")
    add("repeated_words","Давтагдсан үг",repeated,"Хамгийн олон давтагдсан 10 үг. Нөхцөл, дагаврыг нэгтгэхгүй.")
    add("trigrams","Гурван үгийн давталт",stats["trigram_repetition"],"Давтагдсан гурван үгийн дарааллын эзлэх хувь; ишлэл, нэр томьёо мөн давтагдана.")
    add("sentences","Ижил өгүүлбэр",duplicate_sentences,"Зай болон том/жижиг үсгийг жигдрүүлсний дараах давхардсан өгүүлбэрийн тоо.")
    add("burstiness","Өгүүлбэрийн уртын хэлбэлзэл",stats["sentence_length_cv"] if len(lengths)>1 else None,"Стандарт хазайлт / дундаж урт. Хэв маягийн хэмжүүр бөгөөд AI/Human босго байхгүй.","measured" if len(lengths)>1 else "insufficient")
    add("sentence_lengths","Өгүүлбэрийн урт",{"min":min(lengths,default=0),"max":max(lengths,default=0),"mean":stats["avg_sentence_words"]},"Үгийн тоогоор хэмжсэн хамгийн богино, урт, дундаж өгүүлбэр.")
    add("paragraphs","Догол мөр ба жагсаалт",{"paragraphs":len([p for p in re.split(r"\n\s*\n",text) if p.strip()]),"list_lines":len(re.findall(r"(?m)^\s*(?:[-*•]|[0-9]+[.)])\s+",text))},"Формат ба бүтцийн статистик; жагсаалт нь AI бичсэнийг нотлохгүй.")
    add("phrases","Хэвшмэл хэллэг",{"mn":phrases,"en":stats["ai_phrases_found"]},"Гараар сонгосон хэллэгийн тааралт. Хүний бичвэрт ч түгээмэл тул оноонд нэмэхгүй.")
    add("characters","Нуугдмал тэмдэгт",invisible,"Unicode формат удирдах тэмдэгтийн тоо. Хууль ёсны формат, хуулсан текстээс үүсэж болно; засварлаагүй.")
    add("references","Холбоос ба ишлэлийн тэмдэг",{"urls":urls[:20],"numbered_citations":len(re.findall(r"\[\d+(?:[,–-]\d+)*\]",text))},"Зөвхөн бичвэрээс тоолсон. Холбоосыг нээхгүй, ишлэл үнэн эсэхийг шалгахгүй.")
    add("model","AI/Human загвар",{"successful_chunks":len(chunks),"error":model_error,"coverage":coverage},"ModernBERT загварын тусдаа шинжилгээ. Хэсэг бүрийн оноо нь холимог зохиогчийн баталгаа биш.","measured" if chunks else ("insufficient" if len(words)<50 else "unavailable"))
    add("plagiarism","Хуулбар / plagiarism",None,"Эх сурвалжийн индекс болон харьцуулах баримт холбогдоогүй. Ижил өгүүлбэрийн шалгалт үүнийг орлохгүй.","unavailable")
    add("grammar","Монгол зөв бичих ба дүрэм",None,"Монгол хэлний толь, дүрмийн хөдөлгүүр холбоогүй. Зөв бичгийн алдааг AI/Human шинж гэж оноохгүй.","unavailable")
    add("facts","Баримтын үнэн зөв",None,"Энэ Text хэрэгсэлд эх сурвалжтай баримт шалгах үйлчилгээ холбогдоогүй.","unavailable")
    add("perplexity","Perplexity",None,"Тусдаа тохирох хэлний загвараар тооцоогүй; санамсаргүй тоо гаргахгүй.","unavailable")
    return rows
