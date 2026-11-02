"""Full-text local machine translation. Presentation only, never eligibility.

No text is sent to a translation API. Public models download from Hugging Face;
the original text remains intact. Unsupported/failing text is explicitly marked.
"""
import re
import gc
from functools import lru_cache

SUPPORTED = {'en','de','pl','cs','fr','es','it','nl','pt','sv','da','fi','el','hu','ro','bg','hr','sk','sl','et','lv','lt'}


@lru_cache(maxsize=2)
def _model(name):
    from transformers import MarianMTModel, MarianTokenizer
    tokenizer = MarianTokenizer.from_pretrained(name)
    model = MarianMTModel.from_pretrained(name)
    model.eval()
    return tokenizer, model


def detect_language(text):
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
    return detect(text)


def _translate_stage(text, name):
    import torch
    tokenizer, model = _model(name)
    # Token windows cover every input token. No silent tokenizer truncation.
    ids = tokenizer.encode(text, add_special_tokens=False)
    pieces = []
    for start in range(0, len(ids), 220):
        window = tokenizer.decode(ids[start:start+220], skip_special_tokens=True)
        encoded = tokenizer(window, return_tensors='pt', truncation=False)
        with torch.inference_mode():
            output = model.generate(**encoded, max_new_tokens=480, num_beams=4)
        # Reaching generation cap is not a complete translation.
        if output.shape[-1] >= 480:
            raise ValueError('Translation output incomplete')
        pieces.append(tokenizer.decode(output[0], skip_special_tokens=True))
    return '\n'.join(pieces)


def translate_full(text, source=None):
    """(full Arabic or None, explicit presentation status), original never edited."""
    if not text.strip():
        return None, 'النص الأصلي فارغ؛ لا توجد ترجمة'
    try:
        language = source if source in SUPPORTED or source == 'ar' else detect_language(text)
        if language == 'ar':
            return text, 'النص الأصلي عربي'
        if language not in SUPPORTED:
            return None, 'لغة النص غير مدعومة حاليًا؛ النص الأصلي كامل أدناه'
        english = text
        if language != 'en':
            # Dedicated source-to-English model, not generic multilingual model.
            english = _translate_stage(text, 'Helsinki-NLP/opus-mt-' + language + '-en')
        arabic = _translate_stage(english, 'Helsinki-NLP/opus-mt-en-ar')
        return arabic, 'ترجمة آلية محلية قد تخطئ في النفي أو الشروط؛ الأصل هو المرجع' + (' (عبر الإنجليزية)' if language != 'en' else '')
    except Exception:
        return None, 'تعذّرت الترجمة الآلية لهذا النص؛ النص الأصلي كامل أدناه'


def translation_document(jobs, translate=translate_full):
    """Complete private UTF-8 document; no shortened descriptions or missing originals."""
    blocks = ['ترجمة آلية للوظائف: العنوان والوصف والشروط\nالنص الأصلي ورابط الإعلان هما المرجع. هذه ليست ضمان أهلية أو تأشيرة.\n']
    for index, job in enumerate(jobs, 1):
        title = job.get('title') or '(بدون عنوان)'
        original = job.get('original_full_text') or job.get('description') or ''
        incomplete = '' if job.get('original_full_text') else 'تنبيه: تعذّر التحقق من صفحة المصدر؛ النص التالي هو النص المحفوظ وقد يكون ناقصًا.\n'
        source_language = job.get('source_language')
        translated, status = translate(title + '\n\n' + original, source_language)
        label = '⚠️ محتاجة تحقق' if job.get('private_fit_status') != 'pass' else '✅ متوافقة مبدئيًا'
        blocks.append('\n' + '='*50 + '\n' + str(index) + '. ' + label + '\n' + job.get('private_fit_reason','') + '\n' + job.get('url','') + '\n\n' + incomplete + status + '\n\nالعنوان والوصف والشروط بالعربية:\n' + (translated or '[الترجمة غير متاحة، لم يُحذف النص الأصلي]') + '\n\nالنص الأصلي الكامل:\n' + title + '\n\n' + original)
    return '\n'.join(blocks).encode('utf-8')
