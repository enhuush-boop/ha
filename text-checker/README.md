# TAYO — Human & AI Checker

Зөвхөн текст шинжилнэ. Загварын AI-like болон Human-like хоёр оноог зэрэг харуулна; эдгээр нь зохиогчийн бодит магадлал биш. Claim, Image, Video, Link болон браузерийн өргөтгөлийн хамаарал ороогүй.

## Windows дээр ажиллуулах
1. Python 3.12 суулгана (Python Launcher / py хамт).
2. ZIP-ийг задлаад `setup.bat` ажиллуулна. Интернэт хэрэгтэй.
3. `start.bat` ажиллуулж, терминалыг нээлттэй байлгана.
4. http://127.0.0.1:8010 хаягийг нээнэ.

Mac/Linux: `python3.12 -m venv .venv`, `source .venv/bin/activate`, `pip install -r requirements.txt`, `python -m uvicorn app.main:app --host 127.0.0.1 --port 8010`.

## Хязгаарлалт
- Загвар анхны 50+ үгтэй хүсэлтэд ~300 МБ татна. Жин, Python орчин ZIP-д ороогүй.
- 3-аас доош үг эсвэл 20-оос цөөн тэмдэгт, хэт давталттай текстэд загвар ажиллахгүй. Богино утгатай текстэд оноо гаргаж, тогтворгүй байж болохыг тайлбарлана.
- Монгол хэлний AI/Human нарийвчлал баталгаажаагүй; монгол текстийн загварын оноо нь туршилтынх.
- Зохиогчийг батлах, AI ашигласныг нотлох хэрэгсэл биш.
- Хэрэглэгчийн текстийг локал Python сервер шинжилнэ; нэмэлт API түлхүүр хэрэггүй.

Үндсэн эх: https://github.com/enhuush-boop/ha — MIT, LICENSE хавсаргасан.
Text-only extraction retains ModernBERT model loading and input-quality checks. Model weights retain their own license.

## Нэмэлт шалгуурууд
14 бүлэг статистик/загварын хэмжилт, шинжилсэн хамрах хүрээ, 250 үг хүртэлх хэсэг тус бүрийн оноо. Хамгийн ихдээ 8 хэсэг шинжилнэ; дутуу хамрагдсан бол тайланд тэмдэглэнэ. Статистик нь AI/Human оноог нэмэгдүүлэхгүй. Plagiarism, зөв бичих/дүрэм, баримт шалгах, perplexity холбогдоогүй.

## Chrome / Edge extension
Серверийг 127.0.0.1:8010 дээр асаасны дараа chrome://extensions (эсвэл edge://extensions) → Developer mode → Load unpacked → энэ төслийн extension/ хавтас. TAYO icon-ийг дарахад side panel нээгдэнэ. Серверийг ажиллуулсан терминалыг нээлттэй байлгана. Код өөрчилсөн бол extension-ийг Reload хийнэ.
